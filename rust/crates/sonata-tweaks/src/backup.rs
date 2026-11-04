//! Sonata's settings in one file (sonata2/backup.py; Settings > About >
//! Backup, Vini).
//!
//! export(path, data): a .sonata-backup (a zip): the settings folder --
//! appearance, Dock, Launchpad, shortcuts, permissions, sounds... -- and,
//! with data, the data of Sonata's own apps. read_manifest(path): what a
//! backup holds (None: not one). restore(path): its settings (and data) in
//! place of yours; yours go to the Trash first.
//!
//! The session's own files (wayfire.ini and the like) are never in a
//! backup: each computer makes its own. On trial the export holds what the
//! trial build reads (its own settings over the Python Sonata's) and a
//! restore writes only the trial's folders (factory_reset's).

use crate::factory_reset::SESSION_FILES;
use serde_json::{json, Map, Value};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub const FORMAT: i64 = 1;
pub const EXT: &str = ".sonata-backup";
pub const MANIFEST: &str = "sonata-backup.json";
/// backup.json: when it was last exported
const NAME: &str = "backup";

/// Sonata's version (sonata2/__init__.py, the one place it is kept).
pub fn version() -> &'static str {
    const INIT: &str = include_str!("../../../../sonata2/__init__.py");
    INIT.lines().find_map(|l| l.strip_prefix("__version__ = \"")).and_then(|r| r.split('"').next()).unwrap_or("")
}

fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

pub fn default_name() -> String {
    let day = glib::DateTime::now_local().and_then(|d| d.format("%Y-%m-%d")).map(|s| s.to_string()).unwrap_or_default();
    format!("Sonata Settings {day}{EXT}")
}

/// (path on disk, name in the zip) of every file under root.
fn walk(root: &Path, top: &str, skip_top: &[&str], out: &mut Vec<(PathBuf, String)>) {
    fn go(dir: &Path, rel: &str, top: &str, skip_top: &[&str], out: &mut Vec<(PathBuf, String)>) {
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        let mut entries: Vec<_> = rd.flatten().map(|e| (e.file_name().to_string_lossy().into_owned(), e.path())).collect();
        entries.sort();
        let (mut files, mut dirs) = (Vec::new(), Vec::new());
        for (n, p) in entries {
            if n.starts_with('.') || (rel.is_empty() && skip_top.contains(&n.as_str())) || p.is_symlink() {
                continue;
            }
            if p.is_dir() {
                dirs.push((n, p));
            } else if p.is_file() {
                files.push((n, p));
            }
        }
        for (n, p) in files {
            let name = [top, rel, &n].iter().filter(|x| !x.is_empty()).copied().collect::<Vec<_>>().join("/");
            out.push((p, name));
        }
        for (n, p) in dirs {
            let r = if rel.is_empty() { n } else { format!("{rel}/{n}") };
            go(&p, &r, top, skip_top, out);
        }
    }
    go(root, "", top, skip_top, out)
}

/// Blocking: the files of `config_dirs` (first one wins a name) and, with
/// data, `data_root`. Returns how many files went in.
pub fn export_from(path: &Path, data: bool, config_dirs: &[PathBuf], data_root: &Path) -> std::io::Result<usize> {
    let mut files: Vec<(PathBuf, String)> = Vec::new();
    for d in config_dirs {
        let mut here = Vec::new();
        walk(d, "config", &SESSION_FILES, &mut here);
        for (p, n) in here {
            if !files.iter().any(|(_, x)| *x == n) {
                files.push((p, n));
            }
        }
    }
    if data {
        walk(data_root, "data", &[], &mut files);
    }
    let tmp = PathBuf::from(format!("{}.part", path.display()));
    {
        let mut z = zip::ZipWriter::new(std::fs::File::create(&tmp)?);
        let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        let manifest = json!({"format": FORMAT, "sonata": version(), "data": data, "created": now()});
        z.start_file(MANIFEST, opts).map_err(std::io::Error::other)?;
        z.write_all(sonata_config::to_python_json_indent(&manifest, 1).as_bytes())?;
        for (p, n) in &files {
            z.start_file(n.as_str(), opts).map_err(std::io::Error::other)?;
            z.write_all(&std::fs::read(p)?)?;
        }
        z.finish().map_err(std::io::Error::other)?;
    }
    std::fs::rename(&tmp, path)?;
    let _ = sonata_config::update(NAME, &json!({"last_export": now()}));
    Ok(files.len())
}

pub fn export(path: &Path, data: bool) -> std::io::Result<usize> {
    let dirs: Vec<PathBuf> = sonata_config::read_dirs();
    export_from(path, data, &dirs, &crate::factory_reset::data_root())
}

/// When settings were last exported (epoch seconds), None if never.
pub fn last_export() -> Option<i64> {
    crate::util::load(NAME, json!({"last_export": null})).get("last_export").and_then(Value::as_i64)
}

pub fn read_manifest(path: &Path) -> Option<Map<String, Value>> {
    let f = std::fs::File::open(path).ok()?;
    let mut z = zip::ZipArchive::new(f).ok()?;
    let mut text = String::new();
    z.by_name(MANIFEST).ok()?.read_to_string(&mut text).ok()?;
    let m: Map<String, Value> = serde_json::from_str(&text).ok()?;
    (m.get("format").and_then(Value::as_i64) == Some(FORMAT)).then_some(m)
}

fn safe(name: &str) -> bool {
    !name.is_empty() && !name.starts_with('/') && !name.split('/').any(|p| p == "..") && !name.contains('\\')
}

/// What's in root, to the Trash (put back from there); session files stay.
fn trash_all(root: &Path, skip_top: &[&str], trash: &dyn Fn(&Path) -> Result<(), String>) {
    let Ok(rd) = std::fs::read_dir(root) else { return };
    for e in rd.flatten() {
        let n = e.file_name().to_string_lossy().into_owned();
        if !n.starts_with('.') && !skip_top.contains(&n.as_str()) {
            let _ = trash(&e.path());
        }
    }
}

/// Blocking. false: not a Sonata backup (nothing changed).
pub fn restore_into(path: &Path, config_dir: &Path, data_root: &Path, trash: &dyn Fn(&Path) -> Result<(), String>) -> bool {
    if read_manifest(path).is_none() {
        return false;
    }
    let Ok(f) = std::fs::File::open(path) else { return false };
    let Ok(mut z) = zip::ZipArchive::new(f) else { return false };
    let names: Vec<String> = z.file_names().filter(|n| *n != MANIFEST && safe(n) && !n.ends_with('/')).map(String::from).collect();
    let has_data = names.iter().any(|n| n.starts_with("data/"));
    trash_all(config_dir, &SESSION_FILES, trash);
    if has_data {
        trash_all(data_root, &[], trash);
    }
    for n in names {
        let (top, rest) = n.split_once('/').unwrap_or((&n, ""));
        let dest = if top == "config" && !SESSION_FILES.contains(&rest.split('/').next().unwrap_or("")) {
            config_dir.join(rest)
        } else if top == "data" {
            data_root.join(rest)
        } else {
            continue;
        };
        let Ok(mut entry) = z.by_name(&n) else { continue };
        let mut bytes = Vec::new();
        if entry.read_to_end(&mut bytes).is_err() {
            continue;
        }
        if let Some(p) = dest.parent() {
            let _ = std::fs::create_dir_all(p);
        }
        let _ = std::fs::write(&dest, bytes);
    }
    true
}

pub fn restore(path: &Path) -> bool {
    restore_into(path, &crate::factory_reset::config_dir(), &crate::factory_reset::data_root(), &crate::factory_reset::trash)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::fs;

    struct Rig {
        _t: tempfile::TempDir,
        cfg: PathBuf,
        data: PathBuf,
        out: PathBuf,
        trashed: RefCell<Vec<String>>,
    }

    impl Rig {
        fn new() -> Rig {
            let t = tempfile::tempdir().unwrap();
            let (cfg, data, out) = (t.path().join("cfg"), t.path().join("data"), t.path().join("out"));
            for d in [&cfg, &data.join("notes"), &out] {
                fs::create_dir_all(d).unwrap();
            }
            fs::write(cfg.join("appearance.json"), r#"{"accent": "pink"}"#).unwrap();
            fs::write(cfg.join("dock.json"), r#"{"pinned": ["firefox"]}"#).unwrap();
            fs::write(cfg.join("wayfire.ini"), "[core]").unwrap(); // the session's: never in it
            fs::write(data.join("notes/n.json"), "[1]").unwrap();
            Rig { _t: t, cfg, data, out, trashed: RefCell::new(Vec::new()) }
        }

        fn trash(&self, p: &Path) -> Result<(), String> {
            self.trashed.borrow_mut().push(p.file_name().unwrap().to_string_lossy().into_owned());
            if p.is_dir() { fs::remove_dir_all(p) } else { fs::remove_file(p) }.map_err(|e| e.to_string())
        }

        fn restore(&self, path: &Path) -> bool {
            restore_into(path, &self.cfg, &self.data, &|p| self.trash(p))
        }
    }

    #[test]
    fn export_and_restore() {
        let _l = crate::util::test_lock();
        let r = Rig::new();
        let path = r.out.join(default_name());
        assert!(path.to_string_lossy().ends_with(".sonata-backup"));
        assert_eq!(export_from(&path, true, &[r.cfg.clone()], &r.data).unwrap(), 3);
        let mut names: Vec<String> = zip::ZipArchive::new(fs::File::open(&path).unwrap()).unwrap().file_names().map(String::from).collect();
        names.sort();
        assert_eq!(names, ["config/appearance.json", "config/dock.json", "data/notes/n.json", "sonata-backup.json"]);
        assert!(last_export().is_some());
        let m = read_manifest(&path).unwrap();
        assert_eq!(m["data"], json!(true));
        assert_eq!(m["sonata"], json!(version()));
        assert!(!version().is_empty());
        // changed since: the backup's come back, the session file stays
        fs::write(r.cfg.join("appearance.json"), r#"{"accent": "blue"}"#).unwrap();
        fs::write(r.cfg.join("extra.json"), "{}").unwrap();
        assert!(r.restore(&path));
        assert!(fs::read_to_string(r.cfg.join("appearance.json")).unwrap().contains("pink"));
        assert!(!r.cfg.join("extra.json").exists()); // yours went to the Trash
        assert!(r.trashed.borrow().contains(&"extra.json".to_string()));
        assert!(r.cfg.join("wayfire.ini").exists());
        assert!(r.data.join("notes/n.json").exists());
    }

    #[test]
    fn settings_only() {
        let _l = crate::util::test_lock();
        let r = Rig::new();
        let path = r.out.join("s.sonata-backup");
        export_from(&path, false, &[r.cfg.clone()], &r.data).unwrap();
        assert!(!zip::ZipArchive::new(fs::File::open(&path).unwrap()).unwrap().file_names().any(|n| n.starts_with("data/")));
        r.trashed.borrow_mut().clear();
        r.restore(&path);
        assert!(!r.trashed.borrow().contains(&"notes".to_string())); // no data in it: yours untouched
    }

    #[test]
    fn not_a_backup_and_unsafe_names() {
        let r = Rig::new();
        let bad = r.out.join("x.sonata-backup");
        fs::write(&bad, "nope").unwrap();
        assert!(read_manifest(&bad).is_none());
        assert!(!r.restore(&bad));
        let evil = r.out.join("evil.sonata-backup");
        {
            let mut z = zip::ZipWriter::new(fs::File::create(&evil).unwrap());
            let o = zip::write::SimpleFileOptions::default();
            for (n, t) in [("sonata-backup.json", r#"{"format": 1}"#), ("config/../../escaped.json", "{}"), ("config/wayfire.ini", "[evil]")] {
                z.start_file(n, o).unwrap();
                z.write_all(t.as_bytes()).unwrap();
            }
            z.finish().unwrap();
        }
        assert!(r.restore(&evil));
        assert!(!r.cfg.parent().unwrap().join("escaped.json").exists());
        assert_eq!(fs::read_to_string(r.cfg.join("wayfire.ini")).unwrap(), "[core]");
    }
}
