//! Clipboard history and the emoji picker (sonata2/shell/clipboard.py,
//! clip_picker.py, emoji.py): Super+V and Ctrl+Super+Space.
//!
//! In the Python Sonata both pickers are actions of the menu bar process
//! (`app.clipboard`, `app.emoji`), built on first use, and the history is
//! the menu bar's own -- in memory only, never on disk. Here each picker is
//! a small resident process of its own, as Spotlight is:
//!
//!   sonata2-rs clipboard [--background]   the history; opens the panel
//!   sonata2-rs emoji [--background]       the Character Viewer
//!
//! Running one again opens its picker (single instance). `--background`
//! starts it without opening anything: the clipboard one then keeps the
//! history from login on.
//!
//! The clipboard process watches the clipboard (one watcher: two would be
//! two programs answering for it). Emoji's "Frequently Used" is
//! ~/.config/sonata2/emoji.json.

pub mod clip_picker;
pub mod clipboard;
pub mod emoji;
pub mod picker;
pub mod vkeyboard;

#[cfg(test)]
mod testenv;

use gtk::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

pub fn clipboard_app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.clipboard"
}

pub fn emoji_app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.emoji"
}

/// One resident picker per process: built on the first activation, opened
/// by every one after it (`--background`: the first one only builds it).
fn run_picker<P: 'static>(app_id: &str, args: &[String], build: impl Fn(&gtk::Application) -> P + 'static, open: impl Fn(&P) + 'static) -> glib::ExitCode {
    if !args.iter().any(|a| a == "--preview") {
        sonata_shell::layer::ensure_preload();
    }
    let background = std::cell::Cell::new(args.iter().any(|a| a == "--background"));
    let app = gtk::Application::builder().application_id(app_id).build();
    let state: Rc<RefCell<Option<P>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if state.borrow().is_none() {
            sonata_ui::setup();
            state.replace(Some(build(app)));
            hold.replace(Some(app.hold()));
            if background.replace(false) {
                return;
            }
        }
        if let Some(p) = state.borrow().as_ref() {
            open(p);
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

/// `sonata2-rs clipboard [--background]`: the clipboard history and its
/// panel (Super+V).
pub fn run_clipboard(args: &[String]) -> glib::ExitCode {
    run_picker(
        clipboard_app_id(),
        args,
        |app| {
            sonata_ui::css::register_module("shell/clip_picker.py");
            // this process watches the clipboard (see above)
            let history = clipboard::History::new(true);
            let history_c = history.clone();
            app.connect_shutdown(move |_| history_c.stop());
            clip_picker::ClipboardPicker::new(app, history)
        },
        |p| {
            p.open();
        },
    )
}

/// `sonata2-rs emoji [--background]`: the Character Viewer
/// (Ctrl+Super+Space).
pub fn run_emoji(args: &[String]) -> glib::ExitCode {
    run_picker(
        emoji_app_id(),
        args,
        |app| {
            sonata_ui::css::register_module("shell/emoji.py");
            emoji::EmojiPicker::new(app)
        },
        |p| p.open(),
    )
}
