//! Memory management for the apps (backend/memguard.py; macOS: memory
//! pressure, then "Your system has run out of application memory").
//!
//! Every app opened from Sonata has its own scope under sonata-apps.slice
//! (appscope). Under memory pressure, step by step and quietly first:
//! 1. apps in the background (no window on screen) give memory back: the
//!    kernel reclaims part of their scope (memory.reclaim); nothing is lost;
//! 2. systemd-oomd closes the app causing it if the pressure stays high;
//! 3. only if the memory is still almost gone and the system still stalls
//!    after both: the alert, to choose an app to quit.
//!
//! No GTK here (memorywatch is the menu bar side).

use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

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

pub fn slice_dir(uid: u32) -> PathBuf {
    PathBuf::from(format!("/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/sonata-apps.slice"))
}

pub fn own_slice_dir() -> PathBuf {
    slice_dir(unsafe { libc::getuid() })
}

/// (total, available) in MB (None: unknown).
pub fn meminfo(path: &str) -> (Option<u64>, Option<u64>) {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let get = |k: &str| text.lines().find_map(|l| l.strip_prefix(k)?.strip_prefix(':')?.split_whitespace().next()?.parse::<u64>().ok()).map(|kb| kb / 1024);
    (get("MemTotal"), get("MemAvailable"))
}

/// (some, full): avg10 of the memory pressure (0 when unknown).
pub fn psi(path: &str) -> (f64, f64) {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let get = |kind: &str| text.lines().filter(|l| l.split(' ').next() == Some(kind)).find_map(|l| l.split_whitespace().find_map(|w| w.strip_prefix("avg10=")).and_then(|v| v.parse().ok())).unwrap_or(0.0);
    (get("some"), get("full"))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Level {
    Ok,
    /// background apps give memory back
    Pressure,
    /// the alert's condition
    Critical,
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

#[derive(Clone, Debug, PartialEq)]
pub struct Scope {
    pub path: PathBuf,
    pub unit: String,
    /// bytes
    pub memory: u64,
    pub pids: Vec<i64>,
}

fn read_int(path: &Path) -> u64 {
    std::fs::read_to_string(path).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0)
}

/// The apps' scopes (with processes).
pub fn scopes(root: &Path) -> Vec<Scope> {
    let Ok(dir) = std::fs::read_dir(root) else { return vec![] };
    let mut names: Vec<String> = dir.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    names.sort();
    names
        .into_iter()
        .filter_map(|name| {
            let path = root.join(&name);
            if !name.ends_with(".scope") || !path.is_dir() {
                return None;
            }
            let pids: Vec<i64> = std::fs::read_to_string(path.join("cgroup.procs")).ok().and_then(|t| t.split_whitespace().map(|p| p.parse().ok()).collect()).unwrap_or_default();
            (!pids.is_empty()).then(|| Scope { memory: read_int(&path.join("memory.current")), path, unit: name, pids })
        })
        .collect()
}

/// {pid: last focus} of the windows that are on screen (mapped, not minimized).
pub fn on_screen(views: &[Value]) -> HashMap<i64, i64> {
    let mut out = HashMap::new();
    for v in views {
        let role = v.get("role").and_then(Value::as_str).unwrap_or("toplevel");
        let pid = v.get("pid").and_then(Value::as_i64).unwrap_or(0);
        if role == "toplevel" && v.get("mapped").and_then(Value::as_bool).unwrap_or(true) && !v.get("minimized").and_then(Value::as_bool).unwrap_or(false) && pid > 1 {
            let t = v.get("last-focus-timestamp").and_then(Value::as_i64).unwrap_or(0);
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

pub fn reclaim_amount(memory_bytes: u64) -> u64 {
    ((memory_bytes as f64 * RECLAIM_SHARE) as u64).min(RECLAIM_MAX_MB * 1024 * 1024)
}

/// Ask the kernel to take `amount` bytes back from this scope
/// (memory.reclaim). It may get less (EAGAIN): that's fine.
pub fn reclaim(path: &Path, amount: u64) -> bool {
    amount >= 16 * 1024 * 1024 && std::fs::write(path.join("memory.reclaim"), amount.to_string()).is_ok()
}

/// Force Quit: every process of the app's scope at once (cgroup.kill).
pub fn kill(path: &Path) -> bool {
    std::fs::write(path.join("cgroup.kill"), "1").is_ok()
}

fn unescape(text: &str) -> String {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"\\x([0-9a-f]{2})").unwrap()).replace_all(text, |c: &regex::Captures| char::from(u8::from_str_radix(&c[1], 16).unwrap_or(b'?')).to_string()).into_owned()
}

/// (desktop id, web app id) of a scope.
pub fn app_of(scope: &Scope) -> (Option<String>, Option<String>) {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    if let Some(m) = RE.get_or_init(|| regex::Regex::new(r"^app-sonata2-(.+)-\d+\.scope$").unwrap()).captures(&scope.unit) {
        return (Some(unescape(&m[1])), None);
    }
    // a web app's own scope: `sonata2 webapp <id>`
    for pid in scope.pids.iter().take(3) {
        let Ok(raw) = std::fs::read(format!("/proc/{pid}/cmdline")) else { continue };
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const GB: u64 = 1024 * 1024 * 1024;

    fn fake_slice(dir: &Path, scopes: &[(&str, u64, &[i64])]) {
        for (unit, mem, pids) in scopes {
            let d = dir.join(unit);
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join("memory.current"), mem.to_string()).unwrap();
            std::fs::write(d.join("cgroup.procs"), pids.iter().map(|p| p.to_string()).collect::<Vec<_>>().join("\n")).unwrap();
        }
    }

    #[test]
    fn levels() {
        let total = 32000;
        assert_eq!(level(total, 20000, (0.0, 0.0)), Level::Ok);
        assert_eq!(level(total, 3000, (0.0, 0.0)), Level::Pressure); // < 15% free
        assert_eq!(level(total, 20000, (25.0, 0.0)), Level::Pressure); // stalls
        assert_eq!(level(total, 1000, (50.0, 30.0)), Level::Critical);
        assert_eq!(level(total, 1000, (50.0, 2.0)), Level::Pressure); // low, but not stalling
        assert_eq!(level(0, 0, (0.0, 0.0)), Level::Ok);
    }

    #[test]
    fn psi_and_meminfo() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("memory");
        std::fs::write(&p, "some avg10=12.50 avg60=1.00 avg300=0.00 total=1\nfull avg10=3.25 avg60=0.00 avg300=0.00 total=1\n").unwrap();
        assert_eq!(psi(p.to_str().unwrap()), (12.5, 3.25));
        assert_eq!(psi("/nonexistent"), (0.0, 0.0));
        let m = dir.path().join("meminfo");
        std::fs::write(&m, "MemTotal:       32768000 kB\nMemFree:  1 kB\nMemAvailable:   16384000 kB\n").unwrap();
        assert_eq!(meminfo(m.to_str().unwrap()), (Some(32000), Some(16000)));
    }

    #[test]
    fn background_first_biggest() {
        let dir = tempfile::tempdir().unwrap();
        fake_slice(dir.path(), &[("app-sonata2-steam-10.scope", 3 * GB, &[10, 11]), ("app-sonata2-org.gnome.Nautilus-20.scope", GB, &[20]), ("run-p30-i1.scope", 2 * GB, &[30])]);
        let all = scopes(dir.path());
        assert_eq!(all.len(), 3);
        let views = [json!({"pid": 20, "mapped": true, "minimized": false}), json!({"pid": 10, "mapped": true, "minimized": true})];
        let bg: Vec<String> = background(&all, &views).into_iter().map(|s| s.unit).collect();
        assert_eq!(bg, ["app-sonata2-steam-10.scope", "run-p30-i1.scope"]);
    }

    #[test]
    fn reclaim_kill_and_app_of() {
        let dir = tempfile::tempdir().unwrap();
        fake_slice(dir.path(), &[("app-sonata2-steam-10.scope", 8 * GB, &[10])]);
        let s = &scopes(dir.path())[0];
        assert_eq!(reclaim_amount(2 * GB), GB / 2); // a quarter
        let amount = reclaim_amount(s.memory);
        assert_eq!(amount, RECLAIM_MAX_MB * 1024 * 1024); // capped
        assert!(reclaim(&s.path, amount));
        assert_eq!(std::fs::read_to_string(s.path.join("memory.reclaim")).unwrap(), amount.to_string());
        assert!(!reclaim(&s.path, 1024)); // too little to bother
        assert!(kill(&s.path));
        assert_eq!(std::fs::read_to_string(s.path.join("cgroup.kill")).unwrap(), "1");
        let sc = |unit: &str| Scope { path: PathBuf::new(), unit: unit.into(), memory: 0, pids: vec![] };
        assert_eq!(app_of(&sc("app-sonata2-org.gnome.Nautilus-20.scope")), (Some("org.gnome.Nautilus".into()), None));
        assert_eq!(app_of(&sc("app-sonata2-my\\x2dapp-7.scope")), (Some("my-app".into()), None));
    }
}
