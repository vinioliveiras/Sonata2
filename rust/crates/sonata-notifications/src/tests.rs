//! Ported from tests/test_notifications_dnd.py, test_notify_locked.py,
//! test_notify_focus.py, test_badges_after_restart.py and
//! test_dock_badges.py (the notification parts). No window opens: the
//! server runs with a recording presenter, the D-Bus test on its own
//! private bus (GTestDBus).

use crate::model::*;
use crate::server::*;
use glib::prelude::*;
use serde_json::{json, Map, Value};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::{Duration, Instant};

const WEB: &str = "sonata2-webapp-waee32c088c";

/// The tests share one temporary HOME, XDG folders and main context
/// (cargo runs them on threads of one process): they take turns.
fn one_at_a_time() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn fresh_config() {
    let _ = sonata_config::save(CONFIG, &json!({"dnd": false, "apps": {}}).as_object().unwrap().clone());
    let _ = sonata_config::save("dock", &Map::new());
    let _ = sonata_config::save("launchpad", &Map::new());
    let _ = sonata_config::save("webapps", &Map::new());
    forget_locked();
}

#[derive(Default)]
struct Fake {
    /// desktop id -> name
    apps: RefCell<HashMap<String, String>>,
    /// app-id -> desktop id
    matches: RefCell<HashMap<String, String>>,
    webapps: RefCell<Map<String, Value>>,
    has_window: Cell<bool>,
    forward: RefCell<Vec<(String, String)>>,
    launched: RefCell<Vec<String>>,
}

struct FakeEnv(Rc<Fake>);

impl Env for FakeEnv {
    fn app_name(&self, desktop: &str) -> Option<String> {
        let apps = self.0.apps.borrow();
        apps.get(desktop).or_else(|| apps.get(desktop.strip_suffix(".desktop").unwrap_or(desktop))).cloned()
    }
    fn match_app_id(&self, app_id: &str) -> Option<String> {
        self.0.matches.borrow().get(app_id).cloned()
    }
    fn webapps(&self) -> Map<String, Value> {
        self.0.webapps.borrow().clone()
    }
    fn bring_forward(&self, desktop: &str, app: &str) -> bool {
        self.0.forward.borrow_mut().push((desktop.into(), app.into()));
        self.0.has_window.get()
    }
    fn launch(&self, desktop: &str) -> bool {
        self.0.launched.borrow_mut().push(desktop.into());
        true
    }
}

#[derive(Default)]
struct Shown {
    shown: RefCell<Vec<u32>>,
    hidden: RefCell<Vec<u32>>,
    on_screen: RefCell<Vec<u32>>,
    center: Cell<bool>,
}

impl Presenter for Shown {
    fn show_banner(&self, n: &Note) {
        self.shown.borrow_mut().push(n.id);
        self.on_screen.borrow_mut().push(n.id);
    }
    fn hide_banner(&self, id: u32) {
        self.hidden.borrow_mut().push(id);
        self.on_screen.borrow_mut().retain(|i| *i != id);
    }
    fn hide_all_banners(&self) {
        let ids: Vec<u32> = self.on_screen.borrow().clone();
        for id in ids {
            self.hide_banner(id);
        }
    }
    fn center_open(&self) -> bool {
        self.center.get()
    }
}

fn server(fake: &Rc<Fake>) -> (Rc<Server>, Rc<Shown>) {
    let s = Server::with_env(Box::new(FakeEnv(fake.clone())), false);
    let p = Rc::new(Shown::default());
    s.set_presenter(Some(p.clone()));
    (s, p)
}

fn req(app: &str, summary: &str, body: &str, actions: &[&str], urgency: i64, desktop: &str) -> Request {
    Request {
        app_name: app.into(),
        replaces_id: 0,
        app_icon: String::new(),
        summary: summary.into(),
        body: body.into(),
        actions: actions.iter().map(|s| s.to_string()).collect(),
        hints: Hints { urgency: Some(urgency), desktop_entry: desktop.into(), image_path: String::new() },
        expire_timeout: -1,
    }
}

fn send(s: &Server, app: &str, urgency: i64) -> u32 {
    s.notify(req(app, "Hi", "msg", &[], urgency, &app.to_lowercase()))
}

// -- Do Not Disturb (test_notifications_dnd.py) ----------------------------------------------

/// Regression: notifications still showed banners with Do Not Disturb on.
#[test]
fn test_no_banner_even_critical() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    s.set_dnd(true);
    send(&s, "Discord", 1);
    send(&s, "Discord", 2); // Electron marks messages critical
    assert!(p.shown.borrow().is_empty());
    assert_eq!(s.notes().len(), 2); // still in the Notification Center
}

#[test]
fn test_on_hides_banners_on_screen() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    let changes = Rc::new(RefCell::new(Vec::new()));
    let c = changes.clone();
    s.connect_dnd_changed(move |on| c.borrow_mut().push(on));
    let nid = send(&s, "Discord", 1);
    assert_eq!(*p.shown.borrow(), vec![nid]);
    s.set_dnd(true);
    assert!(p.hidden.borrow().contains(&nid));
    assert_eq!(*changes.borrow(), vec![true]);
    assert_eq!(load_config()["dnd"], json!(true));
}

/// Settings turns DND on; a new app's first notification saved an older
/// copy of the settings over it.
#[test]
fn test_new_app_keeps_dnd_from_settings() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    sonata_config::update(CONFIG, &json!({"dnd": true})).unwrap(); // Settings, before the file watch reloads
    send(&s, "NewApp", 1);
    let cfg = load_config();
    assert_eq!(cfg["dnd"], json!(true));
    assert!(p.shown.borrow().is_empty());
    assert!(cfg["apps"].as_object().unwrap().contains_key("newapp"));
}

/// Settings turning Do Not Disturb on (the file watch) hides the banners
/// and tells the Control Center.
#[test]
fn test_dnd_from_settings_hides_banners() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    let changes = Rc::new(RefCell::new(Vec::new()));
    let c = changes.clone();
    s.connect_dnd_changed(move |on| c.borrow_mut().push(on));
    let nid = send(&s, "Discord", 1);
    sonata_config::update(CONFIG, &json!({"dnd": true})).unwrap();
    s.config_changed();
    assert!(s.dnd());
    assert!(p.hidden.borrow().contains(&nid));
    assert_eq!(*changes.borrow(), vec![true]);
}

/// Notification Center open: the notification goes into it, no banner.
#[test]
fn test_no_banner_while_center_open() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    p.center.set(true);
    send(&s, "Discord", 1);
    assert!(p.shown.borrow().is_empty());
    assert_eq!(s.notes().len(), 1);
}

/// Settings > Notifications: an app not allowed shows nothing; one with
/// "None" as its style only collects in the list.
#[test]
fn test_per_app_settings() {
    let _l = one_at_a_time();
    fresh_config();
    sonata_config::save(
        CONFIG,
        json!({"dnd": false, "apps": {"muted": {"allow": false}, "quiet": {"style": "none"}, "nolist": {"center": false}}}).as_object().unwrap(),
    )
    .unwrap();
    let (s, p) = server(&Rc::default());
    send(&s, "Muted", 1);
    assert!(s.notes().is_empty() && p.shown.borrow().is_empty());
    send(&s, "Quiet", 1);
    assert_eq!(s.notes().len(), 1);
    assert!(p.shown.borrow().is_empty());
    let id = send(&s, "NoList", 1);
    assert_eq!(s.notes().len(), 1);
    assert_eq!(*p.shown.borrow(), vec![id]);
}

// -- ids --------------------------------------------------------------------------------------

/// Any id handed out before is replaced in place, listed or not: a
/// banner-only app's updates must not stack.
#[test]
fn test_replaces_id_in_place() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, _p) = server(&Rc::default());
    let a = send(&s, "Chat", 1);
    let mut r = req("Chat", "Again", "new text", &[], 1, "chat");
    r.replaces_id = a;
    assert_eq!(s.notify(r), a);
    assert_eq!(s.notes().len(), 1);
    assert_eq!(s.notes()[0].summary, "Again");
    let mut r = req("Chat", "Unknown", "x", &[], 1, "chat");
    r.replaces_id = 999; // never handed out: a new one
    assert_eq!(s.notify(r), a + 1);
}

#[test]
fn test_actions_in_pairs_and_picture_from_uri() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, _p) = server(&Rc::default());
    let mut r = req("Shot", "Screenshot", "", &["default", "Open", "edit", "Edit", "odd"], 1, "shot");
    r.hints.image_path = "file:///tmp/a%20b.png".into();
    let id = s.notify(r);
    let n = s.note(id).unwrap();
    assert_eq!(n.actions, vec![("default".into(), "Open".into()), ("edit".into(), "Edit".into())]);
    assert_eq!(n.image, "/tmp/a b.png");
    let mut r = req("Shot", "x", "", &[], 1, "shot");
    r.hints.image_path = "relative.png".into(); // not a file path: no picture
    assert_eq!(s.note(s.notify(r)).unwrap().image, "");
}

// -- locked folders (test_notify_locked.py) ---------------------------------------------------

fn locked_folders() {
    sonata_config::save(
        "dock",
        json!({"folders": {"f1": {"name": "Private", "apps": ["org.telegram.desktop"], "locked": true}, "f2": {"name": "Open", "apps": ["discord"]}}}).as_object().unwrap(),
    )
    .unwrap();
    sonata_config::save("launchpad", json!({"pages": [], "hidden": ["whatsapp-web"]}).as_object().unwrap()).unwrap();
    forget_locked();
}

/// Apps in a locked folder show no notifications (Vini).
#[test]
fn test_locked_by_desktop_entry() {
    let _l = one_at_a_time();
    fresh_config();
    locked_folders();
    let env = FakeEnv(Rc::default());
    assert!(locked("org.telegram.desktop", "", &env));
    assert!(locked("org.telegram.desktop.desktop", "", &env));
    assert!(locked("whatsapp-web", "", &env)); // Apps' hidden (locked) folder
    assert!(!locked("discord", "Discord", &env)); // an unlocked folder
}

#[test]
fn test_locked_by_name() {
    let _l = one_at_a_time();
    fresh_config();
    locked_folders();
    let fake = Rc::new(Fake::default());
    fake.apps.borrow_mut().insert("org.telegram.desktop".into(), "Telegram Desktop".into());
    assert!(locked("", "Telegram Desktop", &FakeEnv(fake)));
}

/// A launchpad page's locked folder counts too.
#[test]
fn test_locked_launchpad_folder() {
    let _l = one_at_a_time();
    fresh_config();
    sonata_config::save("launchpad", json!({"pages": [["firefox", {"name": "F", "apps": ["signal"], "locked": true}]], "hidden": []}).as_object().unwrap()).unwrap();
    forget_locked();
    assert!(locked("signal", "", &FakeEnv(Rc::default())));
    assert!(!locked("firefox", "", &FakeEnv(Rc::default())));
}

#[test]
fn test_daemon_drops_them() {
    let _l = one_at_a_time();
    fresh_config();
    locked_folders();
    let (s, p) = server(&Rc::default());
    let id = s.notify(req("Telegram", "Ana", "oi", &[], 1, "org.telegram.desktop"));
    assert!(id > 0);
    assert!(s.notes().is_empty());
    assert!(p.shown.borrow().is_empty());
    assert!(!load_config()["apps"].as_object().unwrap().contains_key("org.telegram.desktop"));
}

// -- clicking brings the app forward (test_notify_focus.py) ----------------------------------

fn view(id: i64, aid: &str, ts: i64, kind: &str) -> Value {
    json!({"id": id, "app-id": aid, "last-focus-timestamp": ts, "type": kind})
}

fn focus_env() -> FakeEnv {
    let fake = Rc::new(Fake::default());
    fake.matches.borrow_mut().insert("Firefox".into(), "firefox".into());
    FakeEnv(fake)
}

#[test]
fn test_latest_window_of_app() {
    let views = json!([view(1, "org.gnome.Nautilus", 5, "toplevel"), view(2, "Firefox", 3, "toplevel"), view(3, "firefox", 9, "toplevel"), view(4, "firefox", 99, "background")]);
    assert_eq!(window_of(&views, "firefox.desktop", "Firefox", &focus_env()), Some(3));
}

#[test]
fn test_matches_through_app_id_lookup() {
    assert_eq!(window_of(&json!([view(7, "Firefox", 1, "toplevel")]), "firefox", "", &focus_env()), Some(7));
}

#[test]
fn test_by_name_without_desktop() {
    let env = focus_env();
    env.0.apps.borrow_mut().insert("firefox".into(), "Firefox".into());
    assert_eq!(window_of(&json!([view(7, "Firefox", 1, "toplevel")]), "", "firefox", &env), Some(7));
}

#[test]
fn test_none_open() {
    assert_eq!(window_of(&json!([view(1, "other", 1, "toplevel")]), "firefox", "Firefox", &focus_env()), None);
    assert_eq!(window_of(&Value::Null, "firefox", "Firefox", &focus_env()), None);
}

fn clicked(actions: &[(&str, &str)], desktop: &str, app: &str, has_window: bool) -> (Rc<Fake>, Rc<Server>) {
    let fake = Rc::new(Fake::default());
    fake.has_window.set(has_window);
    let (s, _p) = server(&fake);
    let mut n = Note::new(4, app, "s", "b");
    n.desktop = desktop.into();
    n.actions = actions.iter().map(|(k, l)| (k.to_string(), l.to_string())).collect();
    s.invoke(&n, "default");
    (fake, s)
}

#[test]
fn test_focuses_open_app() {
    let _l = one_at_a_time();
    let (fake, _s) = clicked(&[], "firefox", "Firefox", true);
    assert_eq!(*fake.forward.borrow(), vec![("firefox".to_string(), "Firefox".to_string())]);
    assert!(fake.launched.borrow().is_empty());
}

#[test]
fn test_opens_closed_app() {
    let _l = one_at_a_time();
    let (fake, _s) = clicked(&[], "firefox", "Firefox", false);
    assert_eq!(*fake.launched.borrow(), vec!["firefox".to_string()]);
}

#[test]
fn test_app_default_action_wins_over_launch() {
    let _l = one_at_a_time();
    let (fake, _s) = clicked(&[("default", "Open")], "firefox", "Firefox", false);
    assert!(fake.launched.borrow().is_empty());
}

#[test]
fn test_click_web_app_opens_when_no_window() {
    let _l = one_at_a_time();
    let (fake, _s) = clicked(&[("default", "")], WEB, "WhatsApp", false);
    assert_eq!(*fake.launched.borrow(), vec![WEB.to_string()]);
    let (fake, _s) = clicked(&[("default", "")], WEB, "WhatsApp", true);
    assert_eq!(*fake.forward.borrow(), vec![(WEB.to_string(), "WhatsApp".to_string())]);
    assert!(fake.launched.borrow().is_empty());
}

/// A click is a read: it leaves the list.
#[test]
fn test_click_takes_it_out_of_the_list() {
    let _l = one_at_a_time();
    fresh_config();
    let fake = Rc::new(Fake::default());
    fake.has_window.set(true);
    let (s, p) = server(&fake);
    let id = send(&s, "Chat", 1);
    s.invoke(&s.note(id).unwrap(), "default");
    assert!(s.notes().is_empty());
    assert!(p.hidden.borrow().contains(&id));
}

// -- web apps (test_dock_badges.py) -----------------------------------------------------------

fn site_entries() -> Map<String, Value> {
    json!({"waee32c088c": {"name": "WhatsApp", "url": "https://web.whatsapp.com/"}, "g1": {"name": "Gmail", "url": "https://mail.google.com/"}}).as_object().unwrap().clone()
}

/// Vini: WhatsApp's notifications went to Chrome (badge 8 on Chrome).
#[test]
fn test_chrome_notification_becomes_whatsapp() {
    let _l = one_at_a_time();
    fresh_config();
    let fake = Rc::new(Fake::default());
    *fake.webapps.borrow_mut() = site_entries();
    fake.apps.borrow_mut().insert(WEB.into(), "WhatsApp".into());
    let (s, _p) = server(&fake);
    s.notify(req("Google Chrome", "Bruna", "web.whatsapp.com\n\nTava a dormir jaaa", &["default", "", "settings", "Settings"], 1, "google-chrome"));
    let n = &s.notes()[0];
    assert_eq!((n.desktop.as_str(), n.app.as_str(), n.body.as_str()), (WEB, "WhatsApp", "Tava a dormir jaaa"));
    assert_eq!(n.actions, vec![("default".to_string(), String::new())]);
}

fn pairs(p: &[(&str, &str)]) -> Vec<(String, String)> {
    p.iter().map(|(k, l)| (k.to_string(), l.to_string())).collect()
}

#[test]
fn test_tidy_web_app() {
    let (body, acts) = tidy("web.whatsapp.com\n\nTava a dormir jaaa", &pairs(&[("default", ""), ("settings", "Settings")]), true, false);
    assert_eq!(body, "Tava a dormir jaaa");
    assert_eq!(acts, pairs(&[("default", "")]));
    let (body, _) = tidy("<a href=\"https://web.whatsapp.com/\">web.whatsapp.com</a>\nhi", &[], true, false);
    assert_eq!(body, "hi");
}

#[test]
fn test_tidy_browser_keeps_site_drops_settings() {
    let (body, acts) = tidy("example.com\n\nhi", &pairs(&[("settings", "Settings"), ("a", "Reply")]), false, true);
    assert_eq!(body, "example.com\nhi");
    assert_eq!(acts, pairs(&[("a", "Reply")]));
}

#[test]
fn test_tidy_other_apps_keep_actions() {
    assert_eq!(tidy("a", &pairs(&[("settings", "Settings")]), false, false).1, pairs(&[("settings", "Settings")]));
}

#[test]
fn test_web_sender_named() {
    let _l = one_at_a_time();
    let fake = Rc::new(Fake::default());
    fake.apps.borrow_mut().insert("sonata2-webapp-wa1".into(), "WhatsApp".into());
    *fake.webapps.borrow_mut() = json!({"wa1": {"name": "WhatsApp"}}).as_object().unwrap().clone();
    let env = FakeEnv(fake);
    let argv = ["chrome", "--user-data-dir=/x/webapps/wa1/chromium"];
    assert_eq!(webapp_of_argv(&argv, &env), Some(("sonata2-webapp-wa1".to_string(), "WhatsApp".to_string())));
    assert_eq!(webapp_of_argv(&["chrome", "--user-data-dir=/x/webapps/gone/chromium"], &env), None); // not installed
    assert_eq!(webapp_of_argv(&["firefox"], &env), None);
}

/// WhatsApp takes its notification back as its window is focused: the
/// banner goes, Notification Center keeps it until it's read there.
#[test]
fn test_web_app_withdrawn_stays_listed() {
    let _l = one_at_a_time();
    fresh_config();
    let (s, p) = server(&Rc::default());
    let web = s.notify(req("WhatsApp", "Ana", "oi", &[], 1, WEB));
    let other = send(&s, "Chat", 1);
    s.close(web, CLOSED_BY_APP);
    s.close(other, CLOSED_BY_APP);
    assert_eq!(s.notes().iter().map(|n| n.id).collect::<Vec<_>>(), vec![web]);
    assert!(p.hidden.borrow().contains(&web));
    s.close(web, DISMISSED);
    assert!(s.notes().is_empty());
}

// -- kept for a restart (test_badges_after_restart.py) ----------------------------------------

/// Vini: the Dock's notification badges only came back with a new
/// notification -- the menu bar (which keeps them) started empty after a
/// restart and published that.
#[test]
fn test_kept_and_read_back() {
    let _l = one_at_a_time();
    let mut a = Note::new(3, "WhatsApp", "Ana", "oi");
    a.desktop = "whatsapp".into();
    a.actions = pairs(&[("default", "Open")]);
    let b = Note::new(4, "Discord", "Bia", "hey");
    save_history(&[a, b]);
    let back = load_history();
    assert_eq!(back.iter().map(|n| (n.id, n.app.as_str(), n.desktop.as_str())).collect::<Vec<_>>(), vec![(3, "WhatsApp", "whatsapp"), (4, "Discord", "")]);
    assert_eq!(back[0].actions, pairs(&[("default", "Open")]));
    assert!(history_path().is_file());
    let _ = std::fs::remove_file(history_path());
}

#[test]
fn test_nothing_or_damaged_is_empty() {
    let _l = one_at_a_time();
    let _ = std::fs::remove_file(history_path());
    assert!(load_history().is_empty());
    std::fs::write(history_path(), "{not json").unwrap();
    assert!(load_history().is_empty());
    let _ = std::fs::remove_file(history_path());
}

/// The file is the Python menu bar's too: the same keys as its Note.
#[test]
fn test_history_reads_the_python_list() {
    let _l = one_at_a_time();
    std::fs::write(
        history_path(),
        r#"[{"id": 7, "app": "Chat", "icon": "", "summary": "s", "body": "b", "actions": [["default", ""]], "desktop": "chat", "urgency": 1, "timeout": -1, "image": "", "at": 1762300000.5}]"#,
    )
    .unwrap();
    let back = load_history();
    assert_eq!(back.len(), 1);
    assert_eq!((back[0].id, back[0].at), (7, 1762300000.5));
    let keys: Vec<String> = back[0].to_json().as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, ["id", "app", "icon", "summary", "body", "actions", "desktop", "urgency", "timeout", "image", "at"]);
    let _ = std::fs::remove_file(history_path());
}

/// The list is written as it changes (a notification, one read): what a
/// restarted menu bar reads back is the Dock's badges as they were.
#[test]
fn test_every_change_is_kept_for_a_restart() {
    let _l = one_at_a_time();
    fresh_config();
    let _ = std::fs::remove_file(history_path());
    let s = Server::with_env(Box::new(FakeEnv(Rc::default())), true);
    let id = s.notify(req("WhatsApp", "Ana", "oi", &[], 1, "whatsapp"));
    assert_eq!(load_history().iter().map(|n| n.id).collect::<Vec<_>>(), vec![id]);
    assert_eq!(sonata_desktop::badges::load(None).1.iter().map(|n| n.id).collect::<Vec<_>>(), vec![id as i64]);
    s.close(id, DISMISSED); // read: gone after a restart too
    assert!(load_history().is_empty());
    let _ = std::fs::remove_file(history_path());
}

#[test]
fn test_start_publishes_what_was_kept() {
    let _l = one_at_a_time();
    let mut n = Note::new(5, "WhatsApp", "Ana", "oi");
    n.desktop = "whatsapp".into();
    save_history(&[n]);
    sonata_desktop::badges::publish(&[], 1, None);
    let s = Server::with_env(Box::new(FakeEnv(Rc::default())), true);
    assert_eq!(s.notes().len(), 1);
    assert_eq!(s.next_id(), 6);
    let (server, notes) = sonata_desktop::badges::load(None);
    assert_eq!(server, Some(std::process::id() as i64));
    assert_eq!(notes.iter().map(|n| n.id).collect::<Vec<_>>(), vec![5]);
    let _ = std::fs::remove_file(history_path());
}

/// On trial (the Python menu bar serving), the Rust one never writes the
/// list or the Dock's badges: they're the Python one's.
#[test]
fn test_trial_leaves_the_shared_files_alone() {
    let _l = one_at_a_time();
    fresh_config();
    let _ = std::fs::remove_file(history_path());
    sonata_desktop::badges::publish(&[], 1, None);
    let s = Server::with_env(Box::new(FakeEnv(Rc::default())), false);
    send(&s, "Chat", 1);
    assert!(!history_path().exists());
    assert_eq!(sonata_desktop::badges::load(None).0, Some(1));
}

// -- the card's text --------------------------------------------------------------------------

#[test]
fn test_ago() {
    let now = 1_000_000.0;
    assert_eq!(ago(now - 5.0, now), sonata_i18n::tr!("now"));
    assert_eq!(ago(now - 300.0, now), sonata_i18n::fmt(&sonata_i18n::tr!("{n}m ago"), &[("n", "5")]));
    assert_eq!(ago(now - 7300.0, now), sonata_i18n::fmt(&sonata_i18n::tr!("{n}h ago"), &[("n", "2")]));
    assert!(!ago(now - 200_000.0, now).is_empty());
}

#[test]
fn test_markup_kept_or_escaped() {
    assert_eq!(markup("<b>Hi</b> there"), "<b>Hi</b> there");
    assert_eq!(markup("1 < 2 & <x"), "1 &lt; 2 &amp; &lt;x");
}

#[test]
fn test_app_key_and_settings() {
    assert_eq!(app_key("firefox", "Firefox"), "firefox");
    assert_eq!(app_key("", " Firefox "), "Firefox");
    let cfg = json!({"apps": {"firefox": {"style": "none"}}}).as_object().unwrap().clone();
    let s = app_settings(&cfg, "firefox");
    assert_eq!(s["style"], json!("none"));
    assert_eq!(s["allow"], json!(true));
}

#[test]
fn test_hints_read_any_urgency_type() {
    let d = glib::VariantDict::new(None);
    d.insert_value("urgency", &2u8.to_variant());
    d.insert_value("desktop-entry", &"org.telegram.desktop".to_variant());
    let h = hints_of(&d.end());
    assert_eq!((h.urgency, h.desktop_entry.as_str()), (Some(2), "org.telegram.desktop"));
    let d = glib::VariantDict::new(None);
    d.insert_value("urgency", &0i32.to_variant());
    assert_eq!(hints_of(&d.end()).urgency, Some(0));
    assert_eq!(hints_of(&glib::VariantDict::new(None).end()).urgency, None);
}

// -- on a private bus -------------------------------------------------------------------------

fn wait_for(cond: impl Fn() -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(5);
    let ctx = glib::MainContext::default();
    while !cond() && Instant::now() < end {
        ctx.iteration(false);
        std::thread::sleep(Duration::from_millis(1));
    }
    cond()
}

fn connect(addr: &str) -> gio::DBusConnection {
    gio::DBusConnection::for_address_sync(addr, gio::DBusConnectionFlags::AUTHENTICATION_CLIENT | gio::DBusConnectionFlags::MESSAGE_BUS_CONNECTION, None, None::<&gio::Cancellable>).unwrap()
}

fn call(conn: &gio::DBusConnection, method: &str, args: Option<glib::Variant>) -> Option<glib::Variant> {
    let got: Rc<RefCell<Option<Result<glib::Variant, glib::Error>>>> = Rc::default();
    let g = got.clone();
    conn.call(Some(BUS_NAME), PATH, IFACE, method, args.as_ref(), None, gio::DBusCallFlags::NONE, 3000, None::<&gio::Cancellable>, move |r| {
        g.replace(Some(r));
    });
    assert!(wait_for(|| got.borrow().is_some()), "{method}: no answer");
    got.take().unwrap().ok()
}

/// Notify's (susssasa{sv}i): the hints as a dictionary, not boxed in a "v".
fn notify_args(app: &str, summary: &str, body: &str, actions: &[&str], hints: glib::Variant, timeout: i32) -> glib::Variant {
    let actions: Vec<String> = actions.iter().map(|s| s.to_string()).collect();
    glib::Variant::tuple_from_iter([app.to_variant(), 0u32.to_variant(), "".to_variant(), summary.to_variant(), body.to_variant(), actions.to_variant(), hints, timeout.to_variant()])
}

#[test]
fn test_server_on_a_private_bus() {
    let _l = one_at_a_time();
    fresh_config();
    glib::MainContext::default()
        .with_thread_default(|| {
            let bus = gio::TestDBus::new(gio::TestDBusFlags::NONE);
            bus.up();
            let addr = bus.bus_address().unwrap().to_string();
            let fake = Rc::new(Fake::default());
            fake.has_window.set(true);
            let (s, p) = server(&fake);
            let conn = connect(&addr);
            assert!(s.export(&conn));
            s.own_name(&conn);
            assert!(wait_for(|| s.owns_name()));

            let app = connect(&addr);
            let signals: Rc<RefCell<Vec<(String, glib::Variant)>>> = Rc::default();
            let sg = signals.clone();
            let _sub = app.signal_subscribe(Some(BUS_NAME), Some(IFACE), None, Some(PATH), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, name, params| {
                sg.borrow_mut().push((name.to_string(), params.clone()));
            });

            let info = call(&app, "GetServerInformation", None).unwrap();
            assert_eq!(info.get::<(String, String, String, String)>().unwrap(), ("Sonata".into(), "Sonata".into(), "2".into(), "1.2".into()));
            let caps = call(&app, "GetCapabilities", None).unwrap().get::<(Vec<String>,)>().unwrap().0;
            assert!(caps.contains(&"actions".to_string()) && caps.contains(&"body-markup".to_string()));

            let hints = glib::VariantDict::new(None);
            hints.insert_value("urgency", &1u8.to_variant());
            hints.insert_value("desktop-entry", &"chat".to_variant());
            let args = notify_args("Chat", "Ana", "oi", &["default", "", "reply", "Reply"], hints.end(), -1);
            let id = call(&app, "Notify", Some(args)).unwrap().get::<(u32,)>().unwrap().0;
            assert_eq!(s.notes().iter().map(|n| (n.id, n.summary.as_str().to_string())).collect::<Vec<_>>(), vec![(id, "Ana".to_string())]);
            assert_eq!(*p.shown.borrow(), vec![id]);

            // a button: ActionInvoked, and the card is read
            s.invoke(&s.note(id).unwrap(), "reply");
            assert!(wait_for(|| signals.borrow().iter().any(|(n, _)| n == "NotificationClosed")));
            {
                let sig = signals.borrow();
                let act = sig.iter().find(|(n, _)| n == "ActionInvoked").unwrap();
                assert_eq!(act.1.get::<(u32, String)>().unwrap(), (id, "reply".to_string()));
                let closed = sig.iter().find(|(n, _)| n == "NotificationClosed").unwrap();
                assert_eq!(closed.1.get::<(u32, u32)>().unwrap(), (id, DISMISSED));
            }
            signals.borrow_mut().clear();

            // the app takes one back
            let id2 = call(&app, "Notify", Some(notify_args("Chat", "Bia", "hey", &[], glib::VariantDict::new(None).end(), 5000))).unwrap().get::<(u32,)>().unwrap().0;
            assert_eq!(id2, id + 1);
            call(&app, "CloseNotification", Some((id2,).to_variant())).unwrap();
            assert!(s.notes().is_empty());
            assert!(wait_for(|| !signals.borrow().is_empty()));
            assert_eq!(signals.borrow()[0].1.get::<(u32, u32)>().unwrap(), (id2, CLOSED_BY_APP));

            // wrong arguments: an error back, never silence (apps wait on the reply)
            assert!(call(&app, "Notify", Some(("x",).to_variant())).is_none());

            drop(s);
            app.close_sync(None::<&gio::Cancellable>).ok();
            conn.close_sync(None::<&gio::Cancellable>).ok();
            bus.down();
        })
        .unwrap();
}

// -- the Today widgets (test_regressions.py: CalendarWidgetRegressions) ----------------------

mod today_widgets {
    use crate::today::*;
    use chrono::Duration;
    use sonata_calendar::{ics, model as cal};

    /// The menu bar's calendar showed no events and didn't open Calendar.
    #[test]
    fn test_menu_bar_calendar_shows_events() {
        let _l = super::one_at_a_time();
        let dir = tempfile::tempdir().unwrap();
        let now = ics::at(2026, 11, 4, 9, 0);
        assert!(upcoming(dir.path(), now, 31).is_empty()); // no files: none made here either
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
        let mut st = cal::Store::new(dir.path().to_path_buf());
        st.load();
        let start = now + Duration::hours(1);
        let mut ev = ics::Event::new("x", start, start + Duration::hours(1));
        ev.summary = "Dentist".into();
        ev.calendar = st.calendars[0].id.clone();
        st.put(ev);
        let events = upcoming(dir.path(), now, 31);
        assert_eq!(events.iter().map(|(o, _)| o.event.summary.as_str()).collect::<Vec<_>>(), vec!["Dentist"]);
        assert!(events[0].1.starts_with('#'));
        assert_eq!(soon(&events, now).len(), 1);
        assert_eq!(until(&events, now), Some(start + Duration::hours(1)));
        assert_eq!(busy(&events, 2026, 11).into_iter().collect::<Vec<_>>(), vec![4]);
        // over: Up Next drops it
        assert!(soon(&events, start + Duration::hours(2)).is_empty());
        assert!(stamp(dir.path()) > 0);
    }

    /// Up Next: today's and tomorrow's, at most three.
    #[test]
    fn test_up_next_only_today_and_tomorrow() {
        let now = ics::at(2026, 11, 4, 9, 0);
        let mk = |uid: &str, d: u32, h: u32| {
            let e = std::rc::Rc::new(ics::Event::new(uid, ics::at(2026, 11, d, h, 0), ics::at(2026, 11, d, h + 1, 0)));
            (ics::Occurrence { event: e.clone(), start: e.start, end: e.end }, "#007aff".to_string())
        };
        let events = vec![mk("past", 4, 7), mk("a", 4, 10), mk("b", 5, 10), mk("c", 5, 12), mk("d", 5, 14), mk("later", 6, 10)];
        assert_eq!(soon(&events, now).iter().map(|(o, _)| o.event.uid.as_str()).collect::<Vec<_>>(), vec!["a", "b", "c"]);
    }

    #[test]
    fn test_month_weeks_start_on_sunday() {
        let w = weeks(2026, 11); // 1 Nov 2026 is a Sunday
        assert_eq!(w[0], [1, 2, 3, 4, 5, 6, 7]);
        assert_eq!(w.len(), 5);
        assert_eq!(w[4], [29, 30, 0, 0, 0, 0, 0]);
        let w = weeks(2026, 2); // a Sunday too, 28 days: four full weeks
        assert_eq!(w.len(), 4);
        let w = weeks(2026, 12); // a Tuesday
        assert_eq!(w[0], [0, 0, 1, 2, 3, 4, 5]);
        assert_eq!(*w.last().unwrap(), [27, 28, 29, 30, 31, 0, 0]);
    }
}

#[test]
fn test_thumbnail_is_a_small_square() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("big.png");
    gtk::gdk_pixbuf::Pixbuf::new(gtk::gdk_pixbuf::Colorspace::Rgb, false, 8, 1200, 1600).unwrap().savev(&p, "png", &[]).unwrap();
    let t = crate::card::thumbnail(p.to_str().unwrap(), THUMB, 2).unwrap();
    use gtk::prelude::TextureExt;
    assert_eq!((t.width(), t.height()), (2 * THUMB, 2 * THUMB));
    assert!(crate::card::thumbnail(dir.path().join("none.png").to_str().unwrap(), THUMB, 2).is_none());
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

/// Vini's Rust session: Notification Center was always solid -- its glass
/// (the display's picture blurred once, a slice of it under each card and
/// the calendar) was never ported. Each panel finds its slice; an answer
/// for an older opening is dropped; no picture: solid.
#[test]
fn test_notification_center_never_glass() {
    in_own_process("tests::notification_center_glass_gtk");
}

#[test]
#[ignore = "GTK: run by test_notification_center_never_glass in a process of its own"]
fn notification_center_glass_gtk() {
    use gtk::prelude::*;
    if gtk::init().is_err() {
        return; // (no display)
    }
    let home = tempfile::tempdir().unwrap();
    std::env::set_var("XDG_CONFIG_HOME", home.path().join("config"));
    std::env::set_var("XDG_RUNTIME_DIR", home.path());
    sonata_ui::setup();
    let server = Server::new(false);
    let app = gtk::Application::builder().flags(gio::ApplicationFlags::NON_UNIQUE).build();
    app.register(gio::Cancellable::NONE).unwrap();
    let asked: Rc<Cell<u32>> = Rc::default();
    let a = asked.clone();
    let hook: crate::center::FrostFn = Rc::new(move |_out, _done, _r| {
        a.set(a.get() + 1);
        true
    });
    let c = crate::center::Center::new(&app, &server, Some(hook));
    // the panels under a column, however deep
    let card = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    card.add_css_class("nt-card");
    let slot = gtk::Revealer::builder().child(&card).reveal_child(true).build();
    let hidden = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    hidden.add_css_class("nt-card");
    hidden.set_visible(false);
    let col = crate::center::FrostBox::new_for_tests();
    col.append(&slot);
    col.append(&hidden);
    let found = col.panels();
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].1, 1.08);
    // an opening asks for a picture (no layer shell here: solid panels, asked nothing)
    c.show_center(None);
    assert!(c.win.has_css_class("no-frost"));
    assert_eq!(asked.get(), 0);
    let tex: gtk::gdk::Texture = gtk::gdk::MemoryTexture::new(2, 2, gtk::gdk::MemoryFormat::R8g8b8a8, &glib::Bytes::from_owned(vec![0u8; 16]), 8).upcast();
    c.frost_done(c.frost_opening(), Some(tex.clone()));
    assert!(!c.win.has_css_class("no-frost") && c.pictured());
    c.frost_done(c.frost_opening() - 1, None); // an older opening's answer
    assert!(c.pictured());
    c.frost_done(c.frost_opening(), None); // none this time: solid
    assert!(c.win.has_css_class("no-frost") && !c.pictured());
}
