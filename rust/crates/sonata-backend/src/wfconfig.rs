//! Wayfire options Sonata changes (sonata2/wfconfig.py): keyboard,
//! trackpad, displays... Sonata's overrides file (kept apart from the
//! installed wayfire.ini; tools/wayfire-config.sh merges it in at login)
//! and, while a session runs, the resolved copy Wayfire reads and reloads
//! live. These are the compositor's own settings: the same files on trial
//! too (there is one compositor).

use std::os::fd::AsRawFd;
use std::path::PathBuf;
use std::sync::Mutex;

static LOCK: Mutex<()> = Mutex::new(());

fn runtime() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

fn cfg_dir() -> PathBuf {
    sonata_config::config_home().join("sonata2")
}

/// Where Settings writes: the overrides, and the running session's copy.
pub fn write_files() -> Vec<PathBuf> {
    let mut files = vec![cfg_dir().join("wayfire-overrides.ini")];
    let run = runtime().join("sonata2-wayfire.ini");
    if run.exists() {
        files.push(run);
    }
    files
}

pub fn read_files() -> Vec<PathBuf> {
    let mut f = vec![cfg_dir().join("wayfire.ini")];
    f.extend(write_files());
    f
}

/// One writer at a time: threads and Sonata's processes (a flock beside the session copy).
fn locked<R>(f: impl FnOnce() -> R) -> R {
    let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(runtime().join("sonata2-wayfire.lock"));
    if let Ok(l) = &lock {
        unsafe {
            libc::flock(l.as_raw_fd(), libc::LOCK_EX);
        }
    }
    f()
}

pub fn get(section: &str, key: &str, default: &str) -> String {
    for path in read_files().iter().rev() {
        let Ok(text) = std::fs::read_to_string(path) else { continue };
        let mut cur: Option<&str> = None;
        for line in text.lines() {
            let s = line.trim();
            if s.starts_with('[') && s.ends_with(']') {
                cur = Some(&s[1..s.len() - 1]);
            } else if cur == Some(section) && s.contains('=') && !s.starts_with('#') {
                let (k, v) = s.split_once('=').unwrap_or_default();
                if k.trim() == key {
                    return v.trim().to_string();
                }
            }
        }
    }
    default.to_string()
}

/// `text` with `key = value` set in [section] (None removes it); comments
/// and other lines stay as they are.
pub fn set_in(text: &str, section: &str, key: &str, value: Option<&str>) -> String {
    let lines: Vec<String> = text.split_inclusive('\n').map(String::from).collect();
    let mut out: Vec<String> = Vec::new();
    let (mut cur, mut done, mut sec_end): (Option<String>, bool, Option<usize>) = (None, false, None);
    for line in &lines {
        let s = line.trim();
        if s.starts_with('[') && s.ends_with(']') {
            if cur.as_deref() == Some(section) && !done {
                sec_end = Some(out.len());
            }
            cur = Some(s[1..s.len() - 1].to_string());
        } else if cur.as_deref() == Some(section) && s.contains('=') && !s.starts_with('#') && s.split_once('=').map(|x| x.0.trim()) == Some(key) {
            if let Some(v) = value {
                out.push(format!("{key} = {v}\n"));
            }
            done = true;
            continue;
        }
        out.push(line.clone());
    }
    if !done {
        if let Some(v) = value {
            if let Some(last) = out.last_mut() {
                if !last.ends_with('\n') {
                    last.push('\n'); // never glue the new key onto that line
                }
            }
            if cur.as_deref() == Some(section) {
                sec_end = Some(out.len());
            }
            match sec_end {
                Some(mut at) => {
                    while at > 0 && out[at - 1].trim().is_empty() {
                        at -= 1; // before the blank line
                    }
                    out.insert(at, format!("{key} = {v}\n"));
                }
                None => {
                    out.push(format!("\n[{section}]\n"));
                    out.push(format!("{key} = {v}\n"));
                }
            }
        }
    }
    out.concat()
}

fn set_files(section: &str, key: &str, value: Option<&str>, files: &[PathBuf]) -> bool {
    let mut ok = false;
    for path in files {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let new = set_in(&text, section, key, value);
        if new == text {
            ok = true; // already so: no write, so Wayfire doesn't reload
            continue;
        }
        // whole new file renamed over the old: Wayfire never reloads a half-written one
        if sonata_config::atomic_write(path, new.as_bytes(), false).is_ok() {
            ok = true;
        }
    }
    ok
}

/// Set `key = value` in [section]; Wayfire applies it at once.
pub fn set(section: &str, key: &str, value: &str) -> bool {
    locked(|| set_files(section, key, Some(value), &write_files()))
}

/// For this session only (the resolved copy, never the overrides); None removes the key.
pub fn runtime_set(section: &str, key: &str, value: Option<&str>) -> bool {
    let run = runtime().join("sonata2-wayfire.ini");
    if !run.exists() {
        return false;
    }
    locked(|| set_files(section, key, value, &[run]))
}

/// [section] and its keys out of the session's Wayfire config(s).
pub fn remove_section(section: &str) -> bool {
    locked(|| {
        let mut ok = false;
        for path in write_files() {
            let Ok(text) = std::fs::read_to_string(&path) else { continue };
            let lines: Vec<&str> = text.split_inclusive('\n').collect();
            let mut out: Vec<&str> = Vec::new();
            let mut skip = false;
            for line in &lines {
                let s = line.trim();
                if s.starts_with('[') && s.ends_with(']') {
                    skip = &s[1..s.len() - 1] == section;
                }
                if !skip {
                    out.push(line);
                }
            }
            while out.len() > 1 && out[out.len() - 1].trim().is_empty() && out[out.len() - 2].trim().is_empty() {
                out.pop();
            }
            if out == lines {
                ok = true;
                continue;
            }
            ok |= sonata_config::atomic_write(&path, out.concat().as_bytes(), false).is_ok();
        }
        ok
    })
}

#[cfg(test)]
mod tests {
    use super::set_in;

    #[test]
    fn keys_are_set_in_their_section() {
        let text = "# Sonata\n[input]\nxkb_layout = us\n\n[core]\nvwidth = 1\n";
        assert_eq!(set_in(text, "input", "xkb_layout", Some("br")), "# Sonata\n[input]\nxkb_layout = br\n\n[core]\nvwidth = 1\n");
        // a new key goes before the section's blank line
        assert_eq!(set_in(text, "input", "kb_repeat_rate", Some("40")), "# Sonata\n[input]\nxkb_layout = us\nkb_repeat_rate = 40\n\n[core]\nvwidth = 1\n");
        // a new section at the end
        assert_eq!(set_in("[a]\nx = 1", "b", "y", Some("2")), "[a]\nx = 1\n\n[b]\ny = 2\n");
        // removed
        assert_eq!(set_in(text, "core", "vwidth", None), "# Sonata\n[input]\nxkb_layout = us\n\n[core]\n");
        // already so: the same text (no write)
        assert_eq!(set_in(text, "input", "xkb_layout", Some("us")), text);
    }
}
