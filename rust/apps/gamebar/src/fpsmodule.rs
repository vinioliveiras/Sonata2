//! Control Center > FPS Limit (shell/fpsmodule.py; Vini: a row of choices,
//! off by default; not in the default layout -- Add Controls). Games' frame
//! rate through frame-pacer (fpslimit.py): Off, 30, 60, 90, 120 or Max (the
//! display's refresh rate). Without frame-pacer the choices are greyed out
//! and the module offers to install it.
//!
//! Under the choices, a frame-time graph of the app in front, named in the
//! title row (Vini: as tall as the other two-row modules): its frame time
//! over the last half minute -- one point per read, the average of the
//! frames since the one before (Vini: frame by frame it ran by too fast) --
//! the limit's target as a dashed line, the average and the 1 % low. From
//! sonata-corners (IPC sonata/fps with frametimes), read only while shown.
//!
//! fpslimit.py itself is sonata-tweaks' (ported apart): the process hands
//! it over once with [`set_backend`]; without one the choices stay greyed.

use gtk::prelude::*;
use serde_json::Value;
use sonata_i18n::{fmt, n_, tr};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

pub const POLL_MS: u64 = 250;
/// points on the graph, one per read: 30 s (newest at the right)
pub const SHOWN: usize = 120;
/// shown through tr
pub const LABELS: [(&str, &str); 2] = [("off", n_!("Off")), ("max", n_!("Max"))];

/// install's done(error or None)
pub type InstallDone = Box<dyn FnOnce(Option<String>)>;

/// What the module needs of fpslimit.py (sonata-tweaks has it).
#[derive(Clone)]
pub struct FpsBackend {
    /// fpslimit.CHOICES: ("off", "30", "60", "90", "120", "max")
    pub choices: Vec<String>,
    /// frame-pacer's layer is in ~/.local
    pub installed: fn() -> bool,
    /// "off", or a number of frames per second, or "max"
    pub get: fn() -> String,
    /// set(limit, the display's refresh rate)
    pub set: fn(&str, f64),
    /// resolve(limit, refresh rate): what frame-pacer gets ("off" or 1..999)
    pub resolve: fn(&str, f64) -> String,
    /// install(done): the latest release into ~/.local; done(error or None)
    /// on the main loop
    pub install: fn(InstallDone),
}

thread_local! {
    static BACKEND: RefCell<Option<FpsBackend>> = const { RefCell::new(None) };
}

/// fpslimit for this process (once, before a module is made).
pub fn set_backend(b: FpsBackend) {
    BACKEND.with(|s| s.replace(Some(b)));
}

pub fn backend() -> Option<FpsBackend> {
    BACKEND.with(|s| s.borrow().clone())
}

fn css() {
    sonata_ui::css::register_module("shell/fpsmodule.py");
}

/// The fastest display's refresh rate (Max follows it).
pub fn refresh_hz() -> f64 {
    sonata_shell::layer::monitors().iter().map(|m| m.refresh_rate() as f64 / 1000.0).fold(0.0, f64::max)
}

fn mean(v: &[f64]) -> f64 {
    v.iter().sum::<f64>() / v.len().max(1) as f64
}

/// The average frame time of the frames drawn since the last read (the
/// newest ones adding up to POLL_MS).
pub fn recent_average(times: &[f64]) -> f64 {
    let (mut total, mut picked) = (0.0, Vec::new());
    for t in times.iter().rev() {
        picked.push(*t);
        total += t;
        if total >= POLL_MS as f64 {
            break;
        }
    }
    mean(&picked)
}

/// (average frame time in ms, 1 % low in fps: the slowest 1 % of frames).
pub fn summary(times: &[f64]) -> (f64, i64) {
    let avg = mean(times);
    let mut sorted = times.to_vec();
    sorted.sort_by(f64::total_cmp);
    let n = (times.len() / 100).max(1);
    let worst = &sorted[sorted.len().saturating_sub(n)..];
    (avg, (1000.0 / mean(worst)).round().max(0.0) as i64)
}

/// The top of the graph in ms: twice the target (or 60 fps), more for big spikes.
pub fn scale(times: &[f64], target: Option<f64>) -> f64 {
    let base = 2.0 * target.unwrap_or(16.7);
    let mut sorted = times.to_vec();
    sorted.sort_by(f64::total_cmp);
    let peak = if sorted.is_empty() { 0.0 } else { sorted[sorted.len() - (sorted.len() / 50).max(1)] };
    base.max((peak * 1.15).min(100.0))
}

/// The app's name for a window's app_id (Steam games too).
pub fn app_name(app_id: &str) -> String {
    if let Some(info) = sonata_desktop::appmatch::match_app_id(app_id).and_then(|d| sonata_ui::apps::lookup(&d)) {
        return info.display_name().to_string();
    }
    let name = sonata_desktop::windowapps::describe(app_id, "").0;
    if name.is_empty() { app_id.to_string() } else { name }
}

/// The plugin's answer (None: no plugin / no Wayfire). Blocking.
pub fn read_frames() -> Option<Value> {
    sonata_backend::wfipc::call("sonata/fps", serde_json::json!({"frametimes": true}))
}

/// What one read shows.
#[derive(Debug, PartialEq)]
pub enum Shown {
    /// nothing measured: the graph's note ("" with no Wayfire at all)
    Nothing { note: String, no_app: bool },
    Live {
        /// another app in front: its name (and a graph of its own)
        new_app: Option<String>,
        live: String,
        avg: String,
        low: String,
    },
}

/// The graph's points and whose they are (FpsModule.history / _app_id).
#[derive(Default)]
pub struct Frames {
    pub history: Vec<f64>,
    pub app_id: Option<String>,
}

impl Frames {
    /// r: the plugin's answer -- anything at all, never trusted.
    pub fn read(&mut self, r: Option<&Value>, name_of: impl Fn(&str) -> String) -> Shown {
        let Some(obj) = r.and_then(Value::as_object).filter(|o| o.contains_key("frametimes")) else {
            self.history.clear();
            self.app_id = None;
            let note = if r.is_some() { tr!("Update Sonata's Wayfire plugin (./install.sh)") } else { String::new() };
            return Shown::Nothing { note, no_app: false };
        };
        let times: Vec<f64> = obj.get("frametimes").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_f64).filter(|t| *t > 0.0).collect()).unwrap_or_default();
        let app = obj.get("app-id").and_then(Value::as_str).unwrap_or("");
        if app.is_empty() || times.len() < 2 {
            self.history.clear();
            self.app_id = None;
            return Shown::Nothing { note: tr!("No game or full-screen app"), no_app: true };
        }
        let mut new_app = None;
        if self.app_id.as_deref() != Some(app) {
            // another app in front: its own graph
            self.history.clear();
            self.app_id = Some(app.to_string());
            new_app = Some(name_of(app));
        }
        self.history.push(recent_average(&times));
        if self.history.len() > SHOWN {
            self.history.drain(..self.history.len() - SHOWN);
        }
        let (avg, low) = summary(&times);
        let fps = obj.get("fps").and_then(Value::as_f64).unwrap_or(0.0) as i64;
        Shown::Live {
            new_app,
            live: fmt(&tr!("{n} fps"), &[("n", &fps.to_string())]),
            avg: format!("{avg:.1} ms"),
            low: fmt(&tr!("1% low {n} fps"), &[("n", &low.to_string())]),
        }
    }
}

// -- the graph ----------------------------------------------------------------------------------
#[derive(Default)]
pub struct GraphState {
    pub times: Vec<f64>,
    pub note: String,
    pub target: Option<f64>,
}

/// Each read's frame time as a line (spikes: stutters), the limit's target dashed.
#[derive(Clone)]
pub struct FrameTimeGraph {
    pub area: gtk::DrawingArea,
    pub state: Rc<RefCell<GraphState>>,
}

impl FrameTimeGraph {
    pub fn new() -> Self {
        let area = gtk::DrawingArea::builder().hexpand(true).vexpand(true).content_height(40).can_target(false).build();
        let state: Rc<RefCell<GraphState>> = Rc::default();
        let st = state.clone();
        area.set_draw_func(move |a, cr, w, h| draw(a, cr, w as f64, h as f64, &st.borrow()));
        Self { area, state }
    }

    pub fn set_times(&self, times: &[f64], note: &str) {
        let mut s = self.state.borrow_mut();
        s.times = times[times.len().saturating_sub(SHOWN)..].to_vec();
        s.note = note.to_string();
        drop(s);
        self.area.queue_draw();
    }
}

impl Default for FrameTimeGraph {
    fn default() -> Self {
        Self::new()
    }
}

fn draw(a: &gtk::DrawingArea, cr: &gtk::cairo::Context, w: f64, h: f64, s: &GraphState) {
    use std::f64::consts::PI;
    let c = a.color();
    let (cr_, cg, cb) = (c.red() as f64, c.green() as f64, c.blue() as f64);
    let r = 6.0;
    cr.new_sub_path();
    cr.arc(w - r, r, r, -PI / 2.0, 0.0);
    cr.arc(w - r, h - r, r, 0.0, PI / 2.0);
    cr.arc(r, h - r, r, PI / 2.0, PI);
    cr.arc(r, r, r, PI, 1.5 * PI);
    cr.close_path();
    cr.set_source_rgba(cr_, cg, cb, 0.06);
    let _ = cr.fill();
    if s.times.is_empty() {
        if !s.note.is_empty() {
            let lay = pangocairo::functions::create_layout(cr);
            lay.set_text(&s.note);
            lay.set_font_description(Some(&gtk::pango::FontDescription::from_string("sans 9")));
            let (_i, log) = lay.pixel_extents();
            cr.move_to((w - log.width() as f64) / 2.0, (h - log.height() as f64) / 2.0);
            cr.set_source_rgba(cr_, cg, cb, 0.5);
            pangocairo::functions::show_layout(cr, &lay);
        }
        return;
    }
    let (top, pad) = (scale(&s.times, s.target), 3.0);
    let y = |ms: f64| h - pad - ms.min(top) / top * (h - 2.0 * pad);
    if let Some(t) = s.target {
        cr.set_source_rgba(cr_, cg, cb, 0.3);
        cr.set_line_width(1.0);
        cr.set_dash(&[3.0, 3.0], 0.0);
        cr.move_to(pad, y(t).round() + 0.5);
        cr.line_to(w - pad, y(t).round() + 0.5);
        let _ = cr.stroke();
        cr.set_dash(&[], 0.0);
    }
    let acc = sonata_ui::rgba("accent");
    cr.set_source_rgba(acc.red() as f64, acc.green() as f64, acc.blue() as f64, 1.0);
    cr.set_line_width(1.4);
    cr.set_line_join(gtk::cairo::LineJoin::Round);
    let step = (w - 2.0 * pad) / (SHOWN.max(2) - 1) as f64;
    let x0 = w - pad - step * (s.times.len() - 1) as f64;
    for (i, t) in s.times.iter().enumerate() {
        if i == 0 {
            cr.move_to(x0, y(*t));
        } else {
            cr.line_to(x0 + i as f64 * step, y(*t));
        }
    }
    let _ = cr.stroke();
}

// -- the module ---------------------------------------------------------------------------------
pub struct FpsModule {
    pub root: gtk::Box,
    pub app: gtk::Label,
    pub live: gtk::Label,
    pub cap: gtk::Label,
    pub install_btn: gtk::Button,
    pub seg: sonata_ui::controls::Segmented,
    pub graph: FrameTimeGraph,
    pub avg: gtk::Label,
    pub low: gtk::Label,
    pub frames: RefCell<Frames>,
    poll: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
}

fn choices() -> Vec<String> {
    backend().map(|b| b.choices).unwrap_or_else(|| ["off", "30", "60", "90", "120", "max"].map(String::from).to_vec())
}

impl FpsModule {
    pub fn new() -> Rc<Self> {
        css();
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).css_classes(["panel-module", "cc-fps"]).build();
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        head.append(&gtk::Label::builder().label(tr!("FPS Limit")).xalign(0.0).css_classes(["panel-module-title"]).build());
        // the app being measured (Vini)
        let app = gtk::Label::builder().xalign(0.0).hexpand(true).css_classes(["cc-fps-app"]).ellipsize(gtk::pango::EllipsizeMode::End).width_chars(1).build();
        head.append(&app);
        let live = gtk::Label::builder().css_classes(["cc-fps-live"]).build(); // "58 fps"
        head.append(&live);
        let cap = gtk::Label::builder().label(tr!("games")).css_classes(["cc-fps-cap"]).build();
        head.append(&cap);
        let install_btn = gtk::Button::builder().label(tr!("Install frame-pacer")).css_classes(["cc-fps-install"]).can_focus(false).visible(false).build();
        head.append(&install_btn);
        root.append(&head);
        let me: Rc<std::cell::OnceCell<std::rc::Weak<Self>>> = Rc::default();
        let list = choices();
        let labeled: Vec<(&str, String)> = list
            .iter()
            .map(|c| (c.as_str(), LABELS.iter().find(|(k, _)| k == c).map(|(_, l)| sonata_i18n::gettext(l)).unwrap_or_else(|| c.clone())))
            .collect();
        let m = me.clone();
        let seg = sonata_ui::controls::Segmented::new(
            &labeled,
            None,
            move |c: &str| {
                if let Some(me) = m.get().and_then(|w| w.upgrade()) {
                    me.choose(c);
                }
            },
            false,
        );
        root.append(&seg.root);
        let graph = FrameTimeGraph::new();
        let over = gtk::Overlay::builder().child(&graph.area).vexpand(true).margin_top(1).build();
        let avg = gtk::Label::builder().css_classes(["ft-cap"]).halign(gtk::Align::Start).valign(gtk::Align::Start).can_target(false).build();
        let low = gtk::Label::builder().css_classes(["ft-cap"]).halign(gtk::Align::End).valign(gtk::Align::Start).can_target(false).build();
        over.add_overlay(&avg);
        over.add_overlay(&low);
        root.append(&over);
        let module = Rc::new(Self { root, app, live, cap, install_btn, seg, graph, avg, low, frames: RefCell::default(), poll: RefCell::default(), busy: Cell::new(false) });
        let _ = me.set(Rc::downgrade(&module));
        let w = Rc::downgrade(&module);
        module.install_btn.connect_clicked(move |_| {
            if let Some(m) = w.upgrade() {
                m.install();
            }
        });
        let w = Rc::downgrade(&module);
        module.root.connect_map(move |_| {
            if let Some(m) = w.upgrade() {
                m.update();
                m.start();
            }
        });
        let w = Rc::downgrade(&module);
        module.root.connect_unmap(move |_| {
            if let Some(m) = w.upgrade() {
                m.stop();
            }
        });
        module
    }

    /// Every button that can be pressed (the Game Bar's controller reaches them).
    pub fn buttons(&self) -> Vec<gtk::Button> {
        let mut out: Vec<gtk::Button> = choices().iter().filter_map(|c| self.seg.buttons.get(c).cloned()).collect();
        out.push(self.install_btn.clone());
        out
    }

    fn installing(&self) -> bool {
        self.cap.label() == tr!("Installing…")
    }

    pub fn update(&self) {
        let b = backend();
        let ok = b.as_ref().is_some_and(|b| (b.installed)());
        self.seg.root.set_sensitive(ok);
        self.install_btn.set_visible(b.is_some() && !ok && !self.installing());
        self.cap.set_visible((ok && self.live.label().is_empty()) || self.installing());
        let cur = b.as_ref().map(|b| (b.get)()).unwrap_or_else(|| "off".into());
        self.seg.select(&cur);
        let hz = refresh_hz();
        if let Some(max) = self.seg.buttons.get("max") {
            let tip = (hz > 0.0).then(|| fmt(&tr!("The display's rate ({n} FPS)"), &[("n", &(hz.round() as i64).to_string())]));
            max.set_tooltip_text(tip.as_deref());
        }
        let lim = b.as_ref().map(|b| (b.resolve)(&cur, hz)).unwrap_or_else(|| "off".into());
        self.graph.state.borrow_mut().target = lim.parse::<u32>().ok().filter(|n| *n > 0).map(|n| 1000.0 / n as f64);
        self.graph.area.queue_draw();
    }

    // -- the frame-time graph: read only while Control Center shows it --------------------------
    fn start(self: &Rc<Self>) {
        if self.poll.borrow().is_some() {
            return;
        }
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local(Duration::from_millis(POLL_MS), move || match w.upgrade() {
            Some(m) => {
                m.tick();
                glib::ControlFlow::Continue
            }
            None => glib::ControlFlow::Break,
        });
        self.poll.replace(Some(id));
        self.tick();
    }

    fn stop(&self) {
        if let Some(id) = self.poll.take() {
            id.remove();
        }
    }

    fn tick(self: &Rc<Self>) {
        if self.busy.replace(true) {
            return;
        }
        let w = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            let r = gio::spawn_blocking(read_frames).await.ok().flatten();
            if let Some(m) = w.upgrade() {
                m.busy.set(false);
                m.show_frames(r.as_ref());
            }
        });
    }

    /// r: the plugin's answer (None: no plugin / no Wayfire).
    pub fn show_frames(&self, r: Option<&Value>) {
        let shown = self.frames.borrow_mut().read(r, app_name);
        match shown {
            Shown::Nothing { note, no_app } => {
                self.graph.set_times(&[], &note);
                self.app.set_label("");
                self.live.set_label("");
                self.avg.set_label("");
                self.low.set_label("");
                if no_app {
                    self.cap.set_visible(backend().is_some_and(|b| (b.installed)()));
                }
            }
            Shown::Live { new_app, live, avg, low } => {
                if let Some(name) = new_app {
                    self.app.set_label(&name);
                }
                self.graph.set_times(&self.frames.borrow().history, "");
                self.live.set_label(&live);
                self.avg.set_label(&avg);
                self.low.set_label(&low);
                self.cap.set_visible(false); // the live figure says it
            }
        }
    }

    pub fn choose(&self, c: &str) {
        if let Some(b) = backend() {
            (b.set)(c, refresh_hz());
        }
        self.update();
    }

    fn install(self: &Rc<Self>) {
        let Some(b) = backend() else { return };
        self.cap.set_label(&tr!("Installing…"));
        self.update();
        let w = Rc::downgrade(self);
        (b.install)(Box::new(move |err: Option<String>| {
            let Some(m) = w.upgrade() else { return };
            m.cap.set_label(&if err.is_none() { tr!("games") } else { tr!("couldn't install") });
            m.cap.set_tooltip_text(Some(&err.unwrap_or_else(|| tr!("Steam picks it up the next time it opens"))));
            m.update();
        }));
    }
}

/// Control Center's module (made once per process: it then lives as long
/// as the process, held by its own box). Keep the Rc from FpsModule::new
/// to drop one.
pub fn module() -> gtk::Widget {
    let m = FpsModule::new();
    let root = m.root.clone();
    // SAFETY: read back nowhere; only kept alive with the box
    unsafe { root.set_data("sonata-fps-module", m) };
    root.upcast()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 0.01
    }

    #[test]
    fn summary_and_the_slowest_frames() {
        let mut t = vec![16.7; 99];
        t.push(50.0);
        let (avg, low) = summary(&t);
        assert!(close(avg, 16.7 * 0.99 + 0.5));
        assert_eq!(low, 20); // the slowest 1 %: 50 ms
    }

    /// test_graph_is_slower (Vini: frame by frame it ran by too fast): one
    /// point per read, the average of the frames since the last; half a
    /// minute shown; a new app in front starts its own graph.
    #[test]
    fn test_graph_is_slower() {
        let _env = crate::testenv::env();
        assert_eq!(SHOWN as u64 * POLL_MS, 30000);
        let mut t = vec![100.0; 10];
        t.extend([10.0; 25]);
        assert!(close(recent_average(&t), 10.0)); // only the newest 250 ms
        assert!(close(recent_average(&[16.0, 18.0]), 17.0));
        let mut f = Frames::default();
        let up = |a: &str| a.to_uppercase();
        for _ in 0..SHOWN + 5 {
            f.read(Some(&json!({"fps": 60, "app-id": "game", "frametimes": vec![16.7; 60]})), up);
        }
        assert_eq!(f.history.len(), SHOWN);
        let s = f.read(Some(&json!({"fps": 60, "app-id": "other", "frametimes": vec![16.7; 60]})), up);
        assert_eq!(f.history.len(), 1);
        assert!(matches!(s, Shown::Live { new_app: Some(ref n), .. } if n == "OTHER"));
    }

    #[test]
    fn a_read_shows_the_app_its_fps_and_its_1_percent_low() {
        let _env = crate::testenv::env();
        let mut f = Frames::default();
        let s = f.read(Some(&json!({"fps": 58, "app-id": "steam_app_1", "frametimes": [16.6, 16.8, 33.0, 16.7]})), |_| "Hades".into());
        assert_eq!(s, Shown::Live { new_app: Some("Hades".into()), live: "58 fps".into(), avg: "20.8 ms".into(), low: "1% low 30 fps".into() });
        assert_eq!(f.history.len(), 1); // one point per read
        let s = f.read(Some(&json!({"fps": 0, "app-id": "", "frametimes": []})), |_| String::new());
        assert_eq!(s, Shown::Nothing { note: "No game or full-screen app".into(), no_app: true });
        // an older plugin: no frame times
        let s = f.read(Some(&json!({"fps": 60, "app-id": "x"})), |_| String::new());
        assert!(matches!(s, Shown::Nothing { ref note, .. } if note.contains("install.sh")));
    }

    /// test_any_reply_is_safe: whatever the plugin answers, never a panic.
    #[test]
    fn test_any_reply_is_safe() {
        let _env = crate::testenv::env();
        let mut f = Frames::default();
        for reply in [json!(null), json!([]), json!(["x"]), json!({"error": "no"}), json!({"frametimes": ["a", 0, -1], "app-id": "g"}), json!({"frametimes": [16.0], "app-id": "g"}), json!({"frametimes": "x", "app-id": 3})] {
            f.read(Some(&reply), |a| a.into());
        }
        let s = f.read(Some(&json!(["not", "a", "dict"])), |a| a.into());
        assert_eq!(s, Shown::Nothing { note: "Update Sonata's Wayfire plugin (./install.sh)".into(), no_app: false });
        assert_eq!(f.read(None, |a| a.into()), Shown::Nothing { note: String::new(), no_app: false });
    }

    #[test]
    fn the_graphs_top() {
        assert!(close(scale(&[], None), 33.4)); // twice 60 fps
        assert!(close(scale(&[10.0], Some(1000.0 / 30.0)), 66.67));
        assert!(close(scale(&[50.0], None), 57.5)); // a spike: more room
        assert!(close(scale(&[500.0], None), 100.0)); // never past 100 ms
    }
}
