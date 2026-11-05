//! Open windows of every app, for the Dock (the part of wl/toplevels.py
//! it uses), through Wayfire's IPC: `window-rules/list-views` once, then
//! its event stream (`window-rules/events/watch`) on the main loop.
//!
//! The Python Dock reads wlr-foreign-toplevel on GTK's own Wayland
//! connection; the Rust workspace has no Wayland client crate yet, and the
//! IPC tells the Dock everything it shows (app id, title, minimized, in
//! front, display). What it can't do: tell Wayfire where a window minimizes
//! to (foreign-toplevel's set_rectangle) -- wlr.rs does that part, on
//! GTK's own connection.
//!
//! Both Docks can watch at once (each its own socket): nothing here
//! clashes with the Python one on trial.

use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::rc::Rc;
use std::time::Duration;

/// The events the Dock follows (and Mission Control's right-click, and
/// scale's state: dock.py's _watch_outputs).
pub const EVENTS: [&str; 11] = [
    "view-mapped",
    "view-unmapped",
    "view-title-changed",
    "view-app-id-changed",
    "view-minimized",
    "view-focused",
    "view-set-output",
    "view-tiled",
    "view-fullscreen",
    "sonata-scale-menu#",
    "plugin-activation-state-changed",
];

/// A browser's "<site> is sharing your screen." bar: no app id, a window
/// of its own. Not an app (wl/toplevels.SHARE_BAR_TEXT).
pub const SHARE_BAR_TEXT: [&str; 3] = [" is sharing ", " está compartilhando ", " compartilhando "];

pub fn is_share_bar(app_id: &str, title: &str) -> bool {
    (app_id.is_empty() || app_id == "unknown") && SHARE_BAR_TEXT.iter().any(|t| title.contains(t))
}

/// Shell surfaces never shown as apps (__main__.SHELL_IDS), Python's and
/// the Rust build's.
pub fn is_shell(app_id: &str) -> bool {
    const SHELL: [&str; 13] = ["dock", "autostart", "wallpaper", "launchpad", "topbar", "gallery", "lock", "spotlight", "greeter", "welcome", "portal", "setup", "polkit"];
    ["io.github.vinioliveiras.sonata2.", "io.github.vinioliveiras.sonata2rs."]
        .iter()
        .any(|p| app_id.strip_prefix(p).is_some_and(|rest| SHELL.contains(&rest)))
}

#[derive(Clone, Debug, PartialEq)]
pub struct Toplevel {
    pub id: i64,
    pub app_id: String,
    pub title: String,
    pub minimized: bool,
    pub activated: bool,
    pub maximized: bool,
    pub output: String,
    pub pid: i64,
}

impl Toplevel {
    /// A Wayfire view (list-views / an event's "view"); None for what isn't
    /// an app's window (panels, popups, the share bar, shell surfaces).
    pub fn from_view(v: &Value) -> Option<Self> {
        let o = v.as_object()?;
        let s = |k: &str| o.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        if o.get("type").and_then(Value::as_str).is_some_and(|t| t != "toplevel") {
            return None;
        }
        if o.get("role").and_then(Value::as_str).is_some_and(|r| r != "toplevel") || o.get("mapped") == Some(&Value::Bool(false)) {
            return None;
        }
        let (app_id, title) = (s("app-id"), s("title"));
        if is_share_bar(&app_id, &title) || is_shell(&app_id) {
            return None;
        }
        // tiled-edges 15: all four, Wayfire's maximized
        let maximized = o.get("tiled-edges").and_then(Value::as_i64) == Some(15);
        Some(Toplevel {
            id: o.get("id").and_then(Value::as_i64)?,
            app_id,
            title,
            minimized: o.get("minimized").and_then(Value::as_bool).unwrap_or(false),
            activated: o.get("activated").and_then(Value::as_bool).unwrap_or(false),
            maximized,
            output: s("output-name"),
            pid: o.get("pid").and_then(Value::as_i64).unwrap_or(0),
        })
    }
}

/// The windows and what to tell when they change.
pub struct Manager {
    tops: RefCell<Vec<Toplevel>>,
    listeners: RefCell<Vec<(u64, Rc<dyn Fn()>)>>,
    events: RefCell<Vec<(u64, Rc<dyn Fn(&Value)>)>>,
    next: Cell<u64>,
    pub available: bool,
    _watch: RefCell<Option<(UnixStream, glib::SourceId)>>,
}

thread_local! {
    static SHARED: RefCell<Option<Rc<Manager>>> = const { RefCell::new(None) };
}

/// The process's manager (one socket however many Docks).
pub fn manager() -> Rc<Manager> {
    SHARED.with(|s| s.borrow_mut().get_or_insert_with(Manager::start).clone())
}

/// Apply one event to the list (pure: tests).
pub fn apply(tops: &mut Vec<Toplevel>, msg: &Value) -> bool {
    let ev = msg.get("event").and_then(Value::as_str).unwrap_or("");
    let view = msg.get("view").unwrap_or(&Value::Null);
    let vid = view.get("id").and_then(Value::as_i64);
    match ev {
        "view-unmapped" => {
            let n = tops.len();
            tops.retain(|t| Some(t.id) != vid);
            n != tops.len()
        }
        "view-focused" => {
            let mut changed = false;
            for t in tops.iter_mut() {
                let on = Some(t.id) == vid;
                changed |= t.activated != on;
                t.activated = on;
            }
            // (the event's own copy may still say "not activated")
            if let Some(mut new) = Toplevel::from_view(view) {
                new.activated = true;
                changed |= upsert(tops, new);
            }
            changed
        }
        "" => false,
        _ if ev.starts_with("view-") => match Toplevel::from_view(view) {
            Some(new) => upsert(tops, new),
            None => {
                // a view that stopped being an app window (or never was)
                let n = tops.len();
                tops.retain(|t| Some(t.id) != vid);
                n != tops.len()
            }
        },
        _ => false,
    }
}

fn upsert(tops: &mut Vec<Toplevel>, new: Toplevel) -> bool {
    match tops.iter_mut().find(|t| t.id == new.id) {
        Some(t) if *t == new => false,
        Some(t) => {
            *t = new;
            true
        }
        None => {
            tops.push(new);
            true
        }
    }
}

impl Manager {
    fn start() -> Rc<Manager> {
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({}));
        let tops = views.as_ref().and_then(Value::as_array).map(|a| a.iter().filter_map(Toplevel::from_view).collect()).unwrap_or_default();
        let m = Rc::new(Manager {
            tops: RefCell::new(tops),
            listeners: RefCell::default(),
            events: RefCell::default(),
            next: Cell::new(1),
            available: views.is_some(),
            _watch: RefCell::default(),
        });
        if m.available {
            m.watch();
        }
        m
    }

    fn watch(self: &Rc<Self>) {
        let Some(path) = sonata_backend::wfipc::socket() else { return };
        let Ok(mut s) = UnixStream::connect(path) else { return };
        let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
        let frame = sonata_backend::wfipc::frame("window-rules/events/watch", &json!({"events": EVENTS}));
        if s.write_all(&frame).is_err() || sonata_backend::wfipc::read_message(&mut s).is_none() {
            return;
        }
        let weak = Rc::downgrade(self);
        let reader = match s.try_clone() {
            Ok(r) => RefCell::new(r),
            Err(_) => return,
        };
        let id = glib::unix_fd_add_local(s.as_raw_fd(), glib::IOCondition::IN | glib::IOCondition::HUP, move |_, cond| {
            let Some(m) = weak.upgrade() else { return glib::ControlFlow::Break };
            if cond.contains(glib::IOCondition::HUP) {
                return glib::ControlFlow::Break; // Wayfire went away
            }
            let Some(msg) = sonata_backend::wfipc::read_message(&mut *reader.borrow_mut()) else { return glib::ControlFlow::Break };
            if msg.get("event").is_none() {
                return glib::ControlFlow::Continue;
            }
            let events: Vec<_> = m.events.borrow().iter().map(|(_, f)| f.clone()).collect();
            for f in events {
                f(&msg);
            }
            if apply(&mut m.tops.borrow_mut(), &msg) {
                m.notify();
            }
            glib::ControlFlow::Continue
        });
        self._watch.replace(Some((s, id)));
    }

    fn notify(&self) {
        let ls: Vec<_> = self.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
        for f in ls {
            f();
        }
    }

    pub fn toplevels(&self) -> Vec<Toplevel> {
        self.tops.borrow().clone()
    }

    pub fn get(&self, id: i64) -> Option<Toplevel> {
        self.tops.borrow().iter().find(|t| t.id == id).cloned()
    }

    /// listener() after every change; the id undoes it (remove_listener).
    pub fn add_listener(&self, f: Rc<dyn Fn()>) -> u64 {
        let id = self.next.get();
        self.next.set(id + 1);
        self.listeners.borrow_mut().push((id, f));
        id
    }

    pub fn remove_listener(&self, id: u64) {
        self.listeners.borrow_mut().retain(|(i, _)| *i != id);
        self.events.borrow_mut().retain(|(i, _)| *i != id);
    }

    /// Every raw event (Mission Control's menu, a window moved display).
    pub fn add_event_listener(&self, f: Rc<dyn Fn(&Value)>) -> u64 {
        let id = self.next.get();
        self.next.set(id + 1);
        self.events.borrow_mut().push((id, f));
        id
    }

    /// The minimized state changes at once here: the Dock's next look (a
    /// second click) mustn't wait for Wayfire's event.
    fn set_local(&self, id: i64, f: impl Fn(&mut Toplevel)) {
        if let Some(t) = self.tops.borrow_mut().iter_mut().find(|t| t.id == id) {
            f(t);
        }
    }

    pub fn activate(&self, t: &Toplevel) {
        let (id, minimized) = (t.id, t.minimized);
        self.set_local(id, |t| t.minimized = false);
        call_bg(move || {
            if minimized {
                sonata_backend::wfipc::call("wm-actions/set-minimized", json!({"view_id": id, "state": false}));
            }
            sonata_backend::wfipc::call("window-rules/focus-view", json!({"id": id}));
        });
    }

    pub fn minimize(&self, t: &Toplevel) {
        let id = t.id;
        self.set_local(id, |t| t.minimized = true);
        call_bg(move || {
            sonata_backend::wfipc::call("wm-actions/set-minimized", json!({"view_id": id, "state": true}));
        });
    }

    pub fn close(&self, t: &Toplevel) {
        let id = t.id;
        call_bg(move || {
            sonata_backend::wfipc::call("window-rules/close-view", json!({"id": id}));
        });
    }

    /// Full size on its display's work area (the IPC has no "maximize":
    /// the work area is what a maximized window gets).
    pub fn set_maximized(&self, t: &Toplevel) {
        let (id, output) = (t.id, t.output.clone());
        call_bg(move || {
            let outputs = sonata_backend::wfipc::call("window-rules/list-outputs", json!({}));
            let area = outputs.as_ref().and_then(Value::as_array).and_then(|a| a.iter().find(|o| o["name"] == output.as_str())).and_then(|o| o.get("workarea").cloned());
            if let Some(g) = area {
                sonata_backend::wfipc::call("window-rules/configure-view", json!({"id": id, "geometry": g}));
            }
        });
    }
}

/// A Wayfire call off the main loop (each is a socket round trip).
pub fn call_bg(work: impl FnOnce() + Send + 'static) {
    let _ = std::thread::Builder::new().name("dock-ipc".into()).spawn(work);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn view(id: i64, app: &str, title: &str) -> Value {
        json!({"id": id, "app-id": app, "title": title, "type": "toplevel", "role": "toplevel", "mapped": true, "minimized": false, "activated": false, "output-name": "eDP-1"})
    }

    #[test]
    fn events_keep_the_list() {
        let mut tops = Vec::new();
        assert!(apply(&mut tops, &json!({"event": "view-mapped", "view": view(1, "firefox", "A")})));
        assert!(apply(&mut tops, &json!({"event": "view-mapped", "view": view(2, "kitty", "B")})));
        assert!(!apply(&mut tops, &json!({"event": "view-mapped", "view": view(2, "kitty", "B")})));
        assert!(apply(&mut tops, &json!({"event": "view-focused", "view": view(2, "kitty", "B")})));
        assert!(tops[1].activated && !tops[0].activated);
        let mut v = view(1, "firefox", "A");
        v["minimized"] = json!(true);
        assert!(apply(&mut tops, &json!({"event": "view-minimized", "view": v})));
        assert!(tops[0].minimized);
        assert!(apply(&mut tops, &json!({"event": "view-unmapped", "view": view(1, "firefox", "A")})));
        assert_eq!(tops.len(), 1);
        assert!(!apply(&mut tops, &json!({"event": "plugin-activation-state-changed", "plugin": "scale"})));
    }

    #[test]
    fn shell_and_share_bars_are_not_apps() {
        assert!(Toplevel::from_view(&view(3, "io.github.vinioliveiras.sonata2.dock", "Dock")).is_none());
        assert!(Toplevel::from_view(&view(3, "io.github.vinioliveiras.sonata2rs.dock", "Dock")).is_none());
        assert!(Toplevel::from_view(&view(4, "", "meet.google.com is sharing your screen.")).is_none());
        assert!(Toplevel::from_view(&view(5, "io.github.vinioliveiras.sonata2.files", "Home")).is_some());
        let mut panel = view(6, "x", "y");
        panel["type"] = json!("panel");
        assert!(Toplevel::from_view(&panel).is_none());
    }
}

/// The Dock's windows as Quit on Close reads them (run_dock hands it the
/// Dock's own toplevel manager).
pub struct QuitWindows(pub Rc<Manager>);

impl sonata_sessionui::quitapps::Windows for QuitWindows {
    fn toplevels(&self) -> Vec<sonata_sessionui::quitapps::Toplevel> {
        self.0.toplevels().into_iter().map(|t| sonata_sessionui::quitapps::Toplevel { id: t.id as u64, app_id: t.app_id, title: t.title }).collect()
    }

    fn close(&self, id: u64) {
        if let Some(t) = self.0.get(id as i64) {
            self.0.close(&t);
        }
    }
}
