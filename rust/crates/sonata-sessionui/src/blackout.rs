//! The screen dark while you're away, unlocked (shell/blackout.py; Vini:
//! it only dimmed, and the pointer stayed on it). Black over every display
//! (layer-shell overlay), fading in like the lock screen's (.lk-blackout,
//! loginui), the pointer hidden; any key or move fades it out again
//! (IdleLock's idle watch).
//!
//! ```text
//! blackout::show(); blackout::hide();
//! ```

use gtk::prelude::*;
use std::cell::RefCell;

pub const NAMESPACE: &str = "sonata2-blackout";
/// .lk-blackout fades out in 600 ms; then the surfaces go
pub const FADE_OUT_MS: u64 = 650;

struct Black {
    win: gtk::Window,
    black: gtk::Box,
}

thread_local! {
    static WINDOWS: RefCell<Vec<Black>> = const { RefCell::new(Vec::new()) };
}

fn css() {
    thread_local!(static DONE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) });
    if !DONE.with(|d| d.replace(true)) {
        sonata_shell::loginui::register_css(); // the .lk-blackout look
        sonata_ui::css::register_module("shell/blackout.py");
    }
}

fn window(monitor: &gtk::gdk::Monitor) -> Black {
    let win = gtk::Window::builder().title("Blackout").decorated(false).css_classes(["sonata-blackout"]).build();
    let black = gtk::Box::builder().css_classes(["lk-blackout"]).hexpand(true).vexpand(true).build();
    win.set_child(Some(&black));
    win.set_cursor_from_name(Some("none")); // no pointer on a dark screen
    if sonata_shell::layer::overlay_fullscreen(&win, NAMESPACE) {
        use gtk4_layer_shell::{KeyboardMode, LayerShell};
        win.set_keyboard_mode(KeyboardMode::None);
        sonata_shell::layer::set_monitor(&win, monitor);
    }
    Black { win, black }
}

/// The pointer off / back, by the compositor (sonata-corners): the
/// blackout's own "no cursor" only applies once the pointer moves onto it,
/// and a still pointer stayed in the middle of the dark screen (Vini).
pub fn pointer(hidden: bool) {
    sonata_shell::lockdisplay::pointer(hidden);
}

pub fn showing() -> bool {
    WINDOWS.with(|w| w.borrow().iter().any(|b| b.black.has_css_class("on")))
}

/// How many black surfaces there are (fading out ones too).
pub fn surfaces() -> usize {
    WINDOWS.with(|w| w.borrow().len())
}

pub fn show() {
    if showing() {
        return;
    }
    css();
    close(None);
    for m in sonata_shell::layer::monitors() {
        let b = window(&m);
        b.win.present();
        WINDOWS.with(|w| w.borrow_mut().push(b));
    }
    pointer(true);
    // one frame transparent, then black: the fade shows (as the lock screen's)
    glib::timeout_add_local_once(std::time::Duration::from_millis(50), || {
        WINDOWS.with(|w| w.borrow().iter().for_each(|b| b.black.add_css_class("on")));
    });
}

pub fn hide() {
    pointer(false);
    let gone: Vec<gtk::Window> = WINDOWS.with(|w| {
        let w = w.borrow();
        w.iter().for_each(|b| b.black.remove_css_class("on"));
        w.iter().map(|b| b.win.clone()).collect()
    });
    glib::timeout_add_local_once(std::time::Duration::from_millis(FADE_OUT_MS), move || close(Some(&gone)));
}

/// These windows (None: all) go -- unless shown again meanwhile (input
/// stopped again right during the fade-out).
fn close(only: Option<&[gtk::Window]>) {
    let out: Vec<Black> = WINDOWS.with(|w| {
        let mut w = w.borrow_mut();
        let (go, keep): (Vec<Black>, Vec<Black>) = w.drain(..).partition(|b| match only {
            None => true,
            Some(list) => list.contains(&b.win) && !b.black.has_css_class("on"),
        });
        *w = keep;
        go
    });
    for b in out {
        b.win.destroy();
    }
}
