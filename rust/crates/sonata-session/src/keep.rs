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

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

fn which(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|f| f.is_file()))
}

pub fn share_session_env() {
    let (Ok(wd), Some(tool)) = (std::env::var("WAYLAND_DISPLAY"), which("dbus-update-activation-environment")) else { return };
    let mark = runtime_dir().join(format!("sonata2-env-{wd}"));
    let socket = runtime_dir().join(&wd);
    let fresh = match (std::fs::metadata(&mark).and_then(|m| m.modified()), std::fs::metadata(&socket).and_then(|m| m.modified())) {
        (Ok(a), Ok(b)) => a >= b,
        _ => false,
    };
    if fresh {
        return; // done for this Wayfire run
    }
    let names: Vec<&str> = ACTIVATION_ENV.iter().copied().filter(|n| std::env::var_os(n).is_some()).collect();
    let ok = Command::new(tool).arg("--systemd").args(&names).stdout(Stdio::null()).stderr(Stdio::null()).status().is_ok();
    if ok {
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

/// One supervised component: its log, its crash history and what to do when it ends.
pub struct Kept {
    pub argv: Vec<String>,
    pub name: String,
    sock: PathBuf,
    session: Option<(u64, u64, i64, i64)>,
    limit: u64,
    cmd: Vec<String>,
    crashes: Vec<Instant>,
    pub child: Option<Child>,
    pidfd: Option<OwnedFd>,
    started: Instant,
    too_big: u64,
    log_path: PathBuf,
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

impl Kept {
    pub fn new(argv: Vec<String>, sock: PathBuf, session: Option<(u64, u64, i64, i64)>, limit: u64) -> Self {
        let name = argv[0].clone();
        let dir = logs::log_dir();
        let _ = std::fs::create_dir_all(&dir);
        let log_path = dir.join(format!("{name}.log"));
        if log_path.exists() {
            let _ = std::fs::rename(&log_path, dir.join(format!("{name}.old.log")));
        }
        Kept { cmd: command_for(&argv), argv, name, sock, session, limit, crashes: Vec::new(), child: None, pidfd: None, started: Instant::now(), too_big: 0, log_path }
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
    pub fn tick(&mut self) -> bool {
        logs::trim(&self.log_path, None);
        let Some(c) = self.child.as_mut() else { return false };
        self.too_big = rss_mb(c.id());
        if self.too_big <= self.limit {
            return false;
        }
        append(&self.log_path, &format!("sonata2 keep: {} holds {} MB (limit {} MB); restarting\n", self.name, self.too_big, self.limit));
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

    pub fn ended(&mut self, code: Option<i32>) -> Verdict {
        self.child = None;
        self.pidfd = None;
        if code.is_none() {
            eprintln!("sonata2 keep: {} used {} MB; started again", self.name, self.too_big);
        }
        let same = same_session(&self.sock, self.session);
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

fn session() -> (PathBuf, Option<(u64, u64, i64, i64)>, u64) {
    share_session_env();
    let sock = runtime_dir().join(std::env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-0".into()));
    let id = socket_id(&sock);
    (sock, id, component_limit_mb(mem_total_mb()))
}

/// Sleep until one of these children ends (its pidfd readable) or `timeout`.
fn wait_any(kept: &[Kept], timeout: Duration) {
    let mut fds: Vec<libc::pollfd> = kept.iter().filter_map(|k| k.pidfd.as_ref()).map(|fd| libc::pollfd { fd: fd.as_raw_fd(), events: libc::POLLIN, revents: 0 }).collect();
    let ms = timeout.as_millis().min(i32::MAX as u128) as i32;
    if fds.is_empty() {
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
    let (sock, id, limit) = session();
    let mut kept: Vec<Kept> = specs
        .iter()
        .map(|s| s.split(',').filter(|a| !a.is_empty()).map(String::from).collect::<Vec<_>>())
        .filter(|a| !a.is_empty())
        .map(|a| Kept::new(a, sock.clone(), id, limit))
        .collect();
    for k in kept.iter_mut() {
        k.start();
    }
    let mut due: Vec<Option<Instant>> = vec![None; kept.len()];
    let mut stopped = vec![false; kept.len()];
    let mut results = Vec::new();
    let mut next_tick = Instant::now() + Duration::from_secs(60);
    while kept.iter().zip(&stopped).any(|(k, s)| !s && (k.child.is_some())) || due.iter().any(Option::is_some) {
        let now = Instant::now();
        let wake = due.iter().flatten().copied().chain([next_tick]).min().unwrap_or(next_tick);
        wait_any(&kept, wake.saturating_duration_since(now));
        for i in 0..kept.len() {
            if let Some(code) = kept[i].poll() {
                match kept[i].ended(code) {
                    Verdict::Stop(n) => {
                        results.push(n);
                        stopped[i] = true;
                    }
                    Verdict::Again(s) => due[i] = Some(Instant::now() + Duration::from_secs(s)),
                }
            }
        }
        let now = Instant::now();
        if now >= next_tick {
            next_tick = now + Duration::from_secs(60);
            for i in 0..kept.len() {
                if kept[i].child.is_some() && kept[i].tick() {
                    let v = kept[i].ended(None);
                    if let Verdict::Again(s) = v {
                        due[i] = Some(Instant::now() + Duration::from_secs(s));
                    }
                }
            }
        }
        for i in 0..kept.len() {
            if due[i].is_some_and(|t| now >= t) {
                due[i] = None;
                if !same_session(&sock, id) {
                    results.push(0);
                    stopped[i] = true;
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

    #[test]
    fn memory_limit_is_five_percent_within_1_to_2_gb() {
        assert_eq!(component_limit_mb(8 * 1024), 1024);
        assert_eq!(component_limit_mb(32 * 1024), 1638);
        assert_eq!(component_limit_mb(128 * 1024), 2048);
    }

    #[test]
    fn ported_components_run_here_the_rest_in_python() {
        std::env::remove_var("SONATA2_LAUNCHER");
        let c = command_for(&["clock".into()]);
        assert!(c[0].ends_with("sonata_session-") || !c[0].is_empty());
        assert_eq!(c[1], "clock");
        assert_eq!(command_for(&["dock".into()])[..2], ["sonata2", "dock"]);
        assert!(rss_mb(std::process::id()) > 0);
    }
}
