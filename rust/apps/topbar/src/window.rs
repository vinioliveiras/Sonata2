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
    "sonata2-topbar"
}

pub struct TopBarWindow {
    pub win: gtk::ApplicationWindow,
    pub bar: Rc<Bar>,
    layered: bool,
    hidden: Cell<bool>,
    inside: Cell<bool>,
    hide_timer: RefCell<Option<glib::SourceId>>,
    slide: RefCell<Option<glib::SourceId>>,
    /// sonata_ui::menu::on_closed's id: let go with the window (a list
    /// holding every unplugged display's bar was a leak per hotplug)
    menu_closed: Cell<u64>,
}

impl Drop for TopBarWindow {
    fn drop(&mut self) {
        if self.menu_closed.get() != 0 {
            sonata_ui::menu::off_closed(self.menu_closed.get());
        }
    }
}

/// Auto-hide's next move for the pointer `inside` or not: Some(hide) after
/// its delay, None to stay as it is. Any menu or panel open in this
/// process keeps a shown bar down (topbar.py: `not ui.menu.OPEN`), not
/// only the bar's own and the tray's.
pub fn autohide_step(inside: bool, hidden: bool, menu_open: bool) -> Option<bool> {
    if inside && hidden {
        Some(false)
    } else if !inside && !hidden && !menu_open {
        Some(true)
    } else {
        None
    }
}

impl TopBarWindow {
    /// On the screen as the menu bar (a layer surface), not a preview window.
    pub fn layered(&self) -> bool {
        self.layered
    }

    pub fn new(app: &gtk::Application, preview: bool, monitor: Option<&gtk::gdk::Monitor>, services: Services) -> Rc<TopBarWindow> {
        let win = gtk::ApplicationWindow::builder().application(app).title("Menu Bar").css_classes(["sonata-topbar"]).decorated(false).resizable(true).build();
        let bar = Bar::new(services);
        bar.monitor.replace(monitor.cloned()); // Control Center's brightness follows this display
        let h = bar::bar_h();
        win.set_size_request(-1, h);
        let layered = !preview && layer::available();
        let me = Rc::new(TopBarWindow { win, bar, layered, hidden: Cell::new(false), inside: Cell::new(false), hide_timer: RefCell::default(), slide: RefCell::default(), menu_closed: Cell::new(0) });
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
                // moves, not the surface). It used to slide in after a fixed
                // 300 ms: at login over the welcome screen, and after a
                // restart before the new Dock was ready to come with it
                me.bar.root.set_offset(h as f64);
                let w = Rc::downgrade(&me);
                intro_wait(move || {
                    if let Some(me) = w.upgrade() {
                        me.slide_to(0.0, 420);
                    }
                });
            }
            // restart: the old bar slides away first (intro.leave_on_signal)
            leave_on_signal();
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
        // any menu of this process closing: the pointer looked at again
        // (topbar.py: ui.menu.on_closed) -- it may have left meanwhile
        let w = Rc::downgrade(self);
        self.menu_closed.set(sonata_ui::menu::on_closed(move || {
            if let Some(m) = w.upgrade() {
                m.pointer(m.inside.get());
            }
        }));
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
            sonata_ui::cancel(t);
        }
        if !self.bar.cfg_bool("autohide", false) {
            return;
        }
        // (a menu open: it stays) -- the bar's own and the tray's are
        // counted; any other of this process (Control Center's, the
        // capture toolbar's, Mission Control's...) is in menu::any_open
        let menu_open = self.bar.open_menus.get() > 0 || sonata_ui::menu::any_open();
        let Some(hide) = autohide_step(inside, self.hidden.get(), menu_open) else { return };
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
            sonata_ui::cancel(s);
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

// -- the login intro and Restart Sonata (shell/intro.py's wait and leave_on_signal) ------------

/// After a restart: from both ready to sliding in (first frames drawn).
pub const ARRIVE_MS: u64 = 120;
/// Never kept out of sight longer than this at login...
pub const INTRO_TIMEOUT_S: u32 = 12;
/// ... nor waiting for the other one after a restart.
pub const RESTART_TIMEOUT_S: u32 = 6;

/// callback() once the intro is over (right away without one). After a
/// restart: once every restarted component is ready -- the Dock and the
/// menu bar slide in together (Vini); this bar says it is ready
/// (intro.ready_file), which `restart` waits for (5 s without it).
pub fn intro_wait(callback: impl FnOnce() + 'static) {
    use sonata_shell::intro;
    if !intro::pending() {
        if !intro::restarting() {
            glib::idle_add_local_once(callback);
            return;
        }
        let _ = std::fs::write(intro::ready_file("topbar"), "");
        when_gone(intro::restart_mark(), RESTART_TIMEOUT_S, move || {
            glib::timeout_add_local_once(Duration::from_millis(ARRIVE_MS), callback);
        });
        return;
    }
    when_gone(intro::mark(), INTRO_TIMEOUT_S, callback);
}

/// callback() once `path` is deleted, or after `timeout_s` at most.
fn when_gone(path: std::path::PathBuf, timeout_s: u32, callback: impl FnOnce() + 'static) {
    let cb: Rc<RefCell<Option<Box<dyn FnOnce()>>>> = Rc::new(RefCell::new(Some(Box::new(callback))));
    let mon: Rc<RefCell<Option<gtk::gio::FileMonitor>>> = Rc::default();
    let fire = {
        let (cb, mon) = (cb.clone(), mon.clone());
        Rc::new(move || {
            if let Some(m) = mon.borrow_mut().take() {
                m.cancel();
            }
            if let Some(f) = cb.borrow_mut().take() {
                f();
            }
        })
    };
    if let Ok(m) = gtk::gio::File::for_path(&path).monitor_file(gtk::gio::FileMonitorFlags::NONE, gtk::gio::Cancellable::NONE) {
        let f = fire.clone();
        m.connect_changed(move |_, _, _, ev| {
            if ev == gtk::gio::FileMonitorEvent::Deleted {
                f();
            }
        });
        mon.replace(Some(m)); // kept alive until it fires
    }
    let f = fire.clone();
    glib::timeout_add_seconds_local_once(timeout_s, move || f());
    if !path.exists() {
        // removed before the monitor existed: no event will come
        glib::idle_add_local_once(move || fire());
    }
}

thread_local! {
    static LEAVING: Cell<bool> = const { Cell::new(false) };
}

/// `sonata2 restart` asks the menu bar to go (SIGUSR1): every display's bar
/// slides away before the new one comes. Without a handler SIGUSR1 ended
/// the process on the spot -- the bars vanished instead of sliding out.
fn leave_on_signal() {
    if LEAVING.with(|l| l.replace(true)) {
        return; // (one handler for every bar)
    }
    glib::unix_signal_add_local(libc::SIGUSR1, || {
        slide_all_away();
        glib::ControlFlow::Continue
    });
}

/// Every bar of this process slides out of sight (restart).
pub fn slide_all_away() {
    let all: Vec<Rc<TopBarWindow>> = WINDOWS.with(|w| w.borrow().iter().filter_map(std::rc::Weak::upgrade).collect());
    for w in all.iter().filter(|w| w.layered) {
        w.slide(true);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An auto-hidden menu bar slid away from under an open menu that
    /// wasn't its own (Control Center's mixer menu, the capture toolbar's
    /// Options, Mission Control's window menu): only the bar's own menus
    /// and the tray's were counted, and a menu closing elsewhere never had
    /// it look at the pointer again (Python: ui.menu.OPEN / on_closed).
    #[test]
    fn test_menu_bar_hid_under_an_open_menu() {
        assert_eq!(autohide_step(false, false, true), None, "hid under an open menu");
        assert_eq!(autohide_step(false, false, false), Some(true));
        assert_eq!(autohide_step(true, true, true), Some(false), "a hidden bar comes down for the pointer");
        assert_eq!(autohide_step(true, false, false), None);
        let src = include_str!("window.rs");
        let pointer = &src[src.find("    fn pointer(").unwrap()..src.find("    fn slide(").unwrap()];
        assert!(pointer.contains("sonata_ui::menu::any_open()"), "only its own menus counted");
        let init = &src[src.find("    fn init_autohide(").unwrap()..src.find("    fn apply_autohide(").unwrap()];
        assert!(init.contains("sonata_ui::menu::on_closed("), "a menu closing elsewhere never re-checked the pointer");
    }

    fn spin_until(done: impl Fn() -> bool, secs: u64) {
        let ctx = glib::MainContext::default();
        let t = Instant::now();
        while !done() && t.elapsed().as_secs() < secs {
            ctx.iteration(false);
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    /// The menu bar slid in 300 ms after it started, whatever happened:
    /// at login over the welcome screen, and after Restart Sonata alone,
    /// without saying it was ready (restart waited its 5 s for the Dock and
    /// the menu bar to come together).
    #[test]
    fn test_slid_in_over_the_welcome_screen() {
        let _e = crate::testenv::env();
        let ctx = glib::MainContext::default();
        let _g = ctx.acquire().unwrap();
        use sonata_shell::intro;
        std::fs::create_dir_all(intro::mark().parent().unwrap()).unwrap();
        // no intro: at once
        let done = Rc::new(Cell::new(false));
        let d = done.clone();
        intro_wait(move || d.set(true));
        spin_until(|| done.get(), 2);
        assert!(done.get());
        // login: only once the welcome screen has gone (its marker deleted)
        std::fs::write(intro::mark(), "").unwrap();
        let done = Rc::new(Cell::new(false));
        let d = done.clone();
        intro_wait(move || d.set(true));
        spin_until(|| done.get(), 1);
        assert!(!done.get(), "slid in while the welcome screen was up");
        std::fs::remove_file(intro::mark()).unwrap();
        spin_until(|| done.get(), 3);
        assert!(done.get());
        // restart: ready said at once, in once the restart mark goes
        std::fs::write(intro::restart_mark(), "restarting\n").unwrap();
        let done = Rc::new(Cell::new(false));
        let d = done.clone();
        intro_wait(move || d.set(true));
        assert!(intro::ready_file("topbar").exists());
        spin_until(|| done.get(), 1);
        assert!(!done.get());
        std::fs::remove_file(intro::restart_mark()).unwrap();
        spin_until(|| done.get(), 3);
        assert!(done.get());
    }
}
