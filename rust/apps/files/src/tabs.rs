//! Files tab strip (files/tabs.py; Finder tabs): shown under the toolbar
//! once a window has two or more tabs. Equal-width tabs with the folder's
//! name, the close x on the left on hover, "+" at the end. Click selects,
//! middle-click closes, dragging a tab sideways reorders it (live). Files
//! dragged onto a tab go into that tab's folder; holding them over a tab
//! brings it to the front (spring-loaded).

use gtk::prelude::*;
use gtk::{gdk, pango};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

/// a drag held over a tab brings it to the front
const SPRING_MS: u64 = 700;

/// What the strip asks of its window (tabs by id).
pub trait Host {
    fn select_tab(&self, id: u64);
    fn close_tab(&self, id: u64);
    fn new_tab(&self);
    fn move_tab(&self, id: u64, index: usize);
    fn drop_files(&self, files: Vec<gio::File>, dest: gio::File, copy: bool) -> bool;
    fn tab_uri(&self, id: u64) -> Option<String>;
}

struct TabButton {
    id: u64,
    root: gtk::Box,
    label: gtk::Label,
    spring: RefCell<Option<glib::SourceId>>,
}

impl TabButton {
    fn cancel_spring(&self) {
        if let Some(s) = self.spring.take() {
            sonata_ui::cancel(s);
        }
    }
}

pub struct TabStrip {
    pub root: gtk::Revealer,
    row: gtk::Box,
    buttons: RefCell<Vec<Rc<TabButton>>>,
    host: RefCell<Option<Weak<dyn Host>>>,
    _monitors: Vec<gio::FileMonitor>,
}

impl TabStrip {
    pub fn new() -> Rc<Self> {
        sonata_ui::css::register_module("files/tabs.py");
        let root = gtk::Revealer::builder().transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(sonata_ui::tokens::ms(150.0)).build();
        let bar = gtk::Box::builder().css_classes(["fs-tabs"]).build();
        let row = gtk::Box::builder().homogeneous(true).hexpand(true).build();
        bar.append(&row);
        let add = gtk::Button::builder().icon_name("list-add-symbolic").css_classes(["fs-tab-add"]).tooltip_text(tr!("New Tab")).valign(gtk::Align::Center).focusable(false).build();
        bar.append(&add);
        root.set_child(Some(&bar));
        let strip = Rc::new_cyclic(|me: &Weak<TabStrip>| {
            let w = me.clone();
            let monitors = sonata_ui::window::follow_tab_bar(move || {
                if let Some(s) = w.upgrade() {
                    s.show();
                }
            });
            TabStrip { root, row, buttons: RefCell::default(), host: RefCell::default(), _monitors: monitors }
        });
        let w = Rc::downgrade(&strip);
        add.connect_clicked(move |_| {
            if let Some(h) = w.upgrade().and_then(|s| s.host()) {
                h.new_tab();
            }
        });
        strip
    }

    pub fn set_host(&self, host: Weak<dyn Host>) {
        self.host.replace(Some(host));
    }

    fn host(&self) -> Option<Rc<dyn Host>> {
        self.host.borrow().as_ref().and_then(Weak::upgrade)
    }

    pub fn add(self: &Rc<Self>, id: u64, title: &str, index: usize) {
        let root = gtk::Box::builder().css_classes(["fs-tab"]).hexpand(true).build();
        let over = gtk::Overlay::builder().hexpand(true).build();
        let mid = gtk::Box::builder().spacing(5).halign(gtk::Align::Center).css_classes(["fs-tab-label"]).build();
        mid.append(&gtk::Image::builder().icon_name("folder-symbolic").css_classes(["fs-tab-icon"]).build());
        let label = gtk::Label::builder().ellipsize(pango::EllipsizeMode::Middle).width_chars(1).build();
        mid.append(&label);
        over.set_child(Some(&mid));
        let close = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["fs-tab-close"]).tooltip_text(tr!("Close Tab")).halign(gtk::Align::Start).valign(gtk::Align::Center).focusable(false).build();
        over.add_overlay(&close);
        root.append(&over);
        let b = Rc::new(TabButton { id, root, label, spring: RefCell::default() });
        let w = Rc::downgrade(self);
        close.connect_clicked(move |_| {
            if let Some(h) = w.upgrade().and_then(|s| s.host()) {
                h.close_tab(id);
            }
        });
        let press = gtk::GestureClick::builder().button(0).build();
        let w = Rc::downgrade(self);
        press.connect_pressed(move |g, _, _, _| {
            let Some(h) = w.upgrade().and_then(|s| s.host()) else { return };
            match g.current_button() {
                gdk::BUTTON_MIDDLE => {
                    g.set_state(gtk::EventSequenceState::Claimed);
                    h.close_tab(id);
                }
                gdk::BUTTON_PRIMARY => h.select_tab(id),
                _ => {}
            }
        });
        b.root.add_controller(press);
        // live reorder: the tab moves to the slot under the pointer
        let drag = gtk::GestureDrag::new();
        let start_x = Rc::new(Cell::new(None::<f64>));
        let (w, sx, broot) = (Rc::downgrade(self), start_x.clone(), b.root.downgrade());
        drag.connect_drag_begin(move |_, x, y| {
            let (Some(s), Some(br)) = (w.upgrade(), broot.upgrade()) else { return };
            sx.set(br.compute_point(&s.row, &gtk::graphene::Point::new(x as f32, y as f32)).map(|p| p.x() as f64));
        });
        let w = Rc::downgrade(self);
        drag.connect_drag_update(move |_, dx, _| {
            let Some(s) = w.upgrade() else { return };
            let n = s.buttons.borrow().len();
            let width = s.row.width() as f64 / n.max(1) as f64;
            let Some(x0) = start_x.get().filter(|_| dx.abs() >= 4.0 && width > 0.0) else { return };
            if let Some(h) = s.host() {
                h.move_tab(id, (((x0 + dx).max(0.0) / width) as usize).min(n - 1));
            }
        });
        b.root.add_controller(drag);
        self.drop_target(&b);
        // (the length read first: borrowed inside borrow_mut()'s call it
        // panicked -- Files and Videos closed as they opened, Vini)
        let at = index.min(self.buttons.borrow().len());
        self.buttons.borrow_mut().insert(at, b.clone());
        self.retitle(id, title);
        self.relayout();
    }

    // files dropped on a tab go to its folder; hovering brings it to the front
    fn drop_target(self: &Rc<Self>, b: &Rc<TabButton>) {
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE);
        let (w, bw) = (Rc::downgrade(self), Rc::downgrade(b));
        tgt.connect_enter(move |_, _, _| {
            let (Some(s), Some(b)) = (w.upgrade(), bw.upgrade()) else { return gdk::DragAction::empty() };
            b.root.add_css_class("drop-target");
            b.cancel_spring();
            let (w2, id) = (Rc::downgrade(&s), b.id);
            let bw2 = Rc::downgrade(&b);
            let src = glib::timeout_add_local_once(std::time::Duration::from_millis(SPRING_MS), move || {
                if let Some(b) = bw2.upgrade() {
                    b.spring.replace(None);
                }
                if let Some(h) = w2.upgrade().and_then(|s| s.host()) {
                    h.select_tab(id);
                }
            });
            b.spring.replace(Some(src));
            gdk::DragAction::MOVE
        });
        let bw = Rc::downgrade(b);
        tgt.connect_leave(move |_| {
            if let Some(b) = bw.upgrade() {
                b.root.remove_css_class("drop-target");
                b.cancel_spring();
            }
        });
        let (w, bw) = (Rc::downgrade(self), Rc::downgrade(b));
        tgt.connect_drop(move |t, v, _, _| {
            let (Some(s), Some(b)) = (w.upgrade(), bw.upgrade()) else { return false };
            b.root.remove_css_class("drop-target");
            b.cancel_spring();
            let (Some(h), Ok(list)) = (s.host(), v.get::<gdk::FileList>()) else { return false };
            let Some(uri) = h.tab_uri(b.id) else { return false };
            let copy = t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK);
            h.drop_files(list.files(), gio::File::for_uri(&uri), copy)
        });
        b.root.add_controller(tgt);
    }

    pub fn remove(&self, id: u64) {
        let gone = {
            let mut bs = self.buttons.borrow_mut();
            let i = bs.iter().position(|b| b.id == id);
            i.map(|i| bs.remove(i))
        };
        if let Some(b) = gone {
            self.row.remove(&b.root);
            b.cancel_spring();
        }
        self.relayout();
    }

    pub fn move_to(&self, id: u64, index: usize) {
        {
            let mut bs = self.buttons.borrow_mut();
            let Some(i) = bs.iter().position(|b| b.id == id) else { return };
            let b = bs.remove(i);
            let n = bs.len();
            bs.insert(index.min(n), b);
        }
        self.relayout();
    }

    fn relayout(&self) {
        let mut prev: Option<gtk::Widget> = None;
        for b in self.buttons.borrow().iter() {
            if b.root.parent().is_none() {
                self.row.append(&b.root);
            }
            self.row.reorder_child_after(&b.root, prev.as_ref());
            prev = Some(b.root.clone().upcast());
        }
        self.show();
    }

    fn show(&self) {
        self.root.set_reveal_child(sonata_ui::window::tab_bar_shown(self.buttons.borrow().len()));
    }

    pub fn set_active(&self, id: u64) {
        for b in self.buttons.borrow().iter() {
            if b.id == id {
                b.root.add_css_class("active");
            } else {
                b.root.remove_css_class("active");
            }
        }
    }

    pub fn retitle(&self, id: u64, title: &str) {
        if let Some(b) = self.buttons.borrow().iter().find(|b| b.id == id) {
            b.label.set_label(title);
            b.root.set_tooltip_text(Some(title));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: Files and Videos closed as they opened ("RefCell already
    /// mutably borrowed" in TabStrip::add: the tab list was read while it
    /// was being changed).
    #[test]
    fn test_files_and_videos_closed_as_they_opened() {
        if !crate::gtk_here() {
            return; // no display, or GTK is another test thread's
        }
        let strip = TabStrip::new();
        strip.add(1, "Home", 0);
        strip.add(2, "Documents", 5);
        assert_eq!(strip.buttons.borrow().iter().map(|b| b.id).collect::<Vec<_>>(), [1, 2]);
    }
}
