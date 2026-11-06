//! Control Center > Keyboard (shell/kbdmodule.py; Vini; Add Controls): the
//! input sources in a row -- the one in use in the accent, a click switches
//! (as the menu bar's input menu and Ctrl+Space do). With one source, a
//! button to add more.
//!
//! ```no_run
//! # use std::rc::Rc;
//! let m = sonata_gamebar::kbdmodule::KeyboardModule::new(
//!     Some(Rc::new(|i| println!("the menu bar uses source {i}"))),   // bar._use_layout
//!     Some(Rc::new(|| println!("Control Center closes"))));
//! ```

use gtk::prelude::*;
use sonata_backend::system;
use sonata_i18n::{gettext, tr};
use std::rc::Rc;

fn css() {
    sonata_ui::css::register_module("shell/kbdmodule.py");
}

/// The menu bar's short name of a layout: "br(abnt2)" -> "BR", "us(intl)" -> "US"
/// (topbar.layout_badge, which the Rust menu bar doesn't have yet).
pub fn layout_badge(layout: &str) -> String {
    let base = layout.split('(').next().unwrap_or("");
    let base = if base.is_empty() { "?" } else { base };
    base.to_uppercase().chars().take(3).collect()
}

/// A layout's name, translated ("pt" -> "Portuguese"); unknown ones as they are.
pub fn layout_name(layout: &str) -> String {
    system::XKB_LAYOUTS.iter().find(|(k, _)| *k == layout).map(|(_, n)| gettext(n)).unwrap_or_else(|| gettext(layout))
}

/// What a click on `layout` does with the sources `now`: None (not there,
/// or in use already), else the index the menu bar switches to and the
/// list with it first (as Wayfire wants).
pub fn picked(now: &[String], layout: &str) -> Option<(usize, Vec<String>)> {
    let i = now.iter().position(|l| l == layout)?;
    if i == 0 {
        return None;
    }
    let mut list = now.to_vec();
    let l = list.remove(i);
    list.insert(0, l);
    Some((i, list))
}

pub struct KeyboardModule {
    pub root: gtk::Box,
    pub name: gtk::Label,
    pub seg: sonata_ui::controls::Segmented,
    pub add_btn: gtk::Button,
    /// as they were when shown: they don't jump around
    pub order: Vec<String>,
}

impl KeyboardModule {
    /// use_layout(i): the menu bar switches to source i (its badges follow);
    /// None: written to Wayfire's config here. close: Control Center closes
    /// (before Settings opens).
    pub fn new(use_layout: Option<Rc<dyn Fn(usize)>>, close: Option<Rc<dyn Fn()>>) -> Rc<Self> {
        Self::with_layouts(system::keyboard_layouts(), use_layout, close)
    }

    pub fn with_layouts(order: Vec<String>, use_layout: Option<Rc<dyn Fn(usize)>>, close: Option<Rc<dyn Fn()>>) -> Rc<Self> {
        css();
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).css_classes(["panel-module", "cc-kbd"]).build();
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        head.append(&gtk::Label::builder().label(tr!("Keyboard")).xalign(0.0).css_classes(["panel-module-title"]).build());
        let name = gtk::Label::builder().xalign(1.0).hexpand(true).css_classes(["cc-kbd-name"]).ellipsize(gtk::pango::EllipsizeMode::End).width_chars(1).build();
        head.append(&name);
        root.append(&head);
        let me: Rc<std::cell::OnceCell<std::rc::Weak<Self>>> = Rc::default();
        let choices: Vec<(&str, String)> = order.iter().map(|l| (l.as_str(), layout_badge(l))).collect();
        let m = me.clone();
        let seg = sonata_ui::controls::Segmented::new(
            &choices,
            order.first().map(String::as_str),
            move |l: &str| {
                if let Some(me) = m.get().and_then(|w| w.upgrade()) {
                    me.pick(l, use_layout.as_deref());
                }
            },
            false,
        );
        for (l, b) in &seg.buttons {
            b.set_tooltip_text(Some(&layout_name(l)));
        }
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        seg.root.set_hexpand(true);
        row.append(&seg.root);
        let add_btn = gtk::Button::builder()
            .label(tr!("Add…"))
            .css_classes(["cc-kbd-add"])
            .can_focus(false)
            .valign(gtk::Align::Center)
            .tooltip_text(tr!("Add an input source (Keyboard settings)"))
            .visible(order.len() < 2)
            .build();
        add_btn.connect_clicked(move |_| {
            if let Some(c) = &close {
                c();
            }
            crate::open_settings("keyboard");
        });
        row.append(&add_btn);
        root.append(&row);
        let first = order.first().cloned().unwrap_or_default();
        let module = Rc::new(Self { root, name, seg, add_btn, order });
        let _ = me.set(Rc::downgrade(&module));
        module.show_current(&first);
        module
    }

    pub fn show_current(&self, layout: &str) {
        self.seg.select(layout);
        self.name.set_label(&layout_name(layout));
    }

    /// That layout in use now (the first of the list, as Wayfire wants).
    fn pick(&self, layout: &str, use_layout: Option<&dyn Fn(usize)>) {
        let now = system::keyboard_layouts();
        let Some((i, list)) = picked(&now, layout) else { return };
        self.show_current(layout);
        match use_layout {
            Some(f) => f(i), // the menu bar's badges follow
            None => {
                gio::spawn_blocking(move || system::set_keyboard_layouts(&list));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn badges_and_names() {
        let _env = crate::testenv::env();
        assert_eq!(layout_badge("br(abnt2)"), "BR");
        assert_eq!(layout_badge("us(intl)"), "US");
        assert_eq!(layout_badge("latam"), "LAT");
        assert_eq!(layout_badge("(x)"), "?");
        assert_eq!(layout_name("pt"), "Portuguese");
        assert_eq!(layout_name("us(intl)"), "U.S. International");
        assert_eq!(layout_name("xx"), "xx");
    }

    /// test_sources_in_a_row_a_click_switches: a click puts that source
    /// first and tells the menu bar its index; the one in use does nothing.
    #[test]
    fn test_sources_in_a_row_a_click_switches() {
        let now = vec!["pt".to_string(), "us(intl)".to_string()];
        assert_eq!(picked(&now, "us(intl)"), Some((1, vec!["us(intl)".into(), "pt".into()])));
        assert_eq!(picked(&now, "pt"), None);
        assert_eq!(picked(&now, "de"), None);
    }
}
