//! The Dock (sonata2/shell/dock.py and dock_*.py, poof.py, uninstall_ui.py):
//! `sonata2-rs dock [--preview] [--dark|--light] [--set KEY=VALUE]...
//! [--label N] [--menu N [--submenu]] [--stack] [--magnify X]`, as
//! `sonata2 dock` (__main__.run_dock).
//!
//! On trial (sonata_config::side_by_side) it runs next to the Python Dock
//! and must never fight it. What a second Dock process could clash on, and
//! what this one does:
//! - the D-Bus name: its own app id (`io.github.vinioliveiras.sonata2rs.dock`),
//!   so Launchpad's "above" / "launcher-open" and `sonata2 show-desktop`
//!   still reach the Python Dock;
//! - dock.json: read from ~/.config/sonata2 (your pins and look), written
//!   only to ~/.config/sonata2-rs -- pins, folders and stacks changed here
//!   stay in the trial's copy;
//! - the screen edge: --preview is a normal window over a wallpaper (the
//!   way to try it); without --preview the layer surface takes the
//!   namespace "sonata2rs-dock" and reserves no space (the Python Dock
//!   already moved the windows up), so the two would overlap at the edge;
//! - the session's lists: it never writes open-apps.json (for "Reopen
//!   Apps") nor reopens the last session's apps -- the Python Dock does,
//!   twice would open every app twice; the login intro and the app scope
//!   slice are left to it too;
//! - windows: both follow Wayfire's IPC (a socket each); a click here
//!   minimizes / brings forward the real windows, as it should.
//!
//! Not in the Rust Dock yet (dock.rs / dock_menu.rs say which): minimize
//! targets, Quit on Close (shell/quitonclose.py), the desktop entries
//! run_dock writes (`sonata2-rs desktop-files` and the Python Dock do),
//! the slide-away on `sonata2 restart` (intro.leave_on_signal).

pub mod dock;
pub mod dock_drop;
pub mod dock_folder;
pub mod dock_menu;
pub mod dock_preview;
pub mod dock_stack;
pub mod poof;
pub mod toplevels;
pub mod uninstall_ui;

pub use dock::{Dock, DockWindow, FILES_KEY, LAUNCHPAD_KEY, PERMANENT};
pub use dock_folder::{draw_lock_badge, lock_badge, FolderIcon};
pub use dock_menu::{add_to_desktop, lock_item, show_in_files};

use adw::prelude::*;
use gtk::{gdk, gio, glib};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.dock"
    } else {
        "io.github.vinioliveiras.sonata2.dock"
    }
}

/// The command line (the Dock's part of __main__'s argparse).
#[derive(Debug, Default, PartialEq)]
pub struct Args {
    pub preview: bool,
    pub appearance: Option<&'static str>,
    pub set: Vec<(String, Value)>,
    pub label: Option<usize>,
    pub menu: Option<usize>,
    pub submenu: bool,
    pub stack: bool,
    pub magnify: Option<f64>,
}

/// --set KEY=VALUE: VALUE as JSON, else as text.
pub fn set_value(kv: &str) -> Option<(String, Value)> {
    let (k, v) = kv.split_once('=')?;
    Some((k.to_string(), serde_json::from_str(v).unwrap_or_else(|_| json!(v))))
}

pub fn parse(args: &[String]) -> Args {
    let mut a = Args::default();
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        let (name, inline) = match arg.split_once('=') {
            Some((n, v)) if n.starts_with("--") => (n, Some(v.to_string())),
            _ => (arg.as_str(), None),
        };
        let mut value = || inline.clone().or_else(|| it.next().cloned()).unwrap_or_default();
        match name {
            "--preview" => a.preview = true,
            "--dark" => a.appearance = Some("dark"),
            "--light" => a.appearance = Some("light"),
            "--set" => a.set.extend(set_value(&value())),
            "--label" => a.label = value().parse::<i64>().ok().filter(|n| *n >= 0).map(|n| n as usize),
            "--menu" => a.menu = value().parse::<i64>().ok().filter(|n| *n >= 0).map(|n| n as usize),
            "--submenu" => a.submenu = true,
            "--stack" => a.stack = true,
            "--magnify" => a.magnify = value().parse::<f64>().ok().filter(|x| *x >= 0.0),
            _ => {}
        }
    }
    a
}

/// What a preview's wallpaper is: $PREVIEW_WALLPAPER (a real photo judges
/// glass and contrast better), else the desktop's own picture.
fn preview_wallpaper() -> Option<gdk::Texture> {
    if let Ok(p) = std::env::var("PREVIEW_WALLPAPER") {
        if let Ok(t) = gdk::Texture::from_file(&gio::File::for_path(p)) {
            return Some(t);
        }
    }
    sonata_shell::loginui::wallpaper_texture(1600)
}

const PREVIEW_CSS: &str = ".preview-wall { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%, #e0567a 70%%, #f3a452 100%%); }";

/// Development preview (shell/preview.py): the Dock in a normal window
/// over a wallpaper, so it can be judged (and tried next to the real one)
/// without a layer surface. The compositor's backdrop blur doesn't exist
/// here, so with glass on the preview paints a blurred copy of the
/// wallpaper under the plate (the same radius as Wayfire's blur).
fn preview_window(app: &adw::Application, dock: &Rc<Dock>) -> gtk::ApplicationWindow {
    let size = std::env::var("PREVIEW_SIZE").unwrap_or_else(|_| "960x260".into());
    let (w, h) = size.split_once('x').and_then(|(a, b)| Some((a.parse().ok()?, b.parse().ok()?))).unwrap_or((960, 260));
    sonata_ui::register("preview", PREVIEW_CSS);
    let win = gtk::ApplicationWindow::builder().application(app).title("Dock (Rust preview)").default_width(w).default_height(h).resizable(false).build();
    let wall = gtk::Picture::builder().content_fit(gtk::ContentFit::Cover).can_shrink(true).hexpand(true).vexpand(true).css_classes(["preview-wall"]).build();
    let over = gtk::Overlay::new();
    over.set_child(Some(&wall));
    dock::apply_margins(dock);
    over.add_overlay(&dock.plate);
    win.set_child(Some(&over));
    let paint = {
        let (wall, d) = (wall.clone(), Rc::downgrade(dock));
        move || {
            let tex = preview_wallpaper();
            wall.set_paintable(tex.as_ref());
            let glass_on = sonata_ui::glass::settings().iter().find(|(i, _)| *i == "dock").is_some_and(|(_, p)| p.on);
            if let Some(d) = d.upgrade() {
                d.backdrop.replace(if glass_on { tex.as_ref().and_then(|t| sonata_shell::loginui::blurred(t, w, h, 18.0)) } else { None });
                d.plate.queue_draw();
            }
        }
    };
    let paint = Rc::new(paint);
    let p = paint.clone();
    win.connect_map(move |_| {
        let p = p.clone();
        glib::timeout_add_local_once(Duration::from_millis(150), move || p());
    });
    adw::StyleManager::default().connect_dark_notify(move |_| {
        let p = paint.clone();
        glib::idle_add_local_once(move || p());
    });
    win
}

/// Settings > Dock > "Show the Dock on every display": a Dock of its own
/// on each other display (same apps; the main one does the extras).
struct Displays {
    app: gtk::Application,
    cfg_main: Rc<DockWindow>,
    extra: RefCell<Vec<Rc<DockWindow>>>,
    manager: Option<Rc<toplevels::Manager>>,
}

impl Displays {
    fn rebuild(&self) {
        for w in self.extra.borrow_mut().drain(..) {
            w.destroy();
        }
        let main = sonata_shell::layer::main_monitor();
        if let Some(m) = &main {
            self.cfg_main.move_to(m);
        }
        let cfg = dock::load_config();
        if !cfg.get("all_displays").is_some_and(dock::truthy) || sonata_config::side_by_side() {
            return;
        }
        for m in sonata_shell::layer::monitors() {
            if main.as_ref().is_some_and(|x| x == &m) {
                continue;
            }
            let w = DockWindow::new(&self.app, Rc::new(RefCell::new(dock::load_config())), self.manager.clone(), Some(m));
            w.win.present();
            self.extra.borrow_mut().push(w);
        }
    }
}

fn later(ms: u64, f: impl FnOnce() + 'static) {
    glib::timeout_add_local_once(Duration::from_millis(ms), f);
}

fn activate(app: &adw::Application, a: &Args, keep: &RefCell<Vec<Box<dyn std::any::Any>>>) {
    if let Some(f) = a.appearance {
        sonata_ui::force_appearance(f);
    }
    sonata_ui::setup();
    if !a.preview && !sonata_config::side_by_side() {
        // apps opened from Sonata: watched by systemd-oomd
        sonata_desktop::appscope::ensure_slice();
    }
    let mut cfg = dock::load_config();
    if !cfg.get("launchpad_added").is_some_and(dock::truthy) {
        // once: pin it after Finder
        cfg.insert("launchpad_added".into(), json!(true));
        let mut pins: Vec<Value> = cfg.get("pinned").and_then(Value::as_array).cloned().unwrap_or_default();
        if !pins.iter().any(|p| p == LAUNCHPAD_KEY) {
            pins.insert(1.min(pins.len()), json!(LAUNCHPAD_KEY));
        }
        cfg.insert("pinned".into(), Value::Array(pins));
        let _ = sonata_config::save("dock", &cfg);
    }
    for (k, v) in &a.set {
        cfg.insert(k.clone(), v.clone()); // for this run (not saved)
    }
    dock::load_css(&cfg);
    let manager = Some(toplevels::manager()).filter(|m| m.available);
    let cfg = Rc::new(RefCell::new(cfg));
    let gapp: gtk::Application = app.clone().upcast();
    // every Dock this process shows (the main one first)
    let windows: Rc<RefCell<Vec<Rc<DockWindow>>>> = Rc::default();
    let d: Rc<Dock> = if a.preview {
        let d = Dock::new(cfg, manager.clone());
        d.app.replace(Some(gapp.clone()));
        let win = preview_window(app, &d);
        win.present();
        keep.borrow_mut().push(Box::new(win));
        d
    } else {
        let w = DockWindow::new(&gapp, cfg, manager.clone(), None);
        w.win.present();
        windows.borrow_mut().push(w.clone());
        if w.layer.get() {
            let displays = Rc::new(Displays { app: gapp.clone(), cfg_main: w.clone(), extra: RefCell::default(), manager: manager.clone() });
            displays.rebuild();
            if let Some(display) = gdk::Display::default() {
                let ds = Rc::downgrade(&displays);
                display.monitors().connect_items_changed(move |_, _, _, _| {
                    let ds = ds.clone();
                    // (a display coming up is listed before its connector is known)
                    later(500, move || {
                        if let Some(ds) = ds.upgrade() {
                            ds.rebuild();
                        }
                    });
                });
            }
            let ds = Rc::downgrade(&displays);
            let last = RefCell::new(dock::load_config().get("all_displays").is_some_and(dock::truthy));
            let mut mons = sonata_config::watch("dock", move || {
                let on = dock::load_config().get("all_displays").is_some_and(dock::truthy);
                if on != *last.borrow() {
                    last.replace(on);
                    if let Some(ds) = ds.upgrade() {
                        ds.rebuild();
                    }
                }
            });
            let ds = Rc::downgrade(&displays);
            mons.extend(sonata_config::watch("displays", move || {
                if let Some(ds) = ds.upgrade() {
                    ds.rebuild(); // the main display changed
                }
            }));
            keep.borrow_mut().push(Box::new(mons));
            keep.borrow_mut().push(Box::new(displays));
        }
        w.dock().expect("a Dock window has its Dock")
    };
    let all = {
        let windows = windows.clone();
        move || -> Vec<Rc<DockWindow>> { windows.borrow().clone() }
    };
    // Launchpad open: the Dock goes above it (macOS keeps the Dock visible
    // over Launchpad, and apps can be dragged onto it); back below windows after
    let act = gio::SimpleAction::new("above", Some(glib::VariantTy::BOOLEAN));
    let (al, dp) = (all.clone(), Rc::downgrade(&d));
    act.connect_activate(move |_, v| {
        let on = v.and_then(|v| v.get::<bool>()).unwrap_or(false);
        for w in al() {
            w.set_above(on);
        }
        if let Some(d) = dp.upgrade() {
            d.above.set(on); // (the preview's)
        }
    });
    app.add_action(&act);
    // the Apps Menu open: a dot under Launchpad's icon, as under an open app (Vini)
    let act = gio::SimpleAction::new("launcher-open", Some(glib::VariantTy::BOOLEAN));
    let (al, dp) = (all.clone(), Rc::downgrade(&d));
    act.connect_activate(move |_, v| {
        let on = v.and_then(|v| v.get::<bool>()).unwrap_or(false);
        for w in al() {
            w.set_launcher_open(on);
        }
        if let Some(d) = dp.upgrade() {
            d.set_launcher_open(on);
        }
    });
    app.add_action(&act);
    let act = gio::SimpleAction::new("show-desktop", None); // Super+D (`sonata2 show-desktop`)
    let m = manager.clone();
    act.connect_activate(move |_, _| {
        if let Some(m) = &m {
            dock::toggle_show_desktop(m);
        }
    });
    app.add_action(&act);
    dev_options(&d, a);
}

/// --label / --stack / --magnify / --menu: a state shown on start (for
/// screenshots and trying the details).
fn dev_options(d: &Rc<Dock>, a: &Args) {
    if let Some(n) = a.label {
        if let Some(t) = d.all_tiles().get(n).cloned() {
            later(300, move || t.label.popup());
        }
    }
    if a.stack {
        if let Some(t) = d.stacks.tiles().first().cloned() {
            let row = d.stacks.clone();
            later(400, move || {
                row.open_panel(&t);
            });
        }
    }
    if let Some(x) = a.magnify {
        let w = d.weak();
        later(300, move || {
            if let Some(d) = w.upgrade() {
                d.magnify_at(x);
            }
        });
    }
    if let Some(n) = a.menu {
        let mut tiles = d.app_tiles();
        tiles.push(d.trash.clone());
        if let Some(t) = tiles.get(n).cloned() {
            let (w, submenu) = (d.weak(), a.submenu);
            later(400, move || {
                let Some(d) = w.upgrade() else { return };
                let pop = if Rc::ptr_eq(&t, &d.trash) { dock_menu::trash_menu(&d, &t) } else { dock_menu::app_menu(&d, &t.key().unwrap_or_default(), &t) };
                if submenu {
                    // (the menu's "Options" row, opened as a click would)
                    later(300, move || {
                        let _ = pop.activate_action("menu.open-submenu", None);
                    });
                }
            });
        }
    }
}

pub fn run(args: &[String]) -> glib::ExitCode {
    let a = parse(args);
    if !a.preview {
        sonata_shell::layer::ensure_preload(); // may start this process again
    }
    // Sonata's own UI never takes the GTK theme meant for other apps; the
    // shell reads Sonata's settings itself (prefs), never waiting on the
    // settings portal -- GDK_DEBUG is put back once GTK has read it, so the
    // apps opened from here get the session's
    std::env::remove_var("GTK_THEME");
    let session_debug = std::env::var("GDK_DEBUG").ok();
    let flags: Vec<String> = session_debug.iter().flat_map(|d| d.split(',')).filter(|f| !f.is_empty() && *f != "portals" && *f != "no-portals").map(String::from).chain(["no-portals".to_string()]).collect();
    std::env::set_var("GDK_DEBUG", flags.join(","));
    std::env::set_var("ADW_DISABLE_PORTAL", "1");
    let app = adw::Application::builder().application_id(app_id()).build();
    let keep: Rc<RefCell<Vec<Box<dyn std::any::Any>>>> = Rc::default();
    let started = std::cell::Cell::new(false);
    app.connect_activate(move |app| {
        if started.replace(true) {
            return; // (a second `sonata2-rs dock`: the running one stays)
        }
        match &session_debug {
            Some(d) => std::env::set_var("GDK_DEBUG", d),
            None => std::env::remove_var("GDK_DEBUG"),
        }
        activate(app, &a, &keep);
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn the_development_options() {
        let a = parse(&s(&["--preview", "--dark", "--set", "icon_size=64", "--set=position=left", "--label", "2", "--magnify", "300", "--menu", "0", "--submenu", "--stack"]));
        assert!(a.preview && a.submenu && a.stack);
        assert_eq!(a.appearance, Some("dark"));
        assert_eq!(a.set, vec![("icon_size".to_string(), json!(64)), ("position".to_string(), json!("left"))]);
        assert_eq!((a.label, a.menu, a.magnify), (Some(2), Some(0), Some(300.0)));
        // -1 is argparse's default: none
        let b = parse(&s(&["--label", "-1", "--light"]));
        assert_eq!((b.label, b.appearance), (None, Some("light")));
        assert_eq!(parse(&[]), Args::default());
    }

    #[test]
    fn set_values_are_json_or_text() {
        assert_eq!(set_value("magnification=true"), Some(("magnification".into(), json!(true))));
        assert_eq!(set_value("position=bottom"), Some(("position".into(), json!("bottom"))));
        assert_eq!(set_value("pinned=[\"a\"]"), Some(("pinned".into(), json!(["a"]))));
        assert_eq!(set_value("nothing"), None);
    }

    /// On trial the Rust Dock never takes the Python Dock's D-Bus name.
    #[test]
    fn its_own_name_on_trial() {
        let id = app_id();
        if sonata_config::side_by_side() {
            assert_eq!(id, "io.github.vinioliveiras.sonata2rs.dock");
        } else {
            assert_eq!(id, "io.github.vinioliveiras.sonata2.dock");
        }
    }
}
