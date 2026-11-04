//! User settings, stored as JSON in `$XDG_CONFIG_HOME/sonata2/<name>.json`
//! -- the same files, read the same way, as `sonata2/config.py`.
//!
//! Missing keys fall back to the defaults the caller passes, keys the
//! caller doesn't know are left alone on load and kept on [`update`], and a
//! file that is missing, unreadable or not a JSON object reads as "nothing
//! stored": a file an older (or the Python) Sonata wrote keeps working.
//!
//! **Side by side** (docs/RUST_PORT.md): while the Rust build is on trial
//! next to the installed Python Sonata, it reads `~/.config/sonata2-rs/`
//! first and then `~/.config/sonata2/` (your real look and settings), and
//! writes only to `~/.config/sonata2-rs/`. The Python Sonata's files are
//! never written. `SONATA2_RS_SIDE_BY_SIDE=0` is the final mode: read and
//! write `~/.config/sonata2/`, as the component that replaced Python's.
//!
//! ```no_run
//! use serde_json::json;
//! let dock = sonata_config::load("dock", &json!({"size": 48, "autohide": false}));
//! sonata_config::update("dock", &json!({"size": 56})).ok();
//! ```

use serde_json::{Map, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

/// The folder the Python Sonata uses (and the Rust build, once final).
pub const NAME: &str = "sonata2";
/// The trial build's own folder, next to it.
pub const TRIAL_NAME: &str = "sonata2-rs";

/// `$XDG_CONFIG_HOME`, or `~/.config`.
pub fn config_home() -> PathBuf {
    match std::env::var_os("XDG_CONFIG_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => home().join(".config"),
    }
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// True while the Rust build runs next to the Python Sonata (the default
/// until a component replaces its Python version).
pub fn side_by_side() -> bool {
    !matches!(std::env::var("SONATA2_RS_SIDE_BY_SIDE").as_deref(), Ok("0") | Ok("false") | Ok("no"))
}

/// Where settings are written: `sonata2-rs/` on trial, `sonata2/` once final.
pub fn write_dir() -> PathBuf {
    config_home().join(if side_by_side() { TRIAL_NAME } else { NAME })
}

/// Where settings are read from, first match wins.
pub fn read_dirs() -> Vec<PathBuf> {
    let home = config_home();
    if side_by_side() {
        vec![home.join(TRIAL_NAME), home.join(NAME)]
    } else {
        vec![home.join(NAME)]
    }
}

/// The file `<name>.json` is read from now: the first of [`read_dirs`] that has it.
pub fn path(name: &str) -> PathBuf {
    let file = format!("{name}.json");
    read_dirs()
        .into_iter()
        .map(|d| d.join(&file))
        .find(|p| p.exists())
        .unwrap_or_else(|| write_dir().join(&file))
}

/// Every path `<name>.json` could be read from (what a watcher follows).
pub fn paths(name: &str) -> Vec<PathBuf> {
    read_dirs().into_iter().map(|d| d.join(format!("{name}.json"))).collect()
}

/// What `<name>.json` holds, as an object; empty when missing or not an object.
pub fn stored(name: &str) -> Map<String, Value> {
    read_object(&path(name))
}

fn read_object(p: &Path) -> Map<String, Value> {
    match fs::read(p).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

/// `defaults` with the stored values of the keys it has (config.load).
/// `defaults` must be a JSON object; anything else reads as an empty one.
pub fn load(name: &str, defaults: &Value) -> Map<String, Value> {
    let mut data = defaults.as_object().cloned().unwrap_or_default();
    for (k, v) in stored(name) {
        if data.contains_key(&k) {
            data.insert(k, v);
        }
    }
    data
}

/// Replace `path` with `data` in one step: a unique temp file in the same
/// folder (several processes write the same files), optionally fsync, then
/// rename -- never a half-written file, never another writer's temp.
pub fn atomic_write(path: &Path, data: &[u8], fsync: bool) -> io::Result<()> {
    let folder = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    fs::create_dir_all(folder)?;
    let base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut tmp = None;
    for i in 0..100u32 {
        let candidate = folder.join(format!(".{base}.{}.{i}.tmp", std::process::id()));
        match OpenOptions::new().write(true).create_new(true).open(&candidate) {
            Ok(f) => {
                tmp = Some((candidate, f));
                break;
            }
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    let (tmp_path, mut f) = tmp.ok_or_else(|| io::Error::new(io::ErrorKind::AlreadyExists, "no temp name"))?;
    let result = (|| {
        f.write_all(data)?;
        if fsync {
            f.sync_all()?;
        }
        drop(f);
        fs::rename(&tmp_path, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp_path);
    }
    result
}

/// One writer at a time for `<name>.json` across Sonata's processes: the
/// same `.<name>.lock` file and flock as config.py, so a Python and a Rust
/// writer of the same folder wait for each other.
struct Lock(#[allow(dead_code)] File);

fn lock(name: &str) -> io::Result<Lock> {
    let dir = write_dir();
    fs::create_dir_all(&dir)?;
    let f = OpenOptions::new().write(true).create(true).truncate(false).open(dir.join(format!(".{name}.lock")))?;
    // (a failed flock -- an odd file system -- still writes, as config.py does)
    unsafe {
        libc::flock(f.as_raw_fd(), libc::LOCK_EX);
    }
    Ok(Lock(f))
}

/// The same text config.py writes: `json.dumps(data, indent=2)`, non-ASCII
/// escaped, so a file reads the same whichever side wrote it last.
pub fn to_python_json(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, 0);
    out
}

fn write_value(out: &mut String, v: &Value, depth: usize) {
    let pad = |n: usize| "  ".repeat(n);
    match v {
        Value::Object(m) if !m.is_empty() => {
            out.push_str("{\n");
            for (i, (k, val)) in m.iter().enumerate() {
                out.push_str(&pad(depth + 1));
                write_string(out, k);
                out.push_str(": ");
                write_value(out, val, depth + 1);
                if i + 1 < m.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            out.push_str(&pad(depth));
            out.push('}');
        }
        Value::Array(a) if !a.is_empty() => {
            out.push_str("[\n");
            for (i, val) in a.iter().enumerate() {
                out.push_str(&pad(depth + 1));
                write_value(out, val, depth + 1);
                if i + 1 < a.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            out.push_str(&pad(depth));
            out.push(']');
        }
        Value::Object(_) => out.push_str("{}"),
        Value::Array(_) => out.push_str("[]"),
        Value::String(s) => write_string(out, s),
        Value::Number(n) => out.push_str(&python_number(n)),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Null => out.push_str("null"),
    }
}

fn python_number(n: &serde_json::Number) -> String {
    if let Some(f) = n.as_f64().filter(|_| n.is_f64()) {
        // Python's repr of a float always shows it is one: 1.0, not 1
        let s = format!("{f:?}");
        return s;
    }
    n.to_string()
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_unlocked(name: &str, data: &Map<String, Value>) -> io::Result<()> {
    let text = to_python_json(&Value::Object(data.clone()));
    atomic_write(&write_dir().join(format!("{name}.json")), text.as_bytes(), false)
}

/// Replace `<name>.json` with `data` (config.save).
pub fn save(name: &str, data: &Map<String, Value>) -> io::Result<()> {
    let _l = lock(name)?;
    write_unlocked(name, data)
}

/// Change some keys of `<name>.json`, keeping every other key as stored
/// (read, change and write under one lock: no lost update). `values` must
/// be a JSON object. On trial, what the Python Sonata stored is the
/// starting point and the result goes to `sonata2-rs/`.
pub fn update(name: &str, values: &Value) -> io::Result<()> {
    let _l = lock(name)?;
    let mut data = stored(name);
    if let Some(v) = values.as_object() {
        for (k, val) in v {
            data.insert(k.clone(), val.clone());
        }
    }
    write_unlocked(name, &data)
}

/// Call `callback` when `<name>.json` changes on disk (e.g. from Settings).
/// Keep the returned monitors alive. On trial both folders are followed.
#[cfg(feature = "watch")]
pub fn watch<F: Fn() + 'static>(name: &str, callback: F) -> Vec<gio::FileMonitor> {
    use gio::prelude::*;
    use std::rc::Rc;
    let callback = Rc::new(callback);
    let mut monitors = Vec::new();
    for p in paths(name) {
        if let Some(dir) = p.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let file = gio::File::for_path(&p);
        let Ok(mon) = file.monitor_file(gio::FileMonitorFlags::WATCH_MOVES, gio::Cancellable::NONE) else {
            continue;
        };
        let cb = callback.clone();
        mon.connect_changed(move |_m, _f, _o, event| {
            use gio::FileMonitorEvent as E;
            if matches!(event, E::ChangesDoneHint | E::Created | E::Renamed | E::MovedIn) {
                cb();
            }
        });
        monitors.push(mon);
    }
    monitors
}

#[cfg(test)]
mod tests;
