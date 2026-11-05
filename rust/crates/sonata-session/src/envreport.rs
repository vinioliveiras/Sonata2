//! ~/.cache/sonata2/env.txt: library and tool versions of this machine, so
//! the development environment can match them (sonata2/__main__.py
//! _write_env_report).
//!
//! Who starts it: the menu bar, once at start (not its preview), with
//! `start`. The GTK side -- GTK, libadwaita and gtk4-layer-shell versions,
//! the distro logo (GTK's icon theme) -- is read by the caller on its main
//! thread and handed over as lines: this crate has no GTK, and the icon
//! theme must not be touched from another thread. Everything else runs in
//! a thread: its ten `tool --version` runs held the menu bar's main loop
//! ~0.1-0.2 s just as it slid in at login.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const TOOLS: [(&str, &str); 10] = [
    ("wayfire", "--version"),
    ("grim", "-h"),
    ("wf-recorder", "--version"),
    ("wlsunset", "-h"),
    ("swayidle", "-v"),
    ("wtype", "-h"),
    ("nmcli", "--version"),
    ("wpctl", "--version"),
    ("brightnessctl", "--version"),
    ("bluetoothctl", "--version"),
];
const TOOL_TIMEOUT: Duration = Duration::from_secs(3);

/// Always ~/.cache (not XDG_CACHE_HOME), like the Python report.
pub fn path() -> PathBuf {
    PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".cache").join("sonata2").join("env.txt")
}

/// Ok(first line of its output, or "" when it printed nothing); Err: it
/// couldn't be run or timed out. Blocking.
fn tool_version(tool: &str, arg: &str) -> Result<String, ()> {
    let mut child = Command::new(tool).arg(arg).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|_| ())?;
    let end = Instant::now() + TOOL_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < end => std::thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(());
            }
        }
    }
    let out = child.wait_with_output().map_err(|_| ())?;
    let (so, se) = (String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    let text = if so.trim().is_empty() { se } else { so };
    Ok(text.trim().lines().next().unwrap_or("").to_string())
}

/// The report's lines. `ui_lines`: the caller's GTK side ("gtk 4.18.5",
/// "libadwaita 1.7.2", "gtk4-layer-shell 1.1.0", "logo /path"...).
/// `which` / `version`: how a tool is found and asked (the tests' own).
pub fn lines_with(ui_lines: &[String], which: impl Fn(&str) -> bool, version: impl Fn(&str, &str) -> Result<String, ()>, os_release: &Path) -> Vec<String> {
    let mut lines = vec![format!("sonata2-rs {}", env!("CARGO_PKG_VERSION"))];
    lines.extend(ui_lines.iter().cloned());
    for (tool, arg) in TOOLS {
        lines.push(if !which(tool) {
            format!("{tool} missing")
        } else {
            match version(tool, arg) {
                Ok(first) if !first.is_empty() => format!("{tool} {first}"),
                _ => format!("{tool} present"),
            }
        });
    }
    if let Ok(text) = std::fs::read_to_string(os_release) {
        lines.push(text.lines().find(|l| l.starts_with("PRETTY_NAME=")).map(|l| l.trim().to_string()).unwrap_or_else(|| "os ?".into()));
    }
    let var = |n: &str| std::env::var(n).unwrap_or_default();
    lines.push(format!("desktop {} session {}", var("XDG_CURRENT_DESKTOP"), var("XDG_SESSION_DESKTOP")));
    lines
}

pub fn write_to(path: &Path, lines: &[String]) {
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = std::fs::write(path, lines.join("\n") + "\n");
}

/// The report now, blocking (a thread's work).
pub fn write(ui_lines: &[String]) {
    let lines = lines_with(ui_lines, |t| sonata_backend::run::which(t).is_some(), tool_version, Path::new("/etc/os-release"));
    write_to(&path(), &lines);
}

/// The menu bar's start: the report written in a thread. False on trial:
/// the Python menu bar writes the same file, and the Rust build's versions
/// would replace the session's real ones.
pub fn start(ui_lines: Vec<String>) -> bool {
    if sonata_config::side_by_side() {
        return false;
    }
    std::thread::Builder::new().name("env-report".into()).spawn(move || write(&ui_lines)).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Product-wide check: the env report ran ten `tool --version` on the
    /// menu bar's main loop right after it started (~60-220 ms frozen as it
    /// slid in). The GTK side comes in as lines read beforehand; the rest
    /// never needs the main thread.
    #[test]
    fn test_in_a_thread_with_the_logo_read_before() {
        let d = tempfile::tempdir().unwrap();
        let os = d.path().join("os-release");
        std::fs::write(&os, "NAME=X\nPRETTY_NAME=\"CachyOS\"\n").unwrap();
        let ui = vec!["gtk 4.18.5".to_string(), "logo /x.svg".to_string()];
        let lines = std::thread::spawn(move || {
            lines_with(&ui, |t| t != "grim", |t, _| if t == "wtype" { Err(()) } else if t == "nmcli" { Ok(String::new()) } else { Ok(format!("{t} 1.0")) }, &os)
        })
        .join()
        .unwrap();
        assert!(lines[0].starts_with("sonata2-rs "));
        assert_eq!(lines[1..3], ["gtk 4.18.5", "logo /x.svg"]);
        assert!(lines.contains(&"grim missing".to_string()));
        assert!(lines.contains(&"wtype present".to_string())); // couldn't be asked
        assert!(lines.contains(&"nmcli present".to_string())); // printed nothing
        assert!(lines.contains(&"wayfire wayfire 1.0".to_string()));
        assert!(lines.contains(&"PRETTY_NAME=\"CachyOS\"".to_string()));
        assert!(lines.last().unwrap().starts_with("desktop "));
        let out = d.path().join(".cache/sonata2/env.txt");
        write_to(&out, &lines);
        assert!(std::fs::read_to_string(&out).unwrap().contains("logo /x.svg\n"));
    }

    #[test]
    fn tool_versions_read() {
        let t0 = Instant::now();
        assert!(tool_version("/nonexistent/tool", "-h").is_err());
        assert_eq!(tool_version("true", "x"), Ok(String::new()));
        assert_eq!(tool_version("echo", "hello").as_deref(), Ok("hello"));
        assert!(t0.elapsed() < TOOL_TIMEOUT);
    }
}
