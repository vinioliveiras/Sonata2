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
