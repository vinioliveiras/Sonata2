//! The menu bar (sonata2/shell/topbar.py) and Control Center
//! (shell/controlcenter.py), with their parts: stats, media, sound mixer,
//! OSD.
//!
//! | Python | here |
//! |---|---|
//! | topbar.py's Bar | [`bar`] (and [`logic`] for its rules) |
//! | topbar.py's _StatusPoll, system.watch_network / watch_audio | [`statuspoll`] |
//! | topbar.py's ControlCenter / QuickControls | [`cc`] |
//! | controlcenter.py | [`ccgrid`] |
//! | topbar.py's TopBarWindow | [`window`] |
//! | topbar.py's AboutWindow / AboutAppWindow | [`about`] |
//! | __main__.py's run_topbar | [`run`] / [`run_with`] |
//! | ui/panel.py's missing pieces, system.run_async, the menus' cache | [`kit`] |
//! | statsui, mpris, mixer_ui, osd, topbar_prefs, menubar_size | the modules of those names; controls: Control Center's slider |
//!
//! What lives in Python modules or crates not ported yet comes in through
//! [`seams::Services`].
//!
//! # Trial
//!
//! Side by side with the Python Sonata (`sonata_config::side_by_side()`),
//! `sonata2-rs topbar` runs as `io.github.vinioliveiras.sonata2rs.topbar`
//! (never the Python bar's bus name), on a layer surface of its own under
//! the Python bar (window.rs), writes its settings to `sonata2-rs/` only
//! (sonata-config), serves notifications only on its own connection
//! (sonata-notifications), owns no StatusNotifierWatcher (sonata-tray) and
//! doesn't listen for logind's Lock (the Python bar starts the lock
//! screen). `sonata2-rs topbar --preview` shows it in a normal window.

pub mod about;
pub mod bar;
pub mod cc;
pub mod ccgrid;
pub mod controls;
pub mod kit;
pub mod logic;
pub mod logo;
pub mod menubar_size;
pub mod mixer_ui;
pub mod mpris;
pub mod osd;
pub mod quiet;
pub mod seams;
pub mod statsui;
pub mod statuspoll;
pub mod topbar_prefs;
pub mod window;

#[cfg(test)]
mod testenv;
#[cfg(test)]
mod gtk_tests;

use gtk::{gio, glib, prelude::*};
use seams::Services;
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;
use window::TopBarWindow;

/// The Control Center built once, unseen, a moment after login: its first
/// opening took ~2.5x the others.
pub const CC_PREWARM_S: u64 = 4;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() { "io.github.vinioliveiras.sonata2rs.topbar" } else { "io.github.vinioliveiras.sonata2.topbar" }
}

/// The dev options: --preview (a normal window over the wallpaper),
/// --menu N (open the N-th item: screenshots), --dark / --light.
#[derive(Debug, Default, PartialEq)]
pub struct Options {
    pub preview: bool,
    pub menu: Option<usize>,
    pub appearance: Option<&'static str>,
}

pub fn parse_args(args: &[String]) -> Options {
    let mut o = Options::default();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--preview" => o.preview = true,
            "--dark" => o.appearance = Some("dark"),
            "--light" => o.appearance = Some("light"),
            "--menu" => o.menu = it.next().and_then(|n| n.parse().ok()),
            _ => {}
        }
    }
    o
}

/// At start (after the login intro): the answers menus will need, so they
/// open already filled.
pub fn prefetch() {
    use sonata_backend::system;
    kit::read_shared("bluetooth", system::bluetooth_devices, |r| kit::keep("bluetooth", (*r).clone()));
    // Wi-Fi's list and Control Center's states in one job: the radio asked once
    kit::read_two("wifi", "cc", |wifi, cc| {
        let on = system::wifi_enabled();
        (wifi.then(|| (on, system::wifi_scan(false))), cc.then(|| (on, system::wifi_current(), system::bluetooth_state(), system::airplane_mode())))
    });
}

/// `sonata2-rs topbar [--preview] [--menu N] [--dark|--light]`.
pub fn run(args: &[String]) -> glib::ExitCode {
    // the seams other crates fill, where they're ported
    let services = Services { mixer: Some(seams::system_mixer()), ..Services::default() };
    run_with(args, services, |_, _| {})
}

/// run() with the seams filled in, and `started(app, main window)` once
/// the main bar is up: the lead's place for what runs in the menu bar's
/// process from other crates (the switcher, capture, Game Bar actions...).
pub fn run_with(args: &[String], services: Services, started: impl Fn(&gtk::Application, &Rc<TopBarWindow>) + 'static) -> glib::ExitCode {
    let opts = parse_args(args);
    if !opts.preview {
        sonata_shell::layer::ensure_preload();
    }
    let mut builder = gtk::Application::builder().application_id(app_id());
    if opts.preview {
        builder = builder.flags(gio::ApplicationFlags::NON_UNIQUE);
    }
    let app = builder.build();
    let state: Rc<RefCell<Option<Rc<TopBarWindow>>>> = Rc::default();
    let others: Rc<RefCell<Vec<Rc<TopBarWindow>>>> = Rc::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        sonata_ui::icons::setup();
        if let Some(a) = opts.appearance {
            sonata_ui::force_appearance(a);
        }
        bar::register_css();
        let main_mon = if opts.preview || !sonata_shell::layer::available() { None } else { sonata_shell::layer::main_monitor() };
        let win = TopBarWindow::new(app, opts.preview, main_mon.as_ref(), services.clone());
        if !opts.preview {
            // the menu bar's services (the main display's only)
            seams::start_mixer_service(app); // each app's saved volume, from the start
            let nc = sonata_notifications::Notifications::new(app);
            // Notification Center's glass: the display pictured and blurred
            // once as it opens (frost.py; the panels were always solid) --
            // blurred more and its colours lifted, as macOS's
            nc.set_frost(Rc::new(|out, done, renderer| {
                use sonata_mission::frost;
                frost::capture_with(out, frost::NC_BLUR, Some(frost::NC_VIBRANCY), done, renderer)
            }));
            win.bar.notifications.replace(Some(nc));
            glib::timeout_add_seconds_local_once(4, prefetch); // menus open already filled
            if !sonata_config::side_by_side() {
                listen_for_lock();
            }
            let b = Rc::downgrade(&win.bar);
            glib::timeout_add_seconds_local_once(CC_PREWARM_S as u32, move || b.upgrade().iter().for_each(|b| b.prewarm_control_center()));
        }
        win.present();
        if main_mon.is_some() {
            // the other displays: a menu bar each (macOS), the same
            // Notification Center (and Do Not Disturb) from their clocks
            let rebuild = {
                let (app, main, others, services) = (app.clone(), win.clone(), others.clone(), services.clone());
                move || {
                    for w in others.borrow_mut().drain(..) {
                        w.bar.stop();
                        w.win.destroy();
                    }
                    let Some(main_now) = sonata_shell::layer::main_monitor() else { return };
                    if main.bar.monitor.borrow().as_ref().map(sonata_shell::layer::connector) != Some(sonata_shell::layer::connector(&main_now)) {
                        main.set_monitor(&main_now); // the main bar moves; the others follow
                    }
                    for m in sonata_shell::layer::monitors() {
                        if sonata_shell::layer::connector(&m) == sonata_shell::layer::connector(&main_now) {
                            continue;
                        }
                        let w = TopBarWindow::new(&app, false, Some(&m), services.clone());
                        w.bar.notifications.replace(main.bar.notifications.borrow().clone());
                        w.present();
                        others.borrow_mut().push(w);
                    }
                }
            };
            rebuild();
            if let Some(d) = gtk::gdk::Display::default() {
                d.monitors().connect_items_changed(move |_, _, _, _| {
                    let r = rebuild.clone();
                    glib::timeout_add_local_once(Duration::from_millis(500), r); // (a display settles first)
                });
            }
        }
        // HUD for the media keys (`sonata2 key ...` activates this over D-Bus)
        let b = Rc::downgrade(&win.bar);
        std::mem::forget(osd::install_action(app, move |_, _, _| b.upgrade().iter().for_each(|b| b.poll_soon())));
        // Input sources: Ctrl+Space goes back to the layout used before (macOS)
        let act = gio::SimpleAction::new("input-next", None);
        let b = Rc::downgrade(&win.bar);
        act.connect_activate(move |_, _| b.upgrade().iter().for_each(|b| b.next_layout()));
        app.add_action(&act);
        if let Some(n) = opts.menu {
            let b = Rc::downgrade(&win.bar);
            glib::timeout_add_local_once(Duration::from_millis(600), move || b.upgrade().iter().for_each(|b| b.open_menu(n)));
        }
        started(app, &win);
        state.replace(Some(win));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

/// logind "Lock" (loginctl lock-session, the Control Center, idle tools)
/// starts Sonata's lock screen; the menu bar always runs, so it listens
/// for the session.
fn listen_for_lock() {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return };
    let (method, arg) = match std::env::var("XDG_SESSION_ID").ok().filter(|s| !s.is_empty()) {
        Some(sid) => ("GetSession", (sid,).to_variant()),
        None => ("GetSessionByPID", (std::process::id(),).to_variant()),
    };
    let Ok(reply) = bus.call_sync(Some("org.freedesktop.login1"), "/org/freedesktop/login1", "org.freedesktop.login1.Manager", method, Some(&arg), None, gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE) else {
        return; // no logind: Ctrl+Super+Q still runs `sonata2 lock` directly
    };
    let Some(path) = reply.child_value(0).str().map(String::from) else { return };
    #[allow(deprecated)]
    bus.signal_subscribe(Some("org.freedesktop.login1"), Some("org.freedesktop.login1.Session"), Some("Lock"), Some(&path), None, gio::DBusSignalFlags::NONE, |_, _, _, _, _, _| kit::spawn_self(&["lock"]));
}

#[cfg(test)]
mod tests {

    /// Vini: Wi-Fi's (and every status icon's) panel opened hung from the
    /// icon's left edge; he wants it centred under the icon, as the Python
    /// menu bar does (ui.panel.popup without align_start).
    #[test]
    fn test_wifi_panel_opened_off_to_the_right() {
        assert!(!crate::bar::STATUS_ALIGN_START);
        let src = include_str!("bar.rs");
        let f = &src[src.find("fn status_panel").unwrap()..];
        assert!(f[..f.find('}').unwrap()].contains("STATUS_ALIGN_START"));
    }
    use super::*;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    /// run_topbar's dev options read as the Python ones are.
    #[test]
    fn the_dev_options() {
        assert_eq!(parse_args(&[]), Options::default());
        assert_eq!(parse_args(&a(&["--preview", "--menu", "3", "--dark"])), Options { preview: true, menu: Some(3), appearance: Some("dark") });
        assert_eq!(parse_args(&a(&["--light", "--menu", "x"])), Options { preview: false, menu: None, appearance: Some("light") });
    }

    /// Never the Python menu bar's bus name on trial.
    #[test]
    fn trial_app_id() {
        let _e = testenv::env();
        std::env::remove_var("SONATA2_RS_SIDE_BY_SIDE");
        assert_eq!(app_id(), if sonata_config::side_by_side() { "io.github.vinioliveiras.sonata2rs.topbar" } else { "io.github.vinioliveiras.sonata2.topbar" });
        if sonata_config::side_by_side() {
            assert_eq!(window::namespace(), "sonata2rs-topbar");
        }
    }
}
