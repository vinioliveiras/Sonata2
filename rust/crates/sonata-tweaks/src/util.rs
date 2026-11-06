//! Small helpers the modules share: the XDG folders read from the
//! environment each time (GLib caches them for the whole process, and
//! Sonata's tests give each run its own), and quiet file writes.

use std::path::{Path, PathBuf};

pub fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

fn xdg(var: &str, fallback: &[&str]) -> PathBuf {
    match std::env::var_os(var) {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => fallback.iter().fold(home(), |p, x| p.join(x)),
    }
}

pub fn data_home() -> PathBuf {
    xdg("XDG_DATA_HOME", &[".local", "share"])
}

pub fn cache_home() -> PathBuf {
    xdg("XDG_CACHE_HOME", &[".cache"])
}

pub fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

/// "~/x" -> "$HOME/x".
pub fn expand(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => home().join(rest),
        None if p == "~" => home(),
        None => PathBuf::from(p),
    }
}

/// A text file's lines ([] when it can't be read).
pub fn read_lines(path: &Path) -> Vec<String> {
    std::fs::read_to_string(path).map(|t| t.lines().map(String::from).collect()).unwrap_or_default()
}

/// Written whole or not at all (a unique temp file renamed over it: apps
/// write these files too); a failure is quiet, as in Python.
pub fn write(path: &Path, text: &str) {
    let _ = sonata_config::atomic_write(path, text.as_bytes(), false);
}

/// A JSON object of a stored setting, as config.load(name, defaults) gives it.
pub fn load(name: &str, defaults: serde_json::Value) -> serde_json::Map<String, serde_json::Value> {
    sonata_config::load(name, &defaults)
}

/// Python's truthiness of a JSON value (bool(cfg.get(...))).
pub fn truthy(v: Option<&serde_json::Value>) -> bool {
    use serde_json::Value;
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// A list of strings in a setting (non-strings dropped).
pub fn strings(v: Option<&serde_json::Value>) -> Vec<String> {
    v.and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
}

/// The desktop id without ".desktop" (gpu.app_key); a login item's own
/// file name when it has none (sonata_ui::apps::desktop_id).
pub fn key_of(info: &gio::AppInfo) -> String {
    let id = sonata_ui::apps::desktop_id(info);
    id.strip_suffix(".desktop").map(String::from).unwrap_or(id)
}

/// Tests that write the shared (temporary) config folder take turns.
#[cfg(test)]
pub fn test_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// POSIX shell quoting (shlex.quote).
pub fn shell_quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\"'\"'"))
    }
}

/// shlex.split (None: unbalanced quotes).
pub fn shell_split(line: &str) -> Option<Vec<String>> {
    if line.trim().is_empty() {
        return Some(Vec::new());
    }
    glib::shell_parse_argv(line).ok().map(|v| v.into_iter().map(|s| s.to_string_lossy().into_owned()).collect())
}

/// shlex.join.
pub fn shell_join(words: &[String]) -> String {
    words.iter().map(|w| shell_quote(w)).collect::<Vec<_>>().join(" ")
}
