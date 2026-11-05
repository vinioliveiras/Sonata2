//! Dock right-click menus (shell/dock_menu.py, macOS Big Sur layout).
//!
//! App:    <open windows>  |  Options > Keep in Dock / Open at Login /
//!         Open File Location  |  Minimize (Maximize), Quit (running) or Open (not running)
//! Trash:  Open  |  Empty Trash...
//!
//! Launchpad uses add_to_desktop, show_in_files, lock_item, gpu_sections
//! and edit_webapp.
//!
//! Mission Control's menu (a window right-clicked there) is the app's menu
//! too, on a see-through surface of its own (layer.menu_at's holder).

use crate::dock::{self, Dock, DockWindow, Tile, PERMANENT};
use crate::toplevels::Toplevel;
use gtk::prelude::*;
use gtk::{gio, glib};
use serde_json::{json, Value};
use sonata_i18n::{n_, tr, trn};
use sonata_ui::menu::{Item, Options};
use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;

/// Sonata's Files (the Python one: folders open there on trial too).
pub const FILES_ID: &str = "io.github.vinioliveiras.sonata2.files.desktop";

/// A menu on `widget`, on the Dock's side; it keeps an auto-hiding Dock up.
pub fn popup(widget: &impl IsA<gtk::Widget>, dock: &Dock, sections: Vec<Vec<Item>>) -> gtk::PopoverMenu {
    let pop = sonata_ui::menu::popup(widget, sections, Options { position: dock.away, at: None, glass: true, passthrough: false, autohide: true });
    let (x, y) = match dock.away {
        gtk::PositionType::Left => (-6, 0),
        gtk::PositionType::Right => (6, 0),
        _ => (0, -6),
    };
    pop.set_offset(x, y);
    dock::menu_opened(&pop);
    pop
}

/// A context menu at a point of `widget`.
pub fn popup_at(widget: &impl IsA<gtk::Widget>, sections: Vec<Vec<Item>>, at: (f64, f64)) -> gtk::PopoverMenu {
    let pop = sonata_ui::menu::popup(widget, sections, Options::at(at.0, at.1));
    dock::menu_opened(&pop);
    pop
}

// -- helpers -----------------------------------------------------------------------------------

/// The file behind an app: its real executable (AppImage, script,
/// binary), or its .desktop file for Flatpak/Snap and unresolvable commands.
pub fn app_file(info: &gio::DesktopAppInfo) -> Option<PathBuf> {
    let exe = sonata_ui::apps::executable(info);
    let base = exe.file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    if !["flatpak", "snap", "env", "sh", "bash"].contains(&base.as_str()) && !exe.as_os_str().is_empty() {
        let path = if exe.is_absolute() { Some(exe.clone()) } else { glib::find_program_in_path(&exe) };
        if let Some(p) = path.filter(|p| p.exists() && !p.starts_with("/snap/bin/")) {
            return std::fs::canonicalize(&p).ok();
        }
    }
    sonata_ui::apps::app_filename(info)
}

/// ~/Desktop (the XDG desktop folder); created when missing, like macOS.
pub fn desktop_dir() -> gio::File {
    let home = glib::home_dir();
    let p = glib::user_special_dir(glib::UserDirectory::Desktop).filter(|p| std::fs::canonicalize(p).ok() != std::fs::canonicalize(&home).ok()).unwrap_or_else(|| home.join("Desktop"));
    let _ = std::fs::create_dir_all(&p);
    gio::File::for_path(p)
}

/// A shortcut to an app (its launcher) on the desktop (Vini, like Windows).
pub fn add_to_desktop(path: Option<&Path>) -> Vec<gio::File> {
    let Some(path) = path else { return Vec::new() };
    sonata_files::ops::make_shortcuts(&[gio::File::for_path(path)], &desktop_dir())
}

/// Show a folder in Files (another process); the system's default file
/// manager if Files' entry isn't there.
pub fn open_folder(uri: &str) {
    // (Sonata's launch path and the display's context: Files comes to the
    // front even when it was already running)
    if let Some(info) = sonata_ui::apps::lookup(FILES_ID) {
        if sonata_ui::apps::launch_uris(&info, &[uri], None).is_ok() {
            return;
        }
    }
    let _ = sonata_ui::apps::launch_default_for_uri(uri, None);
}

/// Open the folder containing `path` in Sonata's Files, the file
/// selected (never another file manager: Vini's call).
pub fn show_in_files(path: Option<&Path>) {
    if let Some(p) = path {
        open_folder(&gio::File::for_path(p).uri());
    }
}

pub fn autostart_dir() -> PathBuf {
    glib::user_config_dir().join("autostart")
}

pub fn autostart_path(did: &str) -> PathBuf {
    autostart_dir().join(format!("{did}.desktop"))
}

pub fn opens_at_login(did: &str) -> bool {
    autostart_path(did).exists()
}

/// XDG autostart entry (a copy of the app's .desktop).
pub fn set_open_at_login(info: &gio::DesktopAppInfo, on: bool) {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    let dst = autostart_path(id.strip_suffix(".desktop").unwrap_or(&id));
    if on {
        let Some(src) = sonata_ui::apps::app_filename(info) else { return };
        let _ = std::fs::create_dir_all(autostart_dir());
        if let Err(e) = std::fs::copy(src, &dst) {
            eprintln!("sonata2-dock: open at login: {e}");
        }
    } else if dst.exists() {
        let _ = std::fs::remove_file(dst);
    }
}

fn trash_count() -> usize {
    sonata_files::ops::trash_items().len()
}

/// Permanently delete the Trash's contents: Files' own (a worker thread,
/// gvfs trash:/// covers other drives too). Without gvfs: the plain Trash folders.
pub fn empty_trash() {
    let plain: Rc<dyn Fn(&gio::File, &glib::Error)> = Rc::new(|_, _| {
        let base = sonata_files::ops::home_trash();
        for sub in ["files", "info"] {
            for e in std::fs::read_dir(base.join(sub)).into_iter().flatten().flatten() {
                let p = e.path();
                let _ = if p.is_dir() && !p.is_symlink() { std::fs::remove_dir_all(&p) } else { std::fs::remove_file(&p) };
            }
        }
        sonata_files::ops::clear_orphans();
    });
    sonata_files::ops::empty_trash(None, Some(plain));
}

/// Return empties, like macOS (an alert never makes a destructive button
/// the default unless asked).
fn empty_trash_options() -> sonata_ui::dialog::AlertOptions {
    sonata_ui::dialog::AlertOptions { default: Some("empty".into()), ..Default::default() }
}

pub fn confirm_empty_trash() {
    let n = trash_count();
    let title = match n {
        0 => tr!("Are you sure you want to permanently erase the items in the Trash?"),
        1 => tr!("Are you sure you want to permanently erase the item in the Trash?"),
        _ => sonata_i18n::fmt(&trn!("Are you sure you want to permanently erase the {n} item in the Trash?", "Are you sure you want to permanently erase the {n} items in the Trash?", n), &[("n", &n.to_string())]),
    };
    sonata_ui::dialog::alert_with(&title, &tr!("You can't undo this action."), &[("cancel", tr!("Cancel"), ""), ("empty", tr!("Empty Trash"), "destructive")], empty_trash_options(), |r, _| {
        if r == "empty" {
            empty_trash();
        }
    }, None);
}

// -- menus -------------------------------------------------------------------------------------

/// Runs fn (Launchpad closes first: its alert is a window of its own).
pub type Around = Rc<dyn Fn(Box<dyn Fn()>)>;

/// Lock App / Unlock App… (applock.py): the Dock's and Launchpad's menus.
pub fn lock_item(info: &gio::DesktopAppInfo, around: Option<Around>) -> Item {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    if sonata_desktop::applock::locked(&id) {
        let info = info.clone();
        return Item::new(&tr!("Unlock App\u{2026}"), move || {
            let info = info.clone();
            let f: Box<dyn Fn()> = Box::new(move || sonata_desktop::applock::ask_unlock(&info, None));
            match &around {
                Some(a) => a(f),
                None => f(),
            }
        });
    }
    Item::new(&tr!("Lock App"), move || sonata_desktop::applock::set_locked(&id, true))
}

/// Open in Sandbox (sandbox.py): an empty home of its own, nothing kept.
/// None for a Sonata web app (it runs Sonata itself, whose web app
/// settings are in the home the sandbox hides).
pub fn sandbox_item(info: &gio::DesktopAppInfo, around: Option<Around>) -> Option<Item> {
    if sonata_desktop::badges::is_webapp(info.id().as_deref().unwrap_or("")) {
        return None;
    }
    let info = info.clone();
    Some(Item::new(&tr!("Open in Sandbox"), move || {
        let info = info.clone();
        let f: Box<dyn Fn()> = Box::new(move || sonata_desktop::sandbox::ask_open(info.upcast_ref()));
        match &around {
            Some(a) => a(f),
            None => f(),
        }
    }))
}

pub fn app_menu(dock: &Dock, key: &str, tile: &Rc<Tile>) -> gtk::PopoverMenu {
    tile.label.popdown();
    popup(&tile.button, dock, app_sections(dock, key, tile))
}

/// Mission Control: a window right-clicked (Sonata's Wayfire tells:
/// wayfire-scale-sonata-events.patch) -- its app's Dock menu, at the
/// pointer (Vini). False: not a Dock app, or no layer shell.
pub fn scale_menu(dock: &Dock, msg: &Value) -> bool {
    let (key, connector, x, y) = scale_target(msg);
    let Some(tile) = dock.tiles.borrow().get(&key).cloned() else { return false };
    let Some(key) = tile.key.borrow().clone() else { return false };
    let app = dock.app.borrow().clone();
    menu_at(app.as_ref(), &connector, x, y, app_sections(dock, &key, &tile))
}

/// What a Wayfire event means for Mission Control's menu.
#[derive(Debug, PartialEq, Eq)]
pub enum ScaleEvent {
    /// a window right-clicked there: its app's menu
    Menu,
    /// Mission Control closed: the menu goes with it
    Closed,
    Other,
}

pub fn scale_event(msg: &Value) -> ScaleEvent {
    match msg.get("event").and_then(Value::as_str) {
        Some("sonata-scale-menu#") => ScaleEvent::Menu,
        Some("plugin-activation-state-changed")
            if msg.get("plugin").and_then(Value::as_str) == Some("scale") && !msg.get("state").and_then(Value::as_bool).unwrap_or(false) =>
        {
            ScaleEvent::Closed
        }
        _ => ScaleEvent::Other,
    }
}

/// What a sonata-scale-menu# event names: the Dock's key for its app, the
/// display, and the pointer in the output layout's coordinates.
pub fn scale_target(msg: &Value) -> (String, String, f64, f64) {
    let aid = msg.get("app-id").and_then(Value::as_str).unwrap_or("");
    let key = sonata_desktop::appmatch::match_app_id(aid).unwrap_or_else(|| aid.to_string());
    let connector = msg.get("output").and_then(Value::as_str).unwrap_or("").to_string();
    let n = |k: &str| msg.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    (key, connector, n("x"), n("y"))
}

/// The menu's holder: one see-through surface over a display, kept for
/// the next menu; the menu open on it now.
#[derive(Default)]
struct Holder {
    win: Option<gtk::Window>,
    pop: Option<gtk::PopoverMenu>,
}

thread_local! {
    static HOLDER: RefCell<Holder> = RefCell::default();
}

const HOLDER_CSS: &str = "window.sonata-menu-holder, window.sonata-menu-holder > contents { background: none; box-shadow: none; }";

/// Closes menu_at's menu; true when one was open.
fn close_held() -> bool {
    let pop = HOLDER.with(|h| h.borrow().pop.clone());
    if let Some(p) = &pop {
        p.popdown();
    }
    pop.is_some()
}

/// Where (x, y) of the output layout is on a display at (gx, gy).
pub fn on_display(x: f64, y: f64, origin: Option<(i32, i32)>) -> (f64, f64) {
    match origin {
        Some((gx, gy)) => (x - gx as f64, y - gy as f64),
        None => (x, y),
    }
}

/// A menu at (x, y) of the screen (the output layout's coordinates) on
/// display `connector`, where no surface of ours is -- a window in Mission
/// Control (layer.menu_at). A see-through surface over the display holds
/// it while it's open; a click anywhere else, or Esc, closes it. False
/// without the layer shell.
pub fn menu_at(app: Option<&gtk::Application>, connector: &str, x: f64, y: f64, sections: Vec<Vec<Item>>) -> bool {
    if !sonata_shell::layer::available() {
        return false;
    }
    let mon = sonata_shell::layer::monitors().into_iter().find(|m| sonata_shell::layer::connector(m) == connector).or_else(sonata_shell::layer::main_monitor);
    let win = HOLDER.with(|h| h.borrow().win.clone()).unwrap_or_else(|| {
        sonata_ui::register("menu-holder", HOLDER_CSS);
        let win = gtk::Window::builder().decorated(false).css_classes(["sonata-menu-holder"]).build();
        win.set_application(app);
        win.set_child(Some(&gtk::Box::builder().hexpand(true).vexpand(true).build()));
        sonata_shell::layer::overlay_fullscreen(&win, "sonata2-menu");
        let click = gtk::GestureClick::builder().button(0).build();
        click.connect_pressed(|_, _, _, _| {
            close_held();
        });
        win.add_controller(click);
        let keys = gtk::EventControllerKey::new();
        keys.connect_key_pressed(|_, kv, _, _| if kv == gtk::gdk::Key::Escape && close_held() { glib::Propagation::Stop } else { glib::Propagation::Proceed });
        win.add_controller(keys);
        HOLDER.with(|h| h.borrow_mut().win = Some(win.clone()));
        win
    });
    let old = HOLDER.with(|h| h.borrow_mut().pop.take());
    if let Some(old) = old {
        old.popdown();
    }
    let origin = mon.as_ref().map(|m| {
        sonata_shell::layer::set_monitor(&win, m);
        let g = m.geometry();
        (g.x(), g.y())
    });
    let (x, y) = on_display(x, y, origin);
    let sections = RefCell::new(Some(sections));
    let show = Rc::new(move |win: &gtk::Window| {
        let Some(sections) = sections.take() else { return };
        let Some(child) = win.child() else { return };
        // no autohide (layer.menu_at): the holder got no click of its own
        // -- it closes the menu itself, on a click anywhere or Esc
        let pop = sonata_ui::menu::popup(&child, sections, held_options(x, y));
        let w = win.downgrade();
        pop.connect_closed(move |p| {
            let mine = HOLDER.with(|h| {
                let mut h = h.borrow_mut();
                let mine = h.pop.as_ref() == Some(p);
                if mine {
                    h.pop = None;
                }
                mine
            });
            if let (true, Some(w)) = (mine, w.upgrade()) {
                w.set_visible(false);
            }
        });
        HOLDER.with(|h| h.borrow_mut().pop = Some(pop));
    });
    if win.is_mapped() {
        show(&win);
    } else {
        let handler: Rc<RefCell<Option<glib::SignalHandlerId>>> = Rc::default();
        let h = handler.clone();
        let id = win.connect_map(move |w| {
            if let Some(id) = h.borrow_mut().take() {
                w.disconnect(id);
            }
            let (w, show) = (w.clone(), show.clone());
            glib::idle_add_local_once(move || show(&w)); // (once the surface has its size)
        });
        handler.replace(Some(id));
        win.present();
    }
    true
}

/// menu_at()'s menu: at the point, never hiding itself (ui.menu's
/// autohide=False; with autohide, a menu on a surface that got no click of
/// its own may never show or close at once).
fn held_options(x: f64, y: f64) -> Options {
    Options { autohide: false, ..Options::at(x, y) }
}

/// menu_at()'s menu closes (Mission Control closed under it: Vini, its
/// items stayed on the screen).
pub fn close_menu_at() {
    close_held();
}

/// The app's menu: its windows, Options, Minimize / Quit.
pub fn app_sections(dock: &Dock, key: &str, tile: &Rc<Tile>) -> Vec<Vec<Item>> {
    let info = tile.info.clone();
    let wins = dock.wins(key);
    let mut sections = Vec::new();
    let m = dock.manager.clone();
    if !wins.is_empty() {
        sections.push(
            wins.iter()
                .map(|t| {
                    let (m, t2) = (m.clone(), t.clone());
                    let label = if t.title.is_empty() { tile.name() } else { t.title.clone() };
                    let (m3, t3) = (m.clone(), t.clone());
                    Item::new(&label, move || {
                        if let Some(m) = &m {
                            m.activate(&t2);
                        }
                    })
                    // the x closes that window
                    .on_close(move || {
                        if let Some(m) = &m3 {
                            m.close(&t3);
                        }
                    })
                })
                .collect(),
        );
    }
    if let (Some(info), false) = (&info, wins.is_empty()) {
        let t = Rc::downgrade(tile);
        let mut s = vec![Item::new(&tr!("New Window"), move || {
            if let Some(t) = t.upgrade() {
                new_window(&t);
            }
        })];
        s.extend(sandbox_item(info, None));
        sections.push(s);
    }
    let pinned = dock.pinned().iter().any(|k| k == key);
    let keep = || {
        let (w, k) = (dock.weak(), key.to_string());
        Item::check(&tr!("Keep in Dock"), pinned, move |on| {
            if let Some(d) = w.upgrade() {
                d.set_pinned(&k, on);
            }
        })
    };
    // Uninstall… right under Keep in Dock, in Options (Vini)
    let uninstall = || -> Vec<Item> {
        if !Dock::can_uninstall(tile) {
            return Vec::new();
        }
        let (w, t) = (dock.weak(), Rc::downgrade(tile));
        vec![Item::new(&tr!("Uninstall…"), move || {
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                d.ask_uninstall(&t);
            }
        })]
    };
    let mut opts: Vec<Vec<Item>> = Vec::new();
    match &info {
        Some(info) if PERMANENT.contains(&key) => {
            // can't leave the Dock (Launchpad / the Apps Menu)
            let i = info.clone();
            opts.push(vec![Item::new(&tr!("Open File Location"), move || show_in_files(app_file(&i).as_deref()))]);
        }
        Some(info) => {
            let id = info.id().map(|s| s.to_string()).unwrap_or_default();
            let mut first = vec![keep()];
            first.extend(uninstall());
            let i = info.clone();
            first.push(Item::check(&tr!("Open at Login"), opens_at_login(id.strip_suffix(".desktop").unwrap_or(&id)), move |on| set_open_at_login(&i, on)));
            first.push(lock_item(info, None));
            opts.push(first);
            let (i1, i2) = (info.clone(), info.clone());
            opts.push(vec![
                Item::new(&tr!("Open File Location"), move || show_in_files(app_file(&i1).as_deref())),
                // its launcher, not its program (Vini: the shortcut showed as a text file)
                Item::new(&tr!("Add to Desktop"), move || {
                    add_to_desktop(sonata_ui::apps::app_filename(&i2).as_deref());
                }),
            ]);
            opts.extend(gpu_sections(info));
            if sonata_desktop::badges::is_webapp(key) {
                let wid = sonata_webapps::id_of(key);
                opts.push(vec![Item::new(&tr!("Edit Web App…"), move || edit_webapp(&wid))]);
            }
            let f = crate::dock_folder::app_items(dock, key);
            if !f.is_empty() {
                opts.push(f);
            }
        }
        None if pinned => {
            let mut s = vec![keep()];
            s.extend(uninstall());
            opts.push(s);
        }
        None => {}
    }
    if !opts.is_empty() {
        sections.push(vec![Item::submenu(&tr!("Options"), opts)]);
    }
    if !wins.is_empty() {
        // Minimize; when every window already is: Maximize (brought back, full size)
        let (m1, w1) = (m.clone(), wins.clone());
        let first = if wins.iter().all(|t| t.minimized) {
            Item::new(&tr!("Maximize"), move || {
                if let Some(m) = &m1 {
                    for t in &w1 {
                        m.activate(t);
                        m.set_maximized(t);
                    }
                }
            })
        } else {
            Item::new(&tr!("Minimize"), move || {
                if let Some(m) = &m1 {
                    for t in &w1 {
                        m.minimize(t);
                    }
                }
            })
        };
        let k1 = key.to_string();
        let (m2, w2, k2) = (m.clone(), wins.clone(), key.to_string());
        sections.push(vec![
            first,
            Item::new(&tr!("Force Quit"), move || {
                force_quit(&k1);
            }),
            Item::new(&tr!("Quit"), move || quit_app(m2.clone(), w2.clone(), &k2)),
        ]);
    } else if let Some(info) = &info {
        let (w, t) = (dock.weak(), Rc::downgrade(tile));
        let mut s = vec![Item::new(&tr!("Open"), move || {
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                d.launch(&t);
            }
        })];
        s.extend(sandbox_item(info, None));
        sections.push(s);
    }
    sections
}

/// The GPU and browser GPU choices (gpu.py, browsergpu.py), a section
/// each when the machine and the app have them: the Dock's Options and
/// Launchpad's menu.
pub fn gpu_sections(info: &gio::DesktopAppInfo) -> Vec<Vec<Item>> {
    let app: &gio::AppInfo = info.upcast_ref();
    [sonata_tweaks::gpu::menu_items(app), sonata_tweaks::browsergpu::menu_items(app)]
        .into_iter()
        .filter(|g| !g.is_empty())
        .map(|g| g.into_iter().map(|i| Item::check(&i.label, i.checked, i.toggled).enabled(i.enabled)).collect())
        .collect()
}

/// The web app's form, filled in (webapps.edit: a process of its own, it
/// takes the keyboard).
pub fn edit_webapp(wid: &str) {
    spawn_sonata(&["webapp", "edit", wid]);
}

/// The apps' processes to quit (quitapps.app_pids): each of the app's
/// windows', never Sonata's own shell nor this process.
pub fn app_pids(views: &Value, key: &str, me: i64) -> Vec<i64> {
    let mut pids: Vec<i64> = views
        .as_array()
        .into_iter()
        .flatten()
        .filter(|v| v.get("type").and_then(Value::as_str) == Some("toplevel"))
        .filter(|v| !v.get("app-id").and_then(Value::as_str).unwrap_or("").contains("sonata2"))
        .filter(|v| {
            let aid = v.get("app-id").and_then(Value::as_str).unwrap_or("");
            sonata_desktop::appmatch::match_app_id(aid).unwrap_or_else(|| aid.to_string()) == key
        })
        .filter_map(|v| v.get("pid")?.as_i64())
        .filter(|p| *p > 1 && *p != me)
        .collect();
    pids.sort();
    pids.dedup();
    pids
}

/// A process's command line, its arguments joined by spaces ("" when gone).
fn cmdline(pid: i64) -> String {
    std::fs::read(format!("/proc/{pid}/cmdline")).map(|c| String::from_utf8_lossy(&c).replace('\0', " ")).unwrap_or_default()
}

/// The pids that may be signalled (quitapps.app_pids' last step): never
/// Sonata's own shell, and never Xwayland -- an X11 window's pid can be
/// Xwayland's, and it goes with the session, taking every X11 app along.
pub fn signallable(pids: Vec<i64>, cmdline: impl Fn(i64) -> String) -> Vec<i64> {
    pids.into_iter()
        .filter(|p| {
            let line = cmdline(*p);
            !line.contains("sonata2") && !line.split(' ').next().unwrap_or("").contains("Xwayland")
        })
        .collect()
}

/// Force Quit's processes (dock_menu.force_quit): every window's of the
/// app, Sonata's own apps included (a hung Files or Settings can be
/// killed), never this process nor Xwayland.
pub fn force_pids(views: &Value, key: &str, me: i64) -> Vec<i64> {
    let mut pids: Vec<i64> = views
        .as_array()
        .into_iter()
        .flatten()
        .filter(|v| {
            let aid = v.get("app-id").and_then(Value::as_str).unwrap_or("");
            sonata_desktop::appmatch::match_app_id(aid).unwrap_or_else(|| aid.to_string()) == key
        })
        .filter_map(|v| v.get("pid")?.as_i64())
        .filter(|p| *p > 1 && *p != me)
        .collect();
    pids.sort();
    pids.dedup();
    pids
}

/// macOS Quit (quitapps.quit_app): the app's windows close, then the app
/// itself gets SIGTERM if it's still running without one (Chrome in the
/// background). One that keeps a window ("Save changes?") just stays.
pub fn quit_app(manager: Option<Rc<crate::toplevels::Manager>>, wins: Vec<Toplevel>, key: &str) {
    let Some(m) = manager else { return };
    let views = sonata_backend::wfipc::call("window-rules/list-views", json!({})).unwrap_or(Value::Null);
    let pids = signallable(app_pids(&views, key, std::process::id() as i64), cmdline);
    for t in &wins {
        m.close(t);
    }
    let ids: Vec<i64> = wins.iter().map(|t| t.id).collect();
    let left = std::cell::Cell::new(12000i64);
    let signalled = std::cell::RefCell::new(Vec::<i64>::new());
    glib::timeout_add_local(Duration::from_millis(250), move || {
        left.set(left.get() - 250);
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({})).unwrap_or(Value::Null);
        let with_window: Vec<i64> = views.as_array().into_iter().flatten().filter(|v| v.get("type").and_then(Value::as_str) == Some("toplevel")).filter_map(|v| v.get("pid")?.as_i64()).collect();
        for p in &pids {
            if !with_window.contains(p) && !signalled.borrow().contains(p) {
                signalled.borrow_mut().push(*p);
                // SAFETY: kill(2) with a plain pid and signal
                unsafe { libc::kill(*p as i32, libc::SIGTERM) };
            }
        }
        let open = ids.iter().any(|id| m.get(*id).is_some());
        let alive = pids.iter().any(|p| Path::new(&format!("/proc/{p}")).exists());
        if (!open && !alive) || left.get() <= 0 {
            return glib::ControlFlow::Break;
        }
        glib::ControlFlow::Continue
    });
}

/// macOS Force Quit: the app's processes are killed at once (SIGKILL),
/// for an app that doesn't answer (its windows' pids from Wayfire).
pub fn force_quit(key: &str) -> bool {
    let views = sonata_backend::wfipc::call("window-rules/list-views", json!({})).unwrap_or(Value::Null);
    let pids: Vec<i64> = force_pids(&views, key, std::process::id() as i64).into_iter().filter(|p| !cmdline(*p).split(' ').next().unwrap_or("").contains("Xwayland")).collect();
    for p in &pids {
        // SAFETY: kill(2) with a plain pid and signal
        if unsafe { libc::kill(*p as i32, libc::SIGKILL) } != 0 {
            println!("sonata2-dock: force quit {key} ({p}): {}", std::io::Error::last_os_error());
        }
    }
    !pids.is_empty()
}

pub const NEW_WINDOW: [&str; 4] = ["new-window", "newwindow", "window-new", "new-empty-window"];

fn is_new_window(action: &str) -> bool {
    NEW_WINDOW.contains(&action.to_lowercase().replace('_', "-").as_str())
}

/// Another window of the app (macOS Dock "New Window", or a middle-click
/// on the icon), whichever the app offers first:
/// 1. its desktop entry's New Window action (browsers, VS Code, Files...);
/// 2. its running GApplication's "new-window" action over D-Bus;
/// 3. launching it again (apps that start one window per launch).
pub fn new_window(tile: &Tile) {
    let Some(info) = tile.info.clone() else { return };
    let ctx = dock::launch_context(&tile.button);
    if let Some(a) = info.list_actions().iter().find(|a| is_new_window(a)) {
        info.launch_action(a, Some(&ctx));
        return;
    }
    if gapplication_new_window(&info) {
        return;
    }
    if let Err(e) = dock::launch_info(&info, &[], Some(ctx)) {
        println!("sonata2-dock: cannot open a new window of {}: {}", info.id().unwrap_or_default(), e.message());
    }
}

fn gapplication_new_window(info: &gio::DesktopAppInfo) -> bool {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    let Some(app_id) = id.strip_suffix(".desktop").filter(|a| gio::dbus_is_name(a)) else { return false };
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return false };
    let owner = bus.call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", Some(&(app_id,).to_variant()), None, gio::DBusCallFlags::NONE, 300, gio::Cancellable::NONE);
    if !owner.ok().and_then(|v| v.get::<(bool,)>()).is_some_and(|v| v.0) {
        return false;
    }
    let path = format!("/{}", app_id.replace('.', "/").replace('-', "_"));
    let Ok(listed) = bus.call_sync(Some(app_id), &path, "org.gtk.Actions", "List", None, None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE) else { return false };
    let Some((names,)) = listed.get::<(Vec<String>,)>() else { return false };
    let Some(name) = names.into_iter().find(|a| is_new_window(a)) else { return false };
    let params = (name.as_str(), Vec::<glib::Variant>::new(), std::collections::HashMap::<String, glib::Variant>::new()).to_variant();
    bus.call_sync(Some(app_id), &path, "org.gtk.Actions", "Activate", Some(&params), None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE).is_ok()
}

pub fn trash_menu(dock: &Dock, tile: &Rc<Tile>) -> gtk::PopoverMenu {
    tile.label.popdown();
    popup(&tile.button, dock, vec![
        vec![Item::new(&tr!("Open"), || open_folder("trash:///"))],
        vec![Item::new(&tr!("Empty Trash…"), confirm_empty_trash).enabled(trash_count() > 0)],
    ])
}

const POSITIONS: [(&str, &str); 3] = [("left", n_!("Left")), ("bottom", n_!("Bottom")), ("right", n_!("Right"))];

fn hiding_item(dock: &Dock) -> Item {
    let on = dock.cfg_bool("autohide");
    let w = dock.weak();
    Item::new(&if on { tr!("Turn Hiding Off") } else { tr!("Turn Hiding On") }, move || {
        if let Some(d) = w.upgrade() {
            d.set_option("autohide", json!(!on));
        }
    })
}

fn position_item(dock: &Dock) -> Item {
    let now = dock.cfg_str("position");
    Item::submenu(&tr!("Position on Screen"), vec![POSITIONS
        .iter()
        .map(|(e, label)| {
            let (w, e) = (dock.weak(), e.to_string());
            Item::check(&sonata_i18n::gettext(label), now == e, move |on| {
                if let (true, Some(d)) = (on, w.upgrade()) {
                    d.set_option("position", json!(e));
                }
            })
        })
        .collect()])
}

/// macOS Dock divider menu.
pub fn divider_menu(dock: &Dock) -> gtk::PopoverMenu {
    let mag = dock.cfg_bool("magnification");
    let w = dock.weak();
    let w2 = dock.weak();
    popup(&dock.sep, dock, vec![
        vec![
            hiding_item(dock),
            Item::new(&if mag { tr!("Turn Magnification Off") } else { tr!("Turn Magnification On") }, move || {
                if let Some(d) = w.upgrade() {
                    d.set_magnification(!mag, None);
                }
            }),
        ],
        vec![
            position_item(dock),
            Item::check(&tr!("Show Recent Applications"), dock.cfg_bool("show_recents"), move |on| {
                if let Some(d) = w2.upgrade() {
                    d.set_option("show_recents", json!(on));
                }
            }),
        ],
        // (New Web App… is only in Apps' menu: Vini)
    ])
}

/// Right-click on the Dock's own space -- the strip under it, its plate
/// between the icons (Vini: like Windows' taskbar): Task Manager, Show
/// Desktop, the Dock's options and its preferences.
pub fn area_menu(dock: &Dock, window: &DockWindow, at: (f64, f64)) -> gtk::PopoverMenu {
    let manager = window.manager.clone();
    let pop = sonata_ui::menu::popup(&dock.plate, vec![
        vec![
            Item::new(&tr!("Task Manager"), || spawn_own(&["activity"])),
            Item::new(&tr!("Show Desktop"), move || {
                if let Some(m) = &manager {
                    dock::toggle_show_desktop(m);
                }
            }),
        ],
        vec![hiding_item(dock), position_item(dock)],
        vec![Item::new(&tr!("Dock Preferences…"), || spawn_sonata(&["settings", "--page", "dock"]))],
    ], Options { position: dock.away, at: Some(at), glass: true, passthrough: false, autohide: true });
    dock::menu_opened(&pop);
    pop
}

/// This Sonata's own component (the Rust binary).
pub fn spawn_own(args: &[&str]) {
    if let Ok(exe) = std::env::current_exe() {
        if let Ok(c) = std::process::Command::new(exe).args(args).spawn() {
            sonata_backend::run::reap(c); // (else a zombie in the Dock once it quits)
        }
    }
}

/// Settings and the web app form: the Rust ones once this build is the
/// desktop (the Rust session); on trial the Python Sonata's, whose
/// settings are the ones in use.
pub fn spawn_sonata(args: &[&str]) {
    if sonata_config::side_by_side() {
        spawn_python(args);
    } else {
        spawn_own(args);
    }
}

/// The Python Sonata's launcher ($SONATA2_LAUNCHER, else `sonata2`).
pub fn spawn_python(args: &[&str]) {
    let py = std::env::var("SONATA2_LAUNCHER").ok().filter(|l| Path::new(l).exists()).unwrap_or_else(|| "sonata2".into());
    match std::process::Command::new(py).args(args).spawn() {
        Ok(c) => sonata_backend::run::reap(c),
        Err(e) => println!("sonata2-dock: {args:?}: {e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: right-click a window, close Mission Control -- the menu's
    /// items stayed on the screen.
    #[test]
    fn test_closes_with_mission_control() {
        assert_eq!(scale_event(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": true})), ScaleEvent::Other); // opening: nothing
        assert_eq!(scale_event(&json!({"event": "plugin-activation-state-changed", "plugin": "expo", "state": false})), ScaleEvent::Other);
        assert_eq!(scale_event(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": false})), ScaleEvent::Closed);
        assert_eq!(scale_event(&json!({"event": "sonata-scale-menu#", "app-id": "org.gnome.Calculator"})), ScaleEvent::Menu);
        assert_eq!(scale_event(&json!({"event": "view-mapped"})), ScaleEvent::Other);
    }

    /// Mission Control's right-click is the Dock's menu, at the pointer on
    /// that display (Vini: it only logged that it wasn't ported).
    #[test]
    fn test_right_click_is_the_docks_menu() {
        let msg = json!({"event": "sonata-scale-menu#", "app-id": "org.gnome.Calculator", "output": "DP-1", "x": 700.0, "y": 400.0});
        let (key, out, x, y) = scale_target(&msg);
        assert_eq!(key, sonata_desktop::appmatch::match_app_id("org.gnome.Calculator").unwrap_or_else(|| "org.gnome.Calculator".into()));
        assert_eq!((out.as_str(), x, y), ("DP-1", 700.0, 400.0));
        assert_eq!(scale_target(&json!({"app-id": "unknown.app"})).1, ""); // no display named: the main one
        // the layout's coordinates, on a display right of a 1920 px one
        assert_eq!(on_display(2620.0, 400.0, Some((1920, 0))), (700.0, 400.0));
        assert_eq!(on_display(5.0, 6.0, None), (5.0, 6.0));
    }

    #[test]
    fn test_close_menu_at() {
        assert!(!close_held()); // none open: nothing
        close_menu_at();
    }

    #[test]
    fn new_window_actions_by_any_spelling() {
        assert!(is_new_window("new-window"));
        assert!(is_new_window("New_Window"));
        assert!(is_new_window("new-empty-window"));
        assert!(!is_new_window("new-private-window"));
    }

    #[test]
    fn autostart_entries_live_in_the_config_folder() {
        assert!(autostart_path("firefox").ends_with("autostart/firefox.desktop"));
    }

    /// Force Quit / Quit only signal the app's own windows' processes,
    /// never Sonata's shell nor the Dock itself.
    #[test]
    fn quit_signals_only_the_apps_processes() {
        let views = json!([
            {"type": "toplevel", "app-id": "kitty", "pid": 100},
            {"type": "toplevel", "app-id": "kitty", "pid": 100},
            {"type": "toplevel", "app-id": "kitty", "pid": 1},
            {"type": "toplevel", "app-id": "kitty", "pid": 42},
            {"type": "toplevel", "app-id": "firefox", "pid": 200},
            {"type": "toplevel", "app-id": "io.github.vinioliveiras.sonata2.files", "pid": 300},
            {"type": "panel", "app-id": "kitty", "pid": 400}
        ]);
        assert_eq!(app_pids(&views, "kitty", 42), vec![100]);
        assert!(app_pids(&views, "io.github.vinioliveiras.sonata2.files", 0).is_empty());
    }

    /// Mission Control's right-click menu was an auto-hiding popover on a
    /// surface that got no click of its own (layer.menu_at: autohide=False).
    #[test]
    fn test_mission_control_menu_autohid() {
        let o = held_options(10.0, 20.0);
        assert!(!o.autohide);
        assert_eq!(o.at, Some((10.0, 20.0)));
    }

    /// Empty Trash…: Return cancelled instead of emptying (Python's alert
    /// has default="empty", like macOS).
    #[test]
    fn test_return_did_not_empty_the_trash() {
        assert_eq!(empty_trash_options().default.as_deref(), Some("empty"));
    }

    /// Quit on an X11 app (Steam, Wine) could SIGTERM Xwayland -- its
    /// windows' pid can be Xwayland's -- and every X11 app closed with it.
    #[test]
    fn test_quit_killed_xwayland() {
        let line = |p: i64| match p {
            10 => "/usr/bin/Xwayland :0 -rootless".to_string(),
            11 => "/usr/bin/python3 -m sonata2 files".to_string(),
            _ => "/usr/bin/steam".to_string(),
        };
        assert_eq!(signallable(vec![10, 11, 12], line), vec![12]);
    }

    /// Force Quit on a hung Sonata Files or Settings did nothing: its
    /// windows were left out as "Sonata's own" (only Quit leaves them out).
    #[test]
    fn test_force_quit_skipped_sonatas_own_apps() {
        let views = json!([
            {"type": "toplevel", "app-id": "io.github.vinioliveiras.sonata2.files", "pid": 300},
            {"type": "toplevel", "app-id": "kitty", "pid": 42}
        ]);
        assert_eq!(force_pids(&views, "io.github.vinioliveiras.sonata2.files", 42), vec![300]);
        assert!(force_pids(&views, "kitty", 42).is_empty());
    }
}
