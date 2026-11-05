//! Several displays (macOS; shell/monitors.py): the wallpaper and a menu
//! bar on every display; the Dock, desktop icons and notifications on the
//! main display. The main display is Settings > Displays' choice
//! (displays.json "main" = connector), else an external monitor when one is
//! connected, else the built-in panel (sonata_shell::layer::pick_main).
//!
//! ```text
//! monitors::each(create, destroy)   // a surface per display, hotplug included
//! monitors::main()                  // the main display
//! monitors::on_main_changed(cb)     // main display chosen elsewhere / unplugged
//! ```
//!
//! Refresh rate: a display nobody picked a mode for runs at its highest
//! refresh rate (Wayfire `mode = highrr`, written once per connector).
//! Scale: one nobody picked a scale for gets auto_scale (its DPI; a 1080p
//! desktop = 1).

use gtk::gdk;
use gtk::prelude::*;
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::rc::Rc;

pub use sonata_shell::layer::connector;

/// per user, writable by them (install.sh --greeter)
pub const GREETER: &str = "/var/lib/sonata-greeter";
const BUILTIN: [&str; 3] = ["eDP", "LVDS", "DSI"];

pub fn list() -> Vec<gdk::Monitor> {
    sonata_shell::layer::monitors()
}

/// The main display (displays.json's choice first).
pub fn main() -> Option<gdk::Monitor> {
    sonata_shell::layer::main_monitor()
}

/// The main display preferring `want` (the login screen: the user's choice).
pub fn main_for(want: &str) -> Option<gdk::Monitor> {
    let ms = list();
    sonata_shell::layer::pick_main(&ms.iter().map(connector).collect::<Vec<_>>(), want).map(|i| ms[i].clone())
}

/// The login screen runs before the session, as another user: it can't
/// read ~/.config. Copy the main display there, like the wallpaper; nothing
/// to do without the login screen's folder. Not on trial: the Python
/// session shares the same values already.
pub fn share_with_login_screen() {
    if sonata_config::side_by_side() {
        return;
    }
    share_in(std::path::Path::new(GREETER));
}

fn share_in(base: &std::path::Path) {
    let folder = base.join(glib::user_name());
    let Ok(c) = std::ffi::CString::new(folder.as_os_str().as_encoded_bytes()) else { return };
    if unsafe { libc::access(c.as_ptr(), libc::W_OK) } != 0 {
        return;
    }
    let main = sonata_config::load("displays", &json!({"main": ""})).get("main").cloned().unwrap_or(json!(""));
    // the login screen goes dark too
    let data = json!({"main": main, "off_after": sonata_shell::lockdisplay::display_seconds()});
    let _ = sonata_config::atomic_write(&folder.join("displays.json"), sonata_config::to_python_json(&data).as_bytes(), false);
}

fn login_shared(base: &std::path::Path, users: &[String], key: &str, ok: impl Fn(&Value) -> bool) -> Option<Value> {
    users.iter().find_map(|name| {
        let text = std::fs::read_to_string(base.join(name).join("displays.json")).ok()?;
        let v = serde_json::from_str::<Value>(&text).ok()?.get(key).cloned()?;
        ok(&v).then_some(v)
    })
}

/// The main display a user chose (the first of `users` that has one), for the login screen.
pub fn login_main(users: &[String]) -> String {
    login_main_in(std::path::Path::new(GREETER), users)
}

fn login_main_in(base: &std::path::Path, users: &[String]) -> String {
    login_shared(base, users, "main", |v| v.as_str().is_some_and(|s| !s.is_empty())).and_then(|v| v.as_str().map(String::from)).unwrap_or_default()
}

/// Seconds before the login screen goes dark: what the user chose in
/// Settings (<= 0 never), else Sonata's default.
pub fn login_off_after(users: &[String]) -> i64 {
    login_off_after_in(std::path::Path::new(GREETER), users)
}

fn login_off_after_in(base: &std::path::Path, users: &[String]) -> i64 {
    login_shared(base, users, "off_after", |v| v.is_i64()).and_then(|v| v.as_i64()).unwrap_or(sonata_shell::lockdisplay::DISPLAY_DEFAULT_S)
}

type Create<T> = Box<dyn Fn(&gdk::Monitor) -> T>;
type Destroy<T> = Box<dyn Fn(T)>;

/// create(monitor) -> surface for every display now and when one is
/// plugged in; destroy(surface) when its display goes away. rebuild()
/// makes them all again (e.g. the main display changed).
pub struct Surfaces<T: 'static> {
    create: Create<T>,
    destroy: Destroy<T>,
    surfaces: RefCell<Vec<(gdk::Monitor, T)>>,
    handler: RefCell<Option<(gio::ListModel, glib::SignalHandlerId)>>,
}

impl<T: 'static> Surfaces<T> {
    pub fn sync(&self) {
        let ms = list();
        let gone: Vec<T> = {
            let mut s = self.surfaces.borrow_mut();
            let (keep, gone): (Vec<_>, Vec<_>) = s.drain(..).partition(|(m, _)| ms.contains(m));
            *s = keep;
            gone.into_iter().map(|(_, t)| t).collect()
        };
        gone.into_iter().for_each(|t| (self.destroy)(t));
        for m in ms {
            if !self.surfaces.borrow().iter().any(|(k, _)| *k == m) {
                let t = (self.create)(&m);
                self.surfaces.borrow_mut().push((m, t));
            }
        }
    }

    pub fn rebuild(&self) {
        let all: Vec<T> = self.surfaces.borrow_mut().drain(..).map(|(_, t)| t).collect();
        all.into_iter().for_each(|t| (self.destroy)(t));
        self.sync();
    }

    /// The surfaces now, by display.
    pub fn with<R>(&self, f: impl FnOnce(&[(gdk::Monitor, T)]) -> R) -> R {
        f(&self.surfaces.borrow())
    }
}

impl<T: 'static> Drop for Surfaces<T> {
    fn drop(&mut self) {
        if let Some((model, id)) = self.handler.borrow_mut().take() {
            model.disconnect(id);
        }
    }
}

pub fn each<T: 'static>(create: impl Fn(&gdk::Monitor) -> T + 'static, destroy: impl Fn(T) + 'static) -> Rc<Surfaces<T>> {
    let s = Rc::new(Surfaces { create: Box::new(create), destroy: Box::new(destroy), surfaces: RefCell::default(), handler: RefCell::default() });
    s.sync();
    if let Some(d) = gdk::Display::default() {
        let model = d.monitors();
        let w = Rc::downgrade(&s);
        // connector names arrive a moment after items-changed on some compositors
        let id = model.connect_items_changed(move |_, _, _, _| {
            let w = w.clone();
            glib::timeout_add_local_once(std::time::Duration::from_millis(200), move || {
                if let Some(s) = w.upgrade() {
                    s.sync();
                }
            });
        });
        *s.handler.borrow_mut() = Some((model, id));
    }
    s
}

/// Keeps on_main_changed's watches alive (dropped: no more calls).
pub struct MainWatch {
    _mons: Vec<gio::FileMonitor>,
    handler: Option<(gio::ListModel, glib::SignalHandlerId)>,
}

impl Drop for MainWatch {
    fn drop(&mut self) {
        if let Some((m, id)) = self.handler.take() {
            m.disconnect(id);
        }
    }
}

/// callback(main monitor) when displays.json or the display list change.
pub fn on_main_changed(callback: impl Fn(Option<gdk::Monitor>) + 'static) -> MainWatch {
    let state = Rc::new(RefCell::new(main()));
    let cb = Rc::new(callback);
    let check = {
        let (state, cb) = (state.clone(), cb.clone());
        Rc::new(move || {
            let m = main();
            if m != *state.borrow() {
                *state.borrow_mut() = m.clone();
                cb(m);
            }
        })
    };
    let c = check.clone();
    let mons = sonata_config::watch("displays", move || c());
    let handler = gdk::Display::default().map(|d| {
        let model = d.monitors();
        let c = check.clone();
        let id = model.connect_items_changed(move |_, _, _, _| {
            let c = c.clone();
            glib::timeout_add_local_once(std::time::Duration::from_millis(250), move || c());
        });
        (model, id)
    });
    MainWatch { _mons: mons, handler }
}

// Sizes look right on a 1080p desktop monitor (Vini; ~92 DPI at 24"). Other
// displays get the scale that keeps that size: from their DPI (the size the
// display reports), else their resolution. Laptops' panels are seen closer:
// a higher reference. Never under 1 (a downscaled picture is blurry and
// costs the GPU more), quarter steps.
pub const DESKTOP_DPI: f64 = 96.0;
pub const BUILTIN_DPI: f64 = 125.0;
/// resolution fallback: the shorter side
pub const SCALE_REFERENCE: f64 = 1080.0;
pub const SCALE_RANGE: (f64, f64) = (1.0, 3.0);

/// Pixels per inch from the size a display reports, or None when that
/// size is missing or made up (projectors and TVs: 0, tiny, huge, or not
/// the picture's shape).
pub fn physical_dpi(w_px: i32, h_px: i32, w_mm: i32, h_mm: i32) -> Option<f64> {
    if w_px.min(h_px).min(w_mm).min(h_mm) <= 0 {
        return None;
    }
    let long_mm = w_mm.max(h_mm) as f64;
    if !(120.0..=1500.0).contains(&long_mm) {
        return None;
    }
    let px_ratio = w_px.max(h_px) as f64 / w_px.min(h_px) as f64;
    let mm_ratio = long_mm / w_mm.min(h_mm) as f64;
    if (px_ratio / mm_ratio - 1.0).abs() > 0.1 {
        return None;
    }
    let dpi = w_px.max(h_px) as f64 / (long_mm / 25.4);
    (50.0..=500.0).contains(&dpi).then_some(dpi) // (centimetres reported as millimetres...)
}

/// The scale for a display of width x height pixels (and w_mm x h_mm).
pub fn auto_scale(width: i32, height: i32, w_mm: i32, h_mm: i32, builtin: bool) -> f64 {
    let s = if let Some(dpi) = physical_dpi(width, height, w_mm, h_mm) {
        dpi / if builtin { BUILTIN_DPI } else { DESKTOP_DPI }
    } else if width.min(height) > 0 {
        width.min(height) as f64 / SCALE_REFERENCE
    } else {
        return 1.0;
    };
    // (Python's round(): halves to even)
    ((s * 4.0).round_ties_even() / 4.0).clamp(SCALE_RANGE.0, SCALE_RANGE.1)
}

/// One display as ensure_refresh sees it.
#[derive(Clone, Debug)]
pub struct Display {
    pub connector: String,
    /// in pixels (logical size x scale)
    pub width: i32,
    pub height: i32,
    pub w_mm: i32,
    pub h_mm: i32,
    /// its logical width (<= 0: not laid out yet)
    pub logical_width: i32,
}

/// Logical -> pixels: the monitor's own fractional scale (GTK 4.14's
/// "scale" property, asked by name: the bindings are built for 4.12), else
/// the whole factor. The whole factor alone rounds 1.25 up to 2: the
/// display seemed twice its size, got a bigger scale, then a smaller one
/// at the next look, and so on.
fn monitor_scale(m: &gdk::Monitor) -> f64 {
    let fractional = m.find_property("scale").filter(|p| p.value_type() == f64::static_type()).map(|_| m.property::<f64>("scale"));
    pixel_factor(fractional, m.scale_factor())
}

fn pixel_factor(fractional: Option<f64>, whole: i32) -> f64 {
    fractional.filter(|k| k.is_finite() && *k > 0.0).unwrap_or(whole.max(1) as f64)
}

fn display_of(m: &gdk::Monitor) -> Display {
    let g = m.geometry();
    let k = monitor_scale(m);
    Display { connector: connector(m), width: (g.width() as f64 * k).round() as i32, height: (g.height() as f64 * k).round() as i32, w_mm: m.width_mm(), h_mm: m.height_mm(), logical_width: g.width() }
}

/// str(float) in Python: "1.0", "1.75".
pub fn py_float(v: f64) -> String {
    if v.fract() == 0.0 && v.abs() < 1e16 {
        format!("{v:.1}")
    } else {
        format!("{v}")
    }
}

/// What ensure_refresh changes: Wayfire's (section, key, value) writes and
/// the automatic scales after them (display-scale.json "auto"), or None when
/// those stay. get(section, key) reads Wayfire's config.
pub fn refresh_plan(displays: &[Display], get: &dyn Fn(&str, &str) -> String, auto: &Map<String, Value>) -> (Vec<(String, String, String)>, Option<Map<String, Value>>) {
    let mut sets = Vec::new();
    let mut auto = auto.clone();
    let mut changed = false;
    for d in displays {
        let c = &d.connector;
        if c.is_empty() {
            continue;
        }
        let section = format!("output:{c}");
        if get(&section, "mode").is_empty() {
            sets.push((section.clone(), "mode".into(), "highrr".into()));
        }
        let cur = get(&section, "scale");
        let mine = cur.is_empty() || auto.get(c).and_then(Value::as_f64).zip(cur.trim().parse::<f64>().ok()).is_some_and(|(a, cur)| (cur - a).abs() < 1e-3);
        if !mine || d.logical_width <= 0 {
            continue;
        }
        let want = auto_scale(d.width, d.height, d.w_mm, d.h_mm, BUILTIN.iter().any(|b| c.starts_with(b)));
        if cur.is_empty() || cur.trim().parse::<f64>().map_or(true, |v| (v - want).abs() > 1e-3) {
            sets.push((section, "scale".into(), py_float(want)));
        }
        if auto.get(c).and_then(Value::as_f64) != Some(want) {
            auto.insert(c.clone(), json!(want));
            changed = true;
        }
    }
    (sets, changed.then_some(auto))
}

/// Displays without a mode of their own: highest refresh rate; without a
/// scale of their own: auto_scale, kept up to date (resolution changed,
/// display plugged in) until one is picked in Settings > Displays -- what
/// was set automatically is remembered (display-scale.json), anything else
/// is the user's. Runs in the menu bar (always there). Never cap the rate
/// to spare the GPU: some monitors glitch below their top rate (Vini's
/// external one does); a user's own mode or scale in Settings wins.
///
/// Not on trial: it writes Wayfire's config, which the Python menu bar keeps.
pub fn ensure_refresh() {
    if sonata_config::side_by_side() {
        return;
    }
    let pending: Rc<RefCell<Option<glib::SourceId>>> = Rc::default();
    let soon = {
        let pending = pending.clone();
        Rc::new(move || {
            // a burst of changes (a mode switch): once
            if let Some(id) = pending.borrow_mut().take() {
                sonata_ui::cancel(id);
            }
            let p = pending.clone();
            *pending.borrow_mut() = Some(glib::timeout_add_local_once(std::time::Duration::from_millis(300), move || {
                p.borrow_mut().take();
                apply_refresh();
            }));
        })
    };
    let watched: Rc<RefCell<Vec<gdk::Monitor>>> = Rc::default();
    let watch = {
        let soon = soon.clone();
        Rc::new(move || {
            for m in list() {
                if !watched.borrow().contains(&m) {
                    let s = soon.clone();
                    m.connect_geometry_notify(move |_| s()); // its resolution changed
                    watched.borrow_mut().push(m);
                }
            }
            soon();
        })
    };
    watch();
    if let Some(d) = gdk::Display::default() {
        // plugged in: watched too
        d.monitors().connect_items_changed(move |_, _, _, _| watch());
    }
}

fn apply_refresh() {
    let auto = sonata_config::load("display-scale", &json!({"auto": {}})).get("auto").and_then(Value::as_object).cloned().unwrap_or_default();
    let displays: Vec<Display> = list().iter().map(display_of).collect();
    let (sets, new_auto) = refresh_plan(&displays, &|s, k| sonata_backend::wfconfig::get(s, k, ""), &auto);
    for (s, k, v) in sets {
        sonata_backend::wfconfig::set(&s, &k, &v);
    }
    if let Some(a) = new_auto {
        let mut data = Map::new();
        data.insert("auto".into(), Value::Object(a));
        let _ = sonata_config::save("display-scale", &data);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(w: i32, h: i32, wmm: i32, hmm: i32) -> f64 {
        auto_scale(w, h, wmm, hmm, false)
    }

    /// Vini: sizes look right on a 1080p monitor; 4K was tiny.
    #[test]
    fn test_scale_by_dpi() {
        assert_eq!(a(1920, 1080, 531, 299), 1.0); // 24" 1080p: the reference
        assert_eq!(a(3840, 2160, 597, 336), 1.75); // 27" 4K
        assert_eq!(a(3840, 2160, 708, 398), 1.5); // 32" 4K
        assert_eq!(a(3840, 2160, 527, 296), 2.0); // 24" 4K
        assert_eq!(a(2560, 1440, 597, 336), 1.25); // 27" 1440p
        assert_eq!(a(1920, 1080, 598, 336), 1.0); // 27" 1080p: never under 1
        assert_eq!(auto_scale(1920, 1080, 309, 174, true), 1.25); // 14" laptop, seen closer
        assert_eq!(auto_scale(2560, 1600, 286, 179, true), 1.75); // 13" 2560x1600 laptop (227 DPI)
        assert_eq!(auto_scale(1366, 768, 344, 194, true), 1.0);
    }

    #[test]
    fn made_up_sizes_use_the_resolution() {
        for (w, h) in [(0, 0), (160, 90), (1600, 900), (500, 500)] {
            // none, cm as mm, a TV, wrong shape
            assert_eq!(physical_dpi(3840, 2160, w, h), None, "{w}x{h}");
            assert_eq!(a(3840, 2160, w, h), 2.0);
        }
        assert_eq!(a(1366, 768, 0, 0), 1.0); // small: never under 1
        assert_eq!(a(1080, 1920, 0, 0), 1.0); // portrait 1080p
        assert_eq!(a(7680, 4320, 0, 0), 3.0);
    }

    fn mon(c: &str, w: i32, h: i32, wmm: i32, hmm: i32) -> Display {
        Display { connector: c.into(), width: w, height: h, w_mm: wmm, h_mm: hmm, logical_width: w }
    }

    fn run(saved: &RefCell<std::collections::HashMap<(String, String), String>>, auto: &mut Map<String, Value>, ds: &[Display]) -> Vec<(String, String, String)> {
        let (sets, new) = refresh_plan(ds, &|s, k| saved.borrow().get(&(s.to_string(), k.to_string())).cloned().unwrap_or_default(), auto);
        for (s, k, v) in &sets {
            saved.borrow_mut().insert((s.clone(), k.clone()), v.clone());
        }
        if let Some(n) = new {
            *auto = n;
        }
        sets.into_iter().filter(|x| x.1 == "scale").collect()
    }

    /// A display nobody picked a scale for gets one from its DPI, kept up to
    /// date until one is picked.
    #[test]
    fn scale_set_and_followed_until_picked() {
        let saved = RefCell::new(std::collections::HashMap::new());
        let put = |s: &str, k: &str, v: &str| saved.borrow_mut().insert((s.into(), k.into()), v.into());
        put("output:HDMI-A-1", "mode", "highrr");
        put("output:DP-1", "mode", "highrr");
        put("output:DP-1", "scale", "1.5");
        let mut auto = Map::new();
        let sets = run(&saved, &mut auto, &[mon("HDMI-A-1", 3840, 2160, 597, 336), mon("DP-1", 1920, 1080, 531, 299)]);
        assert_eq!(sets, [("output:HDMI-A-1".into(), "scale".into(), "1.75".into())]); // DP-1: the user's 150 % stays
        // the resolution goes down to 1080p on the same monitor: the automatic scale follows
        let sets = run(&saved, &mut auto, &[mon("HDMI-A-1", 1920, 1080, 597, 336)]);
        assert_eq!(sets, [("output:HDMI-A-1".into(), "scale".into(), "1.0".into())]);
        // picked in Settings: never changed again
        put("output:HDMI-A-1", "scale", "1.25");
        assert!(run(&saved, &mut auto, &[mon("HDMI-A-1", 3840, 2160, 597, 336)]).is_empty());
    }

    /// The Rust session: a 27" 1440p monitor's automatic scale went back
    /// and forth (125 %, then 175 %, then 125 %...): at 1.25 GTK's whole
    /// scale factor is 2, so the display looked twice its pixels. The
    /// monitor's fractional scale gives its real pixels, and the scale stays.
    #[test]
    fn test_fractional_scale_flipped_back_and_forth() {
        assert_eq!(pixel_factor(Some(1.25), 2), 1.25);
        assert_eq!(pixel_factor(None, 2), 2.0); // an older GTK: the whole factor
        assert_eq!(pixel_factor(Some(0.0), 1), 1.0);
        let saved = RefCell::new(std::collections::HashMap::new());
        saved.borrow_mut().insert(("output:DP-1".to_string(), "mode".to_string()), "highrr".to_string());
        let mut auto = Map::new();
        let sets = run(&saved, &mut auto, &[mon("DP-1", 2560, 1440, 597, 336)]);
        assert_eq!(sets, [("output:DP-1".into(), "scale".into(), "1.25".into())]);
        // now laid out at 1.25: 2048 x 1152 logical
        let k = pixel_factor(Some(1.25), 2);
        let laid = Display { connector: "DP-1".into(), width: (2048.0 * k) as i32, height: (1152.0 * k) as i32, w_mm: 597, h_mm: 336, logical_width: 2048 };
        assert!(run(&saved, &mut auto, &[laid]).is_empty());
    }

    #[test]
    fn highest_refresh_rate_once() {
        let (sets, _) = refresh_plan(&[mon("DP-1", 1920, 1080, 531, 299)], &|_, k| if k == "scale" { "1.0".into() } else { String::new() }, &Map::new());
        assert_eq!(sets, [("output:DP-1".into(), "mode".into(), "highrr".into())]);
        let (sets, _) = refresh_plan(&[mon("DP-1", 1920, 1080, 531, 299)], &|_, k| if k == "mode" { "1920x1080@60".into() } else { "1.0".into() }, &Map::new());
        assert!(sets.is_empty()); // the user's own mode and scale
    }

    #[test]
    fn login_screen_reads_what_was_shared() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("vini")).unwrap();
        std::fs::write(d.path().join("vini/displays.json"), r#"{"main": "HDMI-A-1", "off_after": 300}"#).unwrap();
        std::fs::create_dir_all(d.path().join("ana")).unwrap();
        std::fs::write(d.path().join("ana/displays.json"), r#"{"main": "", "off_after": true}"#).unwrap();
        let users = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(login_main_in(d.path(), &users(&["ana", "vini"])), "HDMI-A-1");
        assert_eq!(login_off_after_in(d.path(), &users(&["vini"])), 300);
        assert_eq!(login_off_after_in(d.path(), &users(&["ana"])), 600); // a bool isn't seconds
        assert_eq!(login_main_in(d.path(), &users(&["nobody"])), "");
    }

    #[test]
    fn shared_with_the_login_screen() {
        let e = crate::testenv::env();
        let base = e.dir.path().join("greeter");
        share_in(&base); // no folder of ours: nothing
        assert!(!base.exists());
        std::fs::create_dir_all(base.join(glib::user_name())).unwrap();
        sonata_config::update("display", &json!({"off_after": 120})).unwrap();
        share_in(&base);
        let v: Value = serde_json::from_str(&std::fs::read_to_string(base.join(glib::user_name()).join("displays.json")).unwrap()).unwrap();
        assert_eq!(v, json!({"main": "", "off_after": 120}));
    }
}
