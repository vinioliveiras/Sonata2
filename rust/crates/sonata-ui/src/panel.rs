//! Panels: popovers with the menu material that hold custom content
//! (sonata2/ui/panel.py), its CSS panel.py's own. Not ported yet: headers,
//! modules, the close dot, keeping a panel to show again.

use gtk::{glib, prelude::*};

pub(crate) fn register_css() {
    crate::css::register_module("ui/panel.py");
}

/// A panel around `child`, anchored to `anchor`; it goes when it closes.
pub fn popup(anchor: &impl IsA<gtk::Widget>, child: &impl IsA<gtk::Widget>, position: gtk::PositionType) -> gtk::Popover {
    let gap = 4;
    let pop = gtk::Popover::builder().has_arrow(false).position(position).build();
    pop.add_css_class("sonata-panel");
    pop.set_child(Some(child));
    let (x, y) = match position {
        gtk::PositionType::Top => (0, -gap),
        gtk::PositionType::Bottom => (0, gap),
        gtk::PositionType::Left => (-gap, 0),
        _ => (gap, 0),
    };
    pop.set_offset(x, y);
    pop.set_parent(anchor);
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
    pop.popup();
    pop
}

pub fn separator() -> gtk::Box {
    let b = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    b.add_css_class("panel-sep");
    b
}

/// A menu-like row: an icon, a label and a trailing widget; with
/// `on_click`, a button. Returns the row and its label.
pub fn row(icon: Option<&str>, text: &str, trailing: Option<&gtk::Widget>, on_click: Option<Box<dyn Fn()>>) -> (gtk::Widget, gtk::Label) {
    let content = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    if let Some(i) = icon {
        let img = gtk::Image::from_icon_name(i);
        img.set_pixel_size(16);
        content.append(&img);
    }
    let label = gtk::Label::builder().label(text).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(30).build();
    content.append(&label);
    if let Some(t) = trailing {
        content.append(t);
    }
    match on_click {
        None => {
            content.add_css_class("panel-row");
            content.add_css_class("static");
            (content.upcast(), label)
        }
        Some(f) => {
            let b = gtk::Button::builder().can_focus(false).child(&content).build();
            b.add_css_class("panel-row");
            b.connect_clicked(move |_| f());
            (b.upcast(), label)
        }
    }
}
