//! Mission Control's backdrop (shell/mission.py): the desktop darkens
//! behind the windows laid out side by side. Only darkens (Vini: blur is
//! too heavy) -- a translucent black layer over the real desktop, nothing
//! decoded or blurred.
//!
//! Wayfire's `scale` plugin lays the windows out (Ctrl+Up, F3, three
//! fingers up); its look is set in config/wayfire.ini [scale]. This puts
//! the dark layer behind them: a layer surface on the bottom layer of the
//! display where scale is active, faded in and out with it. Wayfire IPC
//! reports when scale starts and stops ("plugin-activation-state-changed").
//!
//! With scale (not expo) the desktops bar comes too (spaces.rs), and the
//! close button over the window under the pointer (CloseOverlay).

use crate::spaces::SpacesBar;
use crate::{Ipc, Wayfire};
use gtk::gdk;
use gtk::prelude::*;
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{Duration, Instant};

pub const FADE_MS: f64 = 260.0;
/// how dark the desktop goes behind the windows
pub const DIM: f64 = 0.45;
pub const CLOSE_SIZE: i32 = 24;
/// how far the x sits out of the window's corner
pub const CLOSE_OUT: i32 = 8;
/// expo (all Spaces) gets the same backdrop
pub const PLUGINS: [&str; 2] = ["scale", "expo"];
/// decode size bounds (backdrop_size)
pub const BACKDROP_MIN: i32 = 480;
/// no display known
pub const BACKDROP_FALLBACK: i32 = 1280;
/// the video wallpaper's frame where it paused, written by the desktop as
/// Overview opens (videowallpaper.FRAME_FILE)
pub const FRAME_FILE: &str = "sonata2-wallpaper-frame.jpg";

/// What Mission Control listens to.
pub const EVENTS: [&str; 7] = ["plugin-activation-state-changed", "wset-workspace-changed", "view-mapped", "view-unmapped", "view-workspace-changed", "sonata-scale-drag#", "sonata-scale-hover#"];

/// Pixels on the longest side to decode the backdrop's wallpaper at: a
/// quarter of the largest display's (device pixels). Blur 40 (a Gaussian
/// of ~20 px) leaves nothing finer than that to see. monitors: (width,
/// height, scale).
pub fn backdrop_size(monitors: &[(i32, i32, i32)]) -> i32 {
    let side = monitors.iter().map(|&(w, h, s)| w.max(h) * s.max(1)).max().unwrap_or(0);
    if side > 0 { BACKDROP_MIN.max(side / 4) } else { BACKDROP_FALLBACK }
}

/// The displays as backdrop_size wants them.
pub fn displays() -> Vec<(i32, i32, i32)> {
    sonata_shell::layer::monitors().iter().map(|m| (m.geometry().width(), m.geometry().height(), m.scale_factor())).collect()
}

/// Where the x goes for a window drawn at `bx` (output-local): its top
/// corner on the side of the user's window buttons, a little out of it.
pub fn close_spot(bx: &Value, end: bool) -> (i32, i32) {
    let n = |k: &str| bx[k].as_i64().unwrap_or(0) as i32;
    let x = if end { n("x") + n("width") - CLOSE_SIZE + CLOSE_OUT } else { n("x") - CLOSE_OUT };
    (x.max(0), (n("y") - CLOSE_OUT).max(0))
}

/// What an IPC event asks of Mission Control.
#[derive(Clone, Debug, PartialEq)]
pub enum Route {
    /// the window under the pointer, on that display: its x
    Hover(String),
    /// a window dragged: maybe onto a desktop
    Drag(String),
    /// the desktops changed while the bar shows
    Refresh,
    /// scale or expo started (true) or stopped on that display
    Plugin { plugin: String, output: String, on: bool },
    Ignore,
}

pub fn route(ev: &Value) -> Route {
    let s = |v: &Value| v.as_str().unwrap_or("").to_string();
    match ev["event"].as_str().unwrap_or("plugin-activation-state-changed") {
        "sonata-scale-hover#" => Route::Hover(s(&ev["output"])),
        "sonata-scale-drag#" => Route::Drag(s(&ev["output"])),
        "plugin-activation-state-changed" => {
            let plugin = s(&ev["plugin"]);
            if !PLUGINS.contains(&plugin.as_str()) {
                return Route::Ignore;
            }
            Route::Plugin { plugin, output: s(&ev["output-data"]["name"]), on: ev["state"].as_bool().unwrap_or(false) }
        }
        _ => Route::Refresh,
    }
}

/// Scale and expo each darken a display; the backdrop goes when neither
/// does. Some(true): show it, Some(false): hide it.
pub fn activation(active: &mut HashSet<(String, String)>, plugin: &str, output: &str, on: bool) -> Option<bool> {
    let key = (plugin.to_string(), output.to_string());
    if on {
        active.insert(key);
        return Some(true);
    }
    active.remove(&key);
    (!active.iter().any(|(_, n)| n == output)).then_some(false)
}

/// The frame file, in the runtime folder (videowallpaper._runtime).
pub fn frame_path() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp")).join(FRAME_FILE)
}

thread_local! {
    static FRAME: RefCell<Option<((PathBuf, std::time::SystemTime), gdk::Texture)>> = const { RefCell::new(None) };
}

/// The video wallpaper's frame where it paused, or None for a picture or
/// no frame yet.
pub fn video_frame(dark: bool) -> Option<gdk::Texture> {
    let path = sonata_shell::wallpaper::path_of(&sonata_shell::wallpaper::wallpaper_uri(dark))?;
    if !sonata_shell::wallpaper::is_video(&path.to_string_lossy()) {
        return None;
    }
    let frame = frame_path();
    let key = (path, std::fs::metadata(&frame).ok()?.modified().ok()?);
    FRAME.with(|f| {
        if f.borrow().as_ref().map(|(k, _)| k) != Some(&key) {
            let tex = gdk::Texture::from_filename(&frame).ok()?;
            f.replace(Some((key, tex)));
        }
        f.borrow().as_ref().map(|(_, t)| t.clone())
    })
}

/// A window's close button in Mission Control (Vini), over the window
/// under the pointer: a layer surface just the button's size, put at its
/// corner with the layer's margins (Sonata's Wayfire says which window and
/// where: sonata-scale-hover#).
pub struct CloseOverlay {
    pub win: gtk::Window,
    view: Cell<Option<i64>>,
    ipc: Rc<dyn Ipc>,
}

impl CloseOverlay {
    pub fn new(app: &gtk::Application, monitor: Option<&gdk::Monitor>, ipc: Rc<dyn Ipc>) -> Rc<CloseOverlay> {
        use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
        let win = gtk::Window::builder().application(app).decorated(false).resizable(false).css_classes(["sonata-mission-close"]).build();
        // a plain box, its click taken by the surface: a Gtk.Button here got
        // the press and the release but never said "clicked" (Mission Control
        // holds the pointer: the window never closed)
        let button = gtk::Box::builder().css_classes(["mc-close"]).tooltip_text(sonata_i18n::tr!("Close Window")).build();
        button.set_size_request(CLOSE_SIZE, CLOSE_SIZE);
        button.append(&gtk::Image::builder().icon_name("window-close-symbolic").hexpand(true).halign(gtk::Align::Center).build());
        win.set_child(Some(&button));
        win.init_layer_shell();
        win.set_namespace(Some("sonata2-mission-close"));
        win.set_layer(Layer::Overlay);
        win.set_anchor(Edge::Top, true);
        win.set_anchor(Edge::Left, true);
        win.set_exclusive_zone(-1);
        win.set_keyboard_mode(KeyboardMode::None);
        if let Some(m) = monitor {
            win.set_monitor(Some(m));
        }
        let me = Rc::new(CloseOverlay { win, view: Cell::new(None), ipc });
        let click = gtk::GestureClick::builder().button(1).build();
        let weak = Rc::downgrade(&me);
        click.connect_released(move |_, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.close();
            }
        });
        me.win.add_controller(click);
        me
    }

    /// The window under the pointer (view-id -1: none).
    pub fn hover(&self, ev: &Value) {
        let vid = ev["view-id"].as_i64().unwrap_or(-1);
        if vid < 0 || ev["width"].as_i64().unwrap_or(0) <= 0 {
            self.clear();
            return;
        }
        use gtk4_layer_shell::{Edge, LayerShell};
        self.view.set(Some(vid));
        let (x, y) = close_spot(ev, crate::spaces::close_at_end());
        self.win.set_margin(Edge::Left, x);
        self.win.set_margin(Edge::Top, y);
        if !self.win.is_visible() {
            self.win.present();
        }
    }

    pub fn clear(&self) {
        self.view.set(None);
        self.win.set_visible(false);
    }

    fn close(&self) {
        if let Some(id) = self.view.get() {
            self.ipc.call("window-rules/close-view", json!({"id": id}));
        }
        self.clear();
    }
}

fn monitor_named(name: &str) -> Option<gdk::Monitor> {
    sonata_shell::layer::monitors().into_iter().find(|m| sonata_shell::layer::connector(m) == name)
}

pub struct MissionBackdrop {
    app: gtk::Application,
    /// connector -> its dark layer
    windows: RefCell<HashMap<String, gtk::Window>>,
    fades: RefCell<HashMap<String, glib::SourceId>>,
    /// (plugin, connector)
    active: RefCell<HashSet<(String, String)>>,
    ipc: Rc<dyn Ipc>,
    /// the decoded wallpaper: only the current Light/Dark one, until system.json changes
    texture: RefCell<Option<(bool, Option<gdk::Texture>)>>,
    pub bars: RefCell<HashMap<String, Rc<SpacesBar>>>,
    closers: RefCell<HashMap<String, Rc<CloseOverlay>>>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
    prefs_watched: Cell<bool>,
    watch: RefCell<Option<crate::Watch>>,
}

impl MissionBackdrop {
    /// Listens to Wayfire (when there are layer surfaces and Wayfire).
    pub fn new(app: &gtk::Application) -> Rc<MissionBackdrop> {
        let me = Rc::new(MissionBackdrop {
            app: app.clone(),
            windows: RefCell::default(),
            fades: RefCell::default(),
            active: RefCell::default(),
            ipc: Rc::new(Wayfire),
            texture: RefCell::default(),
            bars: RefCell::default(),
            closers: RefCell::default(),
            monitors: RefCell::default(),
            prefs_watched: Cell::new(false),
            watch: RefCell::default(),
        });
        // a video wallpaper's frame, written as Overview opens
        let weak = Rc::downgrade(&me);
        if let Some(m) = sonata_config::watch_path(&frame_path(), move || {
            if let Some(me) = weak.upgrade() {
                me.frame_changed();
            }
        }) {
            me.monitors.borrow_mut().push(m);
        }
        if sonata_shell::layer::available() {
            let weak = Rc::downgrade(&me);
            let w = crate::watch_events(&EVENTS, move |ev| {
                if let Some(me) = weak.upgrade() {
                    me.event(ev);
                }
            });
            me.watch.replace(w);
        }
        me
    }

    pub fn event(self: &Rc<Self>, ev: &Value) {
        match route(ev) {
            Route::Hover(name) => self.hover(&name, ev),
            Route::Drag(name) => {
                let bar = self.bars.borrow().get(&name).cloned();
                if let Some(bar) = bar.filter(|b| b.is_visible()) {
                    bar.dragging(ev);
                }
            }
            Route::Refresh => {
                let bars: Vec<_> = self.bars.borrow().values().cloned().collect();
                for bar in bars.into_iter().filter(|b| b.is_visible()) {
                    bar.refresh_soon(120);
                }
            }
            Route::Plugin { plugin, output, on } => {
                if plugin == "scale" {
                    self.spaces(&output, on);
                    if !on {
                        if let Some(c) = self.closers.borrow().get(&output) {
                            c.clear();
                        }
                    }
                }
                match activation(&mut self.active.borrow_mut(), &plugin, &output, on) {
                    Some(true) => self.show(&output),
                    Some(false) => self.hide(&output),
                    None => {}
                }
            }
            Route::Ignore => {}
        }
    }

    fn hover(&self, name: &str, ev: &Value) {
        let closer = self.closers.borrow().get(name).cloned();
        let closer = match closer {
            Some(c) => c,
            None => {
                if ev["view-id"].as_i64().unwrap_or(-1) < 0 || !sonata_shell::layer::available() {
                    return; // none yet, nothing to show: none made
                }
                let c = CloseOverlay::new(&self.app, monitor_named(name).as_ref(), self.ipc.clone());
                self.closers.borrow_mut().insert(name.to_string(), c.clone());
                c
            }
        };
        closer.hover(ev);
    }

    fn window(&self, name: &str) -> Option<gtk::Window> {
        if let Some(w) = self.windows.borrow().get(name) {
            return Some(w.clone());
        }
        use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
        let mon = monitor_named(name)?;
        let win = gtk::Window::builder().application(&self.app).decorated(false).can_target(false).build();
        win.add_css_class("sonata-mission"); // translucent black: the desktop shows through, darker
        win.set_child(Some(&gtk::Box::new(gtk::Orientation::Horizontal, 0)));
        win.init_layer_shell();
        win.set_namespace(Some("sonata2-mission"));
        win.set_layer(Layer::Bottom); // above the wallpaper, below the windows
        for e in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
            win.set_anchor(e, true);
        }
        win.set_exclusive_zone(-1);
        win.set_keyboard_mode(KeyboardMode::None);
        win.set_monitor(Some(&mon));
        self.windows.borrow_mut().insert(name.to_string(), win.clone());
        Some(win)
    }

    fn show(&self, name: &str) {
        let Some(win) = self.window(name) else { return };
        if !win.is_visible() {
            win.set_opacity(0.0);
            win.present();
            sonata_shell::layer::set_input_region(&win, &[]);
        }
        self.fade(name, &win, 1.0, None);
    }

    fn hide(&self, name: &str) {
        let Some(win) = self.windows.borrow().get(name).cloned() else { return };
        if win.is_visible() {
            let w = win.clone();
            self.fade(name, &win, 0.0, Some(Box::new(move || w.set_visible(false))));
        }
    }

    /// The desktops bar with scale (Mission Control), on that display.
    fn spaces(self: &Rc<Self>, name: &str, on: bool) {
        let bar = self.bars.borrow().get(name).cloned();
        if !on {
            if let Some(b) = bar {
                b.hide();
            }
            return;
        }
        let wall = self.wallpaper();
        let bar = match bar {
            Some(b) => b,
            None => {
                let Some(mon) = monitor_named(name) else { return };
                let b = SpacesBar::new(&self.app, Some(mon), name, wall.clone(), self.ipc.clone(), Rc::new(crate::spaces::set_count), None);
                self.bars.borrow_mut().insert(name.to_string(), b.clone());
                b
            }
        };
        bar.wallpaper.replace(wall);
        bar.show_bar();
    }

    /// The wallpaper texture, decoded once per Light/Dark picture (not on
    /// every Mission Control open); a wallpaper change drops the cache.
    /// Memory review: decoded at a quarter of the largest display (it is
    /// drawn under blur 40: the same picture on screen) and only the
    /// current Light/Dark one kept: full size, both variants held ~80 MB
    /// for the whole session.
    pub fn wallpaper(self: &Rc<Self>) -> Option<gdk::Texture> {
        if !self.prefs_watched.replace(true) {
            let weak = Rc::downgrade(self);
            let ms = sonata_config::watch("system", move || {
                if let Some(me) = weak.upgrade() {
                    me.texture.replace(None);
                }
            });
            self.monitors.borrow_mut().extend(ms);
        }
        let dark = sonata_ui::is_dark();
        if let Some(frame) = video_frame(dark) {
            return Some(frame); // a video: where it paused (Vini)
        }
        if let Some((d, t)) = self.texture.borrow().as_ref() {
            if *d == dark {
                return t.clone();
            }
        }
        let t = sonata_shell::loginui::wallpaper_texture(backdrop_size(&displays()));
        self.texture.replace(Some((dark, t.clone())));
        t
    }

    /// The video's frame landed (just after Overview opened): the desktops
    /// showing get it.
    fn frame_changed(self: &Rc<Self>) {
        let tex = self.wallpaper();
        let bars: Vec<_> = self.bars.borrow().values().cloned().collect();
        for bar in bars.into_iter().filter(|b| b.is_visible()) {
            if *bar.wallpaper.borrow() != tex {
                bar.wallpaper.replace(tex.clone());
                bar.refresh();
            }
        }
    }

    // a new fade replaces the running one: scale off then on again within
    // FADE_MS, the old fade-out's done() hid the backdrop while scale was on
    fn fade(&self, name: &str, win: &gtk::Window, to: f64, done: Option<Box<dyn FnOnce()>>) {
        if let Some(old) = self.fades.borrow_mut().remove(name) {
            old.remove();
        }
        let (start, from) = (Instant::now(), win.opacity());
        let (w, done) = (win.downgrade(), RefCell::new(done));
        let id = glib::timeout_add_local(Duration::from_millis(16), move || {
            let Some(win) = w.upgrade() else { return glib::ControlFlow::Break };
            let t = (start.elapsed().as_secs_f64() * 1000.0 / FADE_MS).min(1.0);
            win.set_opacity(from + (to - from) * (1.0 - (1.0 - t).powi(3)));
            if t >= 1.0 {
                if let Some(f) = done.take() {
                    f();
                }
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        });
        self.fades.borrow_mut().insert(name.to_string(), id);
    }
}

/// Desktops for the preview: three of them, a few windows, in memory --
/// the preview never asks the real Wayfire nor writes its config.
/// (id, app id, desktop)
type SampleWindow = (i64, String, i64);

#[derive(Default)]
pub struct SampleDesktops {
    /// (count, current, windows: (id, app id, desktop))
    state: RefCell<(i64, i64, Vec<SampleWindow>)>,
}

impl SampleDesktops {
    pub fn new() -> Rc<SampleDesktops> {
        let apps = [(1, "org.gnome.Nautilus", 0), (2, "firefox", 1), (3, "org.gnome.Calculator", 1), (4, "org.gnome.TextEditor", 2)];
        Rc::new(SampleDesktops { state: RefCell::new((3, 1, apps.iter().map(|(i, a, d)| (*i, a.to_string(), *d)).collect())) })
    }

    pub fn set_count(&self, n: i64) {
        let mut st = self.state.borrow_mut();
        st.0 = n.clamp(1, crate::spaces::MAX_DESKTOPS);
        st.1 = st.1.min(st.0 - 1);
    }
}

impl Ipc for SampleDesktops {
    fn call(&self, method: &str, data: Value) -> Option<Value> {
        let mut st = self.state.borrow_mut();
        let (count, current) = (st.0, st.1);
        let x = data["x"].as_i64().unwrap_or(0);
        match method {
            "window-rules/list-outputs" => Some(json!([{"id": 1, "name": "PREVIEW-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "workarea": {"x": 0, "y": 0, "width": 1920, "height": 1080}, "workspace": {"x": current, "y": 0, "grid_width": count, "grid_height": 1}}])),
            "window-rules/list-views" => Some(Value::Array(
                st.2.iter().map(|(id, app, d)| json!({"id": id, "type": "toplevel", "mapped": true, "app-id": app, "output-id": 1, "geometry": {"x": (d - current) * 1920 + 400, "y": 200, "width": 800, "height": 600}})).collect(),
            )),
            "vswitch/set-workspace" | "sonata/set-workspace" => {
                st.1 = x.clamp(0, count - 1);
                Some(json!({"result": "ok"}))
            }
            "vswitch/send-view" => {
                let id = data["view-id"].as_i64();
                for w in st.2.iter_mut().filter(|w| Some(w.0) == id) {
                    w.2 = x;
                }
                Some(json!({"result": "ok"}))
            }
            "window-rules/close-view" => {
                let id = data["id"].as_i64();
                st.2.retain(|w| Some(w.0) != id);
                Some(json!({"result": "ok"}))
            }
            _ => Some(json!({"result": "ok"})),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// tests/test_mission_mem.py: test_size.
    #[test]
    fn test_size() {
        assert_eq!(backdrop_size(&[(2560, 1600, 1)]), 640);
        assert_eq!(backdrop_size(&[(1920, 1080, 1), (1440, 900, 2)]), 720);
        assert_eq!(backdrop_size(&[(1280, 800, 1)]), BACKDROP_MIN);
        assert_eq!(backdrop_size(&[]), BACKDROP_FALLBACK);
    }

    /// tests/test_mission_control.py: the x follows the user's side.
    #[test]
    fn test_spot_follows_the_users_side() {
        let b = json!({"x": 100, "y": 200, "width": 300, "height": 150});
        assert_eq!(close_spot(&b, false), (100 - CLOSE_OUT, 200 - CLOSE_OUT));
        assert_eq!(close_spot(&b, true), (400 - CLOSE_SIZE + CLOSE_OUT, 200 - CLOSE_OUT));
        assert_eq!(close_spot(&json!({"x": 2, "y": 3, "width": 50, "height": 50}), false), (0, 0));
    }

    #[test]
    fn test_events_reach_the_bar_of_that_display() {
        let drag = json!({"event": "sonata-scale-drag#", "state": "drop", "output": "DP-1", "view-id": 3, "x": 5, "y": 6});
        assert_eq!(route(&drag), Route::Drag("DP-1".into()));
        let hover = json!({"event": "sonata-scale-hover#", "view-id": 3, "output": "DP-1"});
        assert_eq!(route(&hover), Route::Hover("DP-1".into()));
        assert!(EVENTS.contains(&"sonata-scale-drag#") && EVENTS.contains(&"sonata-scale-hover#"));
        assert_eq!(route(&json!({"event": "view-mapped"})), Route::Refresh);
        assert_eq!(route(&json!({"event": "plugin-activation-state-changed", "plugin": "move", "state": true})), Route::Ignore);
        assert_eq!(
            route(&json!({"event": "plugin-activation-state-changed", "plugin": "scale", "state": false, "output-data": {"name": "DP-1"}})),
            Route::Plugin { plugin: "scale".into(), output: "DP-1".into(), on: false }
        );
    }

    /// Scale and expo both on a display: the backdrop stays until both are off.
    #[test]
    fn backdrop_until_both_are_off() {
        let mut active = HashSet::new();
        assert_eq!(activation(&mut active, "scale", "DP-1", true), Some(true));
        assert_eq!(activation(&mut active, "expo", "DP-1", true), Some(true));
        assert_eq!(activation(&mut active, "scale", "HDMI-A-1", true), Some(true));
        assert_eq!(activation(&mut active, "scale", "DP-1", false), None);
        assert_eq!(activation(&mut active, "expo", "DP-1", false), Some(false));
        assert_eq!(activation(&mut active, "scale", "HDMI-A-1", false), Some(false));
    }

    #[test]
    fn no_video_frame_for_a_picture() {
        // (the test's home: Sonata's own picture wallpaper)
        assert!(video_frame(false).is_none());
        assert!(frame_path().ends_with(FRAME_FILE));
    }

    #[test]
    fn the_preview_desktops_follow_what_the_bar_does() {
        let s = SampleDesktops::new();
        let ipc: &dyn Ipc = &*s;
        assert_eq!(crate::spaces::close_desktop(ipc, "PREVIEW-1", 0, &|n| s.set_count(n)), crate::spaces::Closed::Changed { count: 2, removed: 0 });
        let out = crate::spaces::find_output(ipc, "PREVIEW-1").unwrap();
        assert_eq!(crate::spaces::workspace(&out), (2, 0));
        let per = crate::spaces::per_desktop(&out, &crate::spaces::views(ipc), 2);
        assert_eq!(per, [vec!["firefox", "org.gnome.Calculator"], vec!["org.gnome.TextEditor"]]); // Files closed with desktop 1
    }
}
