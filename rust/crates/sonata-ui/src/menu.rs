//! Context menus (macOS Big Sur menus), sonata2/ui/menu.py: a GtkPopoverMenu
//! from sections of items, its CSS menu.py's own. Not ported yet: submenus,
//! checks, rows with a close button, passthrough clicks.
//!
//! ```no_run
//! # let widget = gtk::Label::new(None);
//! use sonata_ui::menu::{popup, Item};
//! popup(&widget, vec![vec![Item::new("Delete Alarm", || println!("gone"))]], Some((10.0, 4.0)));
//! ```

use gtk::{gio, glib, prelude::*};
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/menu.py");
}

pub struct Item {
    pub label: String,
    pub action: Rc<dyn Fn()>,
    pub enabled: bool,
}

impl Item {
    pub fn new<F: Fn() + 'static>(label: &str, action: F) -> Self {
        Item { label: label.to_string(), action: Rc::new(action), enabled: true }
    }

    pub fn enabled(mut self, on: bool) -> Self {
        self.enabled = on;
        self
    }
}

/// Show a menu anchored to `widget`; it cleans itself up when closed.
/// at=(x, y) in widget coordinates: a context menu that opens at the
/// pointer, its top-left corner there (macOS).
pub fn popup(widget: &impl IsA<gtk::Widget>, sections: Vec<Vec<Item>>, at: Option<(f64, f64)>) -> gtk::PopoverMenu {
    let group = gio::SimpleActionGroup::new();
    let model = gio::Menu::new();
    let mut n = 0;
    for section in sections {
        let part = gio::Menu::new();
        for item in section {
            let name = format!("a{n}");
            n += 1;
            let action = gio::SimpleAction::new(&name, None);
            action.set_enabled(item.enabled);
            let f = item.action.clone();
            action.connect_activate(move |_, _| f());
            group.add_action(&action);
            part.append(Some(&item.label), Some(&format!("m.{name}")));
        }
        model.append_section(None, &part);
    }
    let pop = gtk::PopoverMenu::from_model_full(&model, gtk::PopoverMenuFlags::NESTED);
    // on the menu, not the anchor: two menus on one anchor would clash
    pop.insert_action_group("m", Some(&group));
    pop.set_has_arrow(false);
    pop.set_position(gtk::PositionType::Top);
    pop.set_offset(0, -6);
    pop.set_parent(widget);
    if let Some((x, y)) = at {
        pop.set_pointing_to(Some(&gtk::gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
        pop.set_position(gtk::PositionType::Bottom);
        pop.set_halign(gtk::Align::Start);
        pop.set_offset(0, 2);
    }
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
    pop.popup();
    pop
}
