//! Context menus (macOS Big Sur menus), sonata2/ui/menu.py: a GtkPopoverMenu
//! from sections of items, its CSS menu.py's own. Items can carry a check
//! (shown after the text, at the right edge, as menu.py does) or a submenu
//! (opening to the side). Not ported yet: rows with a close button,
//! passthrough clicks.
//!
//! ```no_run
//! # let widget = gtk::Label::new(None);
//! use sonata_ui::menu::{popup, Item, Options};
//! popup(&widget, vec![vec![Item::new("Delete Alarm", || println!("gone"))]], Options::at(10.0, 4.0));
//! ```

use gtk::{gio, glib, prelude::*};
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/menu.py");
}

pub struct Item {
    pub label: String,
    action: Option<Rc<dyn Fn()>>,
    toggle: Option<Rc<dyn Fn(bool)>>,
    checked: Option<bool>,
    pub enabled: bool,
    submenu: Vec<Vec<Item>>,
}

impl Item {
    /// A plain item.
    pub fn new<F: Fn() + 'static>(label: &str, action: F) -> Self {
        Item { label: label.to_string(), action: Some(Rc::new(action)), toggle: None, checked: None, enabled: true, submenu: Vec::new() }
    }

    /// A checkmark item; `action` gets the new state.
    pub fn check<F: Fn(bool) + 'static>(label: &str, checked: bool, action: F) -> Self {
        Item { label: label.to_string(), action: None, toggle: Some(Rc::new(action)), checked: Some(checked), enabled: true, submenu: Vec::new() }
    }

    /// An item opening a submenu (sections of items) to the side.
    pub fn submenu(label: &str, sections: Vec<Vec<Item>>) -> Self {
        Item { label: label.to_string(), action: None, toggle: None, checked: None, enabled: true, submenu: sections }
    }

    pub fn enabled(mut self, on: bool) -> Self {
        self.enabled = on;
        self
    }
}

/// Where the menu opens: above / below its anchor, or at a point in it
/// (a context menu, its top-left corner there, as macOS).
pub struct Options {
    pub position: gtk::PositionType,
    pub at: Option<(f64, f64)>,
    pub glass: bool,
}

impl Options {
    pub fn at(x: f64, y: f64) -> Self {
        Options { position: gtk::PositionType::Bottom, at: Some((x, y)), glass: true }
    }

    pub fn below() -> Self {
        Options { position: gtk::PositionType::Bottom, at: None, glass: true }
    }

    pub fn above() -> Self {
        Options { position: gtk::PositionType::Top, at: None, glass: true }
    }
}

fn build(sections: Vec<Vec<Item>>, group: &gio::SimpleActionGroup, prefix: &str) -> gio::Menu {
    let model = gio::Menu::new();
    for (s_i, section) in sections.into_iter().enumerate() {
        let part = gio::Menu::new();
        for (i_i, item) in section.into_iter().enumerate() {
            let name = format!("{prefix}{s_i}_{i_i}");
            if !item.submenu.is_empty() {
                let sub = build(item.submenu, group, &format!("{name}_"));
                let mi = gio::MenuItem::new_submenu(Some(&item.label), &sub);
                if !item.enabled {
                    // a disabled submenu: its row points at an action that is off
                    let off = gio::SimpleAction::new(&name, None);
                    off.set_enabled(false);
                    group.add_action(&off);
                    mi.set_attribute_value("submenu-action", Some(&format!("m.{name}").to_variant()));
                }
                part.append_item(&mi);
                continue;
            }
            let action = match item.checked {
                None => {
                    let a = gio::SimpleAction::new(&name, None);
                    if let Some(f) = item.action.clone() {
                        a.connect_activate(move |_, _| f());
                    }
                    a
                }
                Some(on) => {
                    let a = gio::SimpleAction::new_stateful(&name, None, &on.to_variant());
                    if let Some(f) = item.toggle.clone() {
                        a.connect_activate(move |a, _| {
                            let now = a.state().and_then(|s| s.get::<bool>()).unwrap_or(false);
                            f(!now);
                        });
                    }
                    a
                }
            };
            action.set_enabled(item.enabled);
            group.add_action(&action);
            part.append(Some(&item.label), Some(&format!("m.{name}")));
        }
        model.append_section(None, &part);
    }
    model
}

/// Checkmark rows: the text starts where every other row's does and the
/// check follows it at the right edge (Vini). GTK's own indicator (hidden
/// by CSS) still carries the state; the image mirrors it.
fn checks_after_text(w: &gtk::Widget) {
    let mut c = w.first_child();
    while let Some(child) = c {
        if child.css_name() == "modelbutton" {
            check_row(&child);
            let row = child.clone();
            child.connect_map(move |_| {
                let row = row.clone();
                glib::idle_add_local_once(move || check_row(&row));
            });
        }
        checks_after_text(&child); // also a nested submenu, a popover under its row
        c = child.next_sibling();
    }
}

fn check_row(btn: &gtk::Widget) {
    let has_ours = btn.last_child().is_some_and(|l| l.has_css_class("sonata-menu-check"));
    if has_ours {
        return;
    }
    let Some(boxed) = btn.first_child() else { return };
    let mut c = boxed.first_child();
    let mut ind = None;
    while let Some(x) = c {
        if x.css_name() == "check" || x.css_name() == "radio" {
            ind = Some(x.clone());
            break;
        }
        c = x.next_sibling();
    }
    let Some(ind) = ind else { return };
    let img = gtk::Image::from_icon_name("object-select-symbolic");
    img.add_css_class("sonata-menu-check");
    img.set_halign(gtk::Align::End);
    img.set_hexpand(true);
    img.insert_before(btn, None::<&gtk::Widget>); // last: after the text (and its shortcut)
    let sync = {
        let img = img.clone();
        move |ind: &gtk::Widget| img.set_opacity(if ind.state_flags().contains(gtk::StateFlags::CHECKED) { 1.0 } else { 0.0 })
    };
    sync(&ind);
    ind.connect_state_flags_changed(move |i, _| sync(i));
}

/// Show a menu anchored to `widget`; it cleans itself up when closed.
pub fn popup(widget: &impl IsA<gtk::Widget>, sections: Vec<Vec<Item>>, opts: Options) -> gtk::PopoverMenu {
    let group = gio::SimpleActionGroup::new();
    let model = build(sections, &group, "i");
    let pop = gtk::PopoverMenu::from_model_full(&model, gtk::PopoverMenuFlags::NESTED);
    // on the menu, not the anchor: two menus on one anchor would clash
    pop.insert_action_group("m", Some(&group));
    pop.set_has_arrow(false);
    if opts.glass {
        pop.add_css_class("glass");
    }
    pop.set_position(opts.position);
    pop.set_offset(0, if opts.position == gtk::PositionType::Top { -6 } else { 6 });
    pop.set_parent(widget);
    if let Some((x, y)) = opts.at {
        pop.set_pointing_to(Some(&gtk::gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
        pop.set_position(gtk::PositionType::Bottom);
        pop.set_halign(gtk::Align::Start);
        pop.set_offset(0, 2);
    }
    pop.connect_map(|p| checks_after_text(p.upcast_ref()));
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
    pop.popup();
    pop
}
