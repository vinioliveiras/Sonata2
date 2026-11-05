//! Restart, Shut Down and Log Out quit the apps first (macOS;
//! shell/quitapps.py): every window is asked to close, apps left running
//! without one (Chrome in the background, tray apps) get SIGTERM -- the
//! polite quit, Chrome saves its session on it --, and only when they are
//! gone does the computer restart. Straight to systemctl, they were killed
//! with the session and Chrome asked to restore its tabs at every start
//! (Vini).
//!
//! An app that keeps a window open (its "Save changes?" question) stops
//! it, like on macOS: nothing else happens, and Sonata says which app.
//!
//! ```text
//! end_session("restart", Some(windows), None)   // windows: the toplevel manager
//! quit_app(windows, these, key)                  // Quit in the Dock / app menu
//! ```
//!
//! The toplevel manager (wl/toplevels.py) is the Dock's and the menu bar's:
//! they hand it in through the [`Windows`] trait. Anyone else (Settings'
//! Restart / Log Out) gets [`session_windows`], Wayfire's own list -- as
//! Python's end_session makes a ToplevelManager of its own when none is
//! given.

use serde_json::{json, Value};
use sonata_i18n::{fmt, gettext, n_, tr};
use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::Rc;

pub const TIMEOUT_MS: i64 = 12000;
pub const POLL_MS: i64 = 250;
/// Sonata's shell: never signalled (its own apps' windows close). Matches
/// the trial build's ids and binary too (sonata2rs, sonata2-rs).
pub const OWN: &str = "sonata2";
pub const SESSION_ENDS: [&str; 3] = ["restart", "shutdown", "logout"];

/// A window as the toplevel manager knows it.
#[derive(Clone, Debug, PartialEq)]
pub struct Toplevel {
    /// the manager's own handle (stable while the window lives)
    pub id: u64,
    pub app_id: String,
    pub title: String,
}

/// The session's windows (wl/toplevels.py's ToplevelManager).
pub trait Windows {
    fn toplevels(&self) -> Vec<Toplevel>;
    /// Ask this window to close (it may say no: "Save changes?").
    fn close(&self, id: u64);
}

/// Wayfire's views (`window-rules/list-views`), [] without Wayfire.
pub fn views() -> Vec<Value> {
    match sonata_backend::wfipc::call("window-rules/list-views", json!({})) {
        Some(Value::Array(v)) => v.into_iter().filter(Value::is_object).collect(),
        _ => Vec::new(),
    }
}

/// The session's windows straight from Wayfire's IPC, for a process with
/// no toplevel manager of its own (Settings): listed fresh at every look,
/// closed with `window-rules/close-view`.
pub struct IpcWindows {
    views: Box<dyn Fn() -> Vec<Value>>,
    close: Box<dyn Fn(u64)>,
}

impl IpcWindows {
    pub fn new(views: Box<dyn Fn() -> Vec<Value>>, close: Box<dyn Fn(u64)>) -> Self {
        IpcWindows { views, close }
    }
}

impl Windows for IpcWindows {
    fn toplevels(&self) -> Vec<Toplevel> {
        toplevels_of(&(self.views)())
    }
    fn close(&self, id: u64) {
        (self.close)(id)
    }
}

/// The app windows among Wayfire's views (mapped toplevels, not panels).
pub fn toplevels_of(views: &[Value]) -> Vec<Toplevel> {
    views
        .iter()
        .filter(|v| is_toplevel(v) && v.get("role").and_then(Value::as_str).is_none_or(|r| r == "toplevel") && v.get("mapped") != Some(&Value::Bool(false)))
        .filter_map(|v| Some(Toplevel { id: v.get("id").and_then(Value::as_u64)?, app_id: view_app(v).to_string(), title: v.get("title").and_then(Value::as_str).unwrap_or("").to_string() }))
        .collect()
}

/// Wayfire's windows, None without Wayfire (Python: the manager only when
/// it is available).
pub fn session_windows() -> Option<Rc<dyn Windows>> {
    sonata_backend::wfipc::socket()?;
    Some(Rc::new(IpcWindows::new(
        Box::new(views),
        Box::new(|id| {
            sonata_backend::wfipc::call("window-rules/close-view", json!({"id": id}));
        }),
    )))
}

/// Processes behind the tray icons (apps that run without a window).
pub fn tray_pids() -> HashSet<i64> {
    let mut out = HashSet::new();
    let Ok(conn) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return out };
    let items = conn
        .call_sync(Some(sonata_tray::WATCHER), sonata_tray::WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get", Some(&(sonata_tray::WATCHER, "RegisteredStatusNotifierItems").into()), None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
        .ok()
        .and_then(|v| v.child_value(0).as_variant())
        .and_then(|v| v.get::<Vec<String>>())
        .unwrap_or_default();
    for item in items {
        let name = sonata_tray::split_service(&item, "").0;
        if let Some(pid) = conn
            .call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", Some(&(name.as_str(),).into()), None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.child_value(0).get::<u32>())
        {
            out.insert(pid as i64);
        }
    }
    out
}

pub fn cmdline(pid: i64) -> String {
    std::fs::read(format!("/proc/{pid}/cmdline")).map(|b| String::from_utf8_lossy(&b).replace('\0', " ")).unwrap_or_default()
}

/// os.kill(pid, 0): gone only when there's no such process.
pub fn alive(pid: i64) -> bool {
    pid > 0 && (unsafe { libc::kill(pid as i32, 0) } == 0 || std::io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH))
}

pub fn kill(pid: i64, sig: i32) -> bool {
    pid > 0 && unsafe { libc::kill(pid as i32, sig) } == 0
}

/// The app (desktop id) of a Wayland app id: its .desktop's, else itself.
pub fn key_of(app_id: &str) -> String {
    sonata_desktop::appmatch::match_app_id(app_id).unwrap_or_else(|| app_id.to_string())
}

fn view_pid(v: &Value) -> Option<i64> {
    v.get("pid").and_then(Value::as_i64)
}

fn view_app(v: &Value) -> &str {
    v.get("app-id").and_then(Value::as_str).unwrap_or("")
}

fn is_toplevel(v: &Value) -> bool {
    v.get("type").and_then(Value::as_str) == Some("toplevel")
}

/// Processes with a window now.
pub fn with_window(views: &[Value]) -> HashSet<i64> {
    views.iter().filter(|v| is_toplevel(v)).filter_map(view_pid).collect()
}

/// The apps' processes to quit: each app window's and tray icon's, never
/// Sonata's own shell nor this process. key: only that app's windows.
/// cmdline / app_of: (tests) a process's command line, a view's app.
pub fn app_pids_with(views: &[Value], tray: &HashSet<i64>, key: Option<&str>, cmdline: &dyn Fn(i64) -> String, app_of: &dyn Fn(&Value) -> String) -> HashSet<i64> {
    let mut pids: HashSet<i64> = views
        .iter()
        .filter(|v| is_toplevel(v) && !view_app(v).contains(OWN) && key.is_none_or(|k| app_of(v) == k))
        .filter_map(view_pid)
        .filter(|p| *p > 1)
        .collect();
    pids.extend(tray.iter().filter(|p| **p > 1));
    pids.remove(&(std::process::id() as i64));
    // (an X11 window's pid can be Xwayland's: it goes with the session, never before)
    pids.into_iter()
        .filter(|p| {
            let c = cmdline(*p);
            !c.contains(OWN) && !c.split(' ').next().unwrap_or("").contains("Xwayland")
        })
        .collect()
}

pub fn app_pids(views: &[Value], tray: &HashSet<i64>, key: Option<&str>) -> HashSet<i64> {
    app_pids_with(views, tray, key, &cmdline, &|v| key_of(view_app(v)))
}

/// What a Quitter does outside itself (tests replace them).
pub struct Effects {
    pub views: Box<dyn Fn() -> Vec<Value>>,
    pub alive: Box<dyn Fn(i64) -> bool>,
    pub kill: Box<dyn Fn(i64, i32)>,
}

impl Default for Effects {
    fn default() -> Self {
        Effects {
            views: Box::new(views),
            alive: Box::new(alive),
            kill: Box::new(|p, s| {
                kill(p, s);
            }),
        }
    }
}

struct State {
    pids: HashSet<i64>,
    signalled: HashSet<i64>,
    /// windows asked to close (once each)
    asked: HashSet<u64>,
    left_ms: i64,
    waited_ms: i64,
}

type Done = Box<dyn Fn(&str)>;
type Cancel = Box<dyn Fn(&str, &Toplevel)>;

/// One Restart / Shut Down / Log Out (or one app's Quit) in progress.
pub struct Quitter {
    pub kind: String,
    windows: Option<Rc<dyn Windows>>,
    on_done: Done,
    on_cancel: Cancel,
    fx: Effects,
    /// just these windows (one app)
    only: Option<HashSet<u64>>,
    st: RefCell<State>,
}

impl Quitter {
    /// tray: the tray's processes (only asked when quitting everything, key None).
    pub fn new(kind: &str, windows: Option<Rc<dyn Windows>>, on_done: Done, on_cancel: Cancel, fx: Effects, tray: &dyn Fn() -> HashSet<i64>, only: Option<&[Toplevel]>, key: Option<&str>) -> Rc<Self> {
        let pids = app_pids(&(fx.views)(), &if key.is_none() { tray() } else { HashSet::new() }, key);
        Self::with_pids(kind, windows, on_done, on_cancel, fx, pids, only)
    }

    fn with_pids(kind: &str, windows: Option<Rc<dyn Windows>>, on_done: Done, on_cancel: Cancel, fx: Effects, pids: HashSet<i64>, only: Option<&[Toplevel]>) -> Rc<Self> {
        Rc::new(Quitter {
            kind: kind.into(),
            windows,
            on_done,
            on_cancel,
            fx,
            only: only.map(|ts| ts.iter().map(|t| t.id).collect()),
            st: RefCell::new(State { pids, signalled: HashSet::new(), asked: HashSet::new(), left_ms: TIMEOUT_MS, waited_ms: 0 }),
        })
    }

    pub fn windows(&self) -> Vec<Toplevel> {
        let ts = self.windows.as_ref().map(|w| w.toplevels()).unwrap_or_default();
        match &self.only {
            Some(only) => ts.into_iter().filter(|t| only.contains(&t.id)).collect(), // one app's windows, Sonata's own apps included
            None => ts.into_iter().filter(|t| !t.app_id.contains(OWN)).collect(),
        }
    }

    pub fn start(self: &Rc<Self>) {
        if self.tick() {
            let me = self.clone();
            glib::timeout_add_local(std::time::Duration::from_millis(POLL_MS as u64), move || {
                {
                    let mut st = me.st.borrow_mut();
                    st.left_ms -= POLL_MS;
                    st.waited_ms += POLL_MS;
                }
                if me.tick() {
                    glib::ControlFlow::Continue
                } else {
                    glib::ControlFlow::Break
                }
            });
        }
    }

    /// True while waiting. Each window is asked to close once (a manager
    /// made just now lists them a moment later); processes whose windows
    /// are all gone (or that never had one) get SIGTERM once.
    pub fn tick(&self) -> bool {
        for t in self.windows() {
            if self.st.borrow_mut().asked.insert(t.id) {
                if let Some(w) = &self.windows {
                    w.close(t.id);
                }
            }
        }
        let with = with_window(&(self.fx.views)());
        let to_signal: Vec<i64> = {
            let mut st = self.st.borrow_mut();
            let list: Vec<i64> = st.pids.iter().filter(|p| !with.contains(p) && !st.signalled.contains(p)).copied().collect();
            st.signalled.extend(list.iter().copied());
            list
        };
        for pid in to_signal {
            (self.fx.kill)(pid, libc::SIGTERM);
        }
        let pids: HashSet<i64> = self.st.borrow().pids.iter().copied().filter(|p| (self.fx.alive)(*p)).collect();
        let open = self.windows();
        let (left, waited) = {
            let mut st = self.st.borrow_mut();
            st.pids = pids;
            (st.left_ms, st.waited_ms)
        };
        if open.is_empty() && self.st.borrow().pids.is_empty() && waited >= 2 * POLL_MS {
            (self.on_done)(&self.kind);
            return false;
        }
        if left <= 0 {
            if let Some(t) = open.first() {
                (self.on_cancel)(&self.kind, t); // an app said no (unsaved work)
            } else {
                (self.on_done)(&self.kind); // only background stragglers: go on
            }
            return false;
        }
        true
    }

    /// (tests: time passes)
    pub fn advance(&self, left_ms: i64, waited_ms: i64) {
        let mut st = self.st.borrow_mut();
        st.left_ms -= left_ms;
        st.waited_ms += waited_ms;
    }
}

pub const CANCELLED_TEXT: [(&str, &str); 3] = [("restart", n_!("{name} canceled restart.")), ("shutdown", n_!("{name} canceled shut down.")), ("logout", n_!("{name} canceled log out."))];

/// "Chrome canceled restart." -- the app that kept its window.
pub fn cancelled_text(kind: &str, t: &Toplevel) -> String {
    let info = sonata_desktop::appmatch::match_app_id(&t.app_id).and_then(|d| sonata_ui::apps::lookup(&d));
    let app_name = info.map(|i| {
        use gio::prelude::AppInfoExt;
        i.display_name().to_string()
    });
    cancelled_text_for(kind, t, app_name)
}

/// The same, with the app's name from its .desktop already looked up.
pub fn cancelled_text_for(kind: &str, t: &Toplevel, app_name: Option<String>) -> String {
    let name = match app_name {
        Some(n) => n,
        None if !t.title.is_empty() => t.title.clone(),
        None if !t.app_id.is_empty() => t.app_id.clone(),
        None => tr!("An app"),
    };
    match CANCELLED_TEXT.iter().find(|(k, _)| *k == kind) {
        Some((_, text)) => fmt(&gettext(text), &[("name", &name)]),
        None => format!("{name} canceled {kind}."),
    }
}

fn cancelled(kind: &str, t: &Toplevel) {
    sonata_ui::dialog::alert(&cancelled_text(kind, t), &tr!("Quit it, then try again."), &[("ok", tr!("OK"), "default")], |_| {}, None);
}

/// Quit (the Dock's menu, the app menu): the app's windows close, then the
/// app itself gets SIGTERM if it's still running without one (Chrome in
/// the background) -- macOS quits the whole app (Vini). One that keeps a
/// window ("Save changes?") just stays, nothing else happens.
pub fn quit_app(windows: Rc<dyn Windows>, these: &[Toplevel], key: &str) -> Rc<Quitter> {
    let q = Quitter::new("quit", Some(windows), Box::new(|_| {}), Box::new(|_, _| {}), Effects::default(), &HashSet::new, Some(these), Some(key));
    q.start();
    q
}

/// Quit the apps, then sleep / restart / shut down / log out
/// (system::power_action). reopen: the apps open now come back at the next
/// login (None: the last choice of the confirmation's box). Kept before
/// they quit; dropped if an app stops it (the session goes on).
///
/// On trial too: it's the user asking to restart -- the same as the
/// Python one does, with the same files.
pub fn end_session(kind: &str, windows: Option<Rc<dyn Windows>>, reopen: Option<bool>) -> Rc<Quitter> {
    use sonata_desktop::open_apps;
    // none handed in: Wayfire's own list, or no window would be asked to
    // close (Chrome then offered to restore its tabs)
    let windows = windows.or_else(session_windows);
    if SESSION_ENDS.contains(&kind) {
        std::thread::spawn(sonata_backend::vault::close_all); // the locked folders closed with the session
        if reopen.unwrap_or_else(open_apps::reopen_wanted) {
            let ids: Vec<String> = windows.as_ref().map(|w| w.toplevels().into_iter().map(|t| t.app_id).collect()).unwrap_or_default();
            open_apps::save_for_login(&open_apps::current(&ids));
        } else {
            open_apps::drop_for_login();
        }
    }
    let q = Quitter::new(
        kind,
        windows,
        Box::new(sonata_backend::system::power_action),
        Box::new(|k, t| {
            sonata_desktop::open_apps::drop_for_login(); // still here: nothing to reopen next time
            cancelled(k, t);
        }),
        Effects::default(),
        &tray_pids,
        None,
        None,
    );
    q.start();
    q
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    struct Mgr {
        tops: RefCell<Vec<Toplevel>>,
        refuses: Vec<String>,
        closed: RefCell<Vec<String>>,
        pids: RefCell<std::collections::HashMap<u64, i64>>,
    }

    impl Windows for Mgr {
        fn toplevels(&self) -> Vec<Toplevel> {
            self.tops.borrow().clone()
        }
        fn close(&self, id: u64) {
            let t = self.tops.borrow().iter().find(|t| t.id == id).cloned().unwrap();
            self.closed.borrow_mut().push(t.app_id.clone());
            if !self.refuses.contains(&t.app_id) {
                self.tops.borrow_mut().retain(|x| x.id != id);
            }
        }
    }

    fn mgr(wins: &[(&str, i64)], refuses: &[&str]) -> Rc<Mgr> {
        let tops = wins.iter().enumerate().map(|(i, (a, _))| Toplevel { id: i as u64 + 1, app_id: a.to_string(), title: a.to_string() }).collect();
        let pids = wins.iter().enumerate().map(|(i, (_, p))| (i as u64 + 1, *p)).collect();
        Rc::new(Mgr { tops: RefCell::new(tops), refuses: refuses.iter().map(|s| s.to_string()).collect(), closed: RefCell::default(), pids: RefCell::new(pids) })
    }

    fn views_of(m: &Rc<Mgr>) -> Box<dyn Fn() -> Vec<Value>> {
        let m = m.clone();
        Box::new(move || m.tops.borrow().iter().map(|t| json!({"type": "toplevel", "pid": m.pids.borrow()[&t.id], "app-id": t.app_id})).collect())
    }

    type Log = Rc<RefCell<Vec<String>>>;

    fn run_quit(wins: &[(&str, i64)], tray: &[i64], refuses: &[&str], ignore_term: &[i64]) -> (Rc<Mgr>, Vec<(i64, i32)>, Vec<String>) {
        let m = mgr(wins, refuses);
        let running: Rc<RefCell<HashSet<i64>>> = Rc::new(RefCell::new(wins.iter().map(|w| w.1).chain(tray.iter().copied()).collect()));
        let killed = Rc::new(RefCell::new(Vec::new()));
        let result: Log = Rc::default();
        let (r1, r2, k, run1, run2) = (result.clone(), result.clone(), killed.clone(), running.clone(), running.clone());
        let ignore: Vec<i64> = ignore_term.to_vec();
        let fx = Effects {
            views: views_of(&m),
            alive: Box::new(move |p| run1.borrow().contains(&p)),
            kill: Box::new(move |p, s| {
                k.borrow_mut().push((p, s));
                if !ignore.contains(&p) {
                    run2.borrow_mut().remove(&p);
                }
            }),
        };
        let views = (fx.views)();
        let tray: HashSet<i64> = tray.iter().copied().collect();
        let pids = app_pids_with(&views, &tray, None, &|_| "/opt/app".into(), &|v| view_app(v).to_string());
        let q = Quitter::with_pids("restart", Some(m.clone() as Rc<dyn Windows>), Box::new(move |k| r1.borrow_mut().push(format!("done {k}"))), Box::new(move |_, t| r2.borrow_mut().push(format!("cancel {}", t.app_id))), fx, pids, None);
        q.st.borrow_mut().left_ms = 2000;
        while q.tick() {
            q.advance(POLL_MS, POLL_MS);
        }
        let mut k = killed.borrow().clone();
        k.sort();
        let r = result.borrow().clone();
        (m, k, r)
    }

    /// Vini: Chrome asked to restore its tabs after every restart -- it was
    /// killed with the session.
    #[test]
    fn test_windows_close_background_apps_get_sigterm_then_restart() {
        let (m, killed, result) = run_quit(&[("google-chrome", 100), ("org.gnome.TextEditor", 200)], &[300], &[], &[]); // Spotify: only a tray icon
        let mut closed = m.closed.borrow().clone();
        closed.sort();
        assert_eq!(closed, ["google-chrome", "org.gnome.TextEditor"]);
        assert_eq!(killed, [(100, libc::SIGTERM), (200, libc::SIGTERM), (300, libc::SIGTERM)]);
        assert_eq!(result, ["done restart"]);
    }

    /// 'Save changes?': the restart stops, like on macOS.
    #[test]
    fn an_app_that_keeps_its_window_cancels() {
        let (_m, killed, result) = run_quit(&[("google-chrome", 100), ("libreoffice", 200)], &[], &["libreoffice"], &[]);
        assert_eq!(result, ["cancel libreoffice"]);
        assert!(!killed.contains(&(200, libc::SIGTERM))); // never signalled while it asks
    }

    #[test]
    fn a_stuck_background_process_doesnt_block() {
        let (_m, _k, result) = run_quit(&[], &[300], &[], &[300]);
        assert_eq!(result, ["done restart"]);
    }

    #[test]
    fn never_sonata_itself_nor_xwayland() {
        let views = vec![
            json!({"type": "toplevel", "pid": 10, "app-id": "io.github.vinioliveiras.sonata2.files"}),
            json!({"type": "toplevel", "pid": 11, "app-id": "steam"}),
            json!({"type": "toplevel", "pid": 12, "app-id": "google-chrome"}),
        ];
        let cmd = |p: i64| match p {
            11 => "/usr/bin/Xwayland :0 -rootless".to_string(),
            12 => "/opt/google/chrome/chrome".to_string(),
            _ => String::new(),
        };
        let got = app_pids_with(&views, &[12].into_iter().collect(), None, &cmd, &|v| view_app(v).to_string());
        assert_eq!(got, [12].into_iter().collect());
        // the trial build's own windows too
        let rs = vec![json!({"type": "toplevel", "pid": 13, "app-id": "io.github.vinioliveiras.sonata2rs.clock"})];
        assert!(app_pids_with(&rs, &HashSet::new(), None, &|_| String::new(), &|v| view_app(v).to_string()).is_empty());
    }

    /// Vini: Quit in the Dock closed the windows but Chrome kept running in
    /// the background; it quits the app now, and only that app.
    #[test]
    fn test_quit_quits_the_whole_app() {
        let m = mgr(&[("google-chrome", 100), ("google-chrome", 100), ("org.gnome.TextEditor", 200)], &[]);
        let running = Rc::new(RefCell::new(HashSet::from([100i64, 200])));
        let killed = Rc::new(RefCell::new(Vec::new()));
        let done: Log = Rc::default();
        let (run1, run2, k, d1, d2) = (running.clone(), running.clone(), killed.clone(), done.clone(), done.clone());
        let fx = Effects {
            views: views_of(&m),
            alive: Box::new(move |p| run1.borrow().contains(&p)),
            kill: Box::new(move |p, s| {
                k.borrow_mut().push((p, s));
                run2.borrow_mut().remove(&p);
            }),
        };
        let only: Vec<Toplevel> = m.toplevels()[..2].to_vec();
        let pids = app_pids_with(&(fx.views)(), &HashSet::new(), Some("google-chrome"), &|_| "/opt/app".into(), &|v| view_app(v).to_string());
        let q = Quitter::with_pids("quit", Some(m.clone() as Rc<dyn Windows>), Box::new(move |k| d1.borrow_mut().push(k.to_string())), Box::new(move |_, _| d2.borrow_mut().push("no".into())), fx, pids, Some(&only));
        while q.tick() {
            q.advance(0, POLL_MS);
        }
        assert_eq!(*m.closed.borrow(), ["google-chrome", "google-chrome"]);
        assert_eq!(*killed.borrow(), [(100, libc::SIGTERM)]); // the app itself, nothing else
        assert_eq!(m.toplevels().iter().map(|t| t.app_id.as_str()).collect::<Vec<_>>(), ["org.gnome.TextEditor"]);
        assert_eq!(*done.borrow(), ["quit"]);
    }

    #[test]
    fn cancelled_says_which_app() {
        let _e = crate::testenv::env();
        let t = Toplevel { id: 1, app_id: "no-such-app-xyz".into(), title: "Report.odt".into() };
        assert_eq!(cancelled_text_for("restart", &t, Some("LibreOffice".into())), "LibreOffice canceled restart.");
        assert_eq!(cancelled_text_for("restart", &t, None), "Report.odt canceled restart.");
        assert_eq!(cancelled_text_for("logout", &Toplevel { title: String::new(), ..t.clone() }, None), "no-such-app-xyz canceled log out.");
        assert_eq!(cancelled_text_for("shutdown", &Toplevel { title: String::new(), app_id: String::new(), ..t }, None), "An app canceled shut down.");
    }

    /// Settings > Restart / Log Out asked no window to close (it had no
    /// toplevel manager to hand in): the restart waited its 12 s and Chrome
    /// was killed with the session, then offered to restore its tabs.
    /// Wayfire's own list (IpcWindows) now asks each app window to close.
    #[test]
    fn test_settings_restart_asked_no_window_to_close() {
        let views = json!([
            {"id": 7, "type": "toplevel", "role": "toplevel", "mapped": true, "app-id": "google-chrome", "title": "Inbox", "pid": 4000},
            {"id": 8, "type": "panel", "role": "desktop-environment", "app-id": "io.github.vinioliveiras.sonata2.dock", "pid": 1},
            {"id": 9, "type": "toplevel", "role": "toplevel", "mapped": false, "app-id": "gone", "pid": 4001},
            {"id": 10, "type": "toplevel", "role": "toplevel", "app-id": "io.github.vinioliveiras.sonata2rs.settings", "title": "Settings", "pid": 1}
        ]);
        let list: Vec<Value> = views.as_array().unwrap().clone();
        let tops = toplevels_of(&list);
        assert_eq!(tops.iter().map(|t| t.id).collect::<Vec<_>>(), [7, 10]);
        let closed: Rc<RefCell<Vec<u64>>> = Rc::default();
        let c = closed.clone();
        let w: Rc<dyn Windows> = Rc::new(IpcWindows::new(Box::new(move || list.clone()), Box::new(move |id| c.borrow_mut().push(id))));
        let fx = Effects { views: Box::new(Vec::new), alive: Box::new(|_| false), kill: Box::new(|_, _| {}) };
        let q = Quitter::with_pids("restart", Some(w), Box::new(|_| {}), Box::new(|_, _| {}), fx, HashSet::new(), None);
        q.tick();
        assert_eq!(*closed.borrow(), [7]); // Chrome asked; Sonata's own Settings left alone
        // (no Wayfire in the tests: nothing to list)
        assert!(session_windows().is_none());
    }
}
