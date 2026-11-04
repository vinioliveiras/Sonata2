//! Sonata's app windows (sonata2/ui/window.py): the standard frame, glass,
//! the unified glass toolbar, and the size each window remembers
//! (winsize.py). The CSS is window.py's own (css.json).

use gtk::{glib, prelude::*};
use serde_json::{json, Value};
use std::cell::Cell;
use std::rc::Rc;
use std::time::Duration;

pub(crate) fn register_css() {
    crate::css::register_module("ui/window.py");
}

/// Give an app window Sonata's standard frame (call once, any time).
pub fn standard(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    let w = win.upcast_ref::<gtk::Widget>();
    w.add_css_class("sonata-window");
    w.set_overflow(gtk::Overflow::Hidden); // children clipped to the rounded corners
}

/// Present a window that was hidden instead of closed. GTK remembers a
/// minimize as "minimize when shown" and Wayland never tells it the Dock
/// brought the window back, so a hidden window came back minimized.
pub fn show_again(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    if !win.upcast_ref::<gtk::Widget>().is_visible() {
        win.unminimize();
    }
    win.present();
}

/// A toolbar button: (icon, tooltip, callback).
pub type Tool = (&'static str, String, Box<dyn Fn()>);

/// A toolbar continuing the glass title bar the compositor draws (macOS
/// unified toolbar), `end` buttons at its end. Returns the handle (to add
/// to the window), the bar (its centre is free) and the buttons.
pub fn glass_toolbar(win: &impl IsA<gtk::Widget>, end: Vec<Tool>) -> (gtk::WindowHandle, gtk::CenterBox, Vec<gtk::Button>) {
    win.upcast_ref::<gtk::Widget>().add_css_class("sonata-unified");
    let bar = gtk::CenterBox::new();
    bar.add_css_class("sonata-toolbar");
    let mut buttons = Vec::new();
    if !end.is_empty() {
        let row = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        for (icon, tip, cb) in end {
            let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).can_focus(false).build();
            b.add_css_class("tool");
            b.connect_clicked(move |_| cb());
            row.append(&b);
            buttons.push(b);
        }
        bar.set_end_widget(Some(&row));
    }
    (gtk::WindowHandle::builder().child(&bar).build(), bar, buttons)
}

// -- the size a window opens at (winsize.py: windows.json) ----------------------------------
const MIN: (i64, i64) = (240, 160); // smaller: a glitch, not a size someone chose
const SAVE_SIZE_MS: u64 = 600; // a resize settles, then its size is kept

/// (width, height, maximized) kept for `key`.
pub fn saved_size(key: &str) -> Option<(i32, i32, bool)> {
    let stored = sonata_config::stored("windows");
    let st = stored.get(key)?;
    let num = |k: &str| st.get(k).and_then(|v| v.as_f64().or_else(|| v.as_str()?.parse().ok())).map(|f| f as i64);
    let (w, h) = (num("width")?, num("height")?);
    if w < MIN.0 || h < MIN.1 {
        return None;
    }
    Some((w as i32, h as i32, st.get("maximized").and_then(Value::as_bool).unwrap_or(false)))
}

/// Kept for next time (not written when unchanged).
pub fn save_size(key: &str, width: i32, height: i32, maximized: bool) {
    if (width as i64) < MIN.0 || (height as i64) < MIN.1 || saved_size(key) == Some((width, height, maximized)) {
        return;
    }
    let _ = sonata_config::update("windows", &json!({key: {"width": width, "height": height, "maximized": maximized}}));
}

/// The display's size, for a first size that fits it.
fn screen_size(win: &gtk::Window) -> Option<(i32, i32)> {
    let display = WidgetExt::display(win);
    let monitor = win
        .surface()
        .and_then(|s| display.monitor_at_surface(&s))
        .or_else(|| display.monitors().item(0).and_then(|m| m.downcast::<gtk::gdk::Monitor>().ok()))?;
    let g = monitor.geometry();
    Some((g.width(), g.height()))
}

/// The window opens at the size it last had (first time: w x h, never
/// bigger than the display) and maximized if it was; that size is kept when
/// it closes, and shortly after a resize (a restart ends the app without
/// closing its window).
pub fn remember_size(win: &gtk::Window, key: &str, w: i32, h: i32) {
    let (mut w, mut h, maximized) = saved_size(key).unwrap_or((w, h, false));
    if let Some((sw, sh)) = screen_size(win) {
        w = w.min(sw);
        h = h.min(sh);
    }
    win.set_default_size(w, h);
    if maximized {
        win.maximize();
    }
    let key = key.to_string();
    let keep: Rc<dyn Fn(&gtk::Window)> = Rc::new(move |win: &gtk::Window| {
        if win.is_fullscreen() {
            return;
        }
        let (dw, dh) = if win.is_maximized() || !win.is_realized() {
            win.default_size() // GTK keeps it as the unmaximized size
        } else {
            (win.width(), win.height())
        };
        if dw > 0 && dh > 0 {
            save_size(&key, dw, dh, win.is_maximized());
        }
    });
    let k = keep.clone();
    win.connect_close_request(move |win| {
        k(win);
        glib::Propagation::Proceed
    });
    let pending: Rc<Cell<Option<glib::SourceId>>> = Rc::default();
    let soon: Rc<dyn Fn(&gtk::Window)> = Rc::new(move |win: &gtk::Window| {
        if let Some(id) = pending.take() {
            id.remove();
        }
        let (weak, k, p) = (win.downgrade(), keep.clone(), pending.clone());
        pending.set(Some(glib::timeout_add_local_once(Duration::from_millis(SAVE_SIZE_MS), move || {
            p.set(None);
            if let Some(win) = weak.upgrade().filter(|w| w.is_realized()) {
                k(&win);
            }
        })));
    });
    let s = soon.clone();
    win.connect_default_width_notify(move |w| s(w));
    let s = soon.clone();
    win.connect_default_height_notify(move |w| s(w));
    win.connect_maximized_notify(move |w| soon(w));
}
