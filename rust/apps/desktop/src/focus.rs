//! When a video wallpaper may play (videowallpaper.py's DesktopFocus and
//! its rules): the desktop in front -- Wayfire's "view-focused" says which
//! surface has the keyboard --, no window on the display (Settings >
//! Wallpaper, on by default), not under Mission Control, not locked, the
//! display not dark, no game full screen. Also the drawing sums (cover,
//! the frame's shortfall, the loop's fade) and the silent player's
//! GStreamer ranks.

use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::rc::Rc;

/// the view types (ipc-rules' "type") that are the desktop, an app, or neither
const DESKTOP_TYPES: [&str; 1] = ["background"];
const APP_TYPES: [&str; 1] = ["toplevel"];
/// Wayfire plugins that cover the desktop while on: Mission Control (scale,
/// Super+Tab -- Vini: the video kept playing under it), the desktops' grid
const COVERING_PLUGINS: [&str; 2] = ["scale", "expo"];

pub const FRAME_FILE: &str = "sonata2-wallpaper-frame.jpg"; // the frame shown when Overview opened (mission.py)
pub const FRAME_PX: i32 = 960; // its longest side: drawn small, under Overview's blur
const RECHECK_MS: u64 = 300; // after a window closes or minimizes: the windows asked again
pub const LOCKED_FILE: &str = "sonata2-lock-display.json"; // lockdisplay._state()
pub const DARK_FILE: &str = "sonata2-idle-dark"; // lockdisplay._dark_marker()
pub const FULLSCREEN_FILE: &str = "sonata2-fullscreen"; // gamemode.STATE
/// Settings > Wallpaper: a display with no window keeps playing while
/// another has windows (Vini); off: plays only while the desktop has the focus
pub const PER_DISPLAY_DEFAULT: bool = true;
/// Settings > Wallpaper: keep playing whatever windows are open (Vini; off by default)
pub const ALWAYS_DEFAULT: bool = false;
const LAYOUT_EVENTS: [&str; 9] = ["view-mapped", "view-geometry-changed", "view-workspace-changed", "wset-workspace-changed", "view-set-output", "output-added", "output-removed", "view-tiled", "view-fullscreen"];
const LAYOUT_SOON_MS: u64 = 150; // windows moving: looked at again once they settle a moment

fn kind(v: &Value) -> &str {
    v.get("type").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("toplevel")
}

fn truthy(v: Option<&Value>, default: bool) -> bool {
    match v {
        None | Some(Value::Null) => default && v.is_none(),
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(_) => true,
    }
}

/// After a "view-focused" event: is the desktop the one in front? (view:
/// the event's view, None when nothing has the keyboard.) Panels and
/// overlays -- the menu bar, the Dock, Spotlight, menus -- keep `before`.
pub fn desktop_has_focus(view: Option<&Value>, before: bool) -> bool {
    let Some(view) = view.filter(|v| v.is_object()) else { return true };
    let k = kind(view);
    if DESKTOP_TYPES.contains(&k) {
        return true;
    }
    if APP_TYPES.contains(&k) {
        return false;
    }
    before
}

/// From window-rules/list-views: no app window is the active one.
pub fn front_is_desktop(views: Option<&Value>) -> bool {
    !views.and_then(Value::as_array).into_iter().flatten().any(|v| {
        v.is_object() && truthy(v.get("activated"), false) && APP_TYPES.contains(&kind(v)) && truthy(v.get("mapped"), true) && !truthy(v.get("minimized"), false)
    })
}

/// {display name: no window on it} -- a window counts when it's mapped,
/// not minimized and on the desktop each display shows now (its geometry,
/// relative to that desktop, overlaps the display).
pub fn free_outputs(outputs: Option<&Value>, views: Option<&Value>) -> HashMap<String, bool> {
    let outs: Vec<&Value> = outputs.and_then(Value::as_array).into_iter().flatten().filter(|o| o.get("name").and_then(Value::as_str).is_some_and(|n| !n.is_empty())).collect();
    let name = |o: &Value| o["name"].as_str().unwrap_or("").to_string();
    let mut free: HashMap<String, bool> = outs.iter().map(|o| (name(o), true)).collect();
    let num = |v: &Value, k: &str| v.get(k).and_then(Value::as_f64).unwrap_or(0.0);
    for v in views.and_then(Value::as_array).into_iter().flatten() {
        if !v.is_object() || !APP_TYPES.contains(&kind(v)) || !truthy(v.get("mapped"), true) || truthy(v.get("minimized"), false) {
            continue;
        }
        let o = outs.iter().find(|o| v.get("output-id").is_some() && o.get("id") == v.get("output-id")).or_else(|| outs.iter().find(|o| o.get("name") == v.get("output-name")));
        let Some(o) = o else { continue };
        let (g, og) = (v.get("geometry").cloned().unwrap_or(json!({})), o.get("geometry").cloned().unwrap_or(json!({})));
        let (w, h) = (num(&og, "width"), num(&og, "height"));
        let (x, y, vw, vh) = (num(&g, "x"), num(&g, "y"), num(&g, "width"), num(&g, "height"));
        if vw > 0.0 && vh > 0.0 && x < w && y < h && x + vw > 0.0 && y + vh > 0.0 {
            free.insert(name(o), false);
        }
    }
    free
}

// -- the drawing ----------------------------------------------------------------------------

pub const FADE_MS: f64 = 700.0; // the end of the video fading into its start

/// (w, h) to draw something of aspect `ratio` (else its size) at so it
/// covers width x height, keeping its shape; nothing known: the box itself.
pub fn cover(ratio: f64, size: (f64, f64), width: f64, height: f64) -> (f64, f64) {
    let ratio = if ratio > 0.0 { ratio } else if size.0 > 0.0 && size.1 > 0.0 { size.0 / size.1 } else { 0.0 };
    if ratio <= 0.0 || width <= 0.0 || height <= 0.0 {
        return (width, height);
    }
    if width / height > ratio {
        (width, width / ratio) // the box is wider: as wide as it, taller than it
    } else {
        (height * ratio, height)
    }
}

/// (x, y) <= 1: how much smaller the decoded frame (iw x ih) is than the
/// video says it is (ratio, video height). GTK's GStreamer picture draws
/// its frame at frame size / video size of what it's asked, from the left:
/// a frame a little narrower (hardware decoders) left a black band down
/// the right of the screen (Vini: "the video wallpaper doesn't fill the
/// right side"). Asked that much larger, the frame lands on the whole box;
/// a frame bigger than the video (padding) is cut by GTK itself.
pub fn frame_shortfall(ratio: f64, vh: f64, iw: f64, ih: f64) -> (f64, f64) {
    if !(ratio > 0.0 && vh > 0.0 && iw > 0.0 && ih > 0.0) {
        return (1.0, 1.0);
    }
    let (fx, fy) = (iw / (ratio * vh), ih / vh); // (the video's own width: its height x its shape)
    // only a small shortfall is that band; anything else isn't this
    let keep = |f: f64| if (0.8..1.0).contains(&f) { f } else { 1.0 };
    (keep(fx), keep(fy))
}

/// The old frame's opacity `ms` into the fade (ease in-out).
pub fn fade_alpha(ms: f64) -> f64 {
    let t = ms / FADE_MS;
    if t < 1.0 { (1.0 - t * t * (3.0 - 2.0 * t)).max(0.0) } else { 0.0 }
}

// -- sound ----------------------------------------------------------------------------------

/// GStreamer reads its ranks once, when the first player starts it; 0 =
/// never picked, so autoaudiosink falls back to a fake sink: nothing
/// reaches the speakers whatever the sound server restores for the stream
/// (muting alone didn't keep it quiet -- Vini: "it still plays sound").
pub const AUDIO_SINKS: [&str; 8] = ["pulsesink", "pipewiresink", "alsasink", "jackaudiosink", "osssink", "oss4sink", "openalsink", "sdlaudiosink"];

/// GStreamer's own ranks for them (gst-inspect-1.0): what a sink turned off
/// before this process saw it gets back. All put back at one rank, the
/// first sink to answer won -- OpenAL, JACK or OSS instead of PulseAudio /
/// PipeWire (Vini: with the sound on the wallpaper went black, the sound
/// playing).
pub fn default_rank(sink: &str) -> u32 {
    match sink {
        "pulsesink" => 266,
        "alsasink" | "jackaudiosink" => 256,
        "osssink" | "openalsink" => 128,
        "oss4sink" => 129,
        _ => 0,
    }
}

/// GST_PLUGIN_FEATURE_RANK with every audio sink off (what was set stays).
pub fn silent_ranks(existing: &str) -> String {
    let mut parts: Vec<String> = existing.split(',').filter(|p| !p.is_empty()).map(String::from).collect();
    let have: HashSet<String> = parts.iter().map(|p| p.split(':').next().unwrap_or("").to_string()).collect();
    parts.extend(AUDIO_SINKS.iter().filter(|s| !have.contains(**s)).map(|s| format!("{s}:0")));
    parts.join(",")
}

/// Settings > Wallpaper: the video's sound (off by default) and its volume
/// 0..1 (wallpaper.json, 25 %).
pub fn sound() -> (bool, f64) {
    let cfg = sonata_config::load("wallpaper", &json!({"video_sound": false, "video_volume": 25}));
    let vol = cfg.get("video_volume").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))).map(|v| (v / 100.0).clamp(0.0, 1.0)).unwrap_or(0.25);
    (truthy(cfg.get("video_sound"), false), vol)
}

/// (per display, always) from wallpaper.json.
pub fn settings() -> (bool, bool) {
    let cfg = sonata_config::load("wallpaper", &json!({"video_per_display": PER_DISPLAY_DEFAULT, "video_always": ALWAYS_DEFAULT}));
    (truthy(cfg.get("video_per_display"), PER_DISPLAY_DEFAULT), truthy(cfg.get("video_always"), ALWAYS_DEFAULT))
}

pub fn runtime_dir() -> PathBuf {
    PathBuf::from(std::env::var("XDG_RUNTIME_DIR").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| "/tmp".into()))
}

// -- DesktopFocus ---------------------------------------------------------------------------

/// Wayfire, or a fake one in the tests.
pub trait Ipc {
    fn available(&self) -> bool;
    fn call(&self, method: &str) -> Option<Value>;
}

pub struct Wayfire;

impl Ipc for Wayfire {
    fn available(&self) -> bool {
        sonata_backend::wfipc::available()
    }
    fn call(&self, method: &str) -> Option<Value> {
        sonata_backend::wfipc::call(method, json!({}))
    }
}

type Timer = Box<dyn Fn(u64, Box<dyn FnOnce()>)>;

/// Whether a video wallpaper may play: the desktop in front, not locked,
/// the display not dark. Listeners are called when that changes.
///
/// Per display (Settings, on by default): a display with no window shows
/// the video playing, one with a window shows it paused (its last frame);
/// the player runs while any display shows it playing. display_listeners
/// are called when which displays are free changes.
pub struct DesktopFocus {
    ipc: Box<dyn Ipc>,
    pub desktop: Cell<bool>,
    /// (plugin, output) on now: COVERING_PLUGINS
    pub covered: RefCell<HashSet<(String, String)>>,
    /// display name -> no window on it (free_outputs)
    pub free: RefCell<HashMap<String, bool>>,
    pub per_display: Cell<bool>,
    pub always: Cell<bool>,
    listeners: RefCell<Vec<(u64, Rc<dyn Fn(bool)>)>>,
    display_listeners: RefCell<Vec<(u64, Rc<dyn Fn()>)>>,
    next_id: Cell<u64>,
    was: Cell<Option<bool>>,
    run_dir: PathBuf,
    read_settings: Box<dyn Fn() -> (bool, bool)>,
    timer: Timer,
    layout_soon: Cell<bool>,
    recheck: Rc<Cell<u64>>,
    keep: RefCell<Vec<Box<dyn std::any::Any>>>,
}

thread_local! {
    static ONE: RefCell<Option<Rc<DesktopFocus>>> = const { RefCell::new(None) };
}

impl DesktopFocus {
    /// The process's one, on Wayfire's events, the lock files and
    /// wallpaper.json.
    pub fn get() -> Rc<DesktopFocus> {
        if let Some(f) = ONE.with(|o| o.borrow().clone()) {
            return f;
        }
        let timer: Timer = Box::new(|ms, f| {
            let f = RefCell::new(Some(f));
            glib::timeout_add_local_once(std::time::Duration::from_millis(ms), move || {
                if let Some(f) = f.take() {
                    f()
                }
            });
        });
        let f = DesktopFocus::new(Box::new(Wayfire), runtime_dir(), Box::new(settings), timer);
        f.follow();
        ONE.with(|o| o.replace(Some(f.clone())));
        f
    }

    pub fn new(ipc: Box<dyn Ipc>, run_dir: PathBuf, read_settings: Box<dyn Fn() -> (bool, bool)>, timer: Timer) -> Rc<DesktopFocus> {
        let (per_display, always) = read_settings();
        let f = Rc::new(DesktopFocus {
            ipc,
            desktop: Cell::new(true),
            covered: RefCell::default(),
            free: RefCell::default(),
            per_display: Cell::new(per_display),
            always: Cell::new(always),
            listeners: RefCell::default(),
            display_listeners: RefCell::default(),
            next_id: Cell::new(1),
            was: Cell::new(None),
            run_dir,
            read_settings,
            timer,
            layout_soon: Cell::new(false),
            recheck: Rc::new(Cell::new(0)),
            keep: RefCell::default(),
        });
        if f.ipc.available() {
            f.desktop.set(front_is_desktop(f.ipc.call("window-rules/list-views").as_ref()));
            f.layout();
        }
        f.was.set(Some(f.active()));
        f
    }

    /// Watch Wayfire's events, the lock / dark / full-screen files and the
    /// Settings switches (the process's own one; tests call on_event).
    fn follow(self: &Rc<Self>) {
        let me = Rc::downgrade(self);
        let mut keep: Vec<Box<dyn std::any::Any>> = Vec::new();
        for m in sonata_config::watch("wallpaper", {
            let me = me.clone();
            move || {
                if let Some(f) = me.upgrade() {
                    f.setting_changed();
                }
            }
        }) {
            keep.push(Box::new(m));
        }
        for name in [LOCKED_FILE, DARK_FILE, FULLSCREEN_FILE] {
            use gio::prelude::*;
            if let Ok(mon) = gio::File::for_path(self.run_dir.join(name)).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
                let me = me.clone();
                mon.connect_changed(move |_, _, _, _| {
                    if let Some(f) = me.upgrade() {
                        f.changed(false);
                    }
                });
                keep.push(Box::new(mon));
            }
        }
        if self.ipc.available() {
            let mut events: Vec<&str> = vec!["view-focused", "plugin-activation-state-changed", "view-unmapped", "view-minimized"];
            events.extend(LAYOUT_EVENTS);
            if let Some(w) = watch_events(&events, move |ev| {
                if let Some(f) = me.upgrade() {
                    f.on_event(ev);
                }
            }) {
                keep.push(Box::new(w));
            }
        }
        self.keep.replace(keep);
    }

    /// Locked, the display gone dark (idle), or a game or anything full
    /// screen in front (Vini: paused on every display then, whatever the
    /// Settings switches say).
    pub fn blocked(&self) -> bool {
        if [LOCKED_FILE, DARK_FILE].iter().any(|n| self.run_dir.join(n).exists()) {
            return true;
        }
        std::fs::read(self.run_dir.join(FULLSCREEN_FILE)).is_ok_and(|b| b.first() == Some(&b'1'))
    }

    fn by_display(&self) -> bool {
        self.per_display.get() && !self.free.borrow().is_empty() && self.ipc.available()
    }

    /// May the player run: some display shows it playing.
    pub fn active(&self) -> bool {
        if !self.covered.borrow().is_empty() || self.blocked() {
            return false;
        }
        if self.always.get() {
            return true; // windows don't pause it
        }
        if self.by_display() {
            return self.free.borrow().values().any(|v| *v);
        }
        self.desktop.get()
    }

    /// Does `display` (a connector name) show the video playing (else its
    /// last frame, still).
    pub fn playing_on(&self, display: &str) -> bool {
        if !self.active() {
            return false;
        }
        if self.by_display() && !self.always.get() {
            return self.free.borrow().get(display).copied().unwrap_or(true);
        }
        true
    }

    pub fn add_listener(&self, f: Rc<dyn Fn(bool)>) -> u64 {
        let id = self.next_id.replace(self.next_id.get() + 1);
        self.listeners.borrow_mut().push((id, f));
        id
    }

    pub fn add_display_listener(&self, f: Rc<dyn Fn()>) -> u64 {
        let id = self.next_id.replace(self.next_id.get() + 1);
        self.display_listeners.borrow_mut().push((id, f));
        id
    }

    pub fn remove_listener(&self, id: u64) {
        self.listeners.borrow_mut().retain(|(i, _)| *i != id);
        self.display_listeners.borrow_mut().retain(|(i, _)| *i != id);
    }

    pub fn display_listener_count(&self) -> usize {
        self.display_listeners.borrow().len()
    }

    pub fn setting_changed(&self) {
        let got = (self.read_settings)();
        if got != (self.per_display.get(), self.always.get()) {
            self.per_display.set(got.0);
            self.always.set(got.1);
            self.layout();
            self.changed(true);
        }
    }

    fn layout(&self) {
        if !self.ipc.available() {
            return;
        }
        let free = free_outputs(self.ipc.call("window-rules/list-outputs").as_ref(), self.ipc.call("window-rules/list-views").as_ref());
        if free != *self.free.borrow() {
            self.free.replace(free);
            self.changed(true);
        }
    }

    fn layout_soon(self: &Rc<Self>) {
        if self.layout_soon.replace(true) {
            return;
        }
        let me = Rc::downgrade(self);
        (self.timer)(LAYOUT_SOON_MS, Box::new(move || {
            if let Some(f) = me.upgrade() {
                f.layout_soon.set(false);
                f.layout();
            }
        }));
    }

    /// One of Wayfire's events.
    pub fn on_event(self: &Rc<Self>, ev: &Value) {
        let name = ev.get("event").and_then(Value::as_str).unwrap_or("");
        if LAYOUT_EVENTS.contains(&name) {
            self.layout_soon();
            return;
        }
        let gone = matches!(name, "view-unmapped" | "view-minimized");
        if gone {
            self.layout_soon();
        }
        if name == "plugin-activation-state-changed" {
            let plugin = ev.get("plugin").and_then(Value::as_str).unwrap_or("");
            if COVERING_PLUGINS.contains(&plugin) {
                let out = ev.get("output-data").and_then(|o| o.get("name")).and_then(Value::as_str).unwrap_or("").to_string();
                let key = (plugin.to_string(), out);
                if truthy(ev.get("state"), false) {
                    self.covered.borrow_mut().insert(key);
                } else {
                    self.covered.borrow_mut().remove(&key);
                }
                self.changed(false);
            }
            return;
        }
        let view = ev.get("view").filter(|v| v.is_object());
        if gone || view.is_none() {
            // nothing has the keyboard: also while a window is dragged or
            // resized (Vini: dragging a window started the video) -- the
            // windows say whether one is still in front. Asked again a moment
            // later: the window just closed or minimized can still be listed
            // as the active one (Vini: paused with no window open)
            self.from_views();
            self.recheck_soon();
            return;
        }
        self.desktop.set(desktop_has_focus(view, self.desktop.get()));
        self.changed(false);
    }

    fn from_views(&self) {
        if self.ipc.available() {
            self.desktop.set(front_is_desktop(self.ipc.call("window-rules/list-views").as_ref()));
            self.changed(false);
        }
    }

    fn recheck_soon(self: &Rc<Self>) {
        // (a newer recheck replaces the one waiting: only the last runs)
        let ticket = self.recheck.get() + 1;
        self.recheck.set(ticket);
        let (me, current) = (Rc::downgrade(self), self.recheck.clone());
        (self.timer)(RECHECK_MS, Box::new(move || {
            if current.get() == ticket {
                if let Some(f) = me.upgrade() {
                    f.from_views();
                }
            }
        }));
    }

    pub fn changed(&self, _displays: bool) {
        let now = self.active();
        if self.was.get() != Some(now) {
            self.was.set(Some(now));
            let ls: Vec<_> = self.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
            for f in ls {
                f(now);
            }
        }
        // (which displays play may change with it)
        let ds: Vec<_> = self.display_listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
        for f in ds {
            f();
        }
    }
}

/// Wayfire's events (`window-rules/events/watch`) on the main loop, as the
/// Dock's toplevels.rs reads them. Keep the returned value.
pub fn watch_events(events: &[&str], callback: impl Fn(&Value) + 'static) -> Option<(std::os::unix::net::UnixStream, glib::SourceId)> {
    use std::io::Write;
    use std::os::fd::AsRawFd;
    let path = sonata_backend::wfipc::socket()?;
    let mut s = std::os::unix::net::UnixStream::connect(path).ok()?;
    let _ = s.set_read_timeout(Some(std::time::Duration::from_secs(2)));
    s.write_all(&sonata_backend::wfipc::frame("window-rules/events/watch", &json!({"events": events}))).ok()?;
    sonata_backend::wfipc::read_message(&mut s)?; // {"result": "ok"}
    let reader = RefCell::new(s.try_clone().ok()?);
    let id = glib::unix_fd_add_local(s.as_raw_fd(), glib::IOCondition::IN | glib::IOCondition::HUP, move |_, cond| {
        if cond.contains(glib::IOCondition::HUP) {
            return glib::ControlFlow::Break; // Wayfire went away
        }
        let Some(msg) = sonata_backend::wfipc::read_message(&mut *reader.borrow_mut()) else { return glib::ControlFlow::Break };
        if msg.get("event").is_some() {
            callback(&msg);
        }
        glib::ControlFlow::Continue
    });
    Some((s, id))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeIpc {
        views: Rc<RefCell<Value>>,
        outputs: Rc<RefCell<Value>>,
    }

    impl Ipc for FakeIpc {
        fn available(&self) -> bool {
            true
        }
        fn call(&self, method: &str) -> Option<Value> {
            Some(if method.ends_with("list-outputs") { self.outputs.borrow().clone() } else { self.views.borrow().clone() })
        }
    }

    struct Rig {
        f: Rc<DesktopFocus>,
        views: Rc<RefCell<Value>>,
        outputs: Rc<RefCell<Value>>,
        timers: Rc<RefCell<Vec<Box<dyn FnOnce()>>>>,
        cfg: Rc<Cell<(bool, bool)>>,
        dir: tempfile::TempDir,
    }

    fn rig(views: Value, outputs: Value, cfg: (bool, bool)) -> Rig {
        let (views, outputs) = (Rc::new(RefCell::new(views)), Rc::new(RefCell::new(outputs)));
        let timers: Rc<RefCell<Vec<Box<dyn FnOnce()>>>> = Rc::default();
        let cfg = Rc::new(Cell::new(cfg));
        let dir = tempfile::tempdir().unwrap();
        let t = timers.clone();
        let c = cfg.clone();
        let f = DesktopFocus::new(Box::new(FakeIpc { views: views.clone(), outputs: outputs.clone() }), dir.path().into(), Box::new(move || c.get()), Box::new(move |_, f| t.borrow_mut().push(f)));
        Rig { f, views, outputs, timers, cfg, dir }
    }

    fn focus(r: &Rig, view: Value) {
        r.f.on_event(&json!({"event": "view-focused", "view": view}));
    }

    #[test]
    fn test_rules() {
        assert!(desktop_has_focus(None, false)); // nothing focused
        assert!(desktop_has_focus(Some(&json!({"type": "background"})), false)); // the desktop itself
        assert!(!desktop_has_focus(Some(&json!({"type": "toplevel"})), true)); // an app
        for k in ["panel", "overlay", "x-or", "unmanaged"] {
            // Dock, menu bar, Spotlight, menus
            assert!(desktop_has_focus(Some(&json!({"type": k})), true), "{k}");
            assert!(!desktop_has_focus(Some(&json!({"type": k})), false), "{k}");
        }
    }

    #[test]
    fn test_front_at_start() {
        let app = json!({"type": "toplevel", "activated": true, "mapped": true});
        assert!(!front_is_desktop(Some(&json!([app]))));
        let mut m = app.clone();
        m["minimized"] = json!(true);
        assert!(front_is_desktop(Some(&json!([m]))));
        let mut a = app.clone();
        a["activated"] = json!(false);
        assert!(front_is_desktop(Some(&json!([a]))));
        assert!(front_is_desktop(None));
    }

    #[test]
    fn test_events_lock_and_dark() {
        let r = rig(json!([{"type": "toplevel", "activated": true}]), json!([]), (false, false));
        let seen: Rc<RefCell<Vec<bool>>> = Rc::default();
        let s = seen.clone();
        r.f.add_listener(Rc::new(move |on| s.borrow_mut().push(on)));
        assert!(!r.f.active()); // an app in front at login
        r.f.on_event(&json!({"event": "view-focused", "view": null})); // (a drag: the app still in front)
        assert!(!r.f.active());
        r.views.replace(json!([])); // its window closed
        r.f.on_event(&json!({"event": "view-focused", "view": null}));
        assert!(r.f.active());
        focus(&r, json!({"type": "panel"})); // the Dock: no change
        assert!(r.f.active());
        let lock = r.dir.path().join(LOCKED_FILE);
        std::fs::write(&lock, "{}").unwrap();
        r.f.changed(false); // (the file monitor's call)
        assert!(!r.f.active()); // locked: paused
        std::fs::remove_file(&lock).unwrap();
        r.f.changed(false);
        std::fs::write(r.dir.path().join(DARK_FILE), "1").unwrap();
        r.f.changed(false);
        assert!(!r.f.active()); // the display dark
        assert_eq!(*seen.borrow(), [true, false, true, false]);
    }

    /// Vini: the video paused and stayed paused with no window open: the
    /// window just closed was still listed as the active one when the focus
    /// went to nothing; the windows are asked again a moment later.
    #[test]
    fn test_paused_with_no_window_open() {
        let r = rig(json!([{"type": "toplevel", "activated": true, "mapped": true}]), json!([]), (false, false));
        r.f.on_event(&json!({"event": "view-focused", "view": null})); // closing: still listed
        assert!(!r.f.active());
        r.views.replace(json!([])); // gone now
        let t = r.timers.borrow_mut().pop().unwrap();
        t();
        assert!(r.f.active());
        r.views.replace(json!([{"type": "toplevel", "activated": true, "mapped": true}]));
        focus(&r, json!({"type": "toplevel"}));
        assert!(!r.f.active());
        r.views.replace(json!([]));
        r.f.on_event(&json!({"event": "view-unmapped", "view": {"type": "toplevel"}})); // closed with the focus kept on it
        assert!(r.f.active());
    }

    /// Vini: dragging a window (nothing has the keyboard during the drag)
    /// started the video under it.
    #[test]
    fn test_dragging_a_window_started_the_video() {
        let r = rig(json!([{"type": "toplevel", "activated": true, "mapped": true}]), json!([]), (false, false));
        assert!(!r.f.active());
        r.f.on_event(&json!({"event": "view-focused", "view": null})); // the drag
        assert!(!r.f.active());
        r.views.borrow_mut()[0]["minimized"] = json!(true); // minimized: the desktop shows
        r.f.on_event(&json!({"event": "view-focused", "view": null}));
        assert!(r.f.active());
    }

    /// Vini: Super+Tab (Mission Control) left the video playing.
    #[test]
    fn test_mission_control_paused_the_video() {
        let r = rig(json!([]), json!([]), (false, false));
        let seen: Rc<RefCell<Vec<bool>>> = Rc::default();
        let s = seen.clone();
        r.f.add_listener(Rc::new(move |on| s.borrow_mut().push(on)));
        assert!(r.f.active());
        r.f.on_event(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": true, "output-data": {"name": "DP-1"}}));
        assert!(!r.f.active());
        r.f.on_event(&json!({"event": "plugin-activation-state-changed", "plugin": "zoom", "state": true}));
        r.f.on_event(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": false, "output-data": {"name": "DP-1"}}));
        assert!(r.f.active());
        assert_eq!(*seen.borrow(), [false, true]);
    }

    fn outs() -> Value {
        json!([{"id": 1, "name": "DP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
               {"id": 2, "name": "HDMI-A-1", "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440}}])
    }

    #[test]
    fn test_free_displays() {
        let app = json!({"type": "toplevel", "mapped": true, "output-id": 2, "geometry": {"x": 100, "y": 100, "width": 800, "height": 600}});
        let free = free_outputs(Some(&outs()), Some(&json!([app])));
        assert_eq!(free, HashMap::from([("DP-1".into(), true), ("HDMI-A-1".into(), false)]));
        let with = |k: &str, v: Value| {
            let mut a = app.clone();
            a[k] = v;
            free_outputs(Some(&outs()), Some(&json!([a]))).values().all(|v| *v)
        };
        assert!(with("minimized", json!(true)));
        assert!(with("geometry", json!({"x": 2660, "y": 0, "width": 800, "height": 600}))); // on another desktop
        assert!(with("type", json!("panel")));
    }

    /// Vini: the video went on only while the desktop had the focus, on
    /// every display alike; now a display with no window keeps playing and
    /// one with a window shows it paused (a switch turns that off), and
    /// another switch keeps it playing whatever windows are open.
    #[test]
    fn test_one_display_with_windows_pauses_only_there() {
        let r = rig(json!([{"type": "toplevel", "activated": true, "mapped": true, "output-id": 2, "geometry": {"x": 0, "y": 0, "width": 2560, "height": 1440}}]), outs(), (true, false));
        assert!(r.f.active()); // DP-1 is free: the player runs
        assert!(r.f.playing_on("DP-1"));
        assert!(!r.f.playing_on("HDMI-A-1")); // the one with the window: paused
        r.cfg.set((false, false)); // switched off: as before
        r.f.setting_changed();
        assert!(!r.f.active()); // (an app has the focus)
        r.cfg.set((false, true)); // keep playing with windows
        r.f.setting_changed();
        assert!(r.f.active());
        assert!(r.f.playing_on("HDMI-A-1"));
        r.f.on_event(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": true}));
        assert!(!r.f.active()); // Overview still pauses it
        let _ = &r.outputs;
    }

    /// Vini: in a game (or anything full screen) the wallpaper pauses on
    /// every display -- also a free one, and with "keep playing" on.
    #[test]
    fn test_games_in_full_screen_pause_every_display() {
        let r = rig(json!([]), outs(), (true, true));
        assert!(r.f.active());
        std::fs::write(r.dir.path().join(FULLSCREEN_FILE), "1").unwrap();
        assert!(!r.f.active());
        assert!(!r.f.playing_on("DP-1") && !r.f.playing_on("HDMI-A-1"));
        std::fs::write(r.dir.path().join(FULLSCREEN_FILE), "0").unwrap();
        assert!(r.f.active());
    }

    #[test]
    fn test_cover_sizes() {
        let r = 16.0 / 9.0;
        assert_eq!(cover(r, (0.0, 0.0), 1920.0, 1080.0), (1920.0, 1080.0));
        assert_eq!(cover(r, (0.0, 0.0), 1080.0, 1920.0).1, 1920.0); // a tall box: as tall
        let (w, h) = cover(9.0 / 16.0, (0.0, 0.0), 2560.0, 1080.0); // a phone video on a wide screen
        assert!(w == 2560.0 && h > 1080.0);
        let (w, h) = cover(0.0, (640.0, 360.0), 1000.0, 1000.0); // no ratio: from its size
        assert!(h == 1000.0 && w > 1000.0);
        assert_eq!(cover(0.0, (0.0, 0.0), 300.0, 200.0), (300.0, 200.0)); // nothing known: the box
    }

    #[test]
    fn test_a_narrower_frame_still_reaches_the_right_edge() {
        let (fx, fy) = frame_shortfall(1920.0 / 1080.0, 1080.0, 1909.0, 1080.0);
        assert!((2560.0 / fx * 1909.0 / 1920.0 - 2560.0).abs() < 0.5);
        assert_eq!(fy, 1.0);
        for (fw, fh) in [(1920.0, 1080.0), (1920.0, 1088.0)] {
            // (padding: GTK cuts it itself)
            assert_eq!(frame_shortfall(1920.0 / 1080.0, 1080.0, fw, fh), (1.0, 1.0));
        }
    }

    /// No sound reaches the speakers (muting alone didn't do it).
    #[test]
    fn test_no_audio_sink_for_the_wallpaper_only() {
        for before in ["", "nvh264dec:0"] {
            let ranks = silent_ranks(before);
            for sink in ["pulsesink", "pipewiresink", "alsasink"] {
                assert!(ranks.contains(&format!("{sink}:0")));
            }
            assert!(ranks.starts_with(before));
        }
        assert_eq!(silent_ranks("pulsesink:256").matches("pulsesink").count(), 1); // what was set stays
    }

    /// Vini: with the sound turned on the wallpaper went black: every sink
    /// was put back at one rank, and the first to answer won.
    #[test]
    fn test_sound_on_put_every_sink_back_at_one_rank() {
        let ranks: Vec<u32> = AUDIO_SINKS.iter().map(|s| default_rank(s)).collect();
        assert_eq!(default_rank("pulsesink"), 266);
        assert_eq!(ranks.iter().filter(|r| **r == 266).count(), 1);
        assert_eq!(*ranks.iter().max().unwrap(), 266);
    }

    #[test]
    fn the_loops_fade() {
        assert_eq!(fade_alpha(0.0), 1.0);
        assert!((fade_alpha(350.0) - 0.5).abs() < 1e-9);
        assert_eq!(fade_alpha(700.0), 0.0);
    }

    #[test]
    fn test_sound_off_by_default_and_on_at_its_volume() {
        let _g = crate::testenv::lock();
        assert_eq!(sound(), (false, 0.25));
        sonata_config::save("wallpaper", json!({"video_sound": true, "video_volume": 60}).as_object().unwrap()).unwrap();
        assert_eq!(sound(), (true, 0.6));
        sonata_config::save("wallpaper", json!({"video_sound": 1, "video_volume": "x"}).as_object().unwrap()).unwrap();
        assert_eq!(sound(), (true, 0.25));
        sonata_config::save("wallpaper", &serde_json::Map::new()).unwrap();
    }
}
