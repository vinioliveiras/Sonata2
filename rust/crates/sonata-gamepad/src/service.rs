//! Game controllers drive the desktop (gamepad/service.py; like Steam's
//! desktop mode), for any controller Linux knows (Xbox, PlayStation,
//! 8BitDo...). Runs in the menu bar process; nothing runs while no
//! controller is connected.
//!
//! ```text
//! Left stick     pointer (LT slower, RT faster)     A / cross     click (hold: drag)
//! Right stick    scroll                             X / triangle  right click
//! D-pad          arrow keys                         B / circle    Esc
//! LB / RB        app switcher (A picks, B closes)   Y / square    Launchpad
//! Start          Search                             View/Select   Overview
//! Guide (Xbox / PS)  Game Bar (also in games; Settings can make it Overview again)
//! LB + RB + L3 + R3 held together: desktop control on / off
//! ```
//!
//! While the Game Bar is open the controller drives it instead (Vini: like
//! the Xbox Game Bar), even with desktop control off and in a game: D-pad /
//! left stick move between its controls, A presses, B or Guide closes, LB /
//! RB jump between the bar and the widgets. The controllers are grabbed
//! while it's open, so the game behind doesn't also get the presses.
//!
//! Off by default. Games keep the controller: it is never grabbed, and the
//! desktop control pauses by itself while a fullscreen window has the
//! focus (gamemode) or while Steam runs (its own desktop mode would double
//! every press). Actions are named ([`BUTTONS`]) so a future SteamOS-style
//! theme can bind the same buttons to its own navigation.
//!
//! Settings: gamepad.json {"enabled", "speed", "scroll", "pause_steam",
//! "guide_gamebar"} (Settings > Game Controllers).

use crate::evdev::{self as e, Event, OnEvent, Pad};
use crate::vpointer::{Pointer, VirtualPointer, BTN_LEFT, BTN_RIGHT};
use gio::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io;
use std::path::PathBuf;
use std::rc::{Rc, Weak};
use std::time::{Duration, Instant};

pub const CONFIG: &str = "gamepad";

pub fn defaults() -> Value {
    json!({"enabled": false, "speed": 1.0, "scroll": 1.0, "pause_steam": true, "guide_gamebar": true})
}

/// stick dead zone
pub const DEAD: f64 = 0.18;
/// while a stick is pushed (~120 Hz)
pub const TICK_MS: u64 = 8;
/// pointer speed at full tilt (speed 1.0)
pub const MAX_PX_S: f64 = 1500.0;
pub const SCROLL_PX_S: f64 = 900.0;
/// Held together: desktop control on / off (Vini: L1 + R1 + L3 + R3 -- no
/// game asks for all four at once, and it works the same on every pad).
pub const TOGGLE_CHORD: [u16; 4] = [e::BTN_TL, e::BTN_TR, e::BTN_THUMBL, e::BTN_THUMBR];
/// The Game Bar with the left stick: a push past this is one step, held it repeats.
pub const NAV_PUSH: f64 = 0.6;
pub const NAV_FIRST_MS: u64 = 380;
pub const NAV_REPEAT_MS: u64 = 140;

/// button -> action (named for other front ends)
pub const BUTTONS: [(u16, &str); 14] = [
    (e::BTN_SOUTH, "primary"),
    (e::BTN_NORTH, "secondary"),
    (e::BTN_EAST, "back"),
    (e::BTN_WEST, "launchpad"),
    (e::BTN_TL, "switch_prev"),
    (e::BTN_TR, "switch_next"),
    (e::BTN_START, "spotlight"),
    (e::BTN_SELECT, "mission"),
    (e::BTN_MODE, "guide"),
    (e::KEY_HOMEPAGE, "guide"),
    (e::BTN_DPAD_UP, "up"),
    (e::BTN_DPAD_DOWN, "down"),
    (e::BTN_DPAD_LEFT, "left"),
    (e::BTN_DPAD_RIGHT, "right"),
];
pub const ARROWS: [(&str, &str); 4] = [("up", "Up"), ("down", "Down"), ("left", "Left"), ("right", "Right")];

pub fn button_action(code: u16) -> Option<&'static str> {
    BUTTONS.iter().find(|(c, _)| *c == code).map(|(_, n)| *n)
}

fn arrow_key(name: &str) -> Option<&'static str> {
    ARROWS.iter().find(|(n, _)| *n == name).map(|(_, k)| *k)
}

/// A d-pad that is a hat: its push as a direction.
fn hat_dir(code: u16, value: i32) -> &'static str {
    match (code == e::ABS_HAT0X, value > 0) {
        (true, false) => "left",
        (true, true) => "right",
        (false, false) => "up",
        (false, true) => "down",
    }
}

/// Routine lines: detailed logs only; errors always.
fn log(text: &str, error: bool) {
    if error || sonata_session::logs::verbose() {
        eprintln!("sonata2-gamepad: {text}");
    }
}

pub fn steam_running() -> bool {
    let Ok(rd) = std::fs::read_dir("/proc") else { return false };
    rd.flatten().filter(|d| d.file_name().to_string_lossy().bytes().all(|b| b.is_ascii_digit())).any(|d| {
        std::fs::read_to_string(d.path().join("comm")).is_ok_and(|c| matches!(c.trim(), "steam" | "steamwebhelper"))
    })
}

/// gamemode's state file ($XDG_RUNTIME_DIR/sonata2-fullscreen, written by
/// the menu bar's fullscreen watcher; sonata-tweaks' gamemode::state()).
fn fullscreen_active() -> bool {
    let dir = std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"));
    std::fs::read(dir.join("sonata2-fullscreen")).map(|b| b.first() == Some(&b'1')).unwrap_or(false)
}

/// Dead zone, then a gentle curve (fine control near the centre).
pub fn curve(v: f64) -> f64 {
    let a = v.abs();
    if a < DEAD {
        return 0.0;
    }
    ((a - DEAD) / (1.0 - DEAD)).powf(2.2) * v.signum()
}

/// What the menu bar gives the controllers to drive: the app switcher and
/// the Game Bar while they are open, and its keys. Implemented by the host
/// (the binary), which holds both.
pub trait Targets {
    /// The app switcher is up (visible and not fading out:
    /// sonata_mission::switcher::Switcher::is_open).
    fn switcher_open(&self) -> bool;
    /// Its highlight moves: 1 the next app, -1 the one before (Switcher::step).
    fn switcher_step(&self, direction: i32);
    /// The app picked comes forward (Switcher::switch).
    fn switcher_switch(&self);
    /// It closes, if it's showing at all (Switcher::close).
    fn switcher_close(&self);
    /// The Game Bar is open (GameBar::is_open).
    fn gamebar_open(&self) -> bool;
    /// A controller press for the open Game Bar (GameBar::pad): "primary",
    /// "back", "guide", "switch_prev", "switch_next", "up", "down",
    /// "left", "right".
    fn gamebar_pad(&self, name: &str);
    /// Press a key (xkb name: Escape, Up, F3...) with its real keycode
    /// (the virtual keyboard: sonata_clipboard::vkeyboard::press(name, &[])).
    /// False: no virtual keyboard; `wtype` is tried then.
    fn key(&self, name: &str) -> bool;
}

/// Everything outside: devices, the virtual mouse, the menu bar's actions
/// (a fake one in tests: never real devices, never the real session).
pub(crate) trait System {
    fn find_gamepads(&self) -> Vec<String>;
    fn find_guide_devices(&self, names: &[String]) -> Vec<String>;
    fn open(&self, path: &str, on_event: OnEvent) -> io::Result<Rc<dyn Pad>>;
    fn steam_running(&self) -> bool;
    fn fullscreen(&self) -> bool;
    /// One of the menu bar's actions ("gamebar"; "switcher" with "next" / "prev").
    fn activate(&self, action: &str, param: Option<&str>);
    /// `sonata2-rs <component>` (Launchpad, Search).
    fn spawn(&self, component: &str);
    fn notify(&self, text: &str);
    /// The virtual mouse, made (again) when there's none that works.
    fn with_pointer(&self, f: &mut dyn FnMut(&mut dyn Pointer));
    fn close_pointer(&self);
}

/// The real thing: /dev/input, the Wayland virtual pointer, the app's actions.
struct Real {
    app: glib::WeakRef<gtk::Application>,
    vp: RefCell<Option<VirtualPointer>>,
}

impl System for Real {
    fn find_gamepads(&self) -> Vec<String> {
        e::find_gamepads(e::DEV_INPUT)
    }

    fn find_guide_devices(&self, names: &[String]) -> Vec<String> {
        e::find_guide_devices(names, e::DEV_INPUT)
    }

    fn open(&self, path: &str, on_event: OnEvent) -> io::Result<Rc<dyn Pad>> {
        e::Gamepad::open(path, on_event).map(|p| p as Rc<dyn Pad>)
    }

    fn steam_running(&self) -> bool {
        steam_running()
    }

    fn fullscreen(&self) -> bool {
        fullscreen_active()
    }

    fn activate(&self, action: &str, param: Option<&str>) {
        if let Some(app) = self.app.upgrade() {
            app.activate_action(action, param.map(|p| p.to_variant()).as_ref());
        }
    }

    fn spawn(&self, component: &str) {
        let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into());
        let argv = [std::ffi::OsStr::new(&exe), std::ffi::OsStr::new(component)];
        if let Err(err) = gio::Subprocess::newv(&argv, gio::SubprocessFlags::NONE) {
            log(&format!("can't start {component} ({err})"), true);
        }
    }

    fn notify(&self, text: &str) {
        let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
        let hints: HashMap<String, glib::Variant> = HashMap::new();
        let params = ("Sonata", 0u32, "input-gaming", text, "", Vec::<String>::new(), hints, 2500i32).to_variant();
        bus.call(
            Some("org.freedesktop.Notifications"),
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            Some(&params),
            None,
            gio::DBusCallFlags::NONE,
            1000,
            gio::Cancellable::NONE,
            |_| {},
        );
    }

    fn with_pointer(&self, f: &mut dyn FnMut(&mut dyn Pointer)) {
        let mut vp = self.vp.borrow_mut();
        if !vp.as_ref().is_some_and(|v| v.ok()) {
            *vp = Some(VirtualPointer::connect());
        }
        if let Some(v) = vp.as_mut() {
            f(v);
        }
    }

    fn close_pointer(&self) {
        if let Some(mut v) = self.vp.borrow_mut().take() {
            v.close();
        }
    }
}

/// A `wtype` press: the keys the virtual keyboard doesn't have.
fn wtype(name: &str) {
    if glib::find_program_in_path("wtype").is_none() {
        return;
    }
    let argv = [std::ffi::OsStr::new("wtype"), std::ffi::OsStr::new("-k"), std::ffi::OsStr::new(name)];
    let _ = gio::Subprocess::newv(&argv, gio::SubprocessFlags::STDOUT_SILENCE | gio::SubprocessFlags::STDERR_SILENCE);
}

fn cancel(src: &RefCell<Option<glib::SourceId>>) {
    if let Some(id) = src.take() {
        id.remove();
    }
}

/// The controllers, all of them; one in the menu bar.
pub struct Gamepads {
    me: Weak<Gamepads>,
    sys: Rc<dyn System>,
    targets: Rc<dyn Targets>,
    cfg: RefCell<Map<String, Value>>,
    pads: RefCell<BTreeMap<String, Rc<dyn Pad>>>,
    /// (path, code) -> -1..1 / 0..1
    axes: RefCell<HashMap<(String, u16), f64>>,
    grabbed: Cell<bool>,
    /// the left stick's direction in the Game Bar
    nav_dir: Cell<Option<&'static str>>,
    nav_src: RefCell<Option<glib::SourceId>>,
    tick_src: RefCell<Option<glib::SourceId>>,
    acc: Cell<(f64, f64)>,
    last: Cell<Instant>,
    /// buttons held now (the on / off chord)
    down: RefCell<BTreeSet<u16>>,
    /// fired for this hold: once until let go
    chord_done: Cell<bool>,
    steam: Cell<bool>,
    steam_src: RefCell<Option<glib::SourceId>>,
    scan_src: RefCell<Option<glib::SourceId>>,
    /// virtual mouse buttons we hold down (drag)
    held: RefCell<BTreeSet<u32>>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
}

impl Gamepads {
    /// The menu bar's controllers (topbar.py: `Gamepads(app, switcher,
    /// gamebar)`): listens to /dev/input and Settings from now on; call
    /// [`Gamepads::stop`] on the app's shutdown.
    pub fn start(app: &gtk::Application, targets: Rc<dyn Targets>) -> Rc<Gamepads> {
        let sys = Rc::new(Real { app: app.downgrade(), vp: RefCell::new(None) });
        let g = Gamepads::with(sys, targets);
        let mut mons = Vec::new();
        let w = g.me.clone();
        mons.extend(sonata_config::watch(CONFIG, move || {
            if let Some(g) = w.upgrade() {
                g.config_changed();
            }
        }));
        // one plug creates several nodes (eventN, jsN, hidraw...): scan_soon waits for them to settle
        if let Ok(mon) = gio::File::for_path(e::DEV_INPUT).monitor_directory(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
            let w = g.me.clone();
            mon.connect_changed(move |_, _, _, _| {
                if let Some(g) = w.upgrade() {
                    g.scan_soon();
                }
            });
            mons.push(mon);
        }
        g.monitors.replace(mons);
        g
    }

    pub(crate) fn with(sys: Rc<dyn System>, targets: Rc<dyn Targets>) -> Rc<Gamepads> {
        let g = Rc::new_cyclic(|me| Gamepads {
            me: me.clone(),
            sys,
            targets,
            cfg: RefCell::new(sonata_config::load(CONFIG, &defaults())),
            pads: RefCell::default(),
            axes: RefCell::default(),
            grabbed: Cell::new(false),
            nav_dir: Cell::new(None),
            nav_src: RefCell::new(None),
            tick_src: RefCell::new(None),
            acc: Cell::new((0.0, 0.0)),
            last: Cell::new(Instant::now()),
            down: RefCell::default(),
            chord_done: Cell::new(false),
            steam: Cell::new(false),
            steam_src: RefCell::new(None),
            scan_src: RefCell::new(None),
            held: RefCell::default(),
            monitors: RefCell::default(),
        });
        g.scan();
        g
    }

    fn flag(&self, key: &str, default: bool) -> bool {
        self.cfg.borrow().get(key).and_then(Value::as_bool).unwrap_or(default)
    }

    fn number(&self, key: &str) -> f64 {
        self.cfg.borrow().get(key).and_then(Value::as_f64).unwrap_or(1.0)
    }

    // -- devices ----------------------------------------------------------------------------------
    /// One plug creates several nodes (eventN, jsN, hidraw...): one scan once they settle.
    pub(crate) fn scan_soon(&self) {
        cancel(&self.scan_src);
        let w = self.me.clone();
        let id = glib::timeout_add_local(Duration::from_millis(600), move || {
            if let Some(g) = w.upgrade() {
                g.scan_src.take(); // (this one ends here)
                g.scan();
            }
            glib::ControlFlow::Break
        });
        self.scan_src.replace(Some(id));
    }

    fn on_event(&self) -> OnEvent {
        let w = self.me.clone();
        Rc::new(move |path: &str, ev: Option<Event>| {
            if let Some(g) = w.upgrade() {
                g.event(path, ev);
            }
        })
    }

    /// Listened to even while off: the Guide button opens the Game Bar.
    pub(crate) fn scan(&self) {
        for path in self.sys.find_gamepads() {
            if self.pads.borrow().contains_key(&path) {
                continue;
            }
            match self.sys.open(&path, self.on_event()) {
                Ok(pad) => {
                    let name = pad.name();
                    log(if name.is_empty() { &path } else { &name }, false);
                    if self.grabbed.get() {
                        pad.grab(true); // plugged in while the Game Bar is open
                    }
                    self.pads.borrow_mut().insert(path, pad);
                }
                // not readable: logind gives controllers to the user
                Err(err) => log(&format!("can't read {path} ({err})"), true),
            }
        }
        let names: Vec<String> = self.pads.borrow().values().map(|p| p.name()).filter(|n| !n.is_empty()).collect();
        for path in self.sys.find_guide_devices(&names) {
            // the Xbox button over Bluetooth
            if self.pads.borrow().contains_key(&path) {
                continue;
            }
            match self.sys.open(&path, self.on_event()) {
                Ok(pad) => {
                    log(&format!("{} (Xbox / home button)", pad.name()), false);
                    self.pads.borrow_mut().insert(path, pad);
                }
                Err(err) => log(&format!("can't read {path} ({err})"), true),
            }
        }
        if !self.pads.borrow().is_empty() && self.steam_src.borrow().is_none() {
            self.steam.set(self.sys.steam_running());
            let w = self.me.clone();
            let id = glib::timeout_add_seconds_local(5, move || {
                let Some(g) = w.upgrade() else { return glib::ControlFlow::Break };
                if g.pads.borrow().is_empty() {
                    g.steam_src.take();
                    return glib::ControlFlow::Break;
                }
                g.steam.set(g.sys.steam_running());
                glib::ControlFlow::Continue
            });
            self.steam_src.replace(Some(id));
        }
    }

    fn config_changed(&self) {
        self.cfg.replace(sonata_config::load(CONFIG, &defaults()));
        self.scan();
    }

    // -- state ------------------------------------------------------------------------------------
    pub fn paused(&self) -> bool {
        !self.flag("enabled", false) || (self.steam.get() && self.flag("pause_steam", true)) || self.sys.fullscreen()
    }

    fn norm(&self, path: &str, code: u16, value: i32) -> f64 {
        let pad = self.pads.borrow().get(path).cloned();
        match pad {
            Some(p) => p.norm(code, value),
            None => e::norm_in(None, code, value as f64),
        }
    }

    // -- events -----------------------------------------------------------------------------------
    pub(crate) fn event(&self, path: &str, ev: Option<Event>) {
        let Some((typ, code, value)) = ev else {
            // unplugged
            self.pads.borrow_mut().remove(path);
            self.axes.borrow_mut().retain(|(p, _), _| p != path);
            self.release_held(); // unplugged mid-drag: never a stuck button
            return;
        };
        if self.targets.gamebar_open() {
            // the Game Bar has the controller
            if typ == e::EV_KEY && value != 2 {
                self.chord(code, value != 0);
            }
            self.to_bar(path, typ, code, value);
            return;
        }
        if typ == e::EV_ABS {
            if code == e::ABS_HAT0X || code == e::ABS_HAT0Y {
                // d-pad as a hat: arrows
                if value != 0 && !self.paused() {
                    self.action(hat_dir(code, value), true);
                }
                return;
            }
            let v = self.norm(path, code, value);
            self.axes.borrow_mut().insert((path.to_string(), code), v);
            self.tick_soon();
            return;
        }
        if value == 2 {
            return; // key repeat
        }
        let pressed = value != 0;
        self.chord(code, pressed);
        let Some(name) = button_action(code) else { return };
        if name == "guide" {
            if pressed && self.flag("guide_gamebar", true) {
                // Vini: the Xbox / PS button opens the Game Bar -- in games
                // too, with desktop control off (that's when it's needed)
                self.sys.activate("gamebar", None);
            } else if pressed && !self.paused() {
                self.action("mission", true);
            }
            return;
        }
        if !self.paused() {
            self.action(name, pressed);
        } else if !pressed && (name == "primary" || name == "secondary") {
            self.release_held(); // paused mid-drag: still let go
        }
    }

    fn release_held(&self) {
        let held: Vec<u32> = self.held.borrow().iter().copied().collect();
        for btn in held {
            self.button(btn, false);
        }
    }

    fn button(&self, btn: u32, pressed: bool) {
        if pressed {
            self.held.borrow_mut().insert(btn);
        } else if !self.held.borrow_mut().remove(&btn) {
            return; // never pressed by us: nothing to release
        }
        self.sys.with_pointer(&mut |p| p.button(btn, pressed));
    }

    /// LB + RB + L3 + R3 held together: desktop control on / off (works
    /// while off or paused); once per hold.
    fn chord(&self, code: u16, pressed: bool) {
        {
            let mut down = self.down.borrow_mut();
            if pressed {
                down.insert(code);
            } else {
                down.remove(&code);
            }
            if !TOGGLE_CHORD.iter().all(|b| down.contains(b)) {
                self.chord_done.set(false);
                return;
            }
        }
        if self.chord_done.replace(true) {
            return;
        }
        self.targets.switcher_close(); // LB / RB opened it on the way
        self.set_enabled(!self.flag("enabled", false));
    }

    pub fn set_enabled(&self, on: bool) {
        log(if on { "desktop control on" } else { "desktop control off" }, false);
        self.cfg.borrow_mut().insert("enabled".into(), Value::Bool(on));
        let cfg = self.cfg.borrow().clone();
        if let Err(err) = sonata_config::save(CONFIG, &cfg) {
            log(&format!("can't save the settings ({err})"), true);
        }
        if !on {
            self.axes.borrow_mut().clear();
            self.release_held();
        }
        self.sys.notify(&if on { tr!("Controller: desktop control on") } else { tr!("Controller: desktop control off") });
    }

    // -- the Game Bar -----------------------------------------------------------------------------
    /// Every controller to us alone (the Game Bar is open) or back to all.
    /// The Game Bar calls it as it opens and closes (its host's
    /// grab_gamepads).
    pub fn grab(&self, on: bool) {
        if on == self.grabbed.get() {
            return;
        }
        self.grabbed.set(on);
        let pads: Vec<Rc<dyn Pad>> = self.pads.borrow().values().cloned().collect();
        for pad in pads {
            if !pad.grab(on) {
                log(&format!("can't {} {}", if on { "grab" } else { "release" }, pad.name()), false);
            }
        }
        if !on {
            self.nav(None);
            self.axes.borrow_mut().clear(); // stale tilts never move the pointer after
        }
    }

    fn to_bar(&self, path: &str, typ: u16, code: u16, value: i32) {
        if typ == e::EV_ABS {
            if code == e::ABS_HAT0X || code == e::ABS_HAT0Y {
                if value != 0 {
                    self.targets.gamebar_pad(hat_dir(code, value));
                }
            } else if code == e::ABS_X || code == e::ABS_Y {
                let v = self.norm(path, code, value);
                self.axes.borrow_mut().insert((path.to_string(), code), v);
                self.nav(self.stick_dir());
            }
            return;
        }
        if value != 1 {
            return; // presses only (no repeats, no releases)
        }
        if let Some(name @ ("primary" | "back" | "guide" | "switch_prev" | "switch_next" | "up" | "down" | "left" | "right")) = button_action(code) {
            self.targets.gamebar_pad(name);
        }
    }

    fn stick_dir(&self) -> Option<&'static str> {
        let (x, y) = (self.axis(e::ABS_X), self.axis(e::ABS_Y));
        if x.abs().max(y.abs()) < NAV_PUSH {
            return None;
        }
        Some(if x.abs() > y.abs() {
            if x > 0.0 { "right" } else { "left" }
        } else if y > 0.0 {
            "down"
        } else {
            "up"
        })
    }

    /// One step per push of the left stick; held, it repeats.
    fn nav(&self, direction: Option<&'static str>) {
        if direction == self.nav_dir.get() {
            return;
        }
        self.nav_dir.set(direction);
        cancel(&self.nav_src);
        if direction.is_none() {
            return;
        }
        self.nav_step();
        self.nav_after(NAV_FIRST_MS);
    }

    fn nav_after(&self, ms: u64) {
        if self.nav_dir.get().is_none() {
            return; // the step found the bar closed
        }
        let w = self.me.clone();
        let id = glib::timeout_add_local(Duration::from_millis(ms), move || {
            if let Some(g) = w.upgrade() {
                g.nav_src.take(); // (this one ends here)
                g.nav_step();
                g.nav_after(NAV_REPEAT_MS);
            }
            glib::ControlFlow::Break
        });
        self.nav_src.replace(Some(id));
    }

    fn nav_step(&self) {
        match self.nav_dir.get() {
            Some(dir) if self.targets.gamebar_open() => self.targets.gamebar_pad(dir),
            _ => self.nav(None),
        }
    }

    // -- actions ----------------------------------------------------------------------------------
    fn key(&self, name: &str) {
        if !self.targets.key(name) {
            wtype(name);
        }
    }

    pub(crate) fn action(&self, name: &str, pressed: bool) {
        let switching = self.targets.switcher_open();
        match name {
            "primary" => {
                if switching {
                    if pressed {
                        self.targets.switcher_switch();
                    } else {
                        self.button(BTN_LEFT, false); // pressed before the switcher opened
                    }
                } else {
                    self.button(BTN_LEFT, pressed);
                }
            }
            "secondary" => self.button(BTN_RIGHT, pressed),
            _ if !pressed => {}
            "back" => {
                if switching {
                    self.targets.switcher_close();
                } else {
                    self.key("Escape");
                }
            }
            "left" | "right" if switching => self.targets.switcher_step(if name == "left" { -1 } else { 1 }),
            "switch_next" | "switch_prev" => self.sys.activate("switcher", Some(if name == "switch_next" { "next" } else { "prev" })),
            "launchpad" | "spotlight" => self.sys.spawn(name),
            "mission" => self.key("F3"), // Wayfire's scale (Mission Control) binding
            _ => {
                if let Some(k) = arrow_key(name) {
                    self.key(k);
                }
            }
        }
    }

    // -- sticks -----------------------------------------------------------------------------------
    fn tick_soon(&self) {
        if self.tick_src.borrow().is_some() || self.paused() || !self.pushed() {
            return;
        }
        self.last.set(Instant::now());
        let w = self.me.clone();
        let id = glib::timeout_add_local(Duration::from_millis(TICK_MS), move || match w.upgrade() {
            Some(g) => g.tick(),
            None => glib::ControlFlow::Break,
        });
        self.tick_src.replace(Some(id));
    }

    fn pushed(&self) -> bool {
        self.axes.borrow().iter().any(|((_, c), v)| !e::TRIGGERS.contains(c) && v.abs() >= DEAD)
    }

    fn axis(&self, code: u16) -> f64 {
        self.axes.borrow().iter().filter(|((_, c), _)| *c == code).map(|(_, v)| *v).fold(0.0, |best, v| if v.abs() > best.abs() { v } else { best })
    }

    fn tick(&self) -> glib::ControlFlow {
        let now = Instant::now();
        let dt = (now - self.last.replace(now)).as_secs_f64().min(0.05);
        if self.paused() || !self.pushed() {
            self.tick_src.take();
            self.acc.set((0.0, 0.0));
            return glib::ControlFlow::Break;
        }
        let speed = self.number("speed");
        let slow = self.axis(e::ABS_Z).max(self.axis(e::ABS_BRAKE));
        let fast = self.axis(e::ABS_RZ).max(self.axis(e::ABS_GAS));
        let mult = speed * (1.0 - 0.65 * slow) * (1.0 + 1.2 * fast);
        let (mut ax, mut ay) = self.acc.get();
        ax += curve(self.axis(e::ABS_X)) * MAX_PX_S * mult * dt;
        ay += curve(self.axis(e::ABS_Y)) * MAX_PX_S * mult * dt;
        let (mx, my) = (ax.trunc() as i32, ay.trunc() as i32);
        if mx != 0 || my != 0 {
            ax -= mx as f64;
            ay -= my as f64;
            self.sys.with_pointer(&mut |p| p.move_by(mx, my));
        }
        self.acc.set((ax, ay));
        let scroll = SCROLL_PX_S * self.number("scroll") * dt;
        let (sx, sy) = (curve(self.axis(e::ABS_RX)) * scroll, curve(self.axis(e::ABS_RY)) * scroll);
        if sx != 0.0 || sy != 0.0 {
            self.sys.with_pointer(&mut |p| p.scroll(sx, sy));
        }
        glib::ControlFlow::Continue
    }

    /// The app is shutting down: the controllers go back to the games, a
    /// held virtual button is let go, nothing keeps running.
    pub fn stop(&self) {
        self.grab(false);
        for src in [&self.scan_src, &self.tick_src, &self.nav_src, &self.steam_src] {
            cancel(src);
        }
        self.monitors.borrow_mut().clear();
        self.release_held();
        let pads: Vec<Rc<dyn Pad>> = self.pads.borrow().values().cloned().collect();
        for pad in pads {
            pad.close();
        }
        self.pads.borrow_mut().clear();
        self.sys.close_pointer();
    }
}

#[cfg(test)]
#[path = "service_tests.rs"]
mod tests;
