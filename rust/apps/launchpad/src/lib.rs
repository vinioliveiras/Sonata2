//! Launchpad, macOS Big Sur (sonata2/shell/launchpad.py, launchpad_window.py):
//! a full-screen app grid over the dimmed desktop, or the Apps Menu layout
//! (a glass panel; Settings > Appearance > Apps style).
//!
//! - Pages (Adw.Carousel: swipe, scroll, arrow keys, page dots), as many
//!   rows as the display has room for.
//! - Search at the top; Return opens the first result.
//! - Folders: hold an app over another to make one (named from the apps'
//!   category); click to open, rename in place, drag apps out; Lock Folder;
//!   the Hidden folder (behind the password).
//! - Drag to reorder (after a short pause), hold at the screen side to turn
//!   pages, drag onto the Dock to pin (an app) or copy (a folder).
//! - Jiggle mode (press and hold, or hold Alt): a delete badge -> Uninstall.
//! - Opens / closes with the Big Sur zoom + fade; a click on empty space,
//!   Esc or launching an app closes it.
//!
//! `sonata2-rs launchpad [--preview] [--search TEXT] [--folder] [--jiggle]
//! [--background] [--dark|--light]`, as `sonata2 launchpad`
//! (__main__.run_launchpad): single instance and resident; running it again
//! (or the app's "toggle" action over D-Bus) toggles it, "close" closes it.
//!
//! On trial (sonata_config::side_by_side) it runs next to the Python
//! Launchpad: its own app id (`io.github.vinioliveiras.sonata2rs.launchpad`)
//! and layer namespace (`sonata2rs-launchpad`); launchpad.json (and the
//! Dock's pins and linked folders it edits) is read from ~/.config/sonata2
//! and written only to ~/.config/sonata2-rs; it asks the Dock of its own
//! build (sonata_dock::app_id) to sit above it. `--preview` is a normal
//! window (PREVIEW_SIZE, default 1280x800) over the sample wallpaper,
//! without layer-shell, Wayfire or a word to the Dock: the way to try it.
//!
//! Not ported yet: the hidden warm-up draw at login (layer.prewarm and
//! warm_icons: --background only starts it resident and hidden), the
//! icons gliding to their new place on a re-order (ui.transition), the
//! hanging drag icon (ui.drag: GTK's own drag icon), the GPU and browser
//! GPU menu items (gpu.py, browsergpu.py), "Delete Web App…"; New / Edit
//! Web App and Settings open in the Python Sonata (`sonata2 webapp`,
//! `sonata2 settings`).

pub mod logic;
pub mod menu;
pub mod pad;
pub mod tiles;
pub mod zoom;

use gtk::prelude::*;
use gtk::{gio, glib};
use pad::{Mode, Pad};
use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::Rc;
use std::time::Duration;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.launchpad"
    } else {
        "io.github.vinioliveiras.sonata2.launchpad"
    }
}

fn register_css() {
    use sonata_ui::tokens::ms;
    sonata_ui::css::register_module("ui/edit.py"); // the jiggle and the delete badge
    sonata_ui::css::register_module_with("shell/launchpad.py", &[("fold_in", ms(logic::FOLD_IN_MS).to_string()), ("fold_out", ms(logic::FOLD_OUT_MS).to_string())]);
    sonata_ui::css::register_module_with("shell/launchpad_window.py", &[
        ("open_ms", ms(logic::MENU_OPEN_MS).to_string()),
        ("close_ms", ms(logic::MENU_CLOSE_MS).to_string()),
        ("fade_ms", ms(logic::MENU_FADE_MS).to_string()),
    ]);
}

fn later(ms: u64, f: impl FnOnce() + 'static) {
    glib::timeout_add_local_once(Duration::from_millis(ms), f);
}

/// macOS: an app opened any other way (Dock, a shortcut, Spotlight)
/// closes Launchpad -- a new window, or another one activated; so do
/// Mission Control and all Spaces (Wayfire's scale and expo).
fn follow_windows(pad: &Rc<Pad>) {
    let mgr = sonata_dock::toplevels::manager();
    let ids = |m: &sonata_dock::toplevels::Manager| -> (HashSet<i64>, HashSet<i64>) {
        let tops = m.toplevels();
        (tops.iter().map(|t| t.id).collect(), tops.iter().filter(|t| t.activated).map(|t| t.id).collect())
    };
    let seen = Rc::new(RefCell::new(ids(&mgr)));
    let (w, m2) = (Rc::downgrade(pad), Rc::downgrade(&mgr));
    mgr.add_listener(Rc::new(move || {
        let (Some(p), Some(m)) = (w.upgrade(), m2.upgrade()) else { return };
        let (wins, active) = ids(&m);
        let fresh = {
            let s = seen.borrow();
            wins.difference(&s.0).next().is_some() || active.difference(&s.1).next().is_some()
        };
        seen.replace((wins, active));
        if fresh && p.win.is_visible() && (p.mode.get() == Mode::Menu || p.bin.progress() > 0.5) {
            p.close_launchpad(None);
        }
    }));
    let w = Rc::downgrade(pad);
    mgr.add_event_listener(Rc::new(move |ev| {
        if let Some(p) = w.upgrade().filter(|_| ev.get("event").and_then(|e| e.as_str()) == Some("plugin-activation-state-changed")) {
            p.plugin_changed(ev);
        }
    }));
}

pub fn run(args: &[String]) -> glib::ExitCode {
    let a = Rc::new(logic::parse(args));
    if !a.preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = gtk::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Pad>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if let Some(p) = state.borrow().as_ref() {
            p.toggle(); // second launch: toggle
            return;
        }
        if let Some(f) = a.appearance {
            sonata_ui::force_appearance(f);
        }
        sonata_ui::setup();
        register_css();
        let pad = Pad::new(app, a.preview);
        hold.replace(Some(app.hold())); // stays resident while hidden: instant opening
        state.replace(Some(pad.clone()));
        // (the Dock, before asking to uninstall an app)
        let close = gio::SimpleAction::new("close", None);
        let w = Rc::downgrade(&pad);
        close.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade().filter(|p| p.win.is_visible()) {
                p.close_launchpad(None);
            }
        });
        app.add_action(&close);
        // open / close from the Dock and `sonata2 launchpad` over D-Bus
        let toggle = gio::SimpleAction::new("toggle", None);
        let w = Rc::downgrade(&pad);
        toggle.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade() {
                p.toggle();
            }
        });
        app.add_action(&toggle);
        if a.preview {
            let (w, h) = sonata_mission::preview::preview_size((1280, 800));
            pad.win.set_default_size(w, h);
            if let Some((_, blurred)) = sonata_mission::preview::wallpaper(w, h, sonata_ui::is_dark()) {
                pad.bin.set_backdrop(Some(blurred));
            }
        } else {
            follow_windows(&pad);
        }
        if a.background {
            pad.win.set_visible(false); // login: start resident, hidden
            return;
        }
        pad.open_launchpad();
        if let Some(text) = a.search.clone() {
            let p = pad.clone();
            later(400, move || p.search.set_text(&text));
        }
        if a.folder {
            let p = pad.clone();
            later(500, move || {
                let first = p.model.borrow().pages.iter().flatten().find_map(|it| match it {
                    sonata_desktop::launchpad_model::Item::Folder(f) => Some(f.clone()),
                    _ => None,
                });
                if let Some(f) = first {
                    p.open_folder(&f);
                }
            });
        }
        if a.jiggle {
            let p = pad.clone();
            later(400, move || p.set_jiggle(true, true));
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}
