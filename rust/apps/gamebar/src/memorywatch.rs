//! The menu bar's side of memory management (shell/memorywatch.py): every
//! few seconds a look at the memory (two small files); under pressure the
//! apps in the background give memory back; the alert only as a last resort
//! (macOS: "Your system has run out of application memory"), and never
//! again for a while once dismissed.
//!
//! Vini: the alert must not be a nuisance -- it comes only when the memory
//! is nearly gone AND the whole system stalls on it for CRITICAL_CHECKS
//! checks in a row, after the background apps already gave memory back (and
//! systemd-oomd had its chance), and at most once per ALERT_COOLDOWN_S.
//!
//! backend/memguard.py isn't ported anywhere else yet: its GTK-free half is
//! [`guard`] below (it moves to sonata-backend once something else needs it).
//!
//! ```no_run
//! # fn f(app: &gtk::Application) {
//! let watch = sonata_gamebar::memorywatch::MemoryWatch::start(app);   // the menu bar keeps it
//! # }
//! ```

use adw::prelude::*;
use serde_json::Value;
use sonata_i18n::tr;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const CHECK_S: u32 = 5;
pub const CRITICAL_CHECKS: u32 = 3; // 15 s in a row
pub const ALERT_COOLDOWN_S: u64 = 600;
pub const ALERT_ROWS: usize = 8;

/// backend/memguard.py: the apps' scopes under sonata-apps.slice, the
/// memory level, giving memory back and Force Quit. No GTK here.
pub mod guard {
    use super::*;

    /// less free than this (or the PSI below): background apps give memory back
    pub const PRESSURE_SHARE: f64 = 0.15;
    /// % of time some task stalled on memory (avg10)
    pub const PRESSURE_PSI: f64 = 10.0;
    /// the alert: less free than this (and at least CRITICAL_MIN_MB)...
    pub const CRITICAL_SHARE: f64 = 0.05;
    pub const CRITICAL_MIN_MB: u64 = 512;
    /// ...and every task stalled this much (full avg10)
    pub const CRITICAL_PSI: f64 = 10.0;
    /// of a background app's memory per pass
    pub const RECLAIM_SHARE: f64 = 0.25;
    pub const RECLAIM_MAX_MB: u64 = 1024;
    /// per app
    pub const RECLAIM_EVERY_S: u64 = 30;

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub enum Level {
        Ok,
        /// background apps give memory back
        Pressure,
        /// the alert's condition
        Critical,
    }

    #[derive(Clone, Debug, PartialEq)]
    pub struct Scope {
        pub path: PathBuf,
        pub unit: String,
        /// bytes
        pub memory: u64,
        pub pids: Vec<i64>,
    }

    pub fn slice_dir(uid: u32) -> PathBuf {
        PathBuf::from(format!("/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/sonata-apps.slice"))
    }

    pub fn own_slice_dir() -> PathBuf {
        // SAFETY: getuid has no failure mode
        slice_dir(unsafe { libc::getuid() })
    }

    /// (total, available) in MB; None for what couldn't be read.
    pub fn meminfo(path: &Path) -> (Option<u64>, Option<u64>) {
        let (mut total, mut avail) = (None, None);
        let text = std::fs::read_to_string(path).unwrap_or_default();
        for line in text.lines() {
            let Some((k, v)) = line.split_once(':') else { continue };
            let kb = v.split_whitespace().next().and_then(|n| n.parse::<u64>().ok());
            match k {
                "MemTotal" => total = kb.map(|n| n / 1024),
                "MemAvailable" => avail = kb.map(|n| n / 1024),
                _ => {}
            }
        }
        (total, avail)
    }

    /// (some, full): avg10 of the memory pressure (0 when unknown).
    pub fn psi(path: &Path) -> (f64, f64) {
        let (mut some, mut full) = (0.0, 0.0);
        for line in std::fs::read_to_string(path).unwrap_or_default().lines() {
            let kind = line.split(' ').next().unwrap_or("");
            let avg = line.split_whitespace().find_map(|w| w.strip_prefix("avg10=")).and_then(|n| n.parse::<f64>().ok());
            match (kind, avg) {
                ("some", Some(v)) => some = v,
                ("full", Some(v)) => full = v,
                _ => {}
            }
        }
        (some, full)
    }

    pub fn level(total_mb: u64, available_mb: u64, (some, full): (f64, f64)) -> Level {
        if total_mb == 0 {
            return Level::Ok;
        }
        let (total, avail) = (total_mb as f64, available_mb as f64);
        if avail < (total * CRITICAL_SHARE).max(CRITICAL_MIN_MB as f64) && full >= CRITICAL_PSI {
            return Level::Critical;
        }
        if avail < total * PRESSURE_SHARE || some >= PRESSURE_PSI {
            return Level::Pressure;
        }
        Level::Ok
    }

    fn read_int(path: &Path) -> u64 {
        std::fs::read_to_string(path).ok().and_then(|t| t.trim().parse().ok()).unwrap_or(0)
    }

    /// The apps' scopes with something running in them.
    pub fn scopes(root: &Path) -> Vec<Scope> {
        let Ok(dir) = std::fs::read_dir(root) else { return Vec::new() };
        let mut names: Vec<String> = dir.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        names
            .into_iter()
            .filter_map(|name| {
                let path = root.join(&name);
                if !name.ends_with(".scope") || !path.is_dir() {
                    return None;
                }
                let procs = std::fs::read_to_string(path.join("cgroup.procs")).unwrap_or_default();
                // (one bad number: none of them, as int() raising would)
                let pids: Vec<i64> = procs.split_whitespace().map(|p| p.parse()).collect::<Result<_, _>>().unwrap_or_default();
                if pids.is_empty() {
                    return None;
                }
                Some(Scope { memory: read_int(&path.join("memory.current")), path, unit: name, pids })
            })
            .collect()
    }

    /// {pid: last focus} of the windows on screen (mapped, not minimized).
    pub fn on_screen(views: &[Value]) -> HashMap<i64, i64> {
        let mut out = HashMap::new();
        for v in views {
            let Some(o) = v.as_object() else { continue };
            let role_ok = o.get("role").and_then(Value::as_str).unwrap_or("toplevel") == "toplevel";
            let mapped = o.get("mapped").and_then(Value::as_bool).unwrap_or(true);
            let minimized = o.get("minimized").and_then(Value::as_bool).unwrap_or(false);
            let pid = o.get("pid").and_then(Value::as_i64).unwrap_or(0);
            if role_ok && mapped && !minimized && pid > 1 {
                let t = o.get("last-focus-timestamp").and_then(Value::as_i64).unwrap_or(0);
                let e = out.entry(pid).or_insert(0);
                *e = (*e).max(t);
            }
        }
        out
    }

    /// The scopes with no window on screen, the biggest first.
    pub fn background(all: &[Scope], views: &[Value]) -> Vec<Scope> {
        let shown = on_screen(views);
        let mut out: Vec<Scope> = all.iter().filter(|s| !s.pids.iter().any(|p| shown.contains_key(p))).cloned().collect();
        out.sort_by_key(|s| std::cmp::Reverse(s.memory));
        out
    }

    pub fn reclaim_amount(memory: u64) -> u64 {
        ((memory as f64 * RECLAIM_SHARE).min((RECLAIM_MAX_MB * 1024 * 1024) as f64)) as u64
    }

    /// The kernel takes `amount` bytes back from this scope (memory.reclaim).
    /// It may get less (EAGAIN): that's fine.
    pub fn reclaim(path: &Path, amount: u64) -> bool {
        amount >= 16 * 1024 * 1024 && write_existing(&path.join("memory.reclaim"), &amount.to_string())
    }

    /// Force Quit: every process of the app's scope at once (cgroup.kill).
    pub fn kill(path: &Path) -> bool {
        write_existing(&path.join("cgroup.kill"), "1")
    }

    // (cgroup files exist already: never create one by mistake)
    fn write_existing(path: &Path, text: &str) -> bool {
        use std::io::Write;
        std::fs::OpenOptions::new().write(true).truncate(true).open(path).and_then(|mut f| f.write_all(text.as_bytes())).is_ok()
    }

    fn unescape(text: &str) -> String {
        let mut out = String::new();
        let mut rest = text;
        while let Some(i) = rest.find("\\x") {
            out.push_str(&rest[..i]);
            let hex = rest.get(i + 2..i + 4).filter(|h| h.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c)));
            match hex.and_then(|h| u8::from_str_radix(h, 16).ok()) {
                Some(b) => {
                    out.push(b as char);
                    rest = &rest[i + 4..];
                }
                None => {
                    out.push_str("\\x");
                    rest = &rest[i + 2..];
                }
            }
        }
        out.push_str(rest);
        out
    }

    /// (desktop id, web app id) of a scope.
    pub fn app_of(scope: &Scope, proc_root: &Path) -> (Option<String>, Option<String>) {
        if let Some(mid) = scope.unit.strip_prefix("app-sonata2-").and_then(|r| r.strip_suffix(".scope")) {
            if let Some((name, n)) = mid.rsplit_once('-') {
                if !name.is_empty() && !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()) {
                    return (Some(unescape(name)), None);
                }
            }
        }
        for pid in scope.pids.iter().take(3) {
            // a web app's own scope: `sonata2 webapp <id>`
            let Ok(raw) = std::fs::read(proc_root.join(pid.to_string()).join("cmdline")) else { continue };
            let text = String::from_utf8_lossy(&raw);
            let args: Vec<&str> = text.split('\0').collect();
            for i in 1..args.len().saturating_sub(1) {
                if args[i] == "webapp" && args[i - 1] == "sonata2" {
                    return (None, Some(args[i + 1].to_string()));
                }
            }
        }
        (None, None)
    }
}

use guard::{Level, Scope};

pub fn size_text(n: u64) -> String {
    let gb = n as f64 / 1024f64.powi(3);
    if gb >= 1.0 {
        format!("{gb:.1} GB")
    } else {
        format!("{:.0} MB", n as f64 / 1024f64.powi(2))
    }
}

/// The watch's decisions, apart from GTK and the files (tested alone).
#[derive(Default)]
pub struct Watch {
    pub critical: u32,
    pub reclaimed: bool,
    pub busy: bool,
    pub alert_open: bool,
    /// (monotonic time can be small right after boot: an Option, not 0)
    pub dismissed_at: Option<Instant>,
    pub last_reclaim: HashMap<PathBuf, Instant>,
}

impl Watch {
    /// A check's level; true: background apps give memory back now.
    pub fn on_level(&mut self, lv: Level) -> bool {
        if lv == Level::Ok {
            self.critical = 0;
            self.reclaimed = false;
            return false;
        }
        self.critical = if lv == Level::Critical { self.critical + 1 } else { 0 };
        if self.busy {
            return false;
        }
        self.busy = true;
        true
    }

    /// The background apps had their turn; true: the alert shows now.
    pub fn after(&mut self, any_scopes: bool, now: Instant) -> bool {
        self.busy = false;
        self.reclaimed = true; // step 1 had its turn
        self.critical >= CRITICAL_CHECKS
            && self.reclaimed
            && !self.alert_open
            && any_scopes
            && self.dismissed_at.is_none_or(|t| now.duration_since(t) > Duration::from_secs(ALERT_COOLDOWN_S))
    }

    /// The scopes to give memory back now (each at most every RECLAIM_EVERY_S).
    pub fn due(&mut self, background: Vec<Scope>, now: Instant) -> Vec<Scope> {
        background
            .into_iter()
            .filter(|s| {
                let ok = self.last_reclaim.get(&s.path).is_none_or(|t| now.duration_since(*t) >= Duration::from_secs(guard::RECLAIM_EVERY_S));
                if ok {
                    self.last_reclaim.insert(s.path.clone(), now);
                }
                ok
            })
            .collect()
    }
}

/// (name, icon) of an app's scope.
pub fn app_label(scope: &Scope) -> (String, Option<gio::Icon>) {
    match guard::app_of(scope, Path::new("/proc")) {
        (_, Some(wid)) => {
            let apps = sonata_config::load("webapps", &serde_json::json!({"apps": {}}));
            let name = apps.get("apps").and_then(|a| a.get(&wid)).and_then(|e| e.get("name")).and_then(Value::as_str).filter(|n| !n.is_empty());
            let path = sonata_config::data_dir("webapps").join(&wid).join("icon.png");
            let icon = path.is_file().then(|| gio::FileIcon::new(&gio::File::for_path(&path)).upcast());
            (name.map(String::from).unwrap_or_else(|| tr!("Web App")), icon)
        }
        (Some(did), _) => match sonata_ui::apps::lookup(&did) {
            Some(info) => {
                let name = info.display_name().to_string();
                (if name.is_empty() { did } else { name }, info.icon())
            }
            None => (did.rsplit('.').next().unwrap_or(&did).to_string(), None),
        },
        _ => (scope.unit.clone(), None),
    }
}

/// Lives in the menu bar's process, as long as it does.
pub struct MemoryWatch {
    watch: RefCell<Watch>,
    reclaims: Arc<Mutex<HashMap<PathBuf, Instant>>>,
}

impl MemoryWatch {
    pub fn start(app: &gtk::Application) -> Rc<Self> {
        let me = Rc::new(Self { watch: RefCell::default(), reclaims: Arc::default() });
        let weak = Rc::downgrade(&me);
        let app = app.downgrade();
        glib::timeout_add_seconds_local(CHECK_S, move || match weak.upgrade() {
            Some(me) => {
                me.tick(app.upgrade());
                glib::ControlFlow::Continue
            }
            None => glib::ControlFlow::Break,
        });
        me
    }

    fn tick(self: &Rc<Self>, app: Option<gtk::Application>) {
        let (total, avail) = guard::meminfo(Path::new("/proc/meminfo"));
        let lv = guard::level(total.unwrap_or(0), avail.unwrap_or(0), guard::psi(Path::new("/proc/pressure/memory")));
        if !self.watch.borrow_mut().on_level(lv) {
            return;
        }
        let me = self.clone();
        let reclaims = self.reclaims.clone();
        glib::spawn_future_local(async move {
            // a thread: memory.reclaim can take a while
            let r = gio::spawn_blocking(move || give_back(&reclaims)).await.unwrap_or_default();
            me.after(r, app);
        });
    }

    fn after(self: &Rc<Self>, (all, done): (Vec<Scope>, usize), app: Option<gtk::Application>) {
        if done > 0 {
            println!("sonata2-topbar: memory pressure: {done} background app(s) gave memory back");
        }
        if self.watch.borrow_mut().after(!all.is_empty(), Instant::now()) {
            self.show(all, app);
        }
    }

    // -- the alert (last resort) ------------------------------------------------------------------
    fn show(self: &Rc<Self>, mut all: Vec<Scope>, app: Option<gtk::Application>) {
        all.sort_by_key(|s| std::cmp::Reverse(s.memory));
        all.truncate(ALERT_ROWS);
        let lb = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).css_classes(["boxed-list"]).build();
        for s in &all {
            let (name, icon) = app_label(s);
            let row = gtk::Box::builder().spacing(10).margin_top(6).margin_bottom(6).margin_start(10).margin_end(10).build();
            let img = gtk::Image::builder().pixel_size(24).build();
            match icon {
                Some(i) => img.set_from_gicon(&i),
                None => img.set_icon_name(Some("application-x-executable")),
            }
            row.append(&img);
            row.append(&gtk::Label::builder().label(name).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::End).build());
            row.append(&gtk::Label::builder().label(size_text(s.memory)).css_classes(["dim-label", "numeric"]).build());
            lb.append(&row);
        }
        lb.select_row(lb.row_at_index(0).as_ref());
        self.watch.borrow_mut().alert_open = true;
        let me = Rc::downgrade(self);
        let list = lb.clone();
        let parent = app.and_then(|a| a.active_window()).map(|w| w.upcast::<gtk::Widget>());
        let dlg = sonata_ui::dialog::alert(
            &tr!("Your system has run out of application memory."),
            &tr!("To avoid problems with your computer, quit any apps you're not using."),
            &[("resume", tr!("Resume"), ""), ("quit", tr!("Force Quit"), "destructive")],
            move |rid| {
                if let Some(me) = me.upgrade() {
                    let mut w = me.watch.borrow_mut();
                    w.alert_open = false;
                    w.dismissed_at = Some(Instant::now());
                }
                if let (true, Some(sel)) = (rid == "quit", list.selected_row()) {
                    if let Some(s) = all.get(sel.index() as usize) {
                        guard::kill(&s.path);
                    }
                }
            },
            parent.as_ref(),
        );
        dlg.set_extra_child(Some(&lb));
    }
}

/// Background apps give memory back (off the main loop). Never guesses
/// what's on screen: without Wayfire's list, nothing is reclaimed.
fn give_back(reclaims: &Mutex<HashMap<PathBuf, Instant>>) -> (Vec<Scope>, usize) {
    let views = sonata_backend::wfipc::call("window-rules/list-views", serde_json::json!({})).and_then(|v| v.as_array().cloned());
    let all = guard::scopes(&guard::own_slice_dir());
    let mut done = 0;
    if let Some(views) = views {
        let mut w = Watch { last_reclaim: std::mem::take(&mut *reclaims.lock().unwrap_or_else(|e| e.into_inner())), ..Default::default() };
        for s in w.due(guard::background(&all, &views), Instant::now()) {
            done += guard::reclaim(&s.path, guard::reclaim_amount(s.memory)) as usize;
        }
        *reclaims.lock().unwrap_or_else(|e| e.into_inner()) = w.last_reclaim;
    }
    (all, done)
}

#[cfg(test)]
mod tests {
    use super::guard::*;
    use super::*;

    const GB: u64 = 1024 * 1024 * 1024;

    fn fake_slice(apps: &[(&str, u64, &[i64])]) -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        for (unit, mem, pids) in apps {
            let d = root.path().join(unit);
            std::fs::create_dir_all(&d).unwrap();
            let procs: Vec<String> = pids.iter().map(|p| p.to_string()).collect();
            for (name, val) in [("cgroup.procs", procs.join("\n")), ("memory.current", mem.to_string()), ("memory.reclaim", String::new()), ("cgroup.kill", String::new())] {
                std::fs::write(d.join(name), val).unwrap();
            }
        }
        root
    }

    #[test]
    fn test_levels() {
        let total = 32 * 1024;
        assert_eq!(level(total, 20000, (0.0, 0.0)), Level::Ok);
        assert_eq!(level(total, 3000, (0.0, 0.0)), Level::Pressure); // < 15% free
        assert_eq!(level(total, 20000, (25.0, 0.0)), Level::Pressure); // stalls
        assert_eq!(level(total, 1000, (50.0, 30.0)), Level::Critical);
        assert_eq!(level(total, 1000, (50.0, 2.0)), Level::Pressure); // low, but not stalling
        assert_eq!(level(0, 0, (0.0, 0.0)), Level::Ok);
    }

    #[test]
    fn test_psi_and_meminfo() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("memory");
        std::fs::write(&p, "some avg10=12.50 avg60=1.00 avg300=0.00 total=1\nfull avg10=3.25 avg60=0.00 avg300=0.00 total=1\n").unwrap();
        assert_eq!(psi(&p), (12.5, 3.25));
        assert_eq!(psi(Path::new("/nonexistent")), (0.0, 0.0));
        let m = d.path().join("meminfo");
        std::fs::write(&m, "MemTotal:       32768000 kB\nMemFree: 1 kB\nMemAvailable:    2048000 kB\n").unwrap();
        assert_eq!(meminfo(&m), (Some(32000), Some(2000)));
        assert_eq!(meminfo(Path::new("/nonexistent")), (None, None));
    }

    #[test]
    fn test_background_first_biggest() {
        let root = fake_slice(&[("app-sonata2-steam-10.scope", 3 * GB, &[10, 11]), ("app-sonata2-org.gnome.Nautilus-20.scope", GB, &[20]), ("run-p30-i1.scope", 2 * GB, &[30])]);
        let all = scopes(root.path());
        assert_eq!(all.len(), 3);
        let views = [serde_json::json!({"pid": 20, "mapped": true, "minimized": false}), serde_json::json!({"pid": 10, "mapped": true, "minimized": true})];
        let bg: Vec<String> = background(&all, &views).into_iter().map(|s| s.unit).collect();
        assert_eq!(bg, ["app-sonata2-steam-10.scope", "run-p30-i1.scope"]);
    }

    #[test]
    fn test_reclaim_and_kill() {
        let root = fake_slice(&[("app-sonata2-steam-10.scope", 8 * GB, &[10])]);
        let s = scopes(root.path()).remove(0);
        assert_eq!(reclaim_amount(2 * GB), GB / 2); // a quarter
        let amount = reclaim_amount(s.memory);
        assert_eq!(amount, RECLAIM_MAX_MB * 1024 * 1024); // capped
        assert!(reclaim(&s.path, amount));
        assert_eq!(std::fs::read_to_string(s.path.join("memory.reclaim")).unwrap(), amount.to_string());
        assert!(!reclaim(&s.path, 1024)); // too little to bother
        assert!(kill(&s.path));
        assert_eq!(std::fs::read_to_string(s.path.join("cgroup.kill")).unwrap(), "1");
    }

    #[test]
    fn test_app_of() {
        let sc = |unit: &str| Scope { path: PathBuf::new(), unit: unit.into(), memory: 0, pids: vec![] };
        let none = Path::new("/nonexistent");
        assert_eq!(app_of(&sc("app-sonata2-org.gnome.Nautilus-20.scope"), none), (Some("org.gnome.Nautilus".into()), None));
        assert_eq!(app_of(&sc("app-sonata2-my\\x2dapp-7.scope"), none), (Some("my-app".into()), None));
        // a web app's scope: found by its command line
        let proc_root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(proc_root.path().join("42")).unwrap();
        std::fs::write(proc_root.path().join("42/cmdline"), b"python3\0-m\0sonata2\0webapp\0abc123\0").unwrap();
        let web = Scope { pids: vec![42], ..sc("run-p42.scope") };
        assert_eq!(app_of(&web, proc_root.path()), (None, Some("abc123".into())));
    }

    fn run_ticks(w: &mut Watch, lv: Level, n: u32, scopes: bool) -> u32 {
        let mut shown = 0;
        for _ in 0..n {
            if w.on_level(lv) && w.after(scopes, Instant::now()) {
                shown += 1;
                w.alert_open = true;
            }
        }
        shown
    }

    #[test]
    fn test_alert_only_after_a_while() {
        let mut w = Watch::default();
        assert_eq!(run_ticks(&mut w, Level::Critical, CRITICAL_CHECKS - 1, true), 0);
        assert_eq!(run_ticks(&mut w, Level::Critical, 1, true), 1);
    }

    #[test]
    fn test_pressure_never_alerts() {
        let mut w = Watch::default();
        assert_eq!(run_ticks(&mut w, Level::Pressure, 20, true), 0);
    }

    #[test]
    fn test_cooldown_after_dismiss() {
        let mut w = Watch { dismissed_at: Some(Instant::now()), ..Default::default() };
        assert_eq!(run_ticks(&mut w, Level::Critical, 10, true), 0);
    }

    #[test]
    fn no_alert_without_apps_to_quit() {
        let mut w = Watch::default();
        assert_eq!(run_ticks(&mut w, Level::Critical, 10, false), 0);
    }

    #[test]
    fn each_app_gives_back_at_most_every_30_s() {
        let mut w = Watch::default();
        let s = Scope { path: "/x".into(), unit: "u".into(), memory: GB, pids: vec![1] };
        let t = Instant::now();
        assert_eq!(w.due(vec![s.clone()], t).len(), 1);
        assert_eq!(w.due(vec![s.clone()], t + Duration::from_secs(5)).len(), 0);
        assert_eq!(w.due(vec![s], t + Duration::from_secs(RECLAIM_EVERY_S)).len(), 1);
    }

    #[test]
    fn sizes() {
        assert_eq!(size_text(3 * GB / 2), "1.5 GB");
        assert_eq!(size_text(300 * 1024 * 1024), "300 MB");
    }
}
