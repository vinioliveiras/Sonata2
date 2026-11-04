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
