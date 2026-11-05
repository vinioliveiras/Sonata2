//! macOS-style tab bar (sonata2/textedit/tabs.py): equal-width tabs under
//! the glass toolbar, the selected one in the document's own background; a
//! close button fades in at a tab's left edge on hover, where an edited
//! document shows a dot; "+" at the right. Tabs are dragged sideways to
//! reorder (the others glide out of its way), middle-click closes,
//! right-click asks the owner for a menu.

use gtk::{gdk, pango, prelude::*};
use sonata_i18n::tr;
use sonata_ui::transition::{self, GlideBox};
use std::cell::RefCell;
use std::rc::Rc;

/// What the strip asks of its window, by document id.
pub struct Handlers {
    pub on_select: Box<dyn Fn(&str)>,
    pub on_close: Box<dyn Fn(&str)>,
    pub on_new: Box<dyn Fn()>,
    pub on_reorder: Box<dyn Fn(Vec<String>)>,
    pub on_menu: Box<dyn Fn(&str, &gtk::Widget, f64, f64)>,
}

pub struct Tab {
    pub id: String,
    pub root: gtk::Overlay,
    label: gtk::Label,
}

impl Tab {
    pub fn set_title(&self, title: &str, modified: bool, tooltip: &str) {
        self.label.set_label(title);
        self.root.set_tooltip_text(Some(tooltip));
        if modified {
            self.root.add_css_class("modified");
        } else {
            self.root.remove_css_class("modified");
        }
    }
}

pub struct TabStrip {
    pub root: gtk::Box,
    row: GlideBox,
    tabs: RefCell<Vec<Tab>>,
    handlers: Rc<Handlers>,
    drag: RefCell<Option<(String, f64, bool)>>,
}

impl TabStrip {
    pub fn new(handlers: Handlers) -> Rc<Self> {
        crate::window::register_tabs_css();
        let root = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        root.add_css_class("te-tabs");
        let row = GlideBox::new(gtk::Orientation::Horizontal, 0);
        row.set_homogeneous(true);
        row.set_hexpand(true);
        root.append(&row);
        let plus = gtk::Button::builder().icon_name("list-add-symbolic").can_focus(false).tooltip_text(tr!("New Tab")).build();
        plus.add_css_class("te-tab-new");
        root.append(&plus);
        let handlers = Rc::new(handlers);
        let h = handlers.clone();
        plus.connect_clicked(move |_| (h.on_new)());
        let strip = Rc::new(TabStrip { root, row, tabs: RefCell::default(), handlers, drag: RefCell::default() });
        let drag = gtk::GestureDrag::builder().button(gdk::BUTTON_PRIMARY).build();
        let w = Rc::downgrade(&strip);
        drag.connect_drag_begin(move |g, x, y| {
            if let Some(s) = w.upgrade() {
                s.drag_begin(g, x, y);
            }
        });
        let w = Rc::downgrade(&strip);
        drag.connect_drag_update(move |_, dx, _| {
            if let Some(s) = w.upgrade() {
                s.drag_update(dx);
            }
        });
        let w = Rc::downgrade(&strip);
        drag.connect_drag_end(move |_, _, _| {
            if let Some(s) = w.upgrade() {
                s.drag_end();
            }
        });
        strip.row.add_controller(drag);
        strip
    }

    /// The ids in the order shown.
    pub fn order(&self) -> Vec<String> {
        let tabs = self.tabs.borrow();
        let mut out = Vec::new();
        let mut c = self.row.first_child();
        while let Some(w) = c {
            if let Some(t) = tabs.iter().find(|t| t.root.upcast_ref::<gtk::Widget>() == &w) {
                out.push(t.id.clone());
            }
            c = w.next_sibling();
        }
        out
    }

    pub fn with_tab<R>(&self, id: &str, f: impl FnOnce(&Tab) -> R) -> Option<R> {
        self.tabs.borrow().iter().find(|t| t.id == id).map(f)
    }

    fn widget_at_index(&self, i: usize) -> Option<gtk::Widget> {
        let mut c = self.row.first_child();
        let mut n = 0;
        while let Some(w) = c {
            if n == i {
                return Some(w);
            }
            n += 1;
            c = w.next_sibling();
        }
        None
    }

    pub fn add(self: &Rc<Self>, id: &str, index: Option<usize>) {
        let root = gtk::Overlay::builder().hexpand(true).build();
        root.add_css_class("te-tab");
        let label = gtk::Label::builder()
            .ellipsize(pango::EllipsizeMode::Middle)
            .width_chars(1)
            .max_width_chars(40)
            .hexpand(true)
            .xalign(0.5)
            .build();
        root.set_child(Some(&label));
        let dot = gtk::Box::builder().halign(gtk::Align::Start).valign(gtk::Align::Center).can_target(false).build();
        dot.add_css_class("te-tab-dot");
        root.add_overlay(&dot);
        let close = gtk::Button::builder()
            .icon_name("window-close-symbolic")
            .halign(gtk::Align::Start)
            .valign(gtk::Align::Center)
            .can_focus(false)
            .tooltip_text(tr!("Close Tab"))
            .build();
        close.add_css_class("te-tab-close");
        let (h, i) = (self.handlers.clone(), id.to_string());
        close.connect_clicked(move |_| (h.on_close)(&i));
        root.add_overlay(&close);
        let click = gtk::GestureClick::builder().button(0).build();
        let (h, i) = (self.handlers.clone(), id.to_string());
        click.connect_pressed(move |g, _n, x, y| match g.current_button() {
            gdk::BUTTON_PRIMARY => (h.on_select)(&i),
            gdk::BUTTON_SECONDARY => {
                if let Some(w) = g.widget() {
                    (h.on_menu)(&i, &w, x, y);
                }
            }
            _ => {}
        });
        let (h, i) = (self.handlers.clone(), id.to_string());
        click.connect_released(move |g, _n, _x, _y| {
            if g.current_button() == gdk::BUTTON_MIDDLE {
                (h.on_close)(&i);
            }
        });
        root.add_controller(click);
        let count = self.tabs.borrow().len();
        match index.filter(|i| *i < count) {
            None => self.row.append(&root),
            Some(0) => self.row.insert_child_after(&root, None::<&gtk::Widget>),
            Some(i) => {
                let before = self.widget_at_index(i - 1);
                self.row.insert_child_after(&root, before.as_ref());
            }
        }
        self.tabs.borrow_mut().push(Tab { id: id.to_string(), root, label });
    }

    pub fn remove(&self, id: &str) {
        let mut tabs = self.tabs.borrow_mut();
        if let Some(i) = tabs.iter().position(|t| t.id == id) {
            let t = tabs.remove(i);
            self.row.remove(&t.root);
        }
    }

    pub fn select(&self, id: &str) {
        for t in self.tabs.borrow().iter() {
            if t.id == id {
                t.root.add_css_class("selected");
            } else {
                t.root.remove_css_class("selected");
            }
        }
    }

    pub fn move_to(&self, id: &str, index: usize) {
        let order: Vec<String> = self.order().into_iter().filter(|x| x != id).collect();
        let index = index.min(order.len());
        let tabs = self.tabs.borrow();
        let Some(tab) = tabs.iter().find(|t| t.id == id) else { return };
        let before = if index > 0 { tabs.iter().find(|t| t.id == order[index - 1]).map(|t| t.root.clone()) } else { None };
        // the others slide to their new place (the moved one follows the pointer)
        let others: Vec<gtk::Widget> = tabs.iter().filter(|t| t.id != id).map(|t| t.root.clone().upcast()).collect();
        let glide = transition::glide_record(&others, self.row.upcast_ref());
        self.row.reorder_child_after(&tab.root, before.as_ref());
        transition::glide_play(glide, self.row.upcast_ref(), transition::GLIDE_MS);
    }

    // -- drag to reorder ---------------------------------------------------------------------
    fn tab_at(&self, x: f64) -> Option<String> {
        let tabs = self.tabs.borrow();
        tabs.iter()
            .find(|t| {
                t.root.compute_point(&self.row, &gtk::graphene::Point::new(0.0, 0.0))
                    .is_some_and(|p| p.x() as f64 <= x && x < p.x() as f64 + t.root.width() as f64)
            })
            .map(|t| t.id.clone())
    }

    fn drag_begin(&self, g: &gtk::GestureDrag, x: f64, y: f64) {
        let id = self.tab_at(x);
        // the close button keeps its click
        let mut picked = self.row.pick(x, y, gtk::PickFlags::DEFAULT);
        while let Some(p) = picked {
            if p.is::<gtk::Button>() {
                g.set_state(gtk::EventSequenceState::Denied);
                return;
            }
            if p.has_css_class("te-tab") {
                break;
            }
            picked = p.parent();
        }
        *self.drag.borrow_mut() = id.map(|i| (i, x, false));
    }

    fn drag_update(&self, dx: f64) {
        let Some((id, x0, started)) = self.drag.borrow().clone() else { return };
        if dx.abs() < 6.0 && !started {
            return;
        }
        self.with_tab(&id, |t| t.root.add_css_class("dragging"));
        *self.drag.borrow_mut() = Some((id.clone(), x0, true));
        let order = self.order();
        if order.len() < 2 {
            return;
        }
        let w = (self.row.width() as f64 / order.len() as f64).max(1.0);
        let target = ((x0 + dx) / w).floor().clamp(0.0, order.len() as f64 - 1.0) as usize;
        if order.iter().position(|x| *x == id) != Some(target) {
            self.move_to(&id, target);
            (self.handlers.on_reorder)(self.order());
        }
    }

    fn drag_end(&self) {
        if let Some((id, _, _)) = self.drag.borrow_mut().take() {
            self.with_tab(&id, |t| t.root.remove_css_class("dragging"));
        }
    }
}
