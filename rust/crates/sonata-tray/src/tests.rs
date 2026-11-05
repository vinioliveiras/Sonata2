//! Ported from tests/test_tray.py, test_tray_blank.py,
//! test_steam_tray_menu.py, test_tray_open_app.py, test_quiet_background.py
//! (TrayTooltipTest) and test_regressions.py (the one-tone icons). No
//! window opens; the D-Bus test runs on its own private bus (GTestDBus).

use super::*;
use crate::appmatch::{app_of_pid_with, index_of, window_of_with};
use crate::dbusmenu::{Open, Reply, Transport};
use crate::icon::{next_shape, Next};
use crate::item::{icon_of, merge, tooltip_of, Pending, Props};
use crate::traybox::Clicks;
use glib::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::{Duration, Instant};

fn v<T: ToVariant>(x: T) -> glib::Variant {
    x.to_variant()
}

fn node(id: i32, children: Vec<glib::Variant>, props: &[(&str, glib::Variant)]) -> glib::Variant {
    let d = glib::VariantDict::new(None);
    for (k, val) in props {
        d.insert_value(k, val);
    }
    let kids: Vec<glib::Variant> = children.into_iter().map(|c| glib::Variant::from_variant(&c)).collect();
    glib::Variant::tuple_from_iter([id.to_variant(), d.end(), glib::Variant::array_from_iter_with_type(glib::VariantTy::VARIANT, kids)])
}

fn sample_layout(submenu_filled: bool) -> Node {
    let sub = if submenu_filled { vec![node(21, vec![], &[("label", v("Sub _One"))])] } else { vec![] };
    let lay = node(
        0,
        vec![
            node(1, vec![], &[("label", v("_Open Window"))]),
            node(2, vec![], &[("label", v("Disabled")), ("enabled", v(false))]),
            node(3, vec![], &[("type", v("separator"))]),
            node(4, vec![], &[("label", v("Notifications")), ("toggle-type", v("checkmark")), ("toggle-state", v(1i32))]),
            node(5, vec![], &[("label", v("Mute")), ("toggle-type", v("checkmark")), ("toggle-state", v(0i32))]),
            node(6, vec![], &[("label", v("Hidden")), ("visible", v(false))]),
            node(3, vec![], &[("type", v("separator"))]),
            node(20, sub, &[("label", v("More")), ("children-display", v("submenu"))]),
            node(9, vec![], &[("label", v("Save__As"))]),
        ],
        &[],
    );
    Node::from_variant(&lay).unwrap()
}

fn labels(secs: &[Vec<Entry>]) -> Vec<Vec<String>> {
    secs.iter().map(|s| s.iter().map(|e| e.label().to_string()).collect()).collect()
}

#[test]
fn split_service_forms() {
    assert_eq!(split_service(":1.5/org/ayatana/NotificationItem/x", ""), (":1.5".into(), "/org/ayatana/NotificationItem/x".into()));
    assert_eq!(split_service("org.kde.StatusNotifierItem-12-1", ""), ("org.kde.StatusNotifierItem-12-1".into(), "/StatusNotifierItem".into()));
    assert_eq!(split_service("/StatusNotifierItem", ":1.9"), (":1.9".into(), "/StatusNotifierItem".into()));
}

#[test]
fn watcher_registration_and_removal_logic() {
    let mut w = WatcherState::default();
    assert_eq!(w.register_item("/StatusNotifierItem", ":1.5").as_deref(), Some(":1.5/StatusNotifierItem"));
    assert_eq!(w.register_item("/StatusNotifierItem", ":1.5"), None); // known already
    assert_eq!(w.register_item("org.app.Item", ":1.6").as_deref(), Some("org.app.Item/StatusNotifierItem"));
    assert_eq!(w.register_item("/x", ""), None); // nameless
    assert!(w.register_host(":1.7"));
    assert!(!w.register_host(":1.7"));
    assert_eq!(w.name_gone(":1.5"), (false, vec![":1.5/StatusNotifierItem".to_string()]));
    assert_eq!(w.items, vec!["org.app.Item/StatusNotifierItem".to_string()]);
    assert_eq!(w.name_gone(":1.7"), (true, vec![]));
}

#[test]
fn menu_layout_conversion() {
    let mut l = HashMap::new();
    let secs = layout_sections(&sample_layout(true), &mut l);
    assert_eq!(labels(&secs), vec![vec!["Open Window", "Disabled"], vec!["Notifications", "Mute"], vec!["More", "Save_As"]]);
    assert!(!secs[0][1].enabled());
    assert!(secs[0][0].enabled());
    assert!(matches!(secs[0][0], Entry::Item { id: 1, checked: None, .. }));
    assert!(matches!(secs[1][0], Entry::Item { checked: Some(true), .. }));
    assert!(matches!(secs[1][1], Entry::Item { id: 5, checked: Some(false), .. }));
    match &secs[2][0] {
        Entry::Submenu { sections, .. } => assert_eq!(labels(sections), vec![vec!["Sub One"]]),
        e => panic!("{e:?}"),
    }
    assert_eq!(l.get(&9).map(String::as_str), Some("Save_As"));
    // an empty submenu still opens: one disabled row
    let secs = layout_sections(&sample_layout(false), &mut l);
    match &secs[2][0] {
        Entry::Submenu { sections, .. } => assert_eq!(sections, &vec![vec![Entry::disabled("")]]),
        e => panic!("{e:?}"),
    }
}

#[test]
fn pixmap_conversion() {
    // smallest >= 16, else the largest; short data ignored
    let sizes = [(8, 8, 256), (32, 32, 4096), (64, 64, 16384)];
    assert_eq!(pick_pixmap(&sizes, 16), Some(1));
    assert_eq!(pick_pixmap(&sizes, 128), Some(2));
    assert_eq!(pick_pixmap(&[(4, 4, 10)], 16), None);
    // ARGB red -> RGBA red
    assert_eq!(argb_to_rgba(&[255, 255, 0, 0], 1, 1), vec![255, 0, 0, 255]);
    // through the a(iiay) property
    let data: Vec<u8> = [128u8, 0, 0, 255].repeat(16 * 16);
    let pix = vec![(16i32, 16i32, data)].to_variant();
    let mut props = Props::new();
    props.insert("IconPixmap".into(), pix);
    match icon_of(&props, 1, &|_, _| false) {
        Some(IconSource::Pixmap(p)) => assert_eq!((p.width, p.argb.len()), (16, 1024)),
        o => panic!("{o:?}"),
    }
}

#[test]
fn icon_name_attention_and_theme_path() {
    let mut props = Props::new();
    props.insert("IconName".into(), v("user-available-symbolic"));
    props.insert("AttentionIconName".into(), v("mail-unread-symbolic"));
    let has = |n: &str, _: &str| n != "missing";
    assert!(matches!(icon_of(&props, 1, &has), Some(IconSource::Name(n)) if n == "user-available-symbolic"));
    props.insert("Status".into(), v("NeedsAttention"));
    assert!(matches!(icon_of(&props, 1, &has), Some(IconSource::Name(n)) if n == "mail-unread-symbolic"));
    // the app's own folder first
    let dir = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(dir.path().join("hicolor/22x22/apps")).unwrap();
    std::fs::write(dir.path().join("hicolor/22x22/apps/missing.png"), b"png").unwrap();
    let mut props = Props::new();
    props.insert("IconName".into(), v("missing"));
    props.insert("IconThemePath".into(), v(dir.path().to_str().unwrap()));
    assert!(matches!(icon_of(&props, 1, &has), Some(IconSource::File(p)) if p.ends_with("hicolor/22x22/apps/missing.png")));
    props.insert("IconThemePath".into(), v(""));
    assert!(icon_of(&props, 1, &has).is_none());
}

#[test]
fn tooltip_title_body_and_markup() {
    let mut props = Props::new();
    props.insert("Title".into(), v("Discord"));
    props.insert("ToolTip".into(), ("", Vec::<(i32, i32, Vec<u8>)>::new(), "", "Running").to_variant());
    assert_eq!(tooltip_of(&props), "Discord\nRunning");
    props.insert("ToolTip".into(), ("", Vec::<(i32, i32, Vec<u8>)>::new(), "Telegram", "<b>3</b> unread").to_variant());
    assert_eq!(tooltip_of(&props), "Telegram\n3 unread");
}

/// TrayTooltipTest: an app ticking its tooltip made every icon be read and
/// drawn again.
#[test]
fn test_tooltip_reads_only_the_tooltip_and_keeps_the_icon() {
    let mut p = Pending::default();
    p.note("NewToolTip", true);
    assert_eq!(p.take(), Some(vec!["ToolTip".to_string()]));
    let mut props = Props::new();
    props.insert("IconName".into(), v("app-icon"));
    props.insert("ToolTip".into(), ("", Vec::<(i32, i32, Vec<u8>)>::new(), "Old", "").to_variant());
    let tip = |t: &str| Some(("", Vec::<(i32, i32, Vec<u8>)>::new(), t, "").to_variant());
    assert_eq!(merge(&mut props, vec![("ToolTip".into(), tip("New"))]), (true, false)); // icon kept
    assert_eq!(merge(&mut props, vec![("ToolTip".into(), tip("New"))]), (false, false)); // the same again: no redraw
    assert_eq!(merge(&mut props, vec![("IconName".into(), Some(v("other")))]), (true, true)); // a new icon: drawn again
    assert_eq!(merge(&mut props, vec![("IconName".into(), None)]), (true, true));
    assert!(!props.contains_key("IconName"));
}

#[test]
fn test_unknown_signal_reads_everything() {
    let mut p = Pending::default();
    p.note("XAyatanaNewLabel", true);
    assert_eq!(p.take(), None);
    p.note("NewIcon", false); // not read yet: everything
    assert_eq!(p.take(), None);
    p.note("NewIcon", true);
    p.note("NewTitle", true);
    assert_eq!(p.take(), Some(vec!["IconName".into(), "IconPixmap".into(), "Title".into()]));
}

fn texture(f: impl Fn(usize, usize) -> [u8; 4], n: usize) -> Vec<u8> {
    let mut buf = Vec::new();
    for y in 0..n {
        for x in 0..n {
            buf.extend_from_slice(&f(x, y));
        }
    }
    buf
}

/// Menu bar tray icons must never be coloured: a two-tone icon becomes a
/// silhouette with its light details cut out.
#[test]
fn test_tray_icons_are_one_tone() {
    let n = 16;
    let px = texture(|x, y| if (5..11).contains(&x) && (5..11).contains(&y) { [255, 255, 255, 255] } else { [88, 101, 242, 255] }, n);
    let out = mono_mask(&px, n * 4, n, n).unwrap();
    assert_eq!(out[8 * n * 4 + 8 * 4 + 3], 0); // the light centre: a hole
    assert!(out[n * 4 + 4 + 3] > 200); // the body: solid
}

/// Discord's tray icon looked grey: its logo is drawn half see-through, and
/// only its alpha was kept. The shape is made solid, so it shows white in
/// Dark Mode and black in Light.
#[test]
fn test_see_through_tray_icon_becomes_solid() {
    let n = 16;
    let px = texture(|x, y| if (3..13).contains(&x) && (3..13).contains(&y) { [200, 200, 200, 140] } else { [0, 0, 0, 0] }, n);
    let out = mono_mask(&px, n * 4, n, n).unwrap();
    assert_eq!(out[8 * n * 4 + 8 * 4 + 3], 255); // solid
    assert_eq!(out[3], 0); // outside stays clear
}

/// Vini: Claude's icon in the menu bar went invisible for a while, then came
/// back. An app that sends a blank picture (all see-through) keeps showing
/// its last icon -- or a generic one -- never an empty gap.
#[test]
fn test_blank_tray_icon_keeps_the_last_one() {
    let n = 16;
    assert!(mono_mask(&[255, 255, 255, 0].repeat(n * n), n * 4, n, n).is_none());
    assert!(mono_mask(&[255, 255, 255, 255].repeat(n * n), n * 4, n, n).is_some());
    assert_eq!(next_shape(None, true), Next::Keep);
    assert_eq!(next_shape(None, false), Next::Generic);
    assert!(matches!(next_shape(Some(vec![0; 4]), true), Next::Shape(_)));
}

// -- menus -------------------------------------------------------------------

type Calls = Rc<RefCell<Vec<(String, glib::Variant)>>>;

/// A menu whose calls are recorded; GetLayout answers from `replies`.
fn fake_menu(steam: bool) -> (Rc<DBusMenu>, Calls, Rc<RefCell<Vec<Node>>>) {
    let calls: Calls = Rc::default();
    let replies: Rc<RefCell<Vec<Node>>> = Rc::default();
    let (c, r) = (calls.clone(), replies.clone());
    let t: Transport = Box::new(move |method: &str, args: glib::Variant, _rt: Option<&'static str>, reply: Option<Reply>| {
        c.borrow_mut().push((method.to_string(), args));
        if method == "AboutToShow" {
            if let Some(cb) = reply {
                cb(Some((false,).to_variant()));
            }
        } else if method == "GetLayout" {
            if let Some(cb) = reply {
                let next = if r.borrow().is_empty() { None } else { Some(r.borrow_mut().remove(0)) };
                cb(next.map(|n| glib::Variant::tuple_from_iter([1u32.to_variant(), to_variant(&n)])));
            }
        }
    });
    let m = DBusMenu::with_transport("/MenuBar", t);
    m.set_steam(steam);
    m.sections(&steam_layout(0, "Half-Life 2"));
    (m, calls, replies)
}

fn to_variant(n: &Node) -> glib::Variant {
    let props: Vec<(&str, glib::Variant)> = n.props.iter().map(|(k, v)| (k.as_str(), v.clone())).collect();
    node(n.id, n.children.iter().map(to_variant).collect(), &props)
}

/// Steam's menu (with ids from `base`), rebuilt with new ids as on every
/// AboutToShow / "opened".
fn steam_layout(base: i32, label3: &str) -> Node {
    let kid = |i: i32, label: &str| node(base + i, vec![], &[("label", v(label))]);
    Node::from_variant(&node(0, vec![kid(1, "_Library"), kid(2, "Store"), kid(3, label3), kid(4, "Exit Steam")], &[])).unwrap()
}

fn events(calls: &Calls) -> Vec<(i32, String)> {
    calls
        .borrow()
        .iter()
        .filter(|(m, _)| m == "Event")
        .map(|(_, a)| (a.child_value(0).get::<i32>().unwrap(), a.child_value(1).get::<String>().unwrap()))
        .collect()
}

#[test]
fn steam_links() {
    assert_eq!(steam_link("Library"), Some("steam://open/games"));
    assert_eq!(steam_link("Biblioteca"), Some("steam://open/games"));
    assert_eq!(steam_link("Configurações"), Some("steam://open/settings"));
    assert_eq!(steam_link("Exit Steam"), Some("-shutdown"));
    assert_eq!(steam_link("Half-Life 2"), None); // a recent game: Steam's own Event
}

/// Vini: Steam's menu in the menu bar (Library, Store...) opened nothing --
/// Steam ignores the clicks it gets. Its items open with steam:// links.
#[test]
fn test_steam_items_open_with_links() {
    let (m, calls, _) = fake_menu(true);
    let ran: Rc<RefCell<Vec<String>>> = Rc::default();
    let r = ran.clone();
    *m.run_steam.borrow_mut() = Box::new(move |l| {
        r.borrow_mut().push(l.to_string());
        true
    });
    m.event(1, "clicked");
    m.event(4, "clicked");
    m.event(3, "clicked");
    assert_eq!(*ran.borrow(), vec!["steam://open/games", "-shutdown"]);
    assert_eq!(events(&calls), vec![(3, "clicked".to_string())]); // the game: the usual click
}

#[test]
fn test_other_apps_menus_unchanged() {
    let (m, calls, _) = fake_menu(false);
    *m.run_steam.borrow_mut() = Box::new(|_| panic!("not Steam"));
    m.event(1, "clicked");
    assert_eq!(events(&calls), vec![(1, "clicked".to_string())]);
}

/// The menu "open" with a fake popover: counts its redraws.
fn open_fake(m: &DBusMenu) -> Rc<RefCell<Vec<Vec<Vec<Entry>>>>> {
    let drawn: Rc<RefCell<Vec<Vec<Vec<Entry>>>>> = Rc::default();
    let d = drawn.clone();
    m.open.replace(Some(Rc::new(Open { visible: Box::new(|| true), redraw: Box::new(move |s| d.borrow_mut().push(s)) })));
    drawn
}

// glib::timeout_add_local attaches to the global default context: tests
// that run timers own it, one at a time
static MAIN: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn in_context(f: impl FnOnce()) {
    let _one = MAIN.lock().unwrap_or_else(|e| e.into_inner());
    glib::MainContext::default().with_thread_default(f).unwrap();
}

/// Vini: clicking Steam's menu did nothing and froze the menu bar -- each
/// redraw sent AboutToShow, Steam answered with a rebuilt menu and
/// LayoutUpdated, and the open menu's rows were swapped without end.
#[test]
fn test_redraw_sends_no_about_to_show() {
    in_context(|| {
        let (m, calls, replies) = fake_menu(true);
        open_fake(&m);
        replies.borrow_mut().push(steam_layout(100, "Half-Life 2"));
        for _ in 0..5 {
            m.on_signal("LayoutUpdated"); // a burst: one read
        }
        m.flush();
        let methods: Vec<String> = calls.borrow().iter().map(|(m, _)| m.clone()).collect();
        assert_eq!(methods, vec!["GetLayout"]);
    });
}

#[test]
fn test_renumbered_menu_keeps_rows_and_clicks_reach_steam() {
    in_context(|| {
        let (m, calls, replies) = fake_menu(true);
        *m.run_steam.borrow_mut() = Box::new(|_| true);
        let drawn = open_fake(&m);
        replies.borrow_mut().push(steam_layout(100, "Half-Life 2"));
        m.on_signal("LayoutUpdated");
        m.flush();
        assert!(drawn.borrow().is_empty()); // the same menu: rows untouched
        calls.borrow_mut().clear();
        m.event(3, "clicked"); // the game, shown with id 3
        assert_eq!(events(&calls), vec![(103, "clicked".to_string())]);
        assert!(m.open.borrow().is_none()); // closing: no more redraws
        m.on_signal("LayoutUpdated");
        assert!(!m.has_pending());
    });
}

#[test]
fn test_changed_menu_is_redrawn() {
    in_context(|| {
        let (m, _calls, replies) = fake_menu(true);
        let drawn = open_fake(&m);
        replies.borrow_mut().push(steam_layout(100, "Portal 2"));
        m.on_signal("LayoutUpdated");
        m.flush();
        assert_eq!(drawn.borrow().len(), 1);
        assert_eq!(m.label(103).as_deref(), Some("Portal 2"));
    });
}

#[test]
fn fetch_asks_empty_submenus_then_reads_again() {
    let (m, calls, replies) = fake_menu(false);
    replies.borrow_mut().push(sample_layout(false));
    replies.borrow_mut().push(sample_layout(true));
    let got = Rc::new(RefCell::new(None));
    let g = got.clone();
    m.fetch(move |lay| *g.borrow_mut() = lay);
    let asked: Vec<(String, Option<i32>)> = calls.borrow().iter().map(|(m, a)| (m.clone(), (m == "AboutToShow").then(|| a.child_value(0).get::<i32>().unwrap()))).collect();
    assert_eq!(asked, vec![("AboutToShow".into(), Some(0)), ("GetLayout".into(), None), ("AboutToShow".into(), Some(20)), ("GetLayout".into(), None)]);
    let lay = got.borrow().clone().unwrap();
    assert_eq!(labels(&layout_sections(&lay, &mut HashMap::new()))[2], vec!["More", "Save_As"]);
}

// -- the app behind an icon ----------------------------------------------------

#[test]
fn test_app_of_pid_from_the_executable_or_command() {
    let root = tempfile::tempdir().unwrap();
    let d = root.path().join("42");
    std::fs::create_dir_all(&d).unwrap();
    std::os::unix::fs::symlink("/opt/discord/Discord", d.join("exe")).unwrap();
    std::fs::write(d.join("cmdline"), b"/opt/discord/Discord\0--type=renderer\0").unwrap();
    std::fs::write(d.join("comm"), "Discord\n").unwrap();
    let m = |n: &str| (n == "Discord").then(|| "discord".to_string());
    assert_eq!(app_of_pid_with(42, root.path(), &m).as_deref(), Some("discord"));
    assert_eq!(app_of_pid_with(42, root.path(), &|_| None), None);
    assert_eq!(app_of_pid_with(7, root.path(), &m), None); // gone: nothing, no error
}

#[test]
fn app_index_names() {
    let idx = index_of(&[
        ("com.discordapp.Discord".into(), "discord".into(), "discord-wrapper".into()),
        ("steam".into(), "".into(), "steam-launcher".into()),
        ("steam-native".into(), "".into(), "steam".into()),
    ]);
    assert_eq!(idx.get("discord").map(String::as_str), Some("com.discordapp.Discord"));
    assert_eq!(idx.get("com.discordapp.discord").map(String::as_str), Some("com.discordapp.Discord"));
    assert_eq!(idx.get("discord-wrapper").map(String::as_str), Some("com.discordapp.Discord"));
    // an exact name wins over one without its wrapper suffix
    assert_eq!(idx.get("steam").map(String::as_str), Some("steam"));
}

#[test]
fn window_of_picks_the_apps_last_used_window() {
    let views = serde_json::json!([
        {"id": 1, "app-id": "steam", "type": "toplevel", "last-focus-timestamp": 5},
        {"id": 2, "app-id": "steam", "type": "toplevel", "last-focus-timestamp": 9},
        {"id": 3, "app-id": "steam", "type": "background", "last-focus-timestamp": 20},
        {"id": 4, "app-id": "firefox", "last-focus-timestamp": 30},
    ]);
    let none = |_: &str| None;
    assert_eq!(window_of_with(&views, "steam", "", &none, &none), Some(2));
    assert_eq!(window_of_with(&views, "steam.desktop", "", &none, &none), Some(2));
    assert_eq!(window_of_with(&views, "discord", "", &none, &none), None);
    let m = |a: &str| (a == "firefox").then(|| "org.mozilla.firefox".to_string());
    let name = |d: &str| (d == "org.mozilla.firefox").then(|| "Firefox".to_string());
    assert_eq!(window_of_with(&views, "", "firefox", &m, &name), Some(4));
}

/// Vini's log: starting Steam from its tray menu died in the Python
/// spawn; a real program runs now.
#[test]
fn test_link_really_starts_steam() {
    let d = tempfile::tempdir().unwrap();
    let fake = d.path().join("steam");
    let out = d.path().join("ran");
    std::fs::write(&fake, format!("#!/bin/sh\necho \"$1\" > {}\n", out.display())).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert!(crate::appmatch::spawn_detached(&fake, "steam://open/games"));
    let end = Instant::now() + Duration::from_secs(3);
    while std::fs::read_to_string(&out).map(|s| s.trim().is_empty()).unwrap_or(true) && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(std::fs::read_to_string(&out).unwrap().trim(), "steam://open/games");
}

fn settle(ms: u64) {
    let end = Instant::now() + Duration::from_millis(ms);
    while Instant::now() < end {
        glib::MainContext::default().iteration(false);
        std::thread::sleep(Duration::from_millis(2));
    }
}

/// Vini: double-clicking Steam's icon picked an item of the menu the first
/// click had opened; Spotify's window came and went (its Activate
/// toggles). The single click now waits the double-click time.
#[test]
fn test_first_click_waits_for_a_second() {
    in_context(|| {
        let calls: Rc<RefCell<Vec<&str>>> = Rc::default();
        let clicks: Rc<Clicks> = Rc::default();
        let single = |c: &Rc<RefCell<Vec<&'static str>>>| {
            let c = c.clone();
            move || c.borrow_mut().push("single")
        };
        // double-click: press, release (clicked), press #2 (open), release (clicked)
        clicks.left(50, single(&calls));
        clicks.double();
        calls.borrow_mut().push("open");
        clicks.left(50, single(&calls));
        settle(150);
        assert_eq!(*calls.borrow(), vec!["open"]); // no Activate, no menu
        // a single click: its action, once the double-click time is over
        clicks.left(50, single(&calls));
        assert_eq!(*calls.borrow(), vec!["open"]);
        settle(150);
        assert_eq!(*calls.borrow(), vec!["open", "single"]);
    });
}

// -- on a private bus ------------------------------------------------------------

const ITEM_XML: &str = r#"<node><interface name="org.kde.StatusNotifierItem">
  <property name="Id" type="s" access="read"/><property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/><property name="IconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="Menu" type="o" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"#;

fn connect(addr: &str) -> gio::DBusConnection {
    gio::DBusConnection::for_address_sync(addr, gio::DBusConnectionFlags::AUTHENTICATION_CLIENT | gio::DBusConnectionFlags::MESSAGE_BUS_CONNECTION, None, None::<&gio::Cancellable>).unwrap()
}

fn wait_for(cond: impl Fn() -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(5);
    let ctx = glib::MainContext::default();
    while !cond() && Instant::now() < end {
        ctx.iteration(false);
        std::thread::sleep(Duration::from_millis(1));
    }
    cond()
}

/// An app's item on its own connection: closing it makes its name vanish
/// like a quitting app.
struct FakeItem {
    conn: gio::DBusConnection,
    status: Rc<RefCell<String>>,
    calls: Rc<RefCell<Vec<(String, glib::Variant)>>>,
}

impl FakeItem {
    fn new(addr: &str, ident: &str) -> FakeItem {
        let conn = connect(addr);
        let status = Rc::new(RefCell::new("Active".to_string()));
        let calls: Rc<RefCell<Vec<(String, glib::Variant)>>> = Rc::default();
        let info = gio::DBusNodeInfo::for_xml(ITEM_XML).unwrap().lookup_interface(ITEM_IFACE).unwrap();
        let (id, s, c) = (ident.to_string(), status.clone(), calls.clone());
        let reg = conn
            .register_object("/StatusNotifierItem", &info)
            .method_call(move |_, _, _, _, method, params, inv| {
                c.borrow_mut().push((method.to_string(), params));
                inv.return_value(None);
            })
            .property(move |_, _, _, _, prop| match prop {
                "Id" => id.to_variant(),
                "Title" => "Discord".to_variant(),
                "Status" => s.borrow().to_variant(),
                "IconName" => "user-available-symbolic".to_variant(),
                "ToolTip" => ("", Vec::<(i32, i32, Vec<u8>)>::new(), "Discord", "Running").to_variant(),
                "Menu" => glib::Variant::parse(Some(glib::VariantTy::OBJECT_PATH), "'/'").unwrap(),
                _ => false.to_variant(),
            })
            .build()
            .unwrap();
        std::mem::forget(reg);
        conn.call(Some(WATCHER), WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem", Some(&("/StatusNotifierItem",).to_variant()), None, gio::DBusCallFlags::NO_AUTO_START, 2000, None::<&gio::Cancellable>, |_| {});
        FakeItem { conn, status, calls }
    }

    fn key(&self) -> String {
        format!("{}/StatusNotifierItem", self.conn.unique_name().unwrap())
    }

    fn set_status(&self, status: &str) {
        self.status.replace(status.to_string());
        self.conn.emit_signal(None, "/StatusNotifierItem", ITEM_IFACE, "NewStatus", Some(&(status,).to_variant())).unwrap();
    }
}

#[test]
fn watcher_and_host_on_a_private_bus() {
    in_context(|| {
        let bus = gio::TestDBus::new(gio::TestDBusFlags::NONE);
        bus.up();
        let addr = bus.bus_address().unwrap().to_string();
        let host = Host::new(Some(connect(&addr)), true);
        let watcher = host.watcher().unwrap().clone();
        assert!(wait_for(|| watcher.owned()));
        let seen = Rc::new(Cell::new(0));
        let s = seen.clone();
        let lid = host.connect_changed(move |_| s.set(s.get() + 1));

        let it = FakeItem::new(&addr, "discord");
        let key = it.key();
        assert!(wait_for(|| host.item(&key).is_some_and(|i| i.ready())));
        assert!(watcher.items().contains(&key));
        let item = host.item(&key).unwrap();
        assert_eq!(item.id(), "discord");
        assert_eq!(item.tooltip(), "Discord\nRunning");
        assert!(item.visible());
        assert!(item.menu().is_none()); // Menu "/": none
        assert!(seen.get() > 0);

        // the property, as other hosts read it
        let other = connect(&addr);
        let got: Rc<RefCell<Option<glib::Variant>>> = Rc::default();
        let g = got.clone();
        other.call(Some(WATCHER), WATCHER_PATH, "org.freedesktop.DBus.Properties", "GetAll", Some(&(WATCHER,).to_variant()), None, gio::DBusCallFlags::NONE, 2000, None::<&gio::Cancellable>, move |r| {
            g.replace(r.ok());
        });
        assert!(wait_for(|| got.borrow().is_some()));
        let all = got.take().unwrap();
        let d = glib::VariantDict::new(Some(&all.child_value(0)));
        assert!(d.lookup::<Vec<String>>("RegisteredStatusNotifierItems").unwrap().unwrap().contains(&key));
        assert_eq!(d.lookup::<bool>("IsStatusNotifierHostRegistered").unwrap(), Some(true));

        // Passive hides it, NeedsAttention shows it again
        let serial = item.icon_serial();
        it.set_status("Passive");
        assert!(wait_for(|| !item.visible()));
        assert!(item.icon_serial() > serial); // Status is part of the icon
        it.set_status("NeedsAttention");
        assert!(wait_for(|| item.visible()));

        // actions reach the app
        item.activate(1, 2, None);
        item.scroll(1, "vertical");
        assert!(wait_for(|| it.calls.borrow().len() >= 2));
        assert_eq!(it.calls.borrow()[0].0, "Activate");
        assert_eq!(it.calls.borrow()[1].1.get::<(i32, String)>(), Some((1, "vertical".into())));

        // the app quits: its name vanishes
        it.conn.close_sync(None::<&gio::Cancellable>).unwrap();
        assert!(wait_for(|| host.item(&key).is_none()));
        assert!(!watcher.items().contains(&key));
        host.disconnect(lid);

        // a second host follows this watcher without one of its own (trial)
        let guest = Host::new(Some(connect(&addr)), false);
        assert!(guest.watcher().is_none());
        let it2 = FakeItem::new(&addr, "steam");
        let k2 = it2.key();
        assert!(wait_for(|| guest.item(&k2).is_some_and(|i| i.ready())));
        drop(it2);
        bus.down();
    });
}

/// Vini's Rust session, auto-hide on: the menu bar slid away from under
/// an open tray menu (Python counted every menu in ui.menu.OPEN; the Rust
/// bar only its own). The menu bar hears each tray menu show and go -- a
/// menu redrawn in place (Steam's) included, one "gone" for each "shown".
#[test]
fn test_bar_hid_under_a_tray_menu() {
    in_own_process("tests::bar_hid_under_a_tray_menu_gtk");
}

/// Runs `test` (an #[ignore]d one) alone in a new process of this test
/// binary: GTK takes the main context of the thread that starts it for
/// good, and the other tests here need it.
fn in_own_process(test: &str) {
    let exe = std::env::current_exe().unwrap();
    let out = std::process::Command::new(exe).args(["--ignored", "--exact", test, "--test-threads=1"]).output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{test}:\n{text}\n{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("1 passed"), "{test} didn't run:\n{text}");
}

#[test]
#[ignore = "GTK: run by test_bar_hid_under_a_tray_menu in a process of its own"]
fn bar_hid_under_a_tray_menu_gtk() {
    {
        use gtk::prelude::*;
        if gtk::init().is_err() {
            return; // (no display)
        }
        let (m, _, _) = fake_menu(false);
        let win = gtk::Window::new();
        let btn = gtk::Button::with_label("tray");
        win.set_child(Some(&btn));
        win.present();
        let heard: Rc<RefCell<Vec<bool>>> = Rc::default();
        let h = heard.clone();
        let hook: crate::traybox::MenuHook = Rc::new(move |open| h.borrow_mut().push(open));
        crate::traybox::show(&m, &btn, m.sections(&steam_layout(0, "Half-Life 2")), Some(hook));
        assert_eq!(*heard.borrow(), vec![true]);
        let redraw = m.open.borrow().clone().unwrap();
        (redraw.redraw)(m.sections(&steam_layout(0, "Portal")));
        let ctx = glib::MainContext::default();
        while ctx.iteration(false) {}
        assert_eq!(*heard.borrow(), vec![true, true, false]);
        let now = m.open.borrow().clone().unwrap();
        (now.redraw)(Vec::new()); // (an empty redraw closes it)
        while ctx.iteration(false) {}
        assert_eq!(*heard.borrow(), vec![true, true, false, false]);
        win.destroy();
    }
}
