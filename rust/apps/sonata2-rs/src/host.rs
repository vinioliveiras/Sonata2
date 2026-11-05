//! What the menu bar's process hosts besides the bar (__main__.py's
//! run_topbar and topbar.py's TopBarWindow): the actions Wayfire's key
//! bindings call over D-Bus -- the app switcher (Alt+Tab), screenshots
//! and recording (Super+Shift+3/4/5, Print), the Character Viewer, the
//! clipboard history (Super+V), drawing on the screen, the Game Bar
//! (Super+G), the instant replay -- and Mission Control's backdrop.
//!
//! The pieces live in their own crates, which the menu bar crate can't
//! depend on (some depend on it); this binary depends on them all, so it
//! hands them to the menu bar here. Without this the Rust session's keys
//! asked a menu bar that had none of these actions and nothing happened
//! (Vini: Super+V, the screenshots).

use gtk::{gio, glib, prelude::*};
use sonata_mission::{switcher, Wayfire};
use std::cell::RefCell;
use std::rc::Rc;

/// The actions the menu bar answers (config/wayfire.ini's gdbus calls and
/// `sonata2-rs screenshot`), besides its own "osd" and "input-next".
pub const ACTIONS: &[&str] = &["switcher", "capture", "draw", "replay-save", "emoji", "clipboard", "gamebar"];

/// Everything above, once, on the main display's menu bar (never in a
/// preview: a second backdrop or clipboard watcher would double up).
pub fn topbar_started(app: &gtk::Application, win: &Rc<sonata_topbar::window::TopBarWindow>) {
    if !win.layered() || sonata_config::side_by_side() {
        return;
    }
    install_actions(app);
    // Mission Control's dark backdrop and the desktops bar follow Wayfire's
    // scale plugin from the menu bar (topbar.py: MissionBackdrop(app))
    sonata_ui::css::register_module("shell/mission.py");
    sonata_ui::css::register_module("shell/spaces.py");
    std::mem::forget(sonata_mission::mission::MissionBackdrop::new(app));
}

/// The menu bar's actions, each built on first use and kept after, as
/// run_topbar does.
pub fn install_actions(app: &gtk::Application) {
    // App switcher: Alt+Tab -> "next", Alt+Shift+Tab -> "prev"
    let sw: Rc<RefCell<Option<Rc<switcher::Switcher>>>> = Rc::default();
    let act = gio::SimpleAction::new("switcher", Some(glib::VariantTy::STRING));
    let a = app.downgrade();
    act.connect_activate(move |_, param| {
        let Some(app) = a.upgrade() else { return };
        let back = param.and_then(|p| p.str()) == Some("prev");
        if switcher::mission_control_step(&Wayfire, back) {
            return; // Mission Control open: its highlight moved instead
        }
        let s = sw.borrow().clone().unwrap_or_else(|| {
            sonata_ui::css::register_module("shell/switcher.py");
            let s = switcher::Switcher::new(&app, Rc::new(switcher::IpcWindows(Wayfire)), true);
            sw.replace(Some(s.clone()));
            s
        });
        s.step(if back { -1 } else { 1 });
    });
    app.add_action(&act);

    // Screenshots and recording ("capture"), drawing ("draw"), the instant replay ("replay-save")
    let cap = sonata_capture::capture::Capture::new(app);
    sonata_capture::capture::install_actions(app, &cap);
    std::mem::forget(cap.clone());

    // Character Viewer (Ctrl+Super+Space)
    let emoji: Rc<RefCell<Option<Rc<sonata_clipboard::emoji::EmojiPicker>>>> = Rc::default();
    let act = gio::SimpleAction::new("emoji", None);
    let a = app.downgrade();
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let p = emoji.borrow().clone().unwrap_or_else(|| {
            sonata_ui::css::register_module("shell/emoji.py");
            let p = sonata_clipboard::emoji::EmojiPicker::new(&app);
            emoji.replace(Some(p.clone()));
            p
        });
        p.open();
    });
    app.add_action(&act);

    // Clipboard history (Super+V): the history from login on, the panel on first use
    let history = sonata_clipboard::clipboard::History::new(true);
    let h = history.clone();
    app.connect_shutdown(move |_| h.stop());
    let picker: Rc<RefCell<Option<Rc<sonata_clipboard::clip_picker::ClipboardPicker>>>> = Rc::default();
    let act = gio::SimpleAction::new("clipboard", None);
    let a = app.downgrade();
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let p = picker.borrow().clone().unwrap_or_else(|| {
            sonata_ui::css::register_module("shell/clip_picker.py");
            let p = sonata_clipboard::clip_picker::ClipboardPicker::new(&app, history.clone());
            picker.replace(Some(p.clone()));
            p
        });
        p.open();
    });
    app.add_action(&act);

    // Game Bar (Super+G): built on first use, kept after
    let bar: Rc<RefCell<Option<Rc<sonata_gamebar::gamebar::GameBar>>>> = Rc::default();
    let act = gio::SimpleAction::new("gamebar", None);
    let a = app.downgrade();
    let c = Rc::downgrade(&cap);
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let gb = bar.borrow().clone().unwrap_or_else(|| {
            let gb = sonata_gamebar::gamebar::GameBar::new(app.upcast_ref(), crate::gamebar_host::host(&app, c.clone()));
            bar.replace(Some(gb.clone()));
            gb
        });
        gb.toggle();
    });
    app.add_action(&act);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini, in the Rust session: Super+V, the screenshot keys and the
    /// rest did nothing -- the Rust menu bar had only "osd" and
    /// "input-next" of the actions Wayfire's bindings call. Every action
    /// config/wayfire.ini calls on the menu bar is one it answers.
    #[test]
    fn test_menu_bar_keys_did_nothing() {
        let ini = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../config/wayfire.ini")).unwrap();
        let mut called: Vec<&str> = ini
            .lines()
            .filter(|l| l.contains("sonata2.topbar") && l.contains("ActivateAction"))
            .filter_map(|l| l.split("ActivateAction ").nth(1)?.split_whitespace().next())
            .collect();
        called.sort();
        called.dedup();
        assert!(called.len() >= 8, "{called:?}");
        let own = ["osd", "input-next", "fullscreen"];
        for name in called {
            assert!(ACTIONS.contains(&name) || own.contains(&name), "nobody answers {name}");
        }
        // what `sonata2-rs screenshot` asks for
        assert!(ACTIONS.contains(&"capture"));
    }
}
