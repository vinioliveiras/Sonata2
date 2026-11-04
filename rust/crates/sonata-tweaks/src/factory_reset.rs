//! Resetting Sonata (sonata2/factory_reset.py; Settings > About).
//!
//! settings_only(): every Sonata setting back to its default. What you made
//! stays: the Dock's apps and folders, Launchpad's layout, protected apps,
//! history, your apps' data. everything(): Sonata as just installed: all
//! of the above goes too, its caches are emptied and your apps' data is
//! moved to the Trash -- never deleted outright.
//!
//! The session's own files (wayfire.ini, the dconf profile, fonts.conf,
//! dotfile markers) are never touched: the next login needs them. Both
//! return what they removed (paths), for the log.
//!
//! Which folders: the ones this build writes -- on trial `~/.config/sonata2-rs`,
//! `~/.cache/sonata2-rs` and `~/.local/share/sonata2-rs-data`, so a reset in
//! the trial build never takes the Python Sonata's settings or data with it.

use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

/// made by the session / install.sh, not settings
pub const SESSION_FILES: [&str; 3] = ["wayfire.ini", "dconf-profile", "fonts.conf"];
/// what you made, not settings: kept by settings_only()
pub const CONTENT: [&str; 5] = ["launchpad.json", "activity-history.json", "files-folders.json", "security.json", "setup.json"];
/// dock.json: kept by settings_only()
pub const DOCK_CONTENT: [&str; 4] = ["pinned", "folders", "stacks", "recent"];

fn trial() -> bool {
    sonata_config::side_by_side()
}

pub fn config_dir() -> PathBuf {
    sonata_config::write_dir()
}

pub fn cache_dir() -> PathBuf {
    crate::util::cache_home().join(if trial() { sonata_config::TRIAL_NAME } else { sonata_config::NAME })
}

/// Where your apps' data is (userdata.root()).
pub fn data_root() -> PathBuf {
    crate::util::data_home().join(if trial() { "sonata2-rs-data" } else { "sonata2-data" })
}

fn config_files(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .map(|rd| rd.flatten().filter(|e| e.path().is_file()).map(|e| e.file_name().to_string_lossy().into_owned()).collect())
        .unwrap_or_default();
    names.retain(|n| !n.starts_with('.') && !SESSION_FILES.contains(&n.as_str()));
    names.sort();
    names
}

/// dock.json's content only (its apps and folders), the options gone.
pub fn dock_content(dock: &Map<String, Value>) -> Map<String, Value> {
    DOCK_CONTENT.iter().filter_map(|k| dock.get(*k).filter(|v| !v.is_null()).map(|v| (k.to_string(), v.clone()))).collect()
}

pub fn settings_only_in(dir: &Path) -> Vec<PathBuf> {
    let mut gone = Vec::new();
    for name in config_files(dir) {
        let path = dir.join(&name);
        if CONTENT.contains(&name.as_str()) {
            continue;
        }
        if name == "dock.json" {
            let dock: Map<String, Value> = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
            let _ = sonata_config::atomic_write(&path, sonata_config::to_python_json(&Value::Object(dock_content(&dock))).as_bytes(), false);
            gone.push(path);
            continue;
        }
        if std::fs::remove_file(&path).is_ok() {
            gone.push(path);
        }
    }
    gone
}

pub fn settings_only() -> Vec<PathBuf> {
    settings_only_in(&config_dir())
}

pub fn everything_in(dir: &Path, cache: &Path, data: &Path, trash: &dyn Fn(&Path) -> Result<(), String>) -> Vec<PathBuf> {
    let mut gone: Vec<PathBuf> = config_files(dir).iter().map(|n| dir.join(n)).collect();
    for p in &gone {
        let _ = std::fs::remove_file(p);
    }
    if let Ok(rd) = std::fs::read_dir(cache) {
        for e in rd.flatten() {
            let path = e.path();
            if e.file_name().to_string_lossy().ends_with(".log") {
                continue; // the running parts write there (and help after)
            }
            if path.is_dir() && !path.is_symlink() {
                let _ = std::fs::remove_dir_all(&path);
            } else {
                let _ = std::fs::remove_file(&path);
            }
            gone.push(path);
        }
    }
    if data.is_dir() {
        match trash(data) {
            Ok(()) => gone.push(data.to_path_buf()),
            Err(e) => println!("sonata2: reset: couldn't move {} to the Trash: {e}", data.display()),
        }
    }
    gone
}

/// To the Trash (put back from there).
pub fn trash(path: &Path) -> Result<(), String> {
    use gio::prelude::*;
    gio::File::for_path(path).trash(gio::Cancellable::NONE).map_err(|e| e.message().to_string())
}

pub fn everything() -> Vec<PathBuf> {
    everything_in(&config_dir(), &cache_dir(), &data_root(), &trash)
}


#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn setup() -> (tempfile::TempDir, PathBuf, PathBuf, PathBuf) {
        let t = tempfile::tempdir().unwrap();
        let (c, cache, data) = (t.path().join("cfg"), t.path().join("cache"), t.path().join("data"));
        fs::create_dir_all(&c).unwrap();
        for n in ["wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1", "appearance.json", "topbar.json", "launchpad.json", "security.json", "activity-history.json", "wayfire-overrides.ini", "compositor-display-gpu"] {
            fs::write(c.join(n), "{}").unwrap();
        }
        fs::write(c.join("dock.json"), r#"{"pinned": ["a.desktop", "folder:f1"], "folders": {"f1": {"name": "W", "apps": []}}, "icon_size": 90}"#).unwrap();
        fs::create_dir_all(cache.join("app-icons")).unwrap();
        fs::write(cache.join("app-icons/x.png"), "x").unwrap();
        fs::write(cache.join("dock.log"), "log").unwrap();
        fs::create_dir_all(data.join("notes")).unwrap();
        fs::write(data.join("notes/n.json"), "{}").unwrap();
        (t, c, cache, data)
    }

    #[test]
    fn settings_only_keeps_what_you_made() {
        let (_t, c, cache, data) = setup();
        settings_only_in(&c);
        for n in ["appearance.json", "topbar.json", "wayfire-overrides.ini", "compositor-display-gpu"] {
            assert!(!c.join(n).exists(), "{n}");
        }
        for n in ["launchpad.json", "security.json", "activity-history.json", "wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1"] {
            assert!(c.join(n).exists(), "{n}");
        }
        let dock: Value = serde_json::from_slice(&fs::read(c.join("dock.json")).unwrap()).unwrap();
        assert_eq!(dock["pinned"], json!(["a.desktop", "folder:f1"])); // Dock's apps and folders
        assert!(dock["folders"].get("f1").is_some());
        assert!(dock.get("icon_size").is_none()); // its options: defaults
        assert!(cache.join("app-icons/x.png").exists() && data.is_dir());
    }

    #[test]
    fn everything_but_the_session_and_the_logs() {
        let (_t, c, cache, data) = setup();
        let trashed = std::cell::RefCell::new(Vec::new());
        everything_in(&c, &cache, &data, &|p| {
            trashed.borrow_mut().push(p.to_path_buf());
            Ok(())
        });
        for n in ["appearance.json", "dock.json", "launchpad.json", "security.json", "wayfire-overrides.ini"] {
            assert!(!c.join(n).exists(), "{n}");
        }
        for n in ["wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1"] {
            assert!(c.join(n).exists(), "{n}"); // the next login needs them
        }
        assert!(!cache.join("app-icons").exists() && cache.join("dock.log").exists());
        assert_eq!(*trashed.borrow(), [data]); // to the Trash, never deleted
    }
}
