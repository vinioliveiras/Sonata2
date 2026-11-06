//! Sonata's logs (sonata2/logs.py): off by default (Settings > About >
//! Logs). Off: the parts still log, but to memory
//! ($XDG_RUNTIME_DIR/sonata2-logs, gone at logout) -- `sonata2 doctor` and a
//! bug report still have them; on (the flag file, or SONATA_DEBUG=1):
//! detailed logs in ~/.cache/sonata2, kept between sessions.

use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// a component's log, normal installs (bytes)
pub const CAP: u64 = 1 << 20;
pub const CAP_VERBOSE: u64 = 32 << 20;

fn env_dir(var: &str, fallback: &str) -> PathBuf {
    match std::env::var_os(var) {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => PathBuf::from(fallback),
    }
}

fn name() -> &'static str {
    "sonata2"
}

/// The flag file Settings > About > Logs writes (the Python Sonata's).
pub fn flag_path() -> PathBuf {
    sonata_config::config_home().join("sonata2").join("debug-logging")
}

pub fn verbose() -> bool {
    std::env::var("SONATA_DEBUG").as_deref() == Ok("1") || flag_path().exists()
}

/// Where everything logs: ~/.cache/sonata2 with logs on, else memory.
pub fn log_dir() -> PathBuf {
    log_dir_named(name())
}

/// The session's own logs (Wayfire's, the crash notes tools/sonata-session
/// leaves).
pub fn session_log_dir() -> PathBuf {
    log_dir_named("sonata2")
}

fn log_dir_named(name: &str) -> PathBuf {
    if verbose() {
        let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
        let cache = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".cache"));
        return cache.join(name);
    }
    env_dir("XDG_RUNTIME_DIR", "/tmp").join(format!("{name}-logs"))
}

/// Settings > About > Logs on / off (from the next start of each part).
/// The session's one switch: ~/.config/sonata2/debug-logging.
pub fn set_verbose(on: bool) {
    let path = flag_path();
    if on {
        if let Some(d) = path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(&path, "Sonata writes detailed logs while this file exists.\n");
    } else {
        let _ = std::fs::remove_file(&path);
    }
}

/// Keep a log under its cap: past it, only the newest half stays. (The
/// writer appends -- O_APPEND -- so it carries on at the new end.)
pub fn trim(path: &Path, cap: Option<u64>) {
    let cap = cap.unwrap_or(if verbose() { CAP_VERBOSE } else { CAP });
    let Ok(size) = std::fs::metadata(path).map(|m| m.len()) else { return };
    if size <= cap {
        return;
    }
    let res = (|| -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new().read(true).write(true).open(path)?;
        f.seek(SeekFrom::Start(size - cap / 2))?;
        let mut tail = Vec::new();
        f.read_to_end(&mut tail)?;
        let start = tail.iter().position(|b| *b == b'\n').map(|i| i + 1).unwrap_or(0);
        f.seek(SeekFrom::Start(0))?;
        f.write_all(b"--- (older lines removed: the log is kept small)\n")?;
        f.write_all(&tail[start..])?;
        let at = f.stream_position()?;
        f.set_len(at)
    })();
    let _ = res;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trim_keeps_the_newest_half() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("x.log");
        let text: String = (0..100).map(|i| format!("line {i:03}\n")).collect();
        std::fs::write(&p, &text).unwrap();
        trim(&p, Some(400));
        let out = std::fs::read_to_string(&p).unwrap();
        assert!(out.starts_with("--- (older lines removed"));
        assert!(out.ends_with("line 099\n"));
        assert!(out.len() < 300);
        trim(&p, Some(10_000)); // under the cap: left alone
        assert_eq!(std::fs::read_to_string(&p).unwrap(), out);
    }
}
