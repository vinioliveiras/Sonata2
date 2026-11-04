//! Settings > Desktop & Windows > "Quit apps when their last window
//! closes" (on by default; shell/quitonclose.py -- Vini: closing Steam with
//! its window's X left it running in the background).
//!
//! When an app's last window goes and none comes back within GRACE_MS
//! (Steam swaps its sign-in window for the main one), the whole app gets
//! SIGTERM -- the polite quit (quitapps): the window's process, the app it
//! belongs to (up to what launched it: Sonata, systemd) and everything
//! under that. Only the window's process came back: Steam restarted its
//! web helper, and its window with it (Vini). An app with a window of
//! another app under it (a game Steam started) is left alone, and so is
//! Sonata itself.
//!
//! ```text
//! let q = QuitOnClose::start(windows);   // the Dock's toplevel manager
//! manager.listeners.push(move || q.changed());
//! ```

use crate::quitapps::{self, Windows, OWN};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

pub const GRACE_MS: u64 = 1500;
/// an app whose process wasn't found: asked again this often
pub const RETRY_S: f64 = 30.0;
/// then whatever is left of it goes (Vini: "just kill the process")
pub const KILL_MS: u64 = 3000;

/// what launched an app, never part of it (a terminal's shell too: an
/// app's own script has its name)
pub const BOUNDARY: [&str; 14] = ["systemd", "wayfire", "dbus-daemon", "dbus-broker", "xdg-desktop-portal", "greetd", "login", "bash", "fish", "zsh", "sh", "dash", "tmux", "screen"];

/// pid -> (ppid, comm, cmdline)
pub type Table = HashMap<i64, (i64, String, String)>;

fn cache(parts: &[&str]) -> std::path::PathBuf {
    let home = std::env::var_os("HOME").map(std::path::PathBuf::from).unwrap_or_default();
    let mut p = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(std::path::PathBuf::from).unwrap_or_else(|| home.join(".cache")).join("sonata2");
    for x in parts {
        p.push(x);
    }
    p
}

pub fn norm(did: &str) -> &str {
    did.strip_suffix(".desktop").unwrap_or(did)
}

fn launched_file(key: &str) -> std::path::PathBuf {
    cache(&["launched", &norm(key).replace('/', "_")])
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Sonata opened this app (any Sonata process): a quit pending for it is
/// called off -- opened again right after closing it, the app was quit
/// while it came back (Vini: apps didn't open). The Python Sonata's folder:
/// either build's Dock sees either build's launches.
pub fn mark_launch(desktop_id: &str) {
    if desktop_id.is_empty() {
        return;
    }
    let f = launched_file(desktop_id);
    if let Some(d) = f.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = std::fs::write(f, format!("{}", now()));
}

pub fn launched_at(key: &str) -> f64 {
    std::fs::metadata(launched_file(key)).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// quit.log in the logs' folder: what was quit and why not (small, rotated).
pub fn log(text: &str) {
    let dir = sonata_session::logs::log_dir();
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join("quit.log");
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > 200_000) {
        let _ = std::fs::rename(&path, dir.join("quit.log.old"));
    }
    let stamp = glib::DateTime::now_local().ok().and_then(|t| t.format("%H:%M:%S").ok()).map(|s| s.to_string()).unwrap_or_default();
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{stamp} {text}");
    }
}

fn boot_time() -> i64 {
    static BT: std::sync::OnceLock<i64> = std::sync::OnceLock::new();
    *BT.get_or_init(|| {
        std::fs::read_to_string("/proc/stat").ok().and_then(|s| s.lines().find_map(|l| l.strip_prefix("btime ").and_then(|v| v.trim().parse().ok()))).unwrap_or(0)
    })
}

/// When pid started (epoch seconds), 0 unknown.
pub fn started_at(pid: i64) -> f64 {
    let clk = unsafe { libc::sysconf(libc::_SC_CLK_TCK) }.max(1) as f64;
    std::fs::read(format!("/proc/{pid}/stat"))
        .ok()
        .and_then(|b| {
            let s = String::from_utf8_lossy(&b).to_string();
            let rest = s.rsplit_once(')')?.1.to_string();
            rest.split_whitespace().nth(19)?.parse::<f64>().ok()
        })
        .map(|ticks| boot_time() as f64 + ticks / clk)
        .unwrap_or(0.0)
}

/// The setting (dock.json "quit_on_close", on by default).
pub fn wanted() -> bool {
    sonata_config::load("dock", &json!({"quit_on_close": true})).get("quit_on_close").is_some_and(crate::truthy)
}

/// Every process (from /proc).
pub fn proc_table() -> Table {
    let mut out = Table::new();
    let Ok(dir) = std::fs::read_dir("/proc") else { return out };
    for e in dir.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        let Ok(pid) = name.parse::<i64>() else { continue };
        let Ok(st) = std::fs::read(format!("/proc/{pid}/stat")) else { continue };
        let st = String::from_utf8_lossy(&st).to_string();
        let (Some(a), Some(b)) = (st.find('('), st.rfind(')')) else { continue };
        let comm = st[a + 1..b].to_string();
        let Some(ppid) = st[b + 1..].split_whitespace().nth(1).and_then(|p| p.parse().ok()) else { continue };
        let Ok(cmd) = std::fs::read(format!("/proc/{pid}/cmdline")) else { continue };
        out.insert(pid, (ppid, comm, String::from_utf8_lossy(&cmd).replace('\0', " ")));
    }
    out
}

/// The topmost process of pid's app: its ancestors up to (not including)
/// what launched it -- Sonata, systemd, the compositor -- or init.
pub fn app_root(mut pid: i64, table: &Table) -> i64 {
    for _ in 0..64 {
        let ppid = table.get(&pid).map_or(0, |e| e.0);
        let Some((_, comm, cmd)) = table.get(&ppid).filter(|_| ppid > 1) else { return pid };
        if cmd.contains(OWN) || BOUNDARY.contains(&comm.as_str()) || comm.starts_with("systemd") {
            return pid;
        }
        pid = ppid;
    }
    pid
}

/// root and every process under it, root first.
pub fn subtree(root: i64, table: &Table) -> Vec<i64> {
    let mut kids: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut pids: Vec<&i64> = table.keys().collect();
    pids.sort(); // (Python: the table's order, /proc's)
    for pid in pids {
        kids.entry(table[pid].0).or_default().push(*pid);
    }
    let (mut out, mut todo) = (Vec::new(), std::collections::VecDeque::from([root]));
    while let Some(p) = todo.pop_front() {
        if out.contains(&p) {
            continue;
        }
        out.push(p);
        todo.extend(kids.get(&p).cloned().unwrap_or_default());
    }
    out
}

/// What QuitOnClose does outside itself (tests replace them).
pub struct Effects {
    pub views: Box<dyn Fn() -> Vec<Value>>,
    pub kill: Box<dyn Fn(i64, i32)>,
    pub alive: Box<dyn Fn(i64) -> bool>,
    pub enabled: Box<dyn Fn() -> bool>,
    pub table: Box<dyn Fn() -> Table>,
    pub later: Box<dyn Fn(u64, Box<dyn FnOnce()>)>,
    pub launched: Box<dyn Fn(&str) -> f64>,
    pub started: Box<dyn Fn(i64) -> f64>,
    pub now: Box<dyn Fn() -> f64>,
    pub key_of: Box<dyn Fn(&str) -> String>,
    /// an app's processes from the views (quitapps::app_pids)
    pub app_pids: Box<dyn Fn(&[Value], &str) -> HashSet<i64>>,
    pub log: Box<dyn Fn(&str)>,
}

impl Default for Effects {
    fn default() -> Self {
        Effects {
            views: Box::new(quitapps::views),
            kill: Box::new(|p, s| {
                quitapps::kill(p, s);
            }),
            alive: Box::new(quitapps::alive),
            enabled: Box::new(wanted),
            table: Box::new(proc_table),
            later: Box::new(|ms, f| {
                glib::timeout_add_local_once(std::time::Duration::from_millis(ms), f);
            }),
            launched: Box::new(launched_at),
            started: Box::new(started_at),
            now: Box::new(now),
            key_of: Box::new(quitapps::key_of),
            app_pids: Box::new(|v, k| quitapps::app_pids(v, &HashSet::new(), Some(k))),
            log: Box::new(log),
        }
    }
}

#[derive(Default)]
struct State {
    closed_at: HashMap<String, f64>,
    /// apps (keys) with a window now
    open: HashSet<String>,
    /// key -> its processes, read while it had a window
    pids: HashMap<String, HashSet<i64>>,
    /// key -> when its window came
    seen: HashMap<String, f64>,
    /// when list-views was last read for a late one
    asked: f64,
}

pub struct QuitOnClose {
    windows: Rc<dyn Windows>,
    fx: Effects,
    st: RefCell<State>,
    me: std::rc::Weak<QuitOnClose>,
}

impl QuitOnClose {
    /// None on trial: the Python Dock quits closed apps already.
    pub fn start(windows: Rc<dyn Windows>) -> Option<Rc<Self>> {
        if sonata_config::side_by_side() {
            return None;
        }
        Some(Self::with(windows, Effects::default()))
    }

    pub fn with(windows: Rc<dyn Windows>, fx: Effects) -> Rc<Self> {
        let me = Rc::new_cyclic(|w| QuitOnClose { windows, fx, st: RefCell::new(State { asked: f64::NEG_INFINITY, ..State::default() }), me: w.clone() });
        me.changed();
        me
    }

    fn keys(&self) -> HashSet<String> {
        self.windows.toplevels().iter().filter(|t| !t.app_id.is_empty() && !t.app_id.contains(OWN)).map(|t| (self.fx.key_of)(&t.app_id)).collect()
    }

    /// The windows changed (the toplevel manager's listener).
    pub fn changed(&self) {
        let now = self.keys();
        let t = (self.fx.now)();
        let (new, gone, need) = {
            let mut st = self.st.borrow_mut();
            let new: Vec<String> = now.difference(&st.open).cloned().collect();
            let gone: Vec<String> = st.open.difference(&now).cloned().collect();
            for k in &new {
                st.seen.insert(k.clone(), t);
            }
            // new apps, and open ones whose window the compositor hadn't listed
            // yet (the Claude app kept running: no process was known for it);
            // one still unknown RETRY_S after its window came -- a Flatpak --
            // asked again only that often (not on every title change)
            let retry = t - st.asked >= RETRY_S;
            let mut need: HashSet<String> = new.iter().cloned().collect();
            for k in &now {
                if st.pids.get(k).is_none_or(HashSet::is_empty) && (retry || t - st.seen.get(k).copied().unwrap_or(t) < RETRY_S) {
                    need.insert(k.clone());
                }
            }
            (new, gone, need)
        };
        let _ = new;
        if !need.is_empty() {
            self.st.borrow_mut().asked = t;
            let views = (self.fx.views)();
            for k in need {
                let found = (self.fx.app_pids)(&views, &k);
                let mut st = self.st.borrow_mut();
                if !found.is_empty() {
                    st.pids.insert(k, found);
                } else {
                    st.pids.entry(k).or_default();
                }
            }
        }
        for k in gone {
            {
                let mut st = self.st.borrow_mut();
                st.seen.remove(&k);
                st.closed_at.insert(k.clone(), (self.fx.now)());
            }
            let w = self.me.clone();
            (self.fx.later)(
                GRACE_MS,
                Box::new(move || {
                    if let Some(me) = w.upgrade() {
                        me.maybe_quit(&k);
                    }
                }),
            );
        }
        self.st.borrow_mut().open = now;
    }

    /// The processes known for an app (tests).
    pub fn pids(&self, key: &str) -> HashSet<i64> {
        self.st.borrow().pids.get(key).cloned().unwrap_or_default()
    }

    pub fn open(&self) -> HashSet<String> {
        self.st.borrow().open.clone()
    }

    /// (tests: an app seen with these processes, closed at `when`)
    pub fn note(&self, key: &str, pids: HashSet<i64>, closed: f64) {
        let mut st = self.st.borrow_mut();
        st.pids.insert(key.into(), pids);
        st.closed_at.insert(key.into(), closed);
    }

    pub fn maybe_quit(&self, key: &str) -> bool {
        if self.keys().contains(key) {
            return false; // a window came back
        }
        let (pids, closed) = {
            let mut st = self.st.borrow_mut();
            let pids = st.pids.remove(key).unwrap_or_default();
            let closed = st.closed_at.remove(key);
            (pids, closed.unwrap_or_else(|| (self.fx.now)()))
        };
        if !(self.fx.enabled)() {
            return false;
        }
        let log = &self.fx.log;
        if (self.fx.launched)(key) >= closed {
            log(&format!("{key}: opened again, kept"));
            return false;
        }
        if pids.is_empty() {
            log(&format!("{key}: no process known, nothing quit"));
            return false;
        }
        let windows = quitapps::with_window(&(self.fx.views)());
        let table = (self.fx.table)();
        let (mut targets, mut roots): (Vec<i64>, Vec<i64>) = (Vec::new(), Vec::new());
        let mut sorted: Vec<i64> = pids.into_iter().collect();
        sorted.sort();
        for pid in sorted {
            if !(self.fx.alive)(pid) || windows.contains(&pid) {
                continue;
            }
            let root = app_root(pid, &table);
            let tree = subtree(root, &table);
            if tree.iter().any(|p| windows.contains(p)) {
                log(&format!("{key}: a window under it (a game it started), kept"));
                return false;
            }
            if tree.iter().any(|p| (self.fx.started)(*p) > closed) {
                log(&format!("{key}: a process started after it closed (opened again), kept"));
                return false;
            }
            for p in tree {
                if !targets.contains(&p) {
                    targets.push(p);
                }
            }
            if !roots.contains(&root) {
                roots.push(root);
            }
        }
        let cmd_of = |p: &i64| table.get(p).map(|e| e.2.clone()).unwrap_or_default();
        targets.retain(|p| !cmd_of(p).contains(OWN));
        // SIGTERM to the app itself (a launcher script: the program it runs),
        // which closes its own helpers: SIGTERM to every helper at once left
        // Claude's main process alive without its GPU process, and it
        // couldn't be opened again until killed by hand (Vini)
        let mut first = roots.clone();
        for r in &roots {
            let comm = table.get(r).map(|e| e.1.as_str()).unwrap_or("");
            if comm.ends_with(".sh") || BOUNDARY.contains(&comm) {
                let mut kids: Vec<i64> = table.iter().filter(|(p, e)| e.0 == *r && !first.contains(p)).map(|(p, _)| *p).collect();
                kids.sort();
                first.extend(kids);
            }
        }
        first.retain(|p| targets.contains(p));
        log(&format!("{key}: quit {first:?} (then all of {targets:?} after {KILL_MS} ms)"));
        for pid in &first {
            (self.fx.kill)(*pid, libc::SIGTERM);
        }
        let quit_any = !first.is_empty();
        let w = self.me.clone();
        let key = key.to_string();
        (self.fx.later)(
            KILL_MS,
            Box::new(move || {
                let Some(me) = w.upgrade() else { return };
                // every process read once (Vini: closing apps lagged -- the Dock
                // read all of /proc again for each of the app's processes)
                let now = (me.fx.table)();
                let left: Vec<i64> = targets.iter().copied().filter(|p| (me.fx.alive)(*p) && now.get(p).map(|e| &e.2) == table.get(p).map(|e| &e.2)).collect(); // the same process, not a reused pid
                if !left.is_empty() {
                    (me.fx.log)(&format!("{key}: killed {left:?}"));
                }
                for p in left {
                    (me.fx.kill)(p, libc::SIGKILL);
                }
            }),
        );
        quit_any
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::quitapps::Toplevel;
    use std::cell::Cell;

    #[derive(Default)]
    struct Mgr(RefCell<Vec<Toplevel>>);

    impl Windows for Mgr {
        fn toplevels(&self) -> Vec<Toplevel> {
            self.0.borrow().clone()
        }
        fn close(&self, _id: u64) {}
    }

    fn change(m: &Mgr, q: &QuitOnClose, ids: &[&str]) {
        *m.0.borrow_mut() = ids.iter().enumerate().map(|(i, a)| Toplevel { id: i as u64, app_id: a.to_string(), title: String::new() }).collect();
        q.changed();
    }

    fn table(v: &[(i64, i64, &str, &str)]) -> Table {
        v.iter().map(|(p, pp, c, m)| (*p, (*pp, c.to_string(), m.to_string()))).collect()
    }

    #[test]
    fn root_stops_at_what_launched_it() {
        let mut t = table(&[(10, 1, "systemd", "/usr/lib/systemd/systemd --user"), (20, 10, "chrome", "chrome"), (21, 20, "chrome", "chrome --type=gpu")]);
        assert_eq!(app_root(21, &t), 20);
        assert_eq!(subtree(20, &t), [20, 21]);
        t.insert(20, (30, "chrome".into(), "chrome".into())); // started from a terminal's shell
        t.insert(30, (40, "fish".into(), "fish".into()));
        t.insert(40, (1, "python3".into(), "python3 -m sonata2 terminal".into()));
        assert_eq!(app_root(21, &t), 20); // the shell (and terminal) stay
    }

    // dock (sonata2) -> steam.sh -> steam -> steamwebhelper (the window) ; steam -> game
    fn steam(with_game: bool) -> Table {
        let mut t = table(&[(100, 1, "python3", "python3 -m sonata2 dock"), (400, 100, "steam.sh", "bash steam.sh"), (450, 400, "steam", "steam"), (500, 450, "steamwebhelper", "steamwebhelper")]);
        if with_game {
            t.insert(900, (450, "game.exe".into(), "game.exe".into()));
        }
        t
    }

    struct Rig {
        m: Rc<Mgr>,
        q: Rc<QuitOnClose>,
        killed: Rc<RefCell<Vec<(i64, i32)>>>,
        timers: Rc<RefCell<Vec<Box<dyn FnOnce()>>>>,
        views: Rc<RefCell<Vec<Value>>>,
        tables: Rc<Cell<usize>>,
    }

    fn make(views: Vec<Value>, enabled: bool, t: Table, launched: f64, started: fn(i64) -> f64, pids: Rc<dyn Fn(&[Value]) -> HashSet<i64>>) -> Rig {
        let m = Rc::new(Mgr::default());
        let killed = Rc::new(RefCell::new(Vec::new()));
        let timers: Rc<RefCell<Vec<Box<dyn FnOnce()>>>> = Rc::default();
        let views = Rc::new(RefCell::new(views));
        let tables = Rc::new(Cell::new(0));
        let (k, tm, v, tc) = (killed.clone(), timers.clone(), views.clone(), tables.clone());
        let fx = Effects {
            views: Box::new(move || v.borrow().clone()),
            kill: Box::new(move |p, s| k.borrow_mut().push((p, s))),
            alive: Box::new(|_| true),
            enabled: Box::new(move || enabled),
            table: Box::new(move || {
                tc.set(tc.get() + 1);
                t.clone()
            }),
            later: Box::new(move |_, f| tm.borrow_mut().push(f)),
            launched: Box::new(move |_| launched),
            started: Box::new(started),
            now: Box::new(|| 100.0),
            key_of: Box::new(|a| a.to_string()),
            app_pids: Box::new(move |v, _k| pids(v)),
            log: Box::new(|_| {}),
        };
        let q = QuitOnClose::with(m.clone(), fx);
        Rig { m, q, killed, timers, views, tables }
    }

    fn run_timers(r: &Rig) {
        loop {
            let next = {
                let mut t = r.timers.borrow_mut();
                if t.is_empty() { None } else { Some(t.remove(0)) }
            };
            match next {
                Some(f) => f(),
                None => break,
            }
        }
    }

    fn always(p: i64) -> Rc<dyn Fn(&[Value]) -> HashSet<i64>> {
        Rc::new(move |_| HashSet::from([p]))
    }

    fn sorted(r: &Rig, sig: i32) -> Vec<i64> {
        let mut v: Vec<i64> = r.killed.borrow().iter().filter(|k| k.1 == sig).map(|k| k.0).collect();
        v.sort();
        v
    }

    /// Vini: closing Steam with its window's X left it running in the background.
    #[test]
    fn test_last_window_closed_quits_the_app() {
        let r = make(vec![json!({"type": "toplevel", "pid": 500, "app-id": "steam"})], true, steam(false), 0.0, |_| 0.0, always(500));
        change(&r.m, &r.q, &["steam"]);
        r.views.borrow_mut().clear();
        change(&r.m, &r.q, &[]);
        run_timers(&r);
        // the whole app, not just its window's process: Steam restarted its
        // web helper (the window's process) and the window came back (Vini).
        // SIGTERM to the app (a launcher script: its program); what's left
        // after KILL_MS gets SIGKILL
        assert_eq!(sorted(&r, libc::SIGTERM), [400, 450]);
        assert_eq!(sorted(&r, libc::SIGKILL), [400, 450, 500]);
        assert!(!r.killed.borrow().iter().any(|k| k.0 == 100)); // never Sonata
    }

    /// Steam swaps its sign-in window for the main one.
    #[test]
    fn a_window_back_within_the_grace_keeps_it() {
        let r = make(vec![json!({"type": "toplevel", "pid": 500})], true, steam(false), 0.0, |_| 0.0, always(500));
        change(&r.m, &r.q, &["steam"]);
        change(&r.m, &r.q, &[]);
        change(&r.m, &r.q, &["steam"]);
        run_timers(&r);
        assert!(r.killed.borrow().is_empty());
    }

    /// Vini: apps didn't open -- closed and opened again, the pending quit
    /// ended the app coming back.
    #[test]
    fn test_opened_again_right_after_is_kept() {
        let r = make(vec![json!({"type": "toplevel", "pid": 500})], true, steam(false), 101.0, |_| 0.0, always(500)); // Sonata launched it after the close
        change(&r.m, &r.q, &["steam"]);
        r.views.borrow_mut().clear();
        change(&r.m, &r.q, &[]);
        run_timers(&r);
        assert!(r.killed.borrow().is_empty());
        let r = make(vec![json!({"type": "toplevel", "pid": 500})], true, steam(false), 0.0, |p| if p == 450 { 150.0 } else { 0.0 }, always(500));
        change(&r.m, &r.q, &["steam"]);
        change(&r.m, &r.q, &[]);
        run_timers(&r);
        assert!(r.killed.borrow().is_empty()); // a process newer than the close
    }

    /// The Claude app stayed: its window wasn't listed yet when it appeared.
    #[test]
    fn test_process_found_once_the_compositor_lists_it() {
        let r = make(vec![], true, steam(false), 0.0, |_| 0.0, Rc::new(|v: &[Value]| if v.is_empty() { HashSet::new() } else { HashSet::from([500]) }));
        change(&r.m, &r.q, &["steam"]);
        assert!(r.q.pids("steam").is_empty());
        r.views.borrow_mut().push(json!({"type": "toplevel", "pid": 500}));
        change(&r.m, &r.q, &["steam"]); // e.g. a title change
        assert_eq!(r.q.pids("steam"), HashSet::from([500]));
    }

    #[test]
    fn turned_off_nothing_quits() {
        let r = make(vec![], false, steam(false), 0.0, |_| 0.0, always(500));
        change(&r.m, &r.q, &["steam"]);
        change(&r.m, &r.q, &[]);
        run_timers(&r);
        assert!(r.killed.borrow().is_empty());
    }

    #[test]
    fn a_game_it_started_keeps_it_running() {
        let r = make(vec![json!({"type": "toplevel", "pid": 900, "app-id": "steam_app_1"})], true, steam(true), 0.0, |_| 0.0, always(500)); // the game's window
        change(&r.m, &r.q, &["steam", "steam_app_1"]);
        change(&r.m, &r.q, &["steam_app_1"]);
        run_timers(&r);
        assert!(r.killed.borrow().is_empty());
    }

    #[test]
    fn sonata_itself_is_never_watched() {
        let r = make(vec![], true, steam(false), 0.0, |_| 0.0, always(500));
        change(&r.m, &r.q, &["io.github.vinioliveiras.sonata2.files"]);
        assert!(r.q.open().is_empty());
    }

    /// Vini: closing apps lagged. The final check read the whole process
    /// table once per process of the app (Chrome: dozens) on the Dock's main loop.
    #[test]
    fn test_finish_reads_the_table_once() {
        let mut t = table(&[(10, 1, "chrome", "chrome")]);
        for p in 11..41 {
            t.insert(p, (10, "chrome".into(), format!("chrome --type=r{p}")));
        }
        let r = make(vec![], true, t, 0.0, |_| 0.0, Rc::new(|_: &[Value]| HashSet::new()));
        r.q.note("chrome", HashSet::from([10]), 100.0);
        assert!(r.q.maybe_quit("chrome"));
        r.tables.set(0);
        let f = r.timers.borrow_mut().pop().unwrap();
        f(); // finish(): SIGKILL what's left
        assert_eq!(r.tables.get(), 1);
    }

    #[test]
    fn launches_marked() {
        let _e = crate::testenv::env();
        assert_eq!(launched_at("firefox"), 0.0);
        mark_launch("firefox.desktop");
        assert!(launched_at("firefox") > 0.0);
        assert!(launched_at("firefox.desktop") > 0.0);
    }

    #[test]
    fn this_process_has_a_start_time() {
        assert!(started_at(std::process::id() as i64) > 1_000_000_000.0);
        assert_eq!(started_at(-5), 0.0);
        assert!(proc_table().contains_key(&(std::process::id() as i64)));
    }
}
