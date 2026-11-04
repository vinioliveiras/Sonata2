//! The game bar (sonata2/shell/gamebar.py, fpsmodule.py, kbdmodule.py, memorywatch.py)
//!
//! The Game Bar, Control Center's FPS Limit and Keyboard modules and the
//! memory watch all live in the menu bar's process in Python: the Rust menu
//! bar makes them from here ([`gamebar::GameBar::new`] with its
//! [`gamebar::Host`], [`fpsmodule::module`], [`kbdmodule::KeyboardModule`],
//! [`memorywatch::MemoryWatch::start`]). `sonata2-rs gamebar` is
//! `sonata2 gamebar`: it asks the running menu bar to show it -- on trial,
//! a Game Bar of its own instead (see [`run_gamebar`]).

pub mod fpsmodule;
pub mod gamebar;
pub mod kbdmodule;
pub mod memorywatch;
mod perf;
#[cfg(test)]
mod testenv;

use gtk::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

/// Start Sonata Settings (its own process; the Python one until Settings is ported).
pub fn open_settings(page: &str) {
    let mut cmd = vec!["sonata2", "settings"];
    if !page.is_empty() {
        cmd.extend(["--page", page]);
    }
    sonata_backend::run::spawn(&cmd);
}

/// The trial Game Bar's own id: never the menu bar's, never Python's.
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2rs.gamebar"
}

/// Run an action of a running Sonata process (org.freedesktop.Application),
/// as __main__._app_action does. False: nobody there.
pub fn app_action(app_id: &str, name: &str) -> bool {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return false };
    let args = (name, Vec::<glib::Variant>::new(), std::collections::HashMap::<String, glib::Variant>::new()).to_variant();
    let path = format!("/{}", app_id.replace('.', "/"));
    bus.call_sync(Some(app_id), &path, "org.freedesktop.Application", "ActivateAction", Some(&args), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE).is_ok()
}

/// `sonata2-rs gamebar [--preview]` (Super+G).
///
/// - Final mode: the menu bar shows it (its "gamebar" action), as
///   `sonata2 gamebar` does; exit 1 when no menu bar runs.
/// - Trial (side by side): the Python menu bar keeps Super+G and its own
///   Game Bar; this one is a process of its own
///   (io.github.vinioliveiras.sonata2rs.gamebar) over the game, with what
///   the Game Bar can do without a menu bar (no capture, mixer, Now
///   Playing or quick controls yet). Run again, it opens or closes (single
///   instance); it stays running, idle, between the two, as it would in
///   the menu bar.
/// - `--preview`: the same in a plain window (no layer shell), for a look;
///   it ends when the window closes.
pub fn run_gamebar(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if !preview && !sonata_config::side_by_side() {
        return if app_action("io.github.vinioliveiras.sonata2.topbar", "gamebar") { glib::ExitCode::SUCCESS } else { glib::ExitCode::FAILURE };
    }
    if !preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = adw::Application::builder().application_id(app_id()).build();
    let bar: Rc<RefCell<Option<Rc<gamebar::GameBar>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if bar.borrow().is_none() {
            sonata_ui::setup();
            hold.replace(Some(app.hold()));
            let host = Rc::new(gamebar::NoHost);
            let gb = if preview { gamebar::GameBar::preview(app.upcast_ref(), host) } else { gamebar::GameBar::new(app.upcast_ref(), host) };
            if preview {
                // the preview's window closed (and not just shown anew): it ends
                let a = app.downgrade();
                gb.win.connect_hide(move |w| {
                    let (w, a) = (w.clone(), a.clone());
                    glib::idle_add_local_once(move || {
                        let pinned = w.application().is_some_and(|app| app.windows().len() > 1);
                        if let (false, false, Some(a)) = (w.is_visible(), pinned, a.upgrade()) {
                            a.quit();
                        }
                    });
                });
            }
            bar.replace(Some(gb));
        }
        if let Some(gb) = bar.borrow().clone() {
            gb.toggle();
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}
