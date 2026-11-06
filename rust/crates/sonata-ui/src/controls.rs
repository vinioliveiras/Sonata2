//! Controls (macOS Big Sur), sonata2/ui/controls.py: push button, pop-up
//! button, switch, text field, circle button, segmented control. Plain GTK
//! widgets with Sonata's classes; the CSS is controls.py's own (css.json).

use gtk::prelude::*;
use std::collections::HashMap;

pub(crate) fn register_css() {
    crate::css::register_module("ui/controls.py");
}

/// style: "" (plain), "default" (accent), "destructive".
pub fn push_button<F: Fn() + 'static>(label: &str, on_click: F, style: &str) -> gtk::Button {
    let b = gtk::Button::with_label(label);
    b.add_css_class("sonata-button");
    if !style.is_empty() {
        b.add_css_class(style);
    }
    b.connect_clicked(move |_| on_click());
    b
}

/// A pop-up button (GtkDropDown) of `options`; `on_change(index)`.
pub fn popup_button<F: Fn(u32) + 'static>(options: &[String], selected: u32, on_change: F) -> gtk::DropDown {
    let strs: Vec<&str> = options.iter().map(String::as_str).collect();
    let dd = gtk::DropDown::from_strings(&strs);
    dd.add_css_class("sonata-popup");
    dd.set_selected(selected);
    dd.connect_selected_notify(move |d| on_change(d.selected()));
    dd
}

pub fn switch(active: bool) -> gtk::Switch {
    let sw = gtk::Switch::builder().active(active).valign(gtk::Align::Center).build();
    sw.add_css_class("sonata-switch");
    sw
}

/// A one-line text field.
pub fn text_field(text: &str, placeholder: &str) -> gtk::Entry {
    let e = gtk::Entry::builder().text(text).placeholder_text(placeholder).build();
    e.add_css_class("sonata-field");
    e
}

/// A password field with the peek eye (text_field(secret=True)).
pub fn secret_field(text: &str, placeholder: &str) -> gtk::PasswordEntry {
    let e = gtk::PasswordEntry::builder().show_peek_icon(true).css_classes(["sonata-field"]).build();
    e.set_property("placeholder-text", placeholder);
    e.set_text(text);
    e
}

/// A round accent icon button (a text area's Send).
pub fn round_button<F: Fn() + 'static>(icon: &str, tooltip: &str, on_click: F) -> gtk::Button {
    let b = gtk::Button::builder().icon_name(icon).css_classes(["sonata-round"]).tooltip_text(tooltip).can_focus(false).build();
    b.connect_clicked(move |_| on_click());
    b
}

/// A rounded multi-line field that grows with its text up to max_height
/// (then scrolls), with a placeholder and an optional trailing widget (a
/// send button). on_submit(text): Return (Shift+Return starts a new line).
pub struct TextArea {
    pub root: gtk::Box,
    pub view: gtk::TextView,
}

impl TextArea {
    pub fn new(placeholder: &str, max_height: i32, trailing: Option<&gtk::Widget>, on_submit: impl Fn(String) + 'static, on_change: impl Fn(String) + 'static) -> Self {
        let root = gtk::Box::builder().spacing(6).css_classes(["sonata-text-area"]).build();
        let view = gtk::TextView::builder().wrap_mode(gtk::WrapMode::WordChar).accepts_tab(false).hexpand(true).top_margin(6).bottom_margin(6).build();
        // no scroll bar until the field is at its tallest: a scroll bar's
        // minimum length made a one-line field twice as tall
        let scroll = gtk::ScrolledWindow::builder().child(&view).hscrollbar_policy(gtk::PolicyType::Never).vscrollbar_policy(gtk::PolicyType::External).propagate_natural_height(true).max_content_height(max_height).hexpand(true).build();
        let ph = gtk::Label::builder().label(placeholder).css_classes(["sonata-placeholder"]).xalign(0.0).can_target(false).halign(gtk::Align::Start).valign(gtk::Align::Center).build();
        let field = gtk::Overlay::builder().child(&scroll).hexpand(true).build();
        field.add_overlay(&ph);
        root.append(&field);
        if let Some(t) = trailing {
            t.set_valign(gtk::Align::End);
            root.append(t);
        }
        let (v, s) = (view.downgrade(), scroll.downgrade());
        view.buffer().connect_changed(move |buf| {
            ph.set_visible(buf.char_count() == 0);
            let (v, s) = (v.clone(), s.clone());
            glib::idle_add_local_once(move || {
                if let (Some(v), Some(s)) = (v.upgrade(), s.upgrade()) {
                    let tall = v.measure(gtk::Orientation::Vertical, v.width().max(1)).1 > max_height;
                    s.set_policy(gtk::PolicyType::Never, if tall { gtk::PolicyType::Automatic } else { gtk::PolicyType::External });
                }
            });
            on_change(buf.text(&buf.start_iter(), &buf.end_iter(), false).to_string());
        });
        let keys = gtk::EventControllerKey::new();
        let v = view.downgrade();
        keys.connect_key_pressed(move |_, k, _, state| {
            if (k == gtk::gdk::Key::Return || k == gtk::gdk::Key::KP_Enter) && !state.contains(gtk::gdk::ModifierType::SHIFT_MASK) {
                if let Some(v) = v.upgrade() {
                    let b = v.buffer();
                    on_submit(b.text(&b.start_iter(), &b.end_iter(), false).to_string());
                }
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        view.add_controller(keys);
        Self { root, view }
    }

    pub fn text(&self) -> String {
        let b = self.view.buffer();
        b.text(&b.start_iter(), &b.end_iter(), false).to_string()
    }

    pub fn set_text(&self, text: &str) {
        self.view.buffer().set_text(text);
    }

    pub fn grab_focus(&self) {
        self.view.grab_focus();
    }
}

pub const TONES: [&str; 4] = ["grey", "green", "red", "orange"];

/// A big round button with a word in it, tinted (macOS Clock's Start,
/// Stop, Lap).
pub fn circle_button<F: Fn() + 'static>(label: &str, on_click: F, tone: &str) -> gtk::Button {
    let b = gtk::Button::builder().label(label).can_focus(false).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
    b.add_css_class("sonata-circle");
    b.add_css_class(tone);
    b.connect_clicked(move |_| on_click());
    b
}

/// A circle button's tint.
pub fn set_tone(b: &gtk::Button, tone: &str) {
    for t in TONES {
        if t == tone {
            b.add_css_class(t);
        } else {
            b.remove_css_class(t);
        }
    }
}

/// A row of choices, the current one in the accent -- or, plain, raised in
/// the control colour (a toolbar's tabs).
pub struct Segmented {
    pub root: gtk::Box,
    pub buttons: HashMap<String, gtk::Button>,
}

impl Segmented {
    pub fn new<F: Fn(&str) + Clone + 'static>(choices: &[(&str, String)], current: Option<&str>, on_pick: F, plain: bool) -> Self {
        let root = gtk::Box::builder().spacing(2).homogeneous(true).build();
        root.add_css_class("seg");
        if plain {
            root.add_css_class("plain");
        }
        let mut buttons = HashMap::new();
        for (value, label) in choices {
            let b = gtk::Button::builder().label(label.as_str()).can_focus(false).build();
            let (v, f) = (value.to_string(), on_pick.clone());
            b.connect_clicked(move |_| f(&v));
            root.append(&b);
            buttons.insert(value.to_string(), b);
        }
        let s = Segmented { root, buttons };
        s.select(current.unwrap_or(""));
        s
    }

    /// Mark one, without calling on_pick.
    pub fn select(&self, value: &str) {
        for (v, b) in &self.buttons {
            if v == value {
                b.add_css_class("on");
            } else {
                b.remove_css_class("on");
            }
        }
    }
}

/// A slider was let go: the left button up, a touch ended, an arrow key up
/// (ui.controls.is_release).
pub fn is_release(kind: gtk::gdk::EventType, button: u32, key: Option<gtk::gdk::Key>) -> bool {
    use gtk::gdk::{EventType, Key};
    match kind {
        EventType::ButtonRelease => button == 1,
        EventType::TouchEnd => true,
        EventType::KeyRelease => key.is_some_and(|k| [Key::Left, Key::Right, Key::Up, Key::Down, Key::Page_Up, Key::Page_Down, Key::Home, Key::End].contains(&k)),
        _ => false,
    }
}

/// callback() when a slider is let go (mouse, touch) or an arrow key on it
/// comes up -- not while dragging, and never for a value set from code
/// (macOS plays the volume feedback once, on release; ui.controls.on_release).
pub fn on_release(widget: &impl IsA<gtk::Widget>, callback: impl Fn() + 'static) {
    let legacy = gtk::EventControllerLegacy::new();
    legacy.set_propagation_phase(gtk::PropagationPhase::Capture);
    legacy.connect_event(move |_, ev| {
        let button = ev.downcast_ref::<gtk::gdk::ButtonEvent>().map_or(0, |b| b.button());
        let key = ev.downcast_ref::<gtk::gdk::KeyEvent>().map(|k| k.keyval());
        if is_release(ev.event_type(), button, key) {
            callback();
        }
        gtk::glib::Propagation::Proceed // the slider still gets every event
    });
    widget.add_controller(legacy);
}

/// A thin slider (macOS menu slider): `on_change(value)` while dragging; a
/// double-click puts it back to `default` (controls.slider, style "menu").
pub fn slider<F: Fn(f64) + 'static>(value: f64, on_change: F, lower: f64, upper: f64, default: Option<f64>) -> gtk::Scale {
    let s = gtk::Scale::with_range(gtk::Orientation::Horizontal, lower, upper, 1.0);
    s.add_css_class("sonata-slider");
    s.set_draw_value(false);
    s.set_value(value);
    s.set_hexpand(true);
    s.connect_value_changed(move |sc| on_change(sc.value()));
    if let Some(d) = default {
        reset_on_double_click(&s, d);
    }
    s
}

/// Double-click a slider: back to its default value. The slider claims
/// every press for its own drag, so the clicks are timed here from the raw
/// events (capture phase), and the value is reset when the second click is
/// released -- after the slider's own drag is over.
pub fn reset_on_double_click(scale: &gtk::Scale, default: f64) {
    use std::cell::Cell;
    use std::rc::Rc;
    let state = Rc::new(Cell::new((0u32, 0.0f64, 0.0f64, false)));
    let legacy = gtk::EventControllerLegacy::new();
    legacy.set_propagation_phase(gtk::PropagationPhase::Capture);
    let sc = scale.clone();
    legacy.connect_event(move |_, ev| {
        let (t0, x0, y0, _) = state.get();
        match ev.event_type() {
            gtk::gdk::EventType::ButtonPress => {
                let button = ev.downcast_ref::<gtk::gdk::ButtonEvent>().map(|b| b.button()).unwrap_or(0);
                if button == 1 {
                    let t = ev.time();
                    let (x, y) = ev.position().unwrap_or((0.0, 0.0));
                    let limit = gtk::Settings::default().map(|s| s.gtk_double_click_time() as u32).unwrap_or(400);
                    let near = (x - x0).abs() < 8.0 && (y - y0).abs() < 8.0;
                    let double = t0 != 0 && t.wrapping_sub(t0) <= limit && near;
                    state.set((if double { 0 } else { t }, x, y, double));
                }
            }
            gtk::gdk::EventType::ButtonRelease if state.get().3 => {
                state.set((t0, x0, y0, false));
                let sc = sc.clone();
                gtk::glib::idle_add_local_once(move || sc.set_value(default));
            }
            _ => {}
        }
        gtk::glib::Propagation::Proceed // the slider still gets every event
    });
    scale.add_controller(legacy);
}

#[cfg(test)]
mod release_tests {
    use super::is_release;
    use gtk::gdk::{EventType, Key};

    #[test]
    fn only_letting_go_counts() {
        assert!(is_release(EventType::ButtonRelease, 1, None));
        assert!(!is_release(EventType::ButtonRelease, 3, None));
        assert!(!is_release(EventType::ButtonPress, 1, None));
        assert!(!is_release(EventType::MotionNotify, 0, None));
        assert!(is_release(EventType::TouchEnd, 0, None));
        assert!(is_release(EventType::KeyRelease, 0, Some(Key::Right)));
        assert!(!is_release(EventType::KeyRelease, 0, Some(Key::a)));
    }
}
