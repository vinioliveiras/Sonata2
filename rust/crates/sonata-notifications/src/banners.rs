//! Banners: glass cards at the top right, under the menu bar; they slide
//! in, stay 5 s (critical ones until closed), at most three.

use crate::card;
use crate::model::{Note, BANNER_MS, MAX_BANNERS};
use crate::server::Server;
use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use std::cell::RefCell;
use std::rc::{Rc, Weak};

struct Shown {
    id: u32,
    rev: gtk::Revealer,
    /// its timeout; None once it has fired (or for a critical one)
    expiry: Option<glib::SourceId>,
}

pub struct Banners {
    pub win: gtk::Window,
    list: gtk::Box,
    server: Weak<Server>,
    /// oldest first: the one that goes when a fourth comes
    shown: RefCell<Vec<Shown>>,
}

impl Banners {
    pub fn new(app: &gtk::Application, server: &Rc<Server>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Notifications").decorated(false).resizable(true).build();
        win.add_css_class("sonata-banners");
        let list = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).margin_end(2).margin_bottom(10).build();
        win.set_child(Some(&list));
        if sonata_shell::layer::available() {
            win.init_layer_shell();
            win.set_namespace(Some("sonata2-notifications"));
            win.set_layer(Layer::Overlay);
            win.set_anchor(Edge::Top, true);
            win.set_anchor(Edge::Right, true);
            win.set_margin(Edge::Top, 2); // below the menu bar (its exclusive zone)
            win.set_margin(Edge::Right, 8);
            win.set_keyboard_mode(KeyboardMode::None);
        }
        Rc::new(Banners { win, list, server: Rc::downgrade(server), shown: RefCell::default() })
    }

    /// The banners on screen, newest on top.
    pub fn ids(&self) -> Vec<u32> {
        self.shown.borrow().iter().map(|s| s.id).collect()
    }

    pub fn show(self: &Rc<Self>, n: &Note) {
        let Some(server) = self.server.upgrade() else { return };
        self.hide(n.id, false);
        let c = card::card(n, &server);
        let rev = gtk::Revealer::builder().child(&c.root).transition_type(gtk::RevealerTransitionType::SlideLeft).transition_duration(sonata_ui::tokens::ms(250.0)).build();
        self.list.prepend(&rev);
        if !self.win.is_visible() {
            self.win.set_default_size(1, 1); // shrink to the content again
            self.win.present();
        }
        let r = rev.clone();
        glib::idle_add_local_once(move || r.set_reveal_child(true));
        let expiry = (n.urgency != 2 && n.timeout != 0).then(|| {
            let ms = if n.timeout > 0 { n.timeout as u32 } else { BANNER_MS };
            let (w, id) = (Rc::downgrade(self), n.id);
            glib::timeout_add_local_once(std::time::Duration::from_millis(ms as u64), move || {
                if let Some(b) = w.upgrade() {
                    b.expire(id);
                }
            })
        });
        self.shown.borrow_mut().push(Shown { id: n.id, rev, expiry });
        loop {
            let first = {
                let s = self.shown.borrow();
                (s.len() > MAX_BANNERS).then(|| s[0].id)
            };
            match first {
                Some(id) => self.hide(id, true),
                None => break,
            }
        }
    }

    /// Its time is up: it stays in the Notification Center.
    fn expire(self: &Rc<Self>, id: u32) {
        if let Some(s) = self.shown.borrow_mut().iter_mut().find(|s| s.id == id) {
            s.expiry = None; // (fired: not removed again)
        }
        self.hide(id, true);
    }

    pub fn hide(self: &Rc<Self>, id: u32, animate: bool) {
        let entry = {
            let mut shown = self.shown.borrow_mut();
            let Some(i) = shown.iter().position(|s| s.id == id) else { return };
            shown.remove(i)
        };
        if let Some(src) = entry.expiry {
            sonata_ui::cancel(src);
        }
        let rev = entry.rev;
        if animate {
            rev.set_transition_type(gtk::RevealerTransitionType::Crossfade);
            rev.set_reveal_child(false);
        }
        let w = Rc::downgrade(self);
        let gone = move || {
            let Some(b) = w.upgrade() else { return };
            if rev.parent().is_some() {
                b.list.remove(&rev);
            }
            if b.list.first_child().is_none() {
                b.win.set_visible(false);
            }
        };
        if animate {
            glib::timeout_add_local_once(std::time::Duration::from_millis(260), gone);
        } else {
            gone();
        }
    }

    pub fn hide_all(self: &Rc<Self>, animate: bool) {
        for id in self.ids() {
            self.hide(id, animate);
        }
    }
}
