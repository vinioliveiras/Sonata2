//! `keep` and `keep-all` (sonata2/__main__.py): run shell components and
//! start them again if they crash -- a desktop must never lose its Dock or
//! menu bar. Stopped on purpose (exit 0, SIGTERM, logout) a component stays
//! stopped; crashing over and over (5 times in a minute) it gives up; one
//! that grows past its memory limit (a leak over a long day) is started
//! again; when the compositor it belongs to is gone, it stops.
//!
//! A component the Rust build has (`PORTED`) runs as `sonata2-rs <name>`;
//! any other as the Python Sonata's `sonata2 <name>` -- so a session can
//! mix the two while the port goes on (docs/RUST_PORT.md).
//!
//! No signals and no polling: each child's pidfd wakes the supervisor when
//! it ends; otherwise it wakes once a minute.

use crate::logs;
use std::fs::File;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// The components the Rust build runs itself.
pub const PORTED: [&str; 9] = ["calculator", "clock", "textedit", "preview", "notes", "welcome", "lock", "spotlight", "polkit"];

/// The shell and apps ported since: Rust only in the Rust session
/// ("Sonata (Rust preview)", SONATA2_RS_SIDE_BY_SIDE=0). The Python session
/// keeps its own Dock, menu bar... until each is switched over.
pub const PORTED_SHELL: [&str; 18] = [
    "wallpaper", "dock", "topbar", "launchpad", "files", "settings", "portal", "activity", "calendar", "assistant",
    "terminal", "camera", "videos", "diskutil", "feedback", "setup", "greeter", "webapp",
];

/// Does `sonata2-rs` run this component here.
pub fn ported(name: &str) -> bool {
    ported_in(name, sonata_config::side_by_side())
}

fn ported_in(name: &str, side_by_side: bool) -> bool {
    PORTED.contains(&name) || (!side_by_side && PORTED_SHELL.contains(&name))
}

#[cfg(test)]
mod ported_tests {
    #[test]
    fn rust_shell_only_in_the_rust_session() {
        // the Python session's Dock stays Python; the Rust session's is Rust
        assert!(!super::ported_in("dock", true));
        assert!(super::ported_in("dock", false));
        assert!(super::ported_in("lock", true));
        assert!(!super::ported_in("music", false));
    }
}

/// Hand this session's display to D-Bus/systemd-started services (the
/// portals) before any surface asks for them -- once per display.
pub const ACTIVATION_ENV: [&str; 11] = [
    "WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_DATA_DIRS", "DCONF_PROFILE", "XCURSOR_THEME",
    "XCURSOR_SIZE", "XCURSOR_PATH", "QT_QPA_PLATFORMTHEME", "SONATA_GLASS",
];

/// How long dbus-update-activation-environment may take (__main__.py's
/// timeout): a bus that never answers must not keep the whole shell from
/// starting -- the supervisor runs it before its first component.
const ACTIVATION_TIMEOUT: Duration = Duration::from_secs(10);

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

fn which(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|f| f.is_file()))
}

/// Run `argv` to its end, or kill it after `timeout`. False: it couldn't start.
fn run_for(argv: &[String], timeout: Duration) -> bool {
    let Ok(mut child) = Command::new(&argv[0]).args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn() else { return false };
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !matches!(child.try_wait(), Ok(None)) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = child.kill();
    let _ = child.wait();
    true
}

pub fn share_session_env() {
    let (Ok(wd), Some(tool)) = (std::env::var("WAYLAND_DISPLAY"), which("dbus-update-activation-environment")) else { return };
    let set: Vec<&str> = ACTIVATION_ENV.iter().copied().filter(|n| std::env::var_os(n).is_some()).collect();
    share_session_env_in(&runtime_dir(), &wd, &tool, &set, &|argv| run_for(argv, ACTIVATION_TIMEOUT));
}

/// share_session_env with the world given: the runtime folder, the
/// display, the tool, the variables set, and how to run the tool.
pub fn share_session_env_in(runtime: &Path, wd: &str, tool: &Path, set: &[&str], run: &dyn Fn(&[String]) -> bool) {
    if wd.is_empty() {
        return;
    }
    let mark = runtime.join(format!("sonata2-env-{wd}"));
    let socket = runtime.join(wd);
    let fresh = match (std::fs::metadata(&mark).and_then(|m| m.modified()), std::fs::metadata(&socket).and_then(|m| m.modified())) {
        (Ok(a), Ok(b)) => a >= b,
        _ => false,
    };
    if fresh {
        return; // done for this Wayfire run
    }
    let argv: Vec<String> = [tool.display().to_string(), "--systemd".into()].into_iter().chain(set.iter().map(|s| s.to_string())).collect();
    if run(&argv) {
        let _ = File::create(mark);
    }
}

/// The Wayland socket's identity: a new compositor makes a new one (its
/// inode may be the old one's again; its change time isn't).
pub fn socket_id(path: &Path) -> Option<(u64, u64, i64, i64)> {
    let m = std::fs::metadata(path).ok()?;
    Some((m.dev(), m.ino(), m.ctime(), m.ctime_nsec()))
}

/// A compositor answers on its socket; a crashed one leaves the file behind.
pub fn compositor_alive(path: &Path) -> bool {
    std::os::unix::net::UnixStream::connect(path).is_ok()
}

pub fn same_session(sock: &Path, session: Option<(u64, u64, i64, i64)>) -> bool {
    session.is_some() && socket_id(sock) == session && compositor_alive(sock)
}

/// The most memory a component may hold before it is started again: 5 % of
/// the RAM, 1-2 GB (a healthy Dock or menu bar uses a few hundred MB).
pub fn component_limit_mb(total_mb: u64) -> u64 {
    ((total_mb as f64 * 0.05) as u64).clamp(1024, 2048)
}

/// A process' resident memory in MB (0: gone).
pub fn rss_mb(pid: u32) -> u64 {
    std::fs::read_to_string(format!("/proc/{pid}/status"))
        .ok()
        .and_then(|s| s.lines().find(|l| l.starts_with("VmRSS:")).and_then(|l| l.split_whitespace().nth(1)).and_then(|kb| kb.parse::<u64>().ok()))
        .map(|kb| kb / 1024)
        .unwrap_or(0)
}

fn mem_total_mb() -> u64 {
    let (page, pages) = unsafe { (libc::sysconf(libc::_SC_PAGESIZE), libc::sysconf(libc::_SC_PHYS_PAGES)) };
    if page > 0 && pages > 0 { (page as u64 * pages as u64) >> 20 } else { 0 }
}

/// How a component is started: this binary for a ported one, the Python
/// Sonata's launcher for the rest.
pub fn command_for(argv: &[String]) -> Vec<String> {
    if ported(&argv[0]) {
        let me = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into());
        return std::iter::once(me).chain(argv.iter().cloned()).collect();
    }
    let py = std::env::var("SONATA2_LAUNCHER").ok().filter(|l| Path::new(l).exists()).unwrap_or_else(|| "sonata2".into());
    std::iter::once(py).chain(argv.iter().cloned()).collect()
}

#[derive(Debug, PartialEq)]
pub enum Verdict {
    /// start it again after this many seconds
    Again(u64),
    /// leave it stopped, with this exit code
    Stop(i32),
}

/// What `code` means for a component started `ran_for` ago that crashed at
/// these moments before: the rules shared by `keep` and `keep-all`.
pub fn verdict(code: Option<i32>, same_session: bool, crashes: &mut Vec<Instant>, now: Instant, ran_for: Duration) -> Verdict {
    let Some(code) = code else {
        return Verdict::Again(1); // restarted for its memory: not a crash, not a stop
    };
    // on purpose, or the session ended (also when Sonata already started
    // again on a new compositor: its own Dock and menu bar are coming)
    if [0, -15, -2, 130, 143].contains(&code) || !same_session {
        return Verdict::Stop(0);
    }
    crashes.retain(|t| now.duration_since(*t) < Duration::from_secs(60));
    crashes.push(now);
    if crashes.len() >= 5 {
        return Verdict::Stop(1);
    }
    Verdict::Again(if ran_for > Duration::from_secs(10) { 1 } else { 3 })
}

/// What the supervisor needs from the world: the real session's, or a
/// test's (the Python tests mock the same seams: self_argv, _same_session,
/// logs.log_dir, rss_mb, the minute's wait and the pauses).
pub struct Rules<'a> {
    /// a component's command line (command_for)
    pub command: &'a dyn Fn(&[String]) -> Vec<String>,
    /// the compositor these components belong to still runs (same_session)
    pub same_session: &'a dyn Fn() -> bool,
    /// where each component's log goes (logs::log_dir)
    pub log_dir: PathBuf,
    /// the memory limit, in MB (component_limit_mb)
    pub limit: u64,
    /// a process' resident memory in MB (rss_mb)
    pub rss: &'a dyn Fn(u32) -> u64,
    /// how often the logs and the memory are looked at (a minute)
    pub tick: Duration,
    /// the pause before a start again, for the verdict's seconds
    pub pause: &'a dyn Fn(u64) -> Duration,
}

/// One supervised component: its log, its crash history and what to do when it ends.
pub struct Kept {
    pub argv: Vec<String>,
    pub name: String,
    cmd: Vec<String>,
    crashes: Vec<Instant>,
    pub child: Option<Child>,
    pidfd: Option<OwnedFd>,
    started: Instant,
    too_big: u64,
    log_path: PathBuf,
}

impl Kept {
    pub fn new(argv: Vec<String>, rules: &Rules) -> Self {
        let name = argv[0].clone();
        let dir = rules.log_dir.clone();
        let _ = std::fs::create_dir_all(&dir);
        let log_path = dir.join(format!("{name}.log"));
        if log_path.exists() {
            let _ = std::fs::rename(&log_path, dir.join(format!("{name}.old.log")));
        }
        Kept { cmd: (rules.command)(&argv), argv, name, crashes: Vec::new(), child: None, pidfd: None, started: Instant::now(), too_big: 0, log_path }
    }

    pub fn start(&mut self) {
        self.started = Instant::now();
        self.too_big = 0;
        let log = std::fs::OpenOptions::new().create(true).append(true).open(&self.log_path);
        let (out, err) = match log {
            Ok(mut f) => {
                use std::io::Write;
                let _ = writeln!(f, "--- {} {}", stamp(), self.argv.join(" "));
                (Stdio::from(f.try_clone().unwrap_or_else(|_| File::open("/dev/null").expect("/dev/null"))), Stdio::from(f))
            }
            Err(_) => (Stdio::null(), Stdio::null()),
        };
        self.child = Command::new(&self.cmd[0]).args(&self.cmd[1..]).stdout(out).stderr(err).spawn().ok();
        if self.child.is_none() {
            append(&self.log_path, &format!("sonata2 keep: can't start {}\n", self.cmd.join(" ")));
        }
        self.pidfd = self.child.as_ref().and_then(|c| {
            let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, c.id() as libc::pid_t, 0) };
            (fd >= 0).then(|| unsafe { OwnedFd::from_raw_fd(fd as i32) })
        });
    }

    /// Its exit code once it ended (signals as Python's negative numbers).
    fn poll(&mut self) -> Option<Option<i32>> {
        let c = self.child.as_mut()?;
        match c.try_wait() {
            Ok(Some(st)) => {
                use std::os::unix::process::ExitStatusExt;
                Some(Some(st.code().unwrap_or_else(|| -st.signal().unwrap_or(1))))
            }
            Ok(None) => None,
            Err(_) => Some(Some(1)),
        }
    }

    /// The once-a-minute check: the log stays under its cap, and a leak is
    /// stopped. True: stopped for its memory.
    pub fn tick(&mut self, rules: &Rules) -> bool {
        logs::trim(&self.log_path, None);
        let Some(c) = self.child.as_mut() else { return false };
        self.too_big = (rules.rss)(c.id());
        if self.too_big <= rules.limit {
            return false;
        }
        append(&self.log_path, &format!("sonata2 keep: {} holds {} MB (limit {} MB); restarting\n", self.name, self.too_big, rules.limit));
        unsafe {
            libc::kill(c.id() as i32, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if matches!(c.try_wait(), Ok(Some(_))) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = c.kill();
        let _ = c.wait();
        true
    }

    pub fn ended(&mut self, code: Option<i32>, rules: &Rules) -> Verdict {
        self.child = None;
        self.pidfd = None;
        if code.is_none() {
            eprintln!("sonata2 keep: {} used {} MB; started again", self.name, self.too_big);
        }
        // the compositor is only asked about when it matters (a crash): the
        // stops on purpose stay stops whatever it says
        let on_purpose = code.is_some_and(|c| [0, -15, -2, 130, 143].contains(&c));
        let same = code.is_none() || on_purpose || (rules.same_session)();
        let v = verdict(code, same, &mut self.crashes, Instant::now(), self.started.elapsed());
        match (&v, code) {
            (Verdict::Again(_), Some(c)) => eprintln!("sonata2 keep: {} exited with {c}; restarting", self.argv.join(" ")),
            (Verdict::Stop(1), _) => eprintln!("sonata2 keep: {} keeps crashing; giving up", self.name),
            _ => {}
        }
        v
    }
}

fn stamp() -> String {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) as libc::time_t;
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe {
        libc::localtime_r(&t, &mut tm);
    }
    format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02}", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)
}

fn append(path: &Path, text: &str) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(text.as_bytes());
    }
}

/// Sleep until one of these children ends (its pidfd readable) or `timeout`.
fn wait_any(kept: &[Kept], timeout: Duration) {
    let mut fds: Vec<libc::pollfd> = kept.iter().filter_map(|k| k.pidfd.as_ref()).map(|fd| libc::pollfd { fd: fd.as_raw_fd(), events: libc::POLLIN, revents: 0 }).collect();
    let ms = timeout.as_millis().min(i32::MAX as u128) as i32;
    if fds.is_empty() {
        // (no pidfd: an older kernel -- look again within a second)
        std::thread::sleep(timeout.min(Duration::from_secs(1)));
        return;
    }
    unsafe {
        libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, ms);
    }
}

/// `keep-all wallpaper dock launchpad,--background ...`: `keep` for several
/// components in one process; a component's arguments follow it after commas.
pub fn keep_all(specs: &[String]) -> i32 {
    share_session_env();
    let sock = runtime_dir().join(std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".into()));
    // the compositor these components belong to
    let id = socket_id(&sock);
    let same = move || same_session(&sock, id);
    let rules = Rules {
        command: &command_for,
        same_session: &same,
        log_dir: logs::log_dir(),
        limit: component_limit_mb(mem_total_mb()),
        rss: &rss_mb,
        tick: Duration::from_secs(60),
        pause: &Duration::from_secs,
    };
    keep_all_with(specs, &rules)
}

pub fn keep_all_with(specs: &[String], rules: &Rules) -> i32 {
    let mut kept: Vec<Kept> = specs
        .iter()
        .map(|s| s.split(',').filter(|a| !a.is_empty()).map(String::from).collect::<Vec<_>>())
        .filter(|a| !a.is_empty())
        .map(|a| Kept::new(a, rules))
        .collect();
    for k in kept.iter_mut() {
        k.start();
    }
    let mut due: Vec<Option<Instant>> = vec![None; kept.len()];
    let mut results = Vec::new();
    let mut next_tick = Instant::now() + rules.tick;
    while kept.iter().any(|k| k.child.is_some()) || due.iter().any(Option::is_some) {
        let now = Instant::now();
        let wake = due.iter().flatten().copied().chain([next_tick]).min().unwrap_or(next_tick);
        wait_any(&kept, wake.saturating_duration_since(now));
        for i in 0..kept.len() {
            if let Some(code) = kept[i].poll() {
                match kept[i].ended(code, rules) {
                    Verdict::Stop(n) => results.push(n),
                    Verdict::Again(s) => due[i] = Some(Instant::now() + (rules.pause)(s)),
                }
            }
        }
        let now = Instant::now();
        if now >= next_tick {
            next_tick = now + rules.tick;
            for i in 0..kept.len() {
                if kept[i].child.is_some() && kept[i].tick(rules) {
                    if let Verdict::Again(s) = kept[i].ended(None, rules) {
                        due[i] = Some(Instant::now() + (rules.pause)(s));
                    }
                }
            }
        }
        let now = Instant::now();
        for i in 0..kept.len() {
            if due[i].is_some_and(|t| now >= t) {
                due[i] = None;
                // the compositor went during the pause: a new session's own
                // Dock and menu bar are coming (Vini, 18:53)
                if !(rules.same_session)() {
                    results.push(0);
                    continue;
                }
                kept[i].start();
            }
        }
    }
    results.into_iter().max().unwrap_or(0)
}

/// `keep dock`: one component.
pub fn keep(argv: &[String]) -> i32 {
    let spec = argv.join(",");
    keep_all(&[spec])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[test]
    fn stopped_on_purpose_stays_stopped() {
        let now = Instant::now();
        for code in [0, -15, -2, 130, 143] {
            assert_eq!(verdict(Some(code), true, &mut Vec::new(), now, Duration::from_secs(1)), Verdict::Stop(0), "{code}");
        }
        // the compositor went: stop, whatever the code
        assert_eq!(verdict(Some(1), false, &mut Vec::new(), now, Duration::from_secs(1)), Verdict::Stop(0));
    }

    #[test]
    fn crashes_restart_then_give_up() {
        let now = Instant::now();
        let mut crashes = Vec::new();
        assert_eq!(verdict(Some(1), true, &mut crashes, now, Duration::from_secs(30)), Verdict::Again(1));
        assert_eq!(verdict(Some(-11), true, &mut crashes, now, Duration::from_secs(2)), Verdict::Again(3)); // crashed at once: a longer pause
        verdict(Some(1), true, &mut crashes, now, Duration::from_secs(2));
        verdict(Some(1), true, &mut crashes, now, Duration::from_secs(2));
        assert_eq!(verdict(Some(1), true, &mut crashes, now, Duration::from_secs(2)), Verdict::Stop(1)); // 5 in a minute
        assert_eq!(verdict(None, true, &mut Vec::new(), now, Duration::ZERO), Verdict::Again(1)); // its memory
    }

    /// tests/test_keep_memory.py's test_limit.
    #[test]
    fn memory_limit_is_five_percent_within_1_to_2_gb() {
        assert_eq!(component_limit_mb(8 * 1024), 1024);
        assert_eq!(component_limit_mb(30 * 1024), 1536);
        assert_eq!(component_limit_mb(32 * 1024), 1638);
        assert_eq!(component_limit_mb(128 * 1024), 2048);
    }

    /// tests/test_keep_memory.py's test_rss.
    #[test]
    fn test_rss() {
        assert!(rss_mb(std::process::id()) > 0);
        assert_eq!(rss_mb(999_999_999), 0);
    }

    #[test]
    fn ported_components_run_here_the_rest_in_python() {
        let _env = crate::tests::ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("SONATA2_LAUNCHER");
        let c = command_for(&["clock".into()]);
        assert!(!c[0].is_empty());
        assert_eq!(c[1], "clock");
        assert_eq!(command_for(&["music".into()])[..2], ["sonata2", "music"]);
    }

    // -- the supervisor itself, with fake components (tests/test_keep_all.py) ---------------------
    // a fake component: `ok` stops on purpose, `flaky` crashes on its first
    // run (a marker file) and stops on the second, `crash` always crashes,
    // `term` ends on SIGTERM, `sleepy` runs until stopped; each run is
    // written to <dir>/<name>.runs with its arguments
    const CHILD: &str = r#"
d="$SONATA_TEST_DIR"; echo "$@" >> "$d/$1.runs"
case "$1" in
  ok) exit 0 ;;
  flaky) if [ -e "$d/flaky.once" ]; then exit 0; fi; touch "$d/flaky.once"; exit 3 ;;
  crash) exit 4 ;;
  term) kill -TERM $$ ;;
  sleepy) if [ -e "$d/sleepy.once" ]; then exit 0; fi; touch "$d/sleepy.once"; exec sleep 30 ;;
esac
exit 0
"#;

    struct Run {
        dir: tempfile::TempDir,
        code: i32,
        asked: usize,
    }

    impl Run {
        fn runs(&self, name: &str) -> Vec<String> {
            std::fs::read_to_string(self.dir.path().join(format!("{name}.runs"))).map(|t| t.lines().map(String::from).collect()).unwrap_or_default()
        }
        fn log(&self, name: &str) -> String {
            std::fs::read_to_string(self.dir.path().join(format!("{name}.log"))).unwrap_or_default()
        }
    }

    /// keep-all over the fake components; `same` answers "is it still our
    /// compositor" each time it's asked; `rss` is every child's memory.
    fn run_all(specs: &[&str], same: &dyn Fn(usize) -> bool, rss: u64, before: &dyn Fn(&Path)) -> Run {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path().to_path_buf();
        before(&d);
        let command = {
            let d = d.clone();
            move |argv: &[String]| -> Vec<String> {
                let script = format!("SONATA_TEST_DIR='{}'\n{CHILD}", d.display());
                ["sh", "-c", &script, "sh"].iter().map(|s| s.to_string()).chain(argv.iter().cloned()).collect()
            }
        };
        let asked = Cell::new(0);
        let same_session = || {
            asked.set(asked.get() + 1);
            same(asked.get())
        };
        let rss = move |_pid: u32| rss;
        let rules = Rules {
            command: &command,
            same_session: &same_session,
            log_dir: d.clone(),
            limit: 1024,
            rss: &rss,
            tick: Duration::from_millis(300),
            pause: &|_s| Duration::ZERO,
        };
        let specs: Vec<String> = specs.iter().map(|s| s.to_string()).collect();
        let code = keep_all_with(&specs, &rules);
        Run { dir, code, asked: asked.get() }
    }

    /// test_each_restarted_on_its_own: one stops on purpose and stays
    /// stopped, the other crashed and is started again with its arguments;
    /// each has its own log.
    #[test]
    fn test_each_restarted_on_its_own() {
        let r = run_all(&["ok", "flaky,--background"], &|_| true, 0, &|_| {});
        assert_eq!(r.code, 0);
        assert_eq!(r.runs("ok"), ["ok"]);
        assert_eq!(r.runs("flaky"), ["flaky --background", "flaky --background"]);
        assert!(r.log("ok").starts_with("--- ") && r.log("flaky").contains("flaky --background"));
    }

    /// test_a_crash_loop_gives_up_alone: five crashes in a minute, then no
    /// more -- and only for that one.
    #[test]
    fn test_a_crash_loop_gives_up_alone() {
        let r = run_all(&["crash", "ok"], &|_| true, 0, &|_| {});
        assert_eq!(r.code, 1);
        assert_eq!(r.runs("crash").len(), 5);
        assert_eq!(r.runs("ok"), ["ok"]);
    }

    /// test_review_session.py's test_keep_rotates_the_log_and_stops_on_a_clean_exit:
    /// the previous run's log becomes <name>.old.log; exit 0 is not restarted.
    #[test]
    fn test_keep_rotates_the_log_and_stops_on_a_clean_exit() {
        let r = run_all(&["ok,--background"], &|_| true, 0, &|d| std::fs::write(d.join("ok.log"), "previous run\n").unwrap());
        assert_eq!(r.code, 0);
        assert_eq!(r.runs("ok"), ["ok --background"]);
        assert_eq!(std::fs::read_to_string(r.dir.path().join("ok.old.log")).unwrap(), "previous run\n");
        assert!(r.log("ok").lines().next().unwrap().ends_with("ok --background"));
    }

    /// test_keep_respects_sigterm: stopped on purpose (SIGTERM from
    /// `restart`, logout) it isn't started again -- and the compositor isn't
    /// even asked.
    #[test]
    fn test_keep_respects_sigterm() {
        let r = run_all(&["term"], &|_| true, 0, &|_| {});
        assert_eq!((r.code, r.runs("term").len()), (0, 1));
        assert_eq!(r.asked, 0);
    }

    /// test_open_apps.py's test_a_crashed_compositor_ends_them_too (Vini,
    /// 18:53: Wayfire crashed, its socket file stayed, the old Dock was
    /// started again and reached the new Wayfire).
    #[test]
    fn test_a_crashed_compositor_ends_them_too() {
        let r = run_all(&["crash"], &|_| false, 0, &|_| {});
        assert_eq!((r.code, r.runs("crash").len()), (0, 1));
    }

    /// test_a_new_compositor_during_the_pause: still ours when it crashed,
    /// a new one by the end of the pause -- not started again.
    #[test]
    fn test_a_new_compositor_during_the_pause() {
        let r = run_all(&["crash"], &|n| n == 1, 0, &|_| {});
        assert_eq!((r.code, r.runs("crash").len()), (0, 1));
    }

    /// test_a_crashed_component_still_restarts.
    #[test]
    fn test_a_crashed_component_still_restarts() {
        let r = run_all(&["flaky"], &|_| true, 0, &|_| {});
        assert_eq!((r.code, r.runs("flaky").len()), (0, 2));
    }

    /// tests/test_keep_memory.py's test_restarted_when_too_big: a component
    /// past its memory limit is stopped and started again (not a crash),
    /// with a line in its log.
    #[test]
    fn test_restarted_when_too_big() {
        let r = run_all(&["sleepy"], &|_| true, 99_999, &|_| {});
        assert_eq!((r.code, r.runs("sleepy").len()), (0, 2));
        assert!(r.log("sleepy").contains("holds 99999 MB (limit 1024 MB); restarting"), "{}", r.log("sleepy"));
    }

    /// test_healthy_left_alone: under the limit, the minute's look changes nothing.
    #[test]
    fn test_healthy_left_alone() {
        let d = tempfile::tempdir().unwrap();
        let script = format!("touch '{}/ran'; sleep 0.8", d.path().display());
        let command = move |_a: &[String]| -> Vec<String> { vec!["sh".into(), "-c".into(), script.clone()] };
        let rules = Rules {
            command: &command,
            same_session: &|| true,
            log_dir: d.path().to_path_buf(),
            limit: 1024,
            rss: &|_| 200,
            tick: Duration::from_millis(200),
            pause: &|_| Duration::ZERO,
        };
        assert_eq!(keep_all_with(&["healthy".into()], &rules), 0);
        assert!(!std::fs::read_to_string(d.path().join("healthy.log")).unwrap().contains("restarting"));
    }

    /// test_share_session_env_once_per_compositor: the display variables
    /// that exist go to D-Bus/systemd activation, once per Wayland socket
    /// (a marker newer than the socket).
    #[test]
    fn test_share_session_env_once_per_compositor() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("wayland-9"), "").unwrap();
        let calls = RefCell::new(Vec::new());
        let run = |argv: &[String]| {
            calls.borrow_mut().push(argv.to_vec());
            true
        };
        let tool = Path::new("/usr/bin/dbus-update-activation-environment");
        for _ in 0..2 {
            share_session_env_in(d.path(), "wayland-9", tool, &["WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP"], &run);
        }
        assert_eq!(*calls.borrow(), [vec!["/usr/bin/dbus-update-activation-environment".to_string(), "--systemd".into(), "WAYLAND_DISPLAY".into(), "XDG_CURRENT_DESKTOP".into()]]);
        assert!(d.path().join("sonata2-env-wayland-9").exists());
    }

    /// The bus never answering: dbus-update-activation-environment hung, and
    /// with no time limit the supervisor never started the Dock or the menu
    /// bar (__main__.py gives it 10 s).
    #[test]
    fn test_activation_env_that_hangs_held_up_the_shell() {
        let t = Instant::now();
        assert!(run_for(&["sleep".into(), "30".into()], Duration::from_millis(300)));
        assert!(t.elapsed() < Duration::from_secs(5));
        assert!(!run_for(&["/nonexistent/tool".into()], Duration::from_millis(300)));
    }
}
