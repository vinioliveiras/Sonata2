//! Fullscreen first (sonata2/gamemode.py): while a fullscreen window (a
//! game, a video) has the focus, Sonata steps back -- its periodic work
//! pauses and, when Feral GameMode is installed, the fullscreen app is
//! registered with it. The menu bar process watches Wayfire and publishes
//! the state in $XDG_RUNTIME_DIR/sonata2-fullscreen ("1"/"0"); every
//! Sonata process can follow it.
//!
//! Automatic energy mode: while any window is full screen, "balanced" is
//! raised to "performance" and lowered back after; a mode the user picked
//! is never touched. And while a full-screen game fills the graphics card,
//! blur and window animations are turned off in the running session.
//!
//! Not ported: the menu bar's `Watcher` (it needs the Wayfire event
//! stream, which sonata-backend's wfipc doesn't follow yet).

use serde_json::{Map, Value};
use std::path::PathBuf;

pub fn state() -> PathBuf {
    crate::util::runtime_dir().join("sonata2-fullscreen")
}

/// Set while Automatic energy mode was raised to High Performance for a
/// fullscreen window (kept across a menu bar restart).
pub fn boost_path() -> PathBuf {
    crate::util::runtime_dir().join("sonata2-power-boost")
}

pub fn light_path() -> PathBuf {
    crate::util::runtime_dir().join("sonata2-light-effects.json")
}

pub const GAMEMODE: (&str, &str, &str) = ("com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode");

/// True while fullscreen has the focus.
pub fn active() -> bool {
    std::fs::read(state()).map(|b| b.first() == Some(&b'1')).unwrap_or(false)
}

/// callback(active) whenever it changes. Keep the monitor.
pub fn watch<F: Fn(bool) + 'static>(callback: F) -> Option<gio::FileMonitor> {
    use gio::prelude::*;
    let mon = gio::File::for_path(state()).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE).ok()?;
    let last = std::cell::Cell::new(active());
    mon.connect_changed(move |_, _, _, _| {
        let v = active();
        if v != last.get() {
            last.set(v);
            callback(v);
        }
    });
    Some(mon)
}

/// Automatic is running at High Performance right now.
pub fn boosted() -> bool {
    boost_path().exists()
}

pub fn set_boosted(on: bool) {
    let p = boost_path();
    if on {
        let _ = std::fs::write(&p, "");
    } else if p.exists() {
        let _ = std::fs::remove_file(&p);
    }
}

/// Write the state the other processes follow (the menu bar's watcher).
pub fn write_state(on: bool) {
    let _ = sonata_config::atomic_write(&state(), if on { b"1" } else { b"0" }, false);
}

/// GameMode's priority for the fullscreen app (nothing without gamemoded).
pub fn register_game(pid: i32, register: bool) {
    use glib::variant::ToVariant;
    if pid <= 0 {
        return;
    }
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
    let flags = if register { gio::DBusCallFlags::NONE } else { gio::DBusCallFlags::NO_AUTO_START };
    let (name, path, iface) = GAMEMODE;
    let _ = bus.call_sync(Some(name), path, iface, if register { "RegisterGame" } else { "UnregisterGame" }, Some(&(pid,).to_variant()), Some(glib::VariantTy::new("(i)").unwrap()), flags, 1000, gio::Cancellable::NONE);
}

/// run(work, then): `work` off the main loop, `then` with its result.
type Runner = Box<dyn Fn(Box<dyn FnOnce() -> Option<String> + Send>, Box<dyn FnOnce(Option<String>)>)>;

/// High Performance while anything is full screen, in Automatic mode.
pub struct PowerBoost {
    state: std::rc::Rc<PowerState>,
}

struct PowerState {
    get: std::sync::Arc<dyn Fn() -> Option<String> + Send + Sync>,
    set: std::sync::Arc<dyn Fn(&str) -> bool + Send + Sync>,
    run: Runner,
    full: std::cell::Cell<bool>,
}

fn run_async(work: Box<dyn FnOnce() -> Option<String> + Send>, then: Box<dyn FnOnce(Option<String>)>) {
    let ctx = glib::MainContext::default();
    let handle = std::thread::spawn(work);
    ctx.spawn_local(async move {
        let r = gio::spawn_blocking(move || handle.join().ok().flatten()).await.ok().flatten();
        then(r);
    });
}

impl PowerBoost {
    pub fn new() -> Self {
        Self::with(
            std::sync::Arc::new(sonata_backend::system::power_profile_fast),
            std::sync::Arc::new(sonata_backend::system::set_power_profile),
            Box::new(run_async),
        )
    }

    pub fn with(get: std::sync::Arc<dyn Fn() -> Option<String> + Send + Sync>, set: std::sync::Arc<dyn Fn(&str) -> bool + Send + Sync>, run: Runner) -> Self {
        PowerBoost { state: std::rc::Rc::new(PowerState { get, set, run, full: std::cell::Cell::new(false) }) }
    }

    pub fn full(&self) -> bool {
        self.state.full.get()
    }

    pub fn set_full(&self, on: bool) {
        self.state.full.set(on);
    }

    pub fn update(&self, any_full: bool) {
        let s = &self.state;
        if any_full == s.full.get() {
            return;
        }
        s.full.set(any_full);
        let get = s.get.clone();
        let st = self.state.clone();
        if any_full && !boosted() {
            (s.run)(Box::new(move || get()), Box::new(move |cur| {
                if cur.as_deref() == Some("balanced") && st.full.get() {
                    // Automatic only, still full screen
                    set_boosted(true);
                    let set = st.set.clone();
                    (st.run)(Box::new(move || set("performance").then(String::new)), Box::new(|_| {}));
                }
            }));
        } else if !any_full && boosted() {
            (s.run)(Box::new(move || get()), Box::new(move |cur| {
                set_boosted(false);
                if cur.as_deref() == Some("performance") && !st.full.get() {
                    // the user didn't pick another mode
                    let set = st.set.clone();
                    (st.run)(Box::new(move || set("balanced").then(String::new)), Box::new(|_| {}));
                }
            }));
        }
    }
}

impl Default for PowerBoost {
    fn default() -> Self {
        Self::new()
    }
}

// -- lighter effects while a game fills the graphics card ----------------------------------------
// Blur and window animations each need graphics memory of their own; with a
// game holding nearly all of it they only failed (skipped frames) and left
// other apps less. While a full-screen game plays and the card is >= 90 %
// full, they're turned off in the running session; back when the game leaves
// full screen. Only the session's own copy of the config is changed (made
// again at login), so a crash can't keep them off.
pub const LIGHT_AT: f64 = 0.90;
pub const LIGHT_OPTIONS: [(&str, &str, &str); 4] = [
    ("blur", "blur_by_default", "app_id is \"sonata2-no-blur\""),
    ("animate", "open_animation", "none"),
    ("animate", "close_animation", "none"),
    ("animate", "minimize_animation", "none"),
];

/// Settings > Displays > Graphics (on by default).
pub fn light_effects_wanted() -> bool {
    let cfg = sonata_config::stored(crate::gpu::NAME);
    cfg.get("light_effects").map(|v| crate::util::truthy(Some(v))).unwrap_or(true)
}

/// get/set: wfconfig::get / runtime_set (tests pass fakes).
pub struct LightEffects {
    pub get: Box<dyn Fn(&str, &str) -> String>,
    pub set: Box<dyn Fn(&str, &str, Option<&str>) -> bool>,
    pub wanted: Box<dyn Fn() -> bool>,
    pub path: PathBuf,
    full: bool,
    ratio: f64,
}

impl LightEffects {
    pub fn new() -> Self {
        Self::with(
            Box::new(|s, k| sonata_backend::wfconfig::get(s, k, "")),
            Box::new(sonata_backend::wfconfig::runtime_set),
            Box::new(light_effects_wanted),
            light_path(),
        )
    }

    pub fn with(get: Box<dyn Fn(&str, &str) -> String>, set: Box<dyn Fn(&str, &str, Option<&str>) -> bool>, wanted: Box<dyn Fn() -> bool>, path: PathBuf) -> Self {
        LightEffects { get, set, wanted, path, full: false, ratio: 0.0 }
    }

    pub fn on(&self) -> bool {
        self.path.exists()
    }

    pub fn update(&mut self, full: Option<bool>, used: Option<f64>, total: Option<f64>) {
        if let Some(f) = full {
            self.full = f;
        }
        if let (Some(u), Some(t)) = (used, total) {
            if t != 0.0 {
                self.ratio = u / t;
            }
        }
        if !self.full {
            self.restore(); // the game left full screen
        } else if self.ratio >= LIGHT_AT && !self.on() && (self.wanted)() {
            self.lighten();
        }
    }

    fn lighten(&self) {
        let mut saved = Map::new();
        for (s, k, _) in LIGHT_OPTIONS {
            saved.insert(format!("{s}/{k}"), Value::String((self.get)(s, k)));
        }
        // a temp file renamed over it: killed mid-write, a half (empty) save
        // read back as unreadable and the effects never came back
        if sonata_config::atomic_write(&self.path, sonata_config::to_python_json(&Value::Object(saved)).as_bytes(), false).is_err() {
            return;
        }
        for (s, k, v) in LIGHT_OPTIONS {
            (self.set)(s, k, Some(v));
        }
    }

    fn restore(&self) {
        if !self.on() {
            return;
        }
        let saved: Map<String, Value> = std::fs::read(&self.path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
        let mut ok = true;
        for (s, k, _) in LIGHT_OPTIONS {
            let Some(v) = saved.get(&format!("{s}/{k}")) else { continue }; // unreadable save: leave the session copy
            // "" = the config never set it: drop our value so Wayfire's default is back
            let v = v.as_str().filter(|v| !v.is_empty());
            if !(self.set)(s, k, v) {
                ok = false;
            }
        }
        if !ok {
            // not written (no session copy, a failed write): the save stays, so
            // the next update tries again -- deleted, the animations stayed off
            return;
        }
        let _ = std::fs::remove_file(&self.path);
    }
}

impl Default for LightEffects {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    #[test]
    fn light_effects_off_while_a_game_fills_the_card_and_back_after() {
        let dir = tempfile::tempdir().unwrap();
        let cfg: Rc<RefCell<std::collections::HashMap<String, String>>> = Rc::default();
        cfg.borrow_mut().insert("animate/open_animation".into(), "zoom".into());
        let ok = Rc::new(RefCell::new(true));
        let (g, s, o) = (cfg.clone(), cfg.clone(), ok.clone());
        let mut le = LightEffects::with(
            Box::new(move |a, b| g.borrow().get(&format!("{a}/{b}")).cloned().unwrap_or_default()),
            Box::new(move |a, b, v| {
                if !*o.borrow() {
                    return false;
                }
                let k = format!("{a}/{b}");
                match v {
                    Some(v) => s.borrow_mut().insert(k, v.into()),
                    None => s.borrow_mut().remove(&k),
                };
                true
            }),
            Box::new(|| true),
            dir.path().join("light.json"),
        );
        le.update(Some(true), Some(50.0), Some(100.0));
        assert!(!le.on());
        le.update(None, Some(95.0), Some(100.0));
        assert!(le.on());
        assert_eq!(cfg.borrow()["animate/open_animation"], "none");
        // a failed write keeps the save (tried again)
        *ok.borrow_mut() = false;
        le.update(Some(false), None, None);
        assert!(le.on());
        *ok.borrow_mut() = true;
        le.update(Some(false), None, None);
        assert!(!le.on());
        assert_eq!(cfg.borrow()["animate/open_animation"], "zoom");
        assert!(!cfg.borrow().contains_key("blur/blur_by_default"));
    }

    #[test]
    fn power_boost_raises_automatic_only() {
        let _l = crate::util::test_lock();
        set_boosted(false);
        let cur = std::sync::Arc::new(std::sync::Mutex::new("balanced".to_string()));
        let sets = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let (c, s) = (cur.clone(), sets.clone());
        let pb = PowerBoost::with(
            std::sync::Arc::new(move || Some(c.lock().unwrap().clone())),
            std::sync::Arc::new(move |p| {
                s.lock().unwrap().push(p.into());
                true
            }),
            Box::new(|work, then| then(work())),
        );
        pb.update(true);
        assert!(boosted());
        assert_eq!(*sets.lock().unwrap(), ["performance"]);
        *cur.lock().unwrap() = "performance".into();
        pb.update(false);
        assert!(!boosted());
        assert_eq!(*sets.lock().unwrap(), ["performance", "balanced"]);
        // a mode the user picked is never touched
        *cur.lock().unwrap() = "power-saver".into();
        pb.update(true);
        assert!(!boosted());
        assert_eq!(sets.lock().unwrap().len(), 2);
    }
}
