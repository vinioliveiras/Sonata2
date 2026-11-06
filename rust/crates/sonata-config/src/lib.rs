//! User settings, stored as JSON in `$XDG_CONFIG_HOME/sonata2/<name>.json`
//! -- the same files, read the same way, as `sonata2/config.py`.
//!
//! Missing keys fall back to the defaults the caller passes, keys the
//! caller doesn't know are left alone on load and kept on [`update`], and a
//! file that is missing, unreadable or not a JSON object reads as "nothing
//! stored": a file an older (or the Python) Sonata wrote keeps working.
//!
//! The Rust build is the only Sonata: it reads and writes
//! `~/.config/sonata2/`, the folder the Python Sonata used (its files keep
//! working as they are). What the Rust build kept apart while it was on
//! trial (`~/.config/sonata2-rs/`) is carried over once by [`migrate`].
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

pub mod migrate;

/// Sonata's settings folder in $XDG_CONFIG_HOME.
pub const NAME: &str = "sonata2";

/// The VERSION file at the top of the clone, built in: "0.22.0-alpha".
const VERSION_FILE: &str = include_str!("../../../../VERSION");

/// Sonata's version (the one place it is kept: VERSION; CHANGELOG.md says
/// what changed in it).
pub fn version() -> &'static str {
    VERSION_FILE.trim()
}

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

/// Where settings are written: `$XDG_CONFIG_HOME/sonata2`.
pub fn write_dir() -> PathBuf {
    config_home().join(NAME)
}

/// Where settings are read from, first match wins (one folder; a list
/// for the callers that follow every place a file could be).
pub fn read_dirs() -> Vec<PathBuf> {
    vec![write_dir()]
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
    to_python_json_indent(value, 2)
}

/// `json.dumps(value, indent=indent)` (the apps' data files use indent=1).
pub fn to_python_json_indent(value: &Value, indent: usize) -> String {
    let mut out = String::new();
    write_value(&mut out, value, 0, indent, true);
    out
}

/// `json.dumps(value, ensure_ascii=False, indent=indent)` (Notes' files).
pub fn to_python_json_utf8(value: &Value, indent: usize) -> String {
    let mut out = String::new();
    write_value(&mut out, value, 0, indent, false);
    out
}

fn write_value(out: &mut String, v: &Value, depth: usize, indent: usize, ascii: bool) {
    let pad = |n: usize| " ".repeat(n * indent);
    match v {
        Value::Object(m) if !m.is_empty() => {
            out.push_str("{\n");
            for (i, (k, val)) in m.iter().enumerate() {
                out.push_str(&pad(depth + 1));
                write_string(out, k, ascii);
                out.push_str(": ");
                write_value(out, val, depth + 1, indent, ascii);
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
                write_value(out, val, depth + 1, indent, ascii);
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
        Value::String(s) => write_string(out, s, ascii),
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

fn write_string(out: &mut String, s: &str, ascii: bool) {
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
            c if (c as u32) < 0x20 || (ascii && (c as u32) > 0x7e) => {
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
/// be a JSON object.
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

/// Where Sonata's apps keep your data (userdata.py):
/// `$XDG_DATA_HOME/sonata2-data/<app>`, created -- the folder the Python
/// Sonata's apps used, so their data is there as it was.
pub fn data_dir(app: &str) -> PathBuf {
    let base = match std::env::var_os("XDG_DATA_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => home().join(".local").join("share"),
    };
    let dir = base.join("sonata2-data").join(app);
    if !dir.is_dir() {
        migrate_data(&base, app);
    }
    let _ = fs::create_dir_all(&dir);
    dir
}

/// The apps' data used to live in `<data>/sonata2/<app>`, the folder Sonata
/// itself is installed in: install.sh replaced it on every update (notes
/// and calendars went with it) and a dev install linked it to the git
/// clone. The first time an app asks for its folder, the old one (or what
/// it points to) moves over, unless the new one exists (userdata.migrate).
fn migrate_data(base: &Path, app: &str) -> bool {
    let (old, new) = (base.join("sonata2").join(app), base.join("sonata2-data").join(app));
    if new.symlink_metadata().is_ok() || !old.is_dir() {
        return false;
    }
    let Ok(from) = fs::canonicalize(&old) else { return false };
    if fs::create_dir_all(base.join("sonata2-data")).is_err() {
        return false;
    }
    match fs::rename(&from, &new) {
        Ok(()) => true,
        // another disk (shutil.move copies, then removes the old)
        Err(_) => {
            if copy_tree(&from, &new).is_err() {
                let _ = fs::remove_dir_all(&new);
                return false;
            }
            let _ = fs::remove_dir_all(&from);
            true
        }
    }
}

fn copy_tree(from: &Path, to: &Path) -> io::Result<()> {
    fs::create_dir(to)?;
    for e in fs::read_dir(from)? {
        let e = e?;
        let (src, dst) = (e.path(), to.join(e.file_name()));
        let kind = e.file_type()?;
        if kind.is_dir() {
            copy_tree(&src, &dst)?;
        } else if kind.is_symlink() {
            std::os::unix::fs::symlink(fs::read_link(&src)?, &dst)?;
        } else {
            fs::copy(&src, &dst)?;
        }
    }
    Ok(())
}

/// One of Sonata's own files (data/<name> in the clone: wallpapers, sounds,
/// decor...): in the install next to this binary
/// (`<prefix>/share/sonata2-rs/data/<name>`) whenever there is one -- even
/// for a name it lacks: an installed binary never reaches into the tree it
/// was built from (the menu bar wrote the build tree's title-bar pictures
/// into wayfire-overrides.ini, and a binary built elsewhere left Vini's
/// window buttons invisible) -- else this clone's (development, tests).
pub fn data_path(name: &str) -> PathBuf {
    data_path_in(installed_data().as_deref(), name)
}

/// `<prefix>/share/sonata2-rs/data` next to this binary, when it is there.
pub fn installed_data() -> Option<PathBuf> {
    let prefix = std::env::current_exe().ok()?.parent()?.parent()?.to_path_buf();
    Some(prefix.join("share").join("sonata2-rs").join("data")).filter(|d| d.is_dir())
}

/// The clone's data/ (development).
pub fn clone_data() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../data")
}

fn data_path_in(installed: Option<&Path>, name: &str) -> PathBuf {
    installed.map(Path::to_path_buf).unwrap_or_else(clone_data).join(name)
}

/// Finder's "Set Desktop Picture" (prefs.set_wallpaper): the picture for
/// Light and Dark, in system.json -- the desktop's own setting --
/// mirrored to GSettings, then the desktop's wallpaper is started again.
pub fn set_wallpaper(uri: &str) -> io::Result<()> {
    const BG: &str = "org.gnome.desktop.background";
    let dir = config_home().join(NAME);
    let path = dir.join("system.json");
    let mut data = read_object(&path);
    let same = data.get(&format!("{BG}/picture-uri")).and_then(Value::as_str) == Some(uri)
        && data.get(&format!("{BG}/picture-uri-dark")).and_then(Value::as_str) == Some(uri);
    if same {
        return Ok(());
    }
    {
        fs::create_dir_all(&dir)?;
        let lock = OpenOptions::new().write(true).create(true).truncate(false).open(dir.join(".system.lock"))?;
        unsafe {
            libc::flock(lock.as_raw_fd(), libc::LOCK_EX);
        }
        data = read_object(&path);
        data.insert(format!("{BG}/picture-uri"), Value::String(uri.into()));
        data.insert(format!("{BG}/picture-uri-dark"), Value::String(uri.into()));
        atomic_write(&path, to_python_json(&Value::Object(data)).as_bytes(), false)?;
    }
    for key in ["picture-uri", "picture-uri-dark"] {
        spawn_reaped(std::process::Command::new("gsettings").args(["set", BG, key, &format!("'{}'", uri.replace('\'', "\\'"))]));
    }
    // `sonata2 restart wallpaper`: the new one comes up under the old one
    let launcher = std::env::var("SONATA2_LAUNCHER").unwrap_or_else(|_| "sonata2".into());
    spawn_reaped(std::process::Command::new(launcher).args(["restart", "wallpaper"]));
    Ok(())
}

/// Started and waited for in a thread: a child left unwaited stays a
/// zombie as long as this process lives (sonata-backend's run::reap; this
/// crate comes before it).
fn spawn_reaped(cmd: &mut std::process::Command) {
    if let Ok(mut c) = cmd.spawn() {
        let _ = std::thread::Builder::new().name("reap".into()).spawn(move || {
            let _ = c.wait();
        });
    }
}

/// Follow one file (a data file, not a setting): `callback` when it
/// changes on disk. Keep the monitor alive.
#[cfg(feature = "watch")]
pub fn watch_path<F: Fn() + 'static>(path: &Path, callback: F) -> Option<gio::FileMonitor> {
    use gio::prelude::*;
    let mon = gio::File::for_path(path).monitor_file(gio::FileMonitorFlags::WATCH_MOVES, gio::Cancellable::NONE).ok()?;
    mon.connect_changed(move |_m, _f, _o, event| {
        use gio::FileMonitorEvent as E;
        if matches!(event, E::ChangesDoneHint | E::Created | E::Renamed | E::MovedIn | E::Deleted) {
            callback();
        }
    });
    Some(mon)
}

/// Call `callback` when `<name>.json` changes on disk (e.g. from Settings).
/// Keep the returned monitors alive.
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

pub mod prefs;

#[cfg(test)]
mod tests;
