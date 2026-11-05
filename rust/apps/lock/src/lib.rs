//! The lock screen, macOS Big Sur style (shell/lock.py): `sonata2-rs lock`.
//!
//! ext-session-lock (gtk4-session-lock): the compositor keeps every output
//! covered until we unlock, even if this process dies. The login screen's
//! look and motion (loginui): the wallpaper blurred, the user's picture,
//! name and capsule password field rising into the middle of every display
//! (Vini: on all monitors; a display that comes back after sleep or a cable
//! gets them too), Sleep / Restart / Shut Down at the bottom, the date and
//! time at the top. A wrong password shakes the field; while it's checked
//! (PAM, in a thread) the field turns into a spinner, and on success
//! everything fades before the desktop shows.
//!
//! - A video wallpaper plays under the blur, silent (lockvideo).
//! - The screen corners are drawn in its own windows (ext-session-lock
//!   hides the desktop's), when Settings has them.
//! - Switch User (userswitch) when another user can be reached.
//! - The screen goes dark by the compositor's own input idle (idlewatch),
//!   or this process's timer on a compositor without one.

use adw::prelude::*;
use gtk4_session_lock as sl;
use sonata_i18n::tr;
use sonata_shell::idlewatch::IdleWatch;
use sonata_shell::lockvideo::LockVideo;
use sonata_shell::{layer, lockdisplay, loginui, pam, screencorners, throttle, userswitch};
use sonata_ui::progress::Spinner;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

/// the wallpaper's longest side for the (blurred) backdrop
const LOCK_WALLPAPER_PX: i32 = 1280;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.lock"
    } else {
        "io.github.vinioliveiras.sonata2.lock"
    }
}

fn user() -> String {
    glib::user_name().to_string_lossy().into_owned()
}

/// One display's copy of the lock.
struct Part {
    win: gtk::Window,
    column: gtk::Box,
    entry: gtk::PasswordEntry,
    spinner: Spinner,
    slot: gtk::Stack,
    hint: gtk::Label,
    power: gtk::Box,
    blackout: gtk::Box,
}

struct Lock {
    app: adw::Application,
    lock: sl::Instance,
    texture: Option<gtk::gdk::Texture>,
    /// a video wallpaper, one player for every display
    video: Option<Rc<LockVideo>>,
    parts: RefCell<Vec<Part>>,
    throttle: throttle::Throttle,
    /// the field's text is the same on every display: typing on any is typing in all
    syncing: Cell<bool>,
    countdown: RefCell<Option<glib::SourceId>>,
    idle: RefCell<Option<glib::SourceId>>,
    /// the compositor's input idle, while locked (None: the timer above)
    watch: RefCell<Option<IdleWatch>>,
    /// Switch User shows: asked once per lock, the same on every display
    switchable: Cell<Option<bool>>,
    dark_after: Cell<Option<i64>>,
    off: Cell<bool>,
    leaving: Cell<bool>,
    last_xy: Cell<Option<(i64, i64)>>,
}

impl Lock {
    fn new(app: &adw::Application) -> Rc<Self> {
        let me = Rc::new(Self {
            app: app.clone(),
            lock: sl::Instance::new(),
            texture: loginui::wallpaper_texture(LOCK_WALLPAPER_PX), // shown only blurred: no full-size picture
            video: lock_video(), // a video wallpaper plays under the blur
            parts: RefCell::default(),
            throttle: throttle::Throttle::new(throttle::default_path()),
            syncing: Cell::new(false),
            countdown: RefCell::default(),
            idle: RefCell::default(),
            watch: RefCell::default(),
            switchable: Cell::new(None),
            dark_after: Cell::new(None),
            off: Cell::new(false),
            leaving: Cell::new(false),
            last_xy: Cell::new(None),
        });
        let weak = Rc::downgrade(&me);
        me.lock.connect_locked(move |_| {
            if let Some(me) = weak.upgrade() {
                lockdisplay::mark_locked(true); // `sonata2 lock-wait` returns
                usb(true);
                me.dark(true);
            }
        });
        let a = app.clone();
        me.lock.connect_failed(move |_| {
            // another locker is active
            lockdisplay::mark_locked(false);
            a.quit();
        });
        let weak = Rc::downgrade(&me);
        me.lock.connect_unlocked(move |_| {
            if let Some(me) = weak.upgrade() {
                me.dark(false);
                usb(false);
                lockdisplay::mark_locked(false);
                if let Some(v) = me.video.as_ref() {
                    v.close();
                }
                me.app.quit();
            }
        });
        me.lock.lock();
        me.listen_for_unlock();
        let main = layer::main_monitor(); // the first focus on the main display
        for m in layer::monitors() {
            let primary = main.as_ref() == Some(&m);
            me.window(&m, primary);
        }
        if let Some(display) = gtk::gdk::Display::default() {
            let weak = Rc::downgrade(&me);
            display.monitors().connect_items_changed(move |list, pos, _removed, added| {
                let Some(me) = weak.upgrade() else { return };
                for k in 0..added {
                    if let Some(m) = list.item(pos + k).and_downcast::<gtk::gdk::Monitor>() {
                        me.window(&m, false);
                    }
                }
            });
        }
        me.guard_blocked();
        me
    }

    // -- the display goes dark soon after locking (lockdisplay) ---------------------------
    fn dark(self: &Rc<Self>, locked: bool) {
        if locked {
            lockdisplay::locked();
            self.dark_after.set(lockdisplay::dark_seconds()); // None: you chose "never"
            self.idle_watch();
            self.input();
            if lockdisplay::was_dark() && self.dark_after.get().is_some() {
                self.go_dark(); // locked after the display went dark: it stays dark
            }
        } else {
            if let Some(id) = self.idle.take() {
                id.remove();
            }
            self.wake(); // (through the watch, before it closes)
            if let Some(w) = self.watch.take() {
                w.close();
            }
            lockdisplay::unlocked();
        }
    }

    /// Dark and back by the compositor's own input idle (ext-idle-notify):
    /// any key or move wakes the display, whether or not a lock window sees
    /// it (Vini: it stayed black until the lid was closed and opened).
    fn idle_watch(self: &Rc<Self>) {
        let Some(after) = self.dark_after.get() else { return };
        let w = IdleWatch::connect();
        if !w.input_idle() {
            w.close();
            return;
        }
        let (dark, back) = (Rc::downgrade(self), Rc::downgrade(self));
        w.watch(
            after.clamp(1, u32::MAX as i64) as u32,
            Rc::new(move || {
                if let Some(me) = dark.upgrade() {
                    me.go_dark();
                }
            }),
            Rc::new(move || {
                if let Some(me) = back.upgrade() {
                    me.wake();
                }
            }),
        );
        self.watch.replace(Some(w));
    }

    /// As the session darkens them (idlelock.displays_dark): the panel's
    /// backlight on a laptop, plugged-in monitors really off; the panel's
    /// backlight only when there's no idle watch to power them.
    fn displays(&self, on: bool) {
        match self.watch.borrow().as_ref() {
            Some(w) => w.displays_dark(!on),
            None => lockdisplay::dim(!on),
        }
    }

    /// Black and the backlight at zero -- a laptop's panel itself stays
    /// on: powering it off and on again failed on Vini's laptop.
    fn go_dark(&self) {
        if self.off.get() {
            return;
        }
        for p in self.parts.borrow().iter() {
            p.blackout.add_css_class("on");
        }
        lockdisplay::pointer(true); // no pointer in the middle of the dark screen (Vini)
        lockdisplay::lights(false); // the keyboard too, whatever keeps the session awake
        self.displays(false); // the panel's backlight, or the displays really off
        self.off.set(true);
    }

    fn wake(&self) {
        if !self.off.get() {
            return;
        }
        self.off.set(false);
        self.displays(true);
        for p in self.parts.borrow().iter() {
            p.blackout.remove_css_class("on");
        }
        lockdisplay::pointer(false);
        lockdisplay::lights(true);
        lockdisplay::mark_dark(false);
    }

    /// A key or a move on the lock screen: the displays on, the countdown
    /// again (the compositor's idle watch does both when it can).
    fn input(self: &Rc<Self>) {
        self.wake();
        if let Some(id) = self.idle.take() {
            id.remove();
        }
        if self.watch.borrow().is_some() {
            return;
        }
        let Some(after) = self.dark_after.get() else { return };
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_seconds_local_once(after.max(1) as u32, move || {
            if let Some(me) = weak.upgrade() {
                me.idle.take();
                me.go_dark();
            }
        });
        self.idle.replace(Some(id));
    }

    fn watch_input(self: &Rc<Self>, win: &gtk::Window) {
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.input();
            }
            glib::Propagation::Proceed
        });
        win.add_controller(keys);
        let motion = gtk::EventControllerMotion::new();
        let weak = Rc::downgrade(self);
        motion.connect_motion(move |_, x, y| {
            let Some(me) = weak.upgrade() else { return };
            let now = Some((x.round() as i64, y.round() as i64));
            let last = me.last_xy.replace(now);
            if last.is_some() && last != now {
                me.input(); // (a real move, not the surface appearing)
            }
        });
        win.add_controller(motion);
        let click = gtk::GestureClick::new();
        click.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        click.connect_pressed(move |_, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.input();
            }
        });
        win.add_controller(click);
    }

    // -- one display's lock -------------------------------------------------------------------
    /// The backdrop, the clock, and the picture, name, password and power
    /// buttons on every display.
    fn window(self: &Rc<Self>, monitor: &gtk::gdk::Monitor, primary: bool) {
        let win = gtk::Window::builder().application(&self.app).css_classes(["sonata-lock"]).build();
        let over = gtk::Overlay::builder().css_classes(["gr-fade-in"]).build();
        let backdrop = loginui::Backdrop::new(self.texture.clone(), 0.18, 64.0);
        backdrop.set_video(self.video.as_ref().map(|v| &v.cover));
        over.set_child(Some(&backdrop));
        over.add_overlay(&loginui::top_clock(None)); // the menu bar's place, size and format (Vini)
        let part = self.login(&win);
        loginui::lift(&part.column, Some(monitor));
        over.add_overlay(&part.column);
        over.add_overlay(&part.power);
        if screencorners::enabled() {
            // the desktop's corners hide under the lock
            over.add_overlay(&screencorners::corners_overlay(screencorners::RADIUS));
        }
        if self.off.get() {
            part.blackout.add_css_class("on");
        }
        over.add_overlay(&part.blackout);
        win.set_child(Some(&over));
        self.watch_input(&win);
        self.lock.assign_window_to_monitor(&win, monitor);
        win.present();
        let entry = part.entry.clone();
        let first = self.parts.borrow().is_empty();
        self.parts.borrow_mut().push(part);
        if primary || first {
            glib::idle_add_local_once(move || {
                entry.grab_focus();
            });
        }
        // unplugged (or gone for a moment in sleep): its copy goes; it comes back as a new display
        let weak = Rc::downgrade(self);
        let w = win.clone();
        monitor.connect_invalidate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.parts.borrow_mut().retain(|p| p.win != w);
                w.destroy();
            }
        });
    }

    fn login(self: &Rc<Self>, win: &gtk::Window) -> Part {
        let column = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).halign(gtk::Align::Center).valign(gtk::Align::Center).css_classes(["gr-rise"]).build();
        column.append(&loginui::avatar(108, None, None, None));
        column.append(&gtk::Label::builder().label(loginui::display_name().as_str()).css_classes(["lk-name"]).build());
        let entry = loginui::password_field(None);
        if let Some(first) = self.parts.borrow().first() {
            entry.set_text(&first.entry.text());
            entry.set_sensitive(first.entry.is_sensitive());
        }
        let weak = Rc::downgrade(self);
        entry.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.check();
            }
        });
        let weak = Rc::downgrade(self);
        entry.connect_changed(move |src| {
            let Some(me) = weak.upgrade() else { return };
            if me.syncing.replace(true) {
                return;
            }
            let text = src.text();
            for p in me.parts.borrow().iter() {
                if &p.entry != src && p.entry.text() != text {
                    p.entry.set_text(&text);
                    p.entry.set_position(-1);
                }
            }
            me.syncing.set(false);
        });
        let spinner = Spinner::new(false);
        spinner.add_css_class("gr-spinner");
        spinner.set_halign(gtk::Align::Center);
        spinner.set_valign(gtk::Align::Center);
        let slot = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(220).halign(gtk::Align::Center).build();
        slot.add_named(&entry, Some("field"));
        slot.add_named(&spinner, Some("spinner"));
        if let Some(first) = self.parts.borrow().first() {
            slot.set_visible_child_name(first.slot.visible_child_name().as_deref().unwrap_or("field"));
            spinner.set_spinning(first.spinner.spinning());
        }
        column.append(&slot);
        let hint_text = match self.parts.borrow().first() {
            Some(first) => first.hint.label().to_string(),
            None if pam::available() => String::new(),
            None => tr!("PAM is not available: can't check passwords"),
        };
        let hint = gtk::Label::builder().label(hint_text.as_str()).css_classes(["lk-hint"]).build();
        let mut below: Vec<gtk::Widget> = vec![hint.clone().upcast()];
        if self.can_switch() {
            // another user: Switch User (macOS's fast user switching)
            let links = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).build();
            let switch = gtk::Button::builder().label(tr!("Switch User").as_str()).css_classes(["gr-link"]).focusable(false).focus_on_click(false).build();
            let weak = Rc::downgrade(self);
            switch.connect_clicked(move |_| {
                if let Some(me) = weak.upgrade() {
                    me.switch_user();
                }
            });
            links.append(&switch);
            below.insert(0, links.upcast());
        }
        loginui::balanced(&column, &below); // picture, name and field centred as one
        let power = loginui::power_bar(Rc::new(loginui::logind)); // Sleep keeps the lock; logind asks for the rest
        let blackout = gtk::Box::builder().css_classes(["lk-blackout"]).can_target(false).build(); // dark while idle
        Part { win: win.clone(), column, entry, spinner, slot, hint, power, blackout }
    }

    // -- the password ---------------------------------------------------------------------------
    fn check(self: &Rc<Self>) {
        let Some(pw) = self.parts.borrow().first().map(|p| p.entry.text().to_string()) else { return };
        if pw.is_empty() || self.guard_blocked() || self.leaving.get() {
            return;
        }
        for p in self.parts.borrow().iter() {
            p.entry.set_sensitive(false);
            p.spinner.set_spinning(true);
            p.slot.set_visible_child_name("spinner");
        }
        let me = self.clone();
        glib::spawn_future_local(async move {
            let who = user();
            // any error must end the spinner (and keep the lock closed)
            let ok = gio::spawn_blocking(move || pam::authenticate(&who, &pw)).await.unwrap_or(false);
            me.done(ok);
        });
    }

    fn done(self: &Rc<Self>, ok: bool) {
        if ok {
            self.throttle.succeeded(&user());
            self.leave();
            return;
        }
        let parts = self.parts.borrow();
        for p in parts.iter() {
            p.spinner.set_spinning(false);
            p.slot.set_visible_child_name("field");
            loginui::shake(&p.entry);
        }
        if let Some(p) = parts.first() {
            p.entry.grab_focus();
        }
        drop(parts);
        if self.throttle.failed(&user()) > 0 {
            self.count_down();
        }
    }

    /// Fade away, then the desktop.
    fn leave(self: &Rc<Self>) {
        if self.leaving.replace(true) {
            return;
        }
        for p in self.parts.borrow().iter() {
            p.column.add_css_class("gr-leave");
            p.power.add_css_class("gr-leave");
        }
        let lock = self.lock.clone();
        glib::timeout_add_local_once(Duration::from_millis(400), move || lock.unlock());
    }

    /// Wrong passwords make the field wait ("Try again in 4:59"); true while waiting.
    fn guard_blocked(self: &Rc<Self>) -> bool {
        if self.throttle.wait_left(&user()) > 0 {
            self.count_down();
            return true;
        }
        false
    }

    fn count_down(self: &Rc<Self>) {
        for p in self.parts.borrow().iter() {
            p.entry.set_sensitive(false);
        }
        if self.countdown.borrow().is_some() {
            return;
        }
        let tick = {
            let weak = Rc::downgrade(self);
            move || {
                let Some(me) = weak.upgrade() else { return glib::ControlFlow::Break };
                let left = me.throttle.wait_left(&user());
                let parts = me.parts.borrow();
                if left > 0 {
                    let text = throttle::describe(left);
                    for p in parts.iter() {
                        p.hint.set_label(&text);
                    }
                    return glib::ControlFlow::Continue;
                }
                for p in parts.iter() {
                    p.hint.set_label("");
                    p.entry.set_sensitive(true);
                }
                if let Some(p) = parts.first() {
                    p.entry.grab_focus();
                }
                drop(parts);
                me.countdown.take();
                glib::ControlFlow::Break
            }
        };
        let first = tick.clone();
        if first() == glib::ControlFlow::Continue {
            self.countdown.replace(Some(glib::timeout_add_seconds_local(1, tick)));
        }
    }

    // -- Switch User ----------------------------------------------------------------------------
    /// Switch User shows when another user exists and can be reached
    /// (userswitch); asked once per lock, the same on every display.
    fn can_switch(&self) -> bool {
        if let Some(known) = self.switchable.get() {
            return known;
        }
        let known = userswitch::available(&user());
        self.switchable.set(Some(known));
        known
    }

    /// The login screen (or the other user's session) in front; this
    /// session stays locked behind it (off the UI thread: D-Bus, systemd).
    fn switch_user(self: &Rc<Self>) {
        let me = self.clone();
        glib::spawn_future_local(async move {
            let who = user();
            let ok = gio::spawn_blocking(move || userswitch::switch(&who)).await.unwrap_or(false);
            if !ok {
                me.switch_failed();
            }
        });
    }

    fn switch_failed(&self) {
        let text = tr!("Couldn't switch user");
        for p in self.parts.borrow().iter() {
            p.hint.set_label(&text);
        }
    }

    /// logind "Unlock" for this session: the login screen let you back in
    /// (Switch User, your password checked there) -- this lock goes too,
    /// without asking again. Only logind, and processes polkit allows to
    /// unlock sessions, can send it.
    fn listen_for_unlock(self: &Rc<Self>) {
        let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return };
        let (method, arg) = match std::env::var("XDG_SESSION_ID").ok().filter(|s| !s.is_empty()) {
            Some(sid) => ("GetSession", (sid,).to_variant()),
            None => ("GetSessionByPID", (std::process::id(),).to_variant()),
        };
        let Ok(reply) = bus.call_sync(Some("org.freedesktop.login1"), "/org/freedesktop/login1", "org.freedesktop.login1.Manager", method, Some(&arg), Some(glib::VariantTy::new("(o)").expect("a type")), gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE) else {
            return; // no logind: the password here, as before
        };
        let Some(path) = reply.child_value(0).str().map(String::from) else { return };
        let weak = Rc::downgrade(self);
        #[allow(deprecated)]
        bus.signal_subscribe(Some("org.freedesktop.login1"), Some("org.freedesktop.login1.Session"), Some("Unlock"), Some(&path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.leave(); // the same fade as a password typed here
            }
        });
    }
}

/// The video wallpaper's own player for the lock screen (silent, paused
/// while the display is dark), or None when the wallpaper is a picture.
/// SONATA_LOCK_WALLPAPER (previews) picks the wallpaper, as for the picture.
fn lock_video() -> Option<Rc<LockVideo>> {
    let path = match std::env::var("SONATA_LOCK_WALLPAPER").ok().filter(|s| !s.is_empty()) {
        Some(over) => std::path::PathBuf::from(over),
        None => sonata_shell::wallpaper::path_of(&sonata_shell::wallpaper::wallpaper_uri(adw::StyleManager::default().is_dark()))?,
    };
    if !is_video_wallpaper(&path) {
        return None;
    }
    LockVideo::open(&path)
}

/// A video file the lock screen plays under its blur.
pub fn is_video_wallpaper(path: &std::path::Path) -> bool {
    sonata_shell::wallpaper::is_video(&path.to_string_lossy()) && path.is_file()
}

/// New USB devices blocked while locked (USBGuard), when Security &
/// Privacy > USB asks for it; put back on unlock, whatever the setting.
fn usb(locked: bool) {
    if !locked {
        sonata_backend::usbprotect::restore(); // before quitting: never left blocked
        return;
    }
    let sec = sonata_config::load("security", &serde_json::json!({"lock_after": 0, "lock_before_sleep": true, "usb_protection": false}));
    if sec.get("usb_protection").and_then(|v| v.as_bool()).unwrap_or(false) {
        std::thread::spawn(sonata_backend::usbprotect::block);
    }
}

/// Never lock without a way to unlock: the session lock protocol and PAM.
pub fn can_lock(session_lock: bool, pam: bool) -> bool {
    session_lock && pam
}

/// `sonata2-rs lock`: lock the session (once: a second one does nothing).
pub fn run(_args: &[String]) -> glib::ExitCode {
    layer::ensure_preload();
    // the locked folders close with the screen (open again with their password)
    std::thread::spawn(sonata_backend::vault::close_all);
    let app = adw::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Lock>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        if !can_lock(layer::available() && sl::is_supported(), pam::available()) {
            eprintln!("sonata2-rs: can't lock (needs gtk4-layer-shell >= 1.1 session lock and PAM)");
            return;
        }
        sonata_ui::setup();
        loginui::register_css();
        hold.replace(Some(app.hold()));
        state.replace(Some(Lock::new(app)));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    /// Vini: the lock screen was black with a video wallpaper; it plays
    /// one under the blur -- only a video file that is there.
    #[test]
    fn a_video_wallpaper_plays_under_the_blur() {
        let dir = std::env::temp_dir().join(format!("sonata-lock-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let (video, picture) = (dir.join("waves.mp4"), dir.join("hills.jpg"));
        std::fs::write(&video, b"").unwrap();
        std::fs::write(&picture, b"").unwrap();
        assert!(super::is_video_wallpaper(&video));
        assert!(!super::is_video_wallpaper(&picture));
        assert!(!super::is_video_wallpaper(&dir.join("gone.mp4")));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn never_locks_without_a_way_to_unlock() {
        assert!(super::can_lock(true, true));
        assert!(!super::can_lock(true, false));
        assert!(!super::can_lock(false, true));
    }
}
