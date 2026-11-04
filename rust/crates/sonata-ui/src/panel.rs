//! Panels: popovers with the menu material that hold custom content
//! (sonata2/ui/panel.py), its CSS panel.py's own. Not ported yet: rows,
//! headers, modules, the close dot, keeping a panel to show again.

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
