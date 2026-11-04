//! The menu bar of one display (topbar.py's TopBarWindow): a layer-shell
//! surface along the top edge with its exclusive zone, auto-hide (Settings
//! > Menu Bar, the Dock's way), the slide-in at login; or, with --preview,
//! a normal window with the bar over the wallpaper (screenshots).
//!
//! On trial (sonata_config::side_by_side()) the Python menu bar keeps the
//! top of the screen: the Rust bar is a surface of its own
//! ("sonata2rs-topbar"), stacked under the Python one by the compositor
//! (layer-shell exclusive zones add up), so the two never overlap and
//! windows simply start lower while it runs.

use crate::bar::{self, Bar};
use crate::seams::Services;
use gtk::{glib, prelude::*};
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use sonata_shell::layer;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::{Duration, Instant};

/// Auto-hide slide (the Dock's).
pub const HIDE_MS: u64 = 250;
/// Pointer at the top edge -> the bar comes down.
pub const REVEAL_MS: u64 = 150;
/// A hidden bar keeps this strip at the top edge for the pointer (layer.py's EDGE_TRIGGER).
pub const EDGE_TRIGGER: f64 = 2.0;

/// The first live bar (the main display's): Control Center's and the
/// OSD's "poll now".
pub fn first_bar() -> Option<Rc<Bar>> {
    bar::bars().into_iter().next()
}

pub fn namespace() -> &'static str {
    if sonata_config::side_by_side() { "sonata2rs-topbar" } else { "sonata2-topbar" }
}

pub struct TopBarWindow {
    pub win: gtk::ApplicationWindow,
    pub bar: Rc<Bar>,
    layered: bool,
    hidden: Cell<bool>,
    inside: Cell<bool>,
    hide_timer: RefCell<Option<glib::SourceId>>,
    slide: RefCell<Option<glib::SourceId>>,
}

impl TopBarWindow {
    pub fn new(app: &gtk::Application, preview: bool, monitor: Option<&gtk::gdk::Monitor>, services: Services) -> Rc<TopBarWindow> {
        let win = gtk::ApplicationWindow::builder().application(app).title("Menu Bar").css_classes(["sonata-topbar"]).decorated(false).resizable(true).build();
        let bar = Bar::new(services);
        bar.monitor.replace(monitor.cloned()); // Control Center's brightness follows this display
        let h = bar::bar_h();
        win.set_size_request(-1, h);
        let layered = !preview && layer::available();
        let me = Rc::new(TopBarWindow { win, bar, layered, hidden: Cell::new(false), inside: Cell::new(false), hide_timer: RefCell::default(), slide: RefCell::default() });
        remember(&me);
        if preview {
            me.preview();
            return me;
        }
        me.win.set_child(Some(&me.bar.root));
        if layered {
            let w = &me.win;
            w.init_layer_shell();
            w.set_namespace(Some(namespace()));
            if let Some(m) = monitor {
                w.set_monitor(Some(m));
            }
            w.set_layer(Layer::Top);
            for e in [Edge::Top, Edge::Left, Edge::Right] {
                w.set_anchor(e, true);
            }
            w.set_exclusive_zone(h);
            w.set_keyboard_mode(KeyboardMode::OnDemand);
            me.init_autohide();
            if !me.hidden.get() && sonata_shell::intro::entering() {
                // login / restart: slides down once ready (the bar's drawing
                // moves, not the surface)
                me.bar.root.set_offset(h as f64);
                let w = Rc::downgrade(&me);
                glib::timeout_add_local_once(Duration::from_millis(300), move || {
                    if let Some(me) = w.upgrade() {
                        me.slide_to(0.0, 420);
                    }
                });
            }
        }
        me
    }

    pub fn present(&self) {
        self.win.present();
    }

    pub fn set_monitor(&self, m: &gtk::gdk::Monitor) {
        if self.layered {
            self.win.set_visible(false);
            self.win.set_monitor(Some(m));
            self.bar.monitor.replace(Some(m.clone()));
            self.win.set_visible(true);
        }
    }

    // -- auto-hide (Settings > Menu Bar), the Dock's way ---------------------------------------
    fn init_autohide(self: &Rc<Self>) {
        let motion = gtk::EventControllerMotion::new();
        let w = Rc::downgrade(self);
        motion.connect_enter(move |_, _, _| w.upgrade().iter().for_each(|m| m.pointer(true)));
        let w = Rc::downgrade(self);
        motion.connect_leave(move |_| w.upgrade().iter().for_each(|m| m.pointer(false)));
        self.win.add_controller(motion);
        let w = Rc::downgrade(self);
        self.bar.on_menu_closed.replace(Some(Rc::new(move || {
            if let Some(m) = w.upgrade() {
                m.pointer(m.inside.get());
            }
        })));
        let w = Rc::downgrade(self);
        self.bar.on_autohide.replace(Some(Rc::new(move || w.upgrade().iter().for_each(|m| m.apply_autohide()))));
        let w = Rc::downgrade(self);
        self.bar.reveal.replace(Some(Rc::new(move || {
            if let Some(m) = w.upgrade().filter(|m| m.hidden.get()) {
                m.slide(false);
            }
        })));
        if self.bar.cfg_bool("autohide", false) {
            self.hidden.set(true);
            self.bar.root.set_offset(bar::bar_h() as f64);
            self.win.set_exclusive_zone(0);
        }
        let w = Rc::downgrade(self);
        self.win.connect_realize(move |_| w.upgrade().iter().for_each(|m| m.update_input()));
        let w = Rc::downgrade(self);
        self.win.connect_default_width_notify(move |_| w.upgrade().iter().for_each(|m| m.update_input()));
    }

    /// topbar.json changed: windows get the top of the screen while the bar hides.
    fn apply_autohide(&self) {
        let on = self.bar.cfg_bool("autohide", false);
        self.win.set_exclusive_zone(if on { 0 } else { bar::bar_h() });
        if !on && self.hidden.get() {
            self.slide(false);
        } else {
            self.pointer(self.inside.get());
        }
    }

    fn pointer(&self, inside: bool) {
        self.inside.set(inside);
        if let Some(t) = self.hide_timer.take() {
            t.remove();
        }
        if !self.bar.cfg_bool("autohide", false) {
            return;
        }
        let hide = if inside && self.hidden.get() {
            false
        } else if !inside && !self.hidden.get() && self.bar.open_menus.get() == 0 {
            true // (a menu open: it stays)
        } else {
            return;
        };
        let me = first_window_of(&self.bar);
        let delay = if hide { 400 } else { REVEAL_MS };
        self.hide_timer.replace(Some(glib::timeout_add_local_once(Duration::from_millis(delay), move || {
            if let Some(m) = me.as_ref().and_then(std::rc::Weak::upgrade) {
                m.hide_timer.replace(None);
                m.slide(hide);
            }
        })));
    }

    fn slide(&self, hide: bool) {
        self.hidden.set(hide);
        self.slide_to(if hide { bar::bar_h() as f64 } else { 0.0 }, HIDE_MS);
        self.update_input();
    }

    /// The bar's drawing eased to `target` (a cubic ease-out).
    fn slide_to(&self, target: f64, ms: u64) {
        if let Some(s) = self.slide.take() {
            s.remove();
        }
        let (root, start, t0) = (self.bar.root.clone(), self.bar.root.offset(), Instant::now());
        let ms = sonata_ui::tokens::ms(ms as f64).max(1) as f64;
        let me = first_window_of(&self.bar);
        self.slide.replace(Some(glib::timeout_add_local(Duration::from_millis(16), move || {
            let t = (t0.elapsed().as_secs_f64() * 1000.0 / ms).min(1.0);
            let e = 1.0 - (1.0 - t).powi(3);
            root.set_offset(start + (target - start) * e);
            if t < 1.0 {
                return glib::ControlFlow::Continue;
            }
            if let Some(m) = me.as_ref().and_then(std::rc::Weak::upgrade) {
                m.slide.replace(None);
            }
            glib::ControlFlow::Break
        })));
    }

    /// Hidden: only a thin strip at the top edge takes the pointer; the
    /// clicks under it go to the windows.
    fn update_input(&self) {
        if !self.layered || self.win.surface().is_none() {
            return;
        }
        let w = if self.win.width() > 0 { self.win.width() as f64 } else { 10000.0 };
        layer::set_input_region(self.win.upcast_ref(), &[(0.0, 0.0, w, if self.hidden.get() { EDGE_TRIGGER } else { bar::bar_h() as f64 })]);
    }

    /// Bar over a sample wallpaper in a normal window (screenshots).
    fn preview(self: &Rc<Self>) {
        let b = self.bar.clone();
        glib::timeout_add_local_once(Duration::from_millis(300), move || {
            b.sound_state(Some((70, false)));
            b.battery_state(&(Some(64), "Discharging".into(), false, None));
            b.wifi_state(&(true, true, ("Home".into(), 80, false)));
            b.bt.set_visible(true);
        });
        let (w, h) = std::env::var("PREVIEW_SIZE").ok().and_then(|s| s.split_once('x').and_then(|(a, b)| Some((a.parse().ok()?, b.parse().ok()?)))).unwrap_or((1280, 400));
        self.win.set_default_size(w, h);
        let over = gtk::Overlay::new();
        let pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Fill).hexpand(true).vexpand(true).build();
        if let Some(path) = sonata_shell::wallpaper::path_of(&sonata_shell::wallpaper::still_uri(sonata_ui::is_dark())) {
            pic.set_filename(Some(&path));
            // stands in for the compositor's blur under the bar (not blurred here)
            self.bar.root.set_backdrop(gtk::gdk::Texture::from_filename(&path).ok());
        }
        over.set_child(Some(&pic));
        self.bar.root.set_valign(gtk::Align::Start);
        over.add_overlay(&self.bar.root);
        self.win.set_child(Some(&over));
    }
}

thread_local! {
    static WINDOWS: RefCell<Vec<std::rc::Weak<TopBarWindow>>> = RefCell::default();
}

fn first_window_of(bar: &Rc<Bar>) -> Option<std::rc::Weak<TopBarWindow>> {
    WINDOWS.with(|w| w.borrow().iter().find(|x| x.upgrade().is_some_and(|x| Rc::ptr_eq(&x.bar, bar))).cloned())
}

pub(crate) fn remember(w: &Rc<TopBarWindow>) {
    WINDOWS.with(|ws| {
        let mut ws = ws.borrow_mut();
        ws.retain(|x| x.upgrade().is_some());
        ws.push(Rc::downgrade(w));
    });
}
