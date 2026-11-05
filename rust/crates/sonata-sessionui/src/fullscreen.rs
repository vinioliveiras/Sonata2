//! Full screen like macOS, for every app and game (sonata2/fullscreen.py).
//!
//!   - Ctrl+Super+F (macOS Ctrl+Cmd+F) puts the focused window in full
//!     screen or takes it out (wayfire.ini runs the menu bar's
//!     "fullscreen" action: [`Rules::toggle`]).
//!   - Apps remember it: an app put in full screen that way opens in full
//!     screen next time, and one taken out of it stays windowed.
//!   - Games under Wine / Proton (Heroic, Lutris, Steam...) open in full
//!     screen by themselves when their window asks for about the whole
//!     screen, or is a fixed-size game window: many ask for it in a way
//!     Wayfire never sees as a full-screen request (a screen-sized window,
//!     or a size that doesn't match the display under Xwayland).
//!
//! Remembered in ~/.config/sonata2/fullscreen.json {"apps": {app_id: bool}}.
//! Lives in the menu bar process (it already talks to Wayfire).

use crate::wfevents::{self, is_toplevel, no_parent, str_or, Ipc, Later};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::{Rc, Weak};

pub const NAME: &str = "fullscreen";
/// asks for this much of the display: a game wanting the screen
pub const SCREEN_FRACTION: f64 = 0.9;
/// fixed-size windows at least this big are games
pub const MIN_GAME: (f64, f64) = (800.0, 600.0);
/// Wine sizes its window after mapping it
pub const RECHECK_MS: [u32; 3] = [300, 1500, 4000];

// Wine windows that aren't games: installers, launchers, tools. Short words
// only match as whole words (or a word's start: "unins000", "setup.exe"), so
// games called "Control", "Crash Bandicoot" or "Assassin's Creed Origins"
// still count; the unambiguous ones match anywhere ("UnityCrashHandler64").
pub const NOT_GAMES: [&str; 13] = ["explorer", "setup", "install", "unins", "report", "uplay", "origin", "rockstar", "steam.exe", "notepad", "control panel", "control.exe", "battle.net"];
pub const NOT_GAMES_ANYWHERE: [&str; 14] = ["winecfg", "regedit", "installer", "redist", "dxsetup", "launcher", "crashhandler", "crashreport", "crash reporter", "crashpad", "ubisoftconnect", "eadesktop", "galaxyclient", "webhelper"];

pub fn defaults() -> Value {
    json!({"apps": {}})
}

/// fullscreen.py's _NOT_GAMES: a NOT_GAMES word not inside another word
/// (nothing alphanumeric before it), then digits, "er", "s.exe" or nothing
/// before the next letter -- or a NOT_GAMES_ANYWHERE one anywhere. (The
/// regex crate has no look-behind: written out.)
pub fn not_a_game(name: &str) -> bool {
    if NOT_GAMES_ANYWHERE.iter().any(|w| name.contains(w)) {
        return true;
    }
    let letter_at = |s: &str| s.chars().next().is_some_and(|c| c.is_ascii_lowercase());
    NOT_GAMES.iter().any(|w| {
        name.match_indices(w).any(|(i, _)| {
            let before = name[..i].chars().next_back();
            if before.is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit()) {
                return false;
            }
            let rest = &name[i + w.len()..];
            // a digit or anything not a letter ends the word (\d* backs off to none)
            !letter_at(rest) || rest.strip_prefix("er").is_some_and(|r| !letter_at(r)) || rest.strip_prefix("s.exe").is_some_and(|r| !letter_at(r))
        })
    })
}

fn app(view: &Value) -> &str {
    view.get("app-id").and_then(Value::as_str).unwrap_or("").trim()
}

fn num(v: Option<&Value>) -> f64 {
    v.and_then(Value::as_f64).unwrap_or(0.0)
}

/// A window of a Windows program (Wine, Proton).
pub fn is_wine(view: &Value) -> bool {
    let a = app(view).to_lowercase();
    if a.ends_with(".exe") || a.starts_with("steam_app_") {
        return true;
    }
    let pid = view.get("pid").and_then(Value::as_i64).unwrap_or(0);
    if pid > 0 {
        if let Ok(exe) = std::fs::read_link(format!("/proc/{pid}/exe")) {
            let exe = exe.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            return exe.contains("wine") || exe.contains("proton");
        }
    }
    false
}

/// A Wine window that wants the screen (or a big fixed-size one).
pub fn looks_like_game(view: &Value, screen: (f64, f64)) -> bool {
    if !is_wine(view) || !no_parent(view) {
        return false;
    }
    if !is_toplevel(view) || str_or(view, "type", "toplevel") != Some("toplevel") {
        return false;
    }
    let title = view.get("title").and_then(Value::as_str).unwrap_or("");
    if not_a_game(&format!("{} {}", app(view), title).to_lowercase()) {
        return false;
    }
    let g = view.get("geometry");
    let (w, h) = (num(g.and_then(|g| g.get("width"))), num(g.and_then(|g| g.get("height"))));
    let (sw, sh) = screen;
    if sw != 0.0 && sh != 0.0 && (w >= sw * SCREEN_FRACTION || h >= sh * SCREEN_FRACTION) {
        return true;
    }
    let size = |k| view.get(k).filter(|v| crate::truthy(v)).cloned().unwrap_or_else(|| json!({}));
    let (lo, hi) = (size("min-size"), size("max-size"));
    let fixed = lo.get("width").is_some_and(crate::truthy) && lo == hi;
    fixed && w >= MIN_GAME.0 && h >= MIN_GAME.1
}

pub const LOG: &str = "windows.log";
pub const LOG_LINES: usize = 400;

/// Python's str() of a JSON value in an f-string.
fn py(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".into(),
        Some(Value::Bool(b)) => (if *b { "True" } else { "False" }).into(),
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
    }
}

/// Python's repr() of a string (or None).
fn py_repr(v: Option<&str>) -> String {
    match v {
        None => "None".into(),
        Some(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
    }
}

/// One line per window shown / closed (~/.cache/sonata2/windows.log, the
/// last LOG_LINES): where apps put their windows, for window bugs (Steam's
/// main window never showing up).
pub fn log_view(event: &str, view: &Value) {
    if !sonata_session::logs::verbose() {
        return;
    }
    let g = view.get("geometry");
    let gv = |k| py(g.and_then(|g| g.get(k)));
    let title: String = view.get("title").and_then(Value::as_str).unwrap_or("").chars().take(60).collect();
    let time = glib::DateTime::now_local().and_then(|t| t.format("%H:%M:%S")).map(|s| s.to_string()).unwrap_or_default();
    let line = format!(
        "{time} {:8} id={} app={} title={} pid={} role={} type={} layer={} at={},{} {}x{} out={} ws={} min={} full={} parent={}\n",
        event.replace("view-", ""),
        py(view.get("id")),
        py_repr(view.get("app-id").and_then(Value::as_str)),
        py_repr(Some(&title)),
        py(view.get("pid")),
        py(view.get("role")),
        py(view.get("type")),
        py(view.get("layer")),
        gv("x"),
        gv("y"),
        gv("width"),
        gv("height"),
        py(view.get("output-name")),
        py(view.get("wset-index")),
        py(view.get("minimized")),
        py(view.get("fullscreen")),
        py(view.get("parent")),
    );
    let dir = sonata_session::logs::log_dir();
    let path = dir.join(LOG);
    let _ = std::fs::create_dir_all(&dir);
    // kept to its last lines by hand: a window opening is no reason to grow a log for ever
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > (LOG_LINES * 400) as u64) {
        if let Ok(text) = std::fs::read_to_string(&path) {
            let lines: Vec<&str> = text.split_inclusive('\n').collect();
            let _ = std::fs::write(&path, lines[lines.len().saturating_sub(LOG_LINES / 2)..].concat());
        }
    }
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// Ctrl+Super+F remembered per app, and games put in full screen.
pub struct Rules {
    ipc: Rc<dyn Ipc>,
    later: Later,
    cfg: RefCell<Map<String, Value>>,
    /// views already decided
    done: RefCell<HashSet<i64>>,
    me: Weak<Rules>,
}

impl Rules {
    /// The menu bar's, made once. None on trial: the Python menu bar
    /// already answers Ctrl+Super+F and puts games in full screen -- a
    /// second copy would toggle every window twice.
    pub fn start() -> Option<Rc<Self>> {
        if sonata_config::side_by_side() {
            return None;
        }
        Some(Self::with(Rc::new(wfevents::WayfireIpc::default()), wfevents::main_loop_later()))
    }

    /// With this Wayfire and timer (tests hand in fakes).
    pub fn with(ipc: Rc<dyn Ipc>, later: Later) -> Rc<Self> {
        let me = Rc::new_cyclic(|w| Rules { ipc, later, cfg: RefCell::new(sonata_config::load(NAME, &defaults())), done: RefCell::default(), me: w.clone() });
        if me.ipc.available() {
            let w = me.me.clone();
            me.ipc.watch(
                &["view-mapped", "view-unmapped"],
                Box::new(move |msg| {
                    if let Some(me) = w.upgrade() {
                        me.event(msg);
                    }
                }),
            );
        }
        me
    }

    // -- memory --------------------------------------------------------------------------------
    /// What the app was left as: Some(true) full screen, Some(false)
    /// windowed, None never toggled.
    pub fn remembered(&self, app: &str) -> Option<Value> {
        self.cfg.borrow().get("apps").and_then(|a| a.get(app)).filter(|v| !v.is_null()).cloned()
    }

    pub fn remember(&self, app: &str, on: bool) {
        if app.is_empty() {
            return;
        }
        // read again: another process (Settings' reset) may have written it
        let mut cfg = sonata_config::load(NAME, &defaults());
        if !cfg.get("apps").is_some_and(Value::is_object) {
            cfg.insert("apps".into(), json!({}));
        }
        cfg.get_mut("apps").and_then(Value::as_object_mut).unwrap().insert(app.into(), Value::Bool(on));
        let _ = sonata_config::save(NAME, &cfg);
        *self.cfg.borrow_mut() = cfg;
    }

    // -- Wayfire -------------------------------------------------------------------------------
    fn screen(&self, view: &Value) -> (f64, f64) {
        let id = view.get("output-id").cloned().unwrap_or(json!(-1));
        let out = self.ipc.call("window-rules/output-info", json!({"id": id}));
        let g = out.as_ref().and_then(|o| o.get("geometry"));
        (num(g.and_then(|g| g.get("width"))), num(g.and_then(|g| g.get("height"))))
    }

    pub fn set_fullscreen(&self, view: &Value, on: bool) {
        let Some(id) = view.get("id") else { return };
        self.ipc.call("wm-actions/set-fullscreen", json!({"view_id": id, "state": on}));
    }

    // -- the shortcut --------------------------------------------------------------------------
    /// Ctrl+Super+F: the focused window in or out of full screen, remembered for its app.
    pub fn toggle(&self) {
        let views = wfevents::views(&*self.ipc);
        let Some(front) = views.iter().find(|v| v.get("activated").is_some_and(crate::truthy) && is_toplevel(v) && str_or(v, "layer", "workspace") == Some("workspace")) else { return };
        let on = !front.get("fullscreen").is_some_and(crate::truthy);
        self.set_fullscreen(front, on);
        self.remember(app(front), on);
    }

    // -- new windows ---------------------------------------------------------------------------
    /// A Wayfire event (view-mapped / view-unmapped).
    pub fn event(&self, msg: &Value) {
        let view = msg.get("view").cloned().unwrap_or(Value::Null);
        let Some(vid) = view.get("id").and_then(Value::as_i64) else { return };
        let ev = msg.get("event").and_then(Value::as_str).unwrap_or("");
        log_view(ev, &view);
        if ev == "view-unmapped" {
            self.done.borrow_mut().remove(&vid);
            return;
        }
        if !is_toplevel(&view) || !no_parent(&view) {
            return;
        }
        match self.remembered(app(&view)) {
            Some(want) if crate::truthy(&want) => {
                self.done.borrow_mut().insert(vid);
                self.set_fullscreen(&view, true);
            }
            None if is_wine(&view) => {
                for ms in RECHECK_MS {
                    let w = self.me.clone();
                    (self.later)(
                        ms,
                        Box::new(move || {
                            if let Some(me) = w.upgrade() {
                                me.check_game(vid);
                            }
                        }),
                    );
                }
            }
            _ => {}
        }
    }

    fn check_game(&self, vid: i64) {
        if self.done.borrow().contains(&vid) {
            return;
        }
        let views = wfevents::views(&*self.ipc);
        let Some(view) = views.iter().find(|v| v.get("id").and_then(Value::as_i64) == Some(vid)) else { return };
        if view.get("fullscreen").is_some_and(crate::truthy) {
            self.done.borrow_mut().insert(vid); // it went full screen by itself
            return;
        }
        if looks_like_game(view, self.screen(view)) {
            self.done.borrow_mut().insert(vid);
            self.set_fullscreen(view, true);
        }
    }
}

#[cfg(test)]
mod tests {
    //! tests/test_fullscreen.py. Bug: Skate 3 from Heroic opened as a
    //! window with a title bar.
    use super::*;
    use std::cell::Cell;

    fn view(vid: i64, app: &str, w: i64, h: i64) -> Value {
        json!({"id": vid, "app-id": app, "title": "Skate 3", "pid": -1, "role": "toplevel", "type": "toplevel",
               "parent": -1, "geometry": {"x": 0, "y": 0, "width": w, "height": h}, "output-id": 1,
               "fullscreen": false, "activated": true, "layer": "workspace"})
    }

    fn with(mut v: Value, extra: Value) -> Value {
        for (k, x) in extra.as_object().unwrap() {
            v[k] = x.clone();
        }
        v
    }

    #[derive(Default)]
    pub(crate) struct FakeIpc {
        pub views: RefCell<Vec<Value>>,
        pub sent: RefCell<Vec<(i64, bool)>>,
        pub cb: RefCell<Option<Box<dyn Fn(&Value)>>>,
        pub calls: Cell<usize>,
    }

    impl FakeIpc {
        pub fn new(views: Vec<Value>) -> Rc<Self> {
            Rc::new(FakeIpc { views: RefCell::new(views), ..Default::default() })
        }
        pub fn send(&self, msg: Value) {
            (self.cb.borrow().as_ref().unwrap())(&msg);
        }
    }

    impl Ipc for FakeIpc {
        fn available(&self) -> bool {
            true
        }
        fn call(&self, method: &str, data: Value) -> Option<Value> {
            self.calls.set(self.calls.get() + 1);
            match method {
                "window-rules/list-views" => Some(Value::Array(self.views.borrow().clone())),
                "window-rules/output-info" => Some(json!({"geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}})),
                "wm-actions/set-fullscreen" => {
                    let (id, on) = (data["view_id"].as_i64().unwrap(), data["state"].as_bool().unwrap());
                    self.sent.borrow_mut().push((id, on));
                    for v in self.views.borrow_mut().iter_mut() {
                        if v["id"] == id {
                            v["fullscreen"] = json!(on);
                        }
                    }
                    Some(json!({"result": "ok"}))
                }
                _ => Some(json!({"result": "ok"})),
            }
        }
        fn watch(&self, _events: &[&str], callback: Box<dyn Fn(&Value)>) -> bool {
            *self.cb.borrow_mut() = Some(callback);
            true
        }
    }

    type Timers = Rc<RefCell<Vec<Box<dyn FnOnce()>>>>;

    fn rules(views: Vec<Value>) -> (Rc<Rules>, Rc<FakeIpc>, Timers) {
        let ipc = FakeIpc::new(views);
        let timers: Timers = Rc::default();
        let t = timers.clone();
        let r = Rules::with(ipc.clone(), Rc::new(move |_, f| t.borrow_mut().push(f)));
        (r, ipc, timers)
    }

    fn run(timers: &Timers) {
        let fs: Vec<_> = timers.borrow_mut().drain(..).collect();
        for f in fs {
            f();
        }
    }

    #[test]
    fn wine_detection() {
        assert!(is_wine(&view(1, "Skate3Recomp.exe", 1920, 1080)));
        assert!(is_wine(&view(1, "steam_app_1234", 1920, 1080)));
        assert!(!is_wine(&view(1, "org.gnome.Nautilus", 1920, 1080)));
    }

    #[test]
    fn wine_game_asking_for_the_screen_goes_full_screen() {
        let _e = crate::testenv::env();
        let v = view(1, "skate3recomp.exe", 1920, 1000); // squeezed under the menu bar
        let (_r, ipc, timers) = rules(vec![v.clone()]);
        ipc.send(json!({"event": "view-mapped", "view": v}));
        run(&timers);
        assert_eq!(*ipc.sent.borrow(), [(1, true)]);
    }

    #[test]
    fn fixed_size_game_window() {
        let v = with(view(1, "skate3recomp.exe", 1280, 720), json!({"min-size": {"width": 1280, "height": 720}, "max-size": {"width": 1280, "height": 720}}));
        assert!(looks_like_game(&v, (1920.0, 1080.0)));
    }

    #[test]
    fn launchers_installers_and_small_windows_stay_windows() {
        let s = (1920.0, 1080.0);
        assert!(!looks_like_game(&view(1, "setup.exe", 1920, 1080), s));
        assert!(!looks_like_game(&view(1, "epicgameslauncher.exe", 1920, 1080), s));
        assert!(!looks_like_game(&view(1, "skate3recomp.exe", 800, 600), s));
        assert!(!looks_like_game(&view(1, "org.gnome.Nautilus", 1920, 1080), s)); // native: asks itself
        assert!(!looks_like_game(&with(view(1, "skate3recomp.exe", 1920, 1080), json!({"parent": 4})), s)); // a dialog
    }

    #[test]
    fn not_games_match_whole_words_only() {
        // (fullscreen.py's regex, case by case)
        for name in ["unins000.exe", "setup.exe", "setups.exe", "installer", "control panel", "my installer", "unityCrashHandler64.exe".to_lowercase().as_str(), "reporter.exe", "steam.exe x", "install2x"] {
            assert!(not_a_game(name), "{name}");
        }
        for name in ["control.exe2", "control x", "crash bandicoot", "assassin's creed origins", "explorers of the deep", "xsetup.exe", "reporting", "origins", "setupa"] {
            assert_eq!(not_a_game(name), name == "control.exe2", "{name}");
        }
    }

    #[test]
    fn shortcut_toggles_and_is_remembered() {
        let _e = crate::testenv::env();
        let (r, ipc, _t) = rules(vec![view(1, "org.gnome.TextEditor", 900, 700)]);
        r.toggle();
        assert_eq!(*ipc.sent.borrow(), [(1, true)]);
        assert_eq!(r.remembered("org.gnome.TextEditor"), Some(json!(true)));
        // next session / next launch
        let v = view(7, "org.gnome.TextEditor", 900, 700);
        let (_r2, ipc2, _t2) = rules(vec![v.clone()]);
        ipc2.send(json!({"event": "view-mapped", "view": v}));
        assert_eq!(*ipc2.sent.borrow(), [(7, true)]);
    }

    #[test]
    fn taken_out_of_full_screen_stays_windowed() {
        let _e = crate::testenv::env();
        let v = with(view(1, "skate3recomp.exe", 1920, 1080), json!({"fullscreen": true}));
        let (r, ipc, timers) = rules(vec![v]);
        r.toggle(); // the user leaves full screen
        assert_eq!(r.remembered("skate3recomp.exe"), Some(json!(false)));
        ipc.sent.borrow_mut().clear();
        let v2 = view(2, "skate3recomp.exe", 1920, 1080);
        *ipc.views.borrow_mut() = vec![v2.clone()];
        ipc.send(json!({"event": "view-mapped", "view": v2}));
        run(&timers);
        assert!(ipc.sent.borrow().is_empty()); // no automatic full screen any more
    }

    #[test]
    fn rules_not_started_on_trial() {
        let _e = crate::testenv::env();
        std::env::remove_var("SONATA2_RS_SIDE_BY_SIDE");
        assert!(Rules::start().is_none());
    }
}
