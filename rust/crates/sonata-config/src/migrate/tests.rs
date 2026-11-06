//! The upgrade from the Python Sonata (and the Rust trial) to the
//! Rust-only Sonata loses nothing (Vini: "anyone updating must lose no data
//! or settings"). Every test is a temporary HOME of its own.

use super::*;
use std::time::{Duration, SystemTime};

fn write(p: &Path, text: &str, age_s: u64) {
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p, text).unwrap();
    let t = SystemTime::now() - Duration::from_secs(age_s);
    fs::File::options().write(true).open(p).unwrap().set_modified(t).unwrap();
}

fn read(p: &Path) -> String {
    fs::read_to_string(p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

/// Every file under `dir` with its bytes, for byte-for-byte comparisons.
fn snapshot(dir: &Path) -> Vec<(PathBuf, Vec<u8>)> {
    files(dir).unwrap().into_iter().map(|rel| {
        let p = dir.join(&rel);
        let bytes = if p.symlink_metadata().unwrap().file_type().is_symlink() { fs::read_link(&p).unwrap().into_os_string().into_encoded_bytes() } else { fs::read(&p).unwrap() };
        (rel, bytes)
    }).collect()
}

struct Home {
    _dir: tempfile::TempDir,
    cfg: PathBuf,
    data: PathBuf,
    prefix: PathBuf,
    backup: PathBuf,
}

fn home() -> Home {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().to_path_buf();
    Home { cfg: p.join(".config"), data: p.join(".local/share"), prefix: p.join(".local"), backup: p.join(".local/share/sonata2-backups/upgrade-test"), _dir: dir }
}

/// A Python Sonata user's settings: every kind of file Sonata keeps there.
fn real_settings(h: &Home) {
    let s = h.cfg.join("sonata2");
    write(&s.join("dock.json"), r#"{"size": 56, "apps": ["io.github.vinioliveiras.sonata2.files.desktop"]}"#, 500);
    write(&s.join("launcher.json"), r#"{"pages": [["firefox.desktop"]]}"#, 500);
    write(&s.join("desktop.json"), r#"{"positions": {"a.txt": [1, 2]}}"#, 500);
    write(&s.join("system.json"), r#"{"org.gnome.desktop.background/picture-uri": "file:///home/v/Pictures/x.jpg"}"#, 500);
    write(&s.join("keyring.json"), r#"{"backend": "gnome"}"#, 500);
    write(&s.join("wayfire.ini"), "[core]\nplugins = blur\n# mine\n", 500);
    write(&s.join(".wayfire.ini.installed"), "[core]\nplugins = blur\n", 500);
    write(&s.join("wayfire-overrides.ini"), "[input]\nxkb_layout = br\n", 500);
    write(&s.join("clock.json"), r#"{"tab": "alarms"}"#, 100);
    write(&s.join("textedit.json"), r#"{"font": 13}"#, 1000);
}

/// The trial's own settings (~/.config/sonata2-rs) are merged into the
/// real ones: a file only the trial has is carried over, the newer of two
/// copies is kept, and every real file the trial had no copy of stays
/// byte for byte. Nothing is deleted: the trial's folder goes to the backup.
#[test]
fn test_trial_settings_merged_without_losing_the_real_ones() {
    let h = home();
    real_settings(&h);
    let trial = h.cfg.join("sonata2-rs");
    write(&trial.join("clock.json"), r#"{"tab": "timers"}"#, 2000); // older than the real one
    write(&trial.join("textedit.json"), r#"{"font": 15}"#, 10); // newer than the real one
    write(&trial.join("notes.json"), r#"{"sort": "title"}"#, 50); // the trial's only
    let before = snapshot(&h.cfg.join("sonata2"));

    let r = user(&h.cfg, &h.data, &h.backup).unwrap();

    let real = h.cfg.join("sonata2");
    // untouched by the merge: byte for byte
    for (rel, bytes) in &before {
        if rel != Path::new("textedit.json") {
            assert_eq!(&fs::read(real.join(rel)).unwrap(), bytes, "{}", rel.display());
        }
    }
    assert_eq!(read(&real.join("clock.json")), r#"{"tab": "alarms"}"#, "the newer real file stays");
    assert_eq!(read(&real.join("textedit.json")), r#"{"font": 15}"#, "the newer trial file wins");
    assert_eq!(read(&real.join("notes.json")), r#"{"sort": "title"}"#);
    // the replaced real file and the whole trial folder are in the backup
    assert_eq!(read(&h.backup.join("replaced/config-sonata2-rs/textedit.json")), r#"{"font": 13}"#);
    assert_eq!(read(&h.backup.join("config-sonata2-rs/clock.json")), r#"{"tab": "timers"}"#);
    assert!(!trial.exists());
    assert_eq!(r.copied, [real.join("notes.json")]);
    assert_eq!(r.replaced, [real.join("textedit.json")]);
    assert_eq!(r.kept_newer, [real.join("clock.json")]);
}

/// TextEdit's and Notes' data on trial (~/.local/share/sonata2-rs-data)
/// reach the real folders (sonata2-data); a newer real file is never
/// overwritten, and the copy keeps its bytes and its time.
#[test]
fn test_trial_app_data_merged_newer_kept() {
    let h = home();
    let real = h.data.join("sonata2-data");
    write(&real.join("notes/notes.json"), r#"[{"title": "real, newer"}]"#, 10);
    write(&real.join("clock/alarms.json"), "[7]", 10);
    write(&real.join("webapps/webapps.json"), r#"{"wa": {}}"#, 10);
    let trial = h.data.join("sonata2-rs-data");
    write(&trial.join("notes/notes.json"), r#"[{"title": "trial, older"}]"#, 5000);
    write(&trial.join("textedit/session.json"), r#"{"tabs": ["/x"]}"#, 60);
    write(&trial.join("textedit/autosave/1.txt"), "unsaved text", 60);
    let keep = snapshot(&real);

    user(&h.cfg, &h.data, &h.backup).unwrap();

    assert_eq!(read(&real.join("notes/notes.json")), r#"[{"title": "real, newer"}]"#);
    assert_eq!(read(&real.join("textedit/session.json")), r#"{"tabs": ["/x"]}"#);
    assert_eq!(read(&real.join("textedit/autosave/1.txt")), "unsaved text");
    for (rel, bytes) in keep {
        assert_eq!(fs::read(real.join(&rel)).unwrap(), bytes, "{}", rel.display());
    }
    // its time came along (a later merge sees the same file, not a newer one)
    assert_eq!(mtime(&real.join("textedit/session.json")), mtime(&h.backup.join("data-sonata2-rs-data/textedit/session.json")));
    assert_eq!(read(&h.backup.join("data-sonata2-rs-data/notes/notes.json")), r#"[{"title": "trial, older"}]"#);
    assert!(!trial.exists());
}

/// Running the installer again (an update) finds nothing more to move.
#[test]
fn test_second_run_changes_nothing() {
    let h = home();
    real_settings(&h);
    write(&h.cfg.join("sonata2-rs/notes.json"), "{}", 5);
    user(&h.cfg, &h.data, &h.backup).unwrap();
    let after = snapshot(&h.cfg.join("sonata2"));
    let r = user(&h.cfg, &h.data, &h.backup).unwrap();
    assert_eq!(r, Report::default());
    assert_eq!(snapshot(&h.cfg.join("sonata2")), after);
}

/// The old Python install: its folder, its launcher and the "Sonata (Rust
/// preview)" launcher go to the backup (Python's caches dropped), the app
/// data an old version kept inside the install folder is merged into
/// sonata2-data first, and the settings aren't touched at all.
#[test]
fn test_old_python_install_backed_up_and_its_data_kept() {
    let h = home();
    real_settings(&h);
    let settings = snapshot(&h.cfg.join("sonata2"));
    let old = h.prefix.join("share/sonata2");
    write(&old.join("sonata2/__init__.py"), "__version__ = \"0.22.0-alpha\"\n", 900);
    write(&old.join("sonata2/__pycache__/x.pyc"), "cache", 900);
    write(&old.join("tools/sonata-session"), "#!/bin/sh\n", 900);
    write(&old.join("calendar/home.ics"), "BEGIN:VCALENDAR (old place)", 900);
    write(&old.join("notes/notes.json"), "[\"old place, older\"]", 900);
    write(&h.data.join("sonata2-data/notes/notes.json"), "[\"sonata2-data, newer\"]", 10);
    write(&h.prefix.join("bin/sonata2"), "#!/bin/sh\nexec python3 -m sonata2 \"$@\"\n", 900);
    write(&h.prefix.join("bin/sonata-rs-session"), "#!/bin/sh\n", 900);
    write(&h.prefix.join("share/sonata2-rs/sounds/a.oga"), "ogg", 900);
    std::os::unix::fs::symlink("/clone/data/icons", h.prefix.join("share/sonata2-rs/icons")).unwrap();
    write(&h.prefix.join("share/sonata2-rs/locale/pt_BR/a.json"), "{}", 900);

    install(&h.prefix, Some(&h.data), &h.backup).unwrap();

    assert!(!old.exists() && !h.prefix.join("bin/sonata2").exists() && !h.prefix.join("bin/sonata-rs-session").exists());
    let py = h.backup.join("python-install");
    assert_eq!(read(&py.join("sonata2/__init__.py")), "__version__ = \"0.22.0-alpha\"\n");
    assert!(!py.join("sonata2/__pycache__").exists(), "caches aren't kept");
    assert_eq!(read(&h.backup.join("bin/sonata2")), "#!/bin/sh\nexec python3 -m sonata2 \"$@\"\n");
    assert_eq!(read(&h.data.join("sonata2-data/calendar/home.ics")), "BEGIN:VCALENDAR (old place)");
    assert_eq!(read(&h.data.join("sonata2-data/notes/notes.json")), "[\"sonata2-data, newer\"]");
    assert_eq!(read(&py.join("notes/notes.json")), "[\"old place, older\"]");
    assert_eq!(read(&h.backup.join("rust-trial/sounds/a.oga")), "ogg");
    assert_eq!(fs::read_link(h.backup.join("rust-trial/icons")).unwrap(), Path::new("/clone/data/icons"));
    assert!(h.prefix.join("share/sonata2-rs/locale/pt_BR/a.json").exists(), "replaced by the installer, not moved");
    assert_eq!(snapshot(&h.cfg.join("sonata2")), settings, "settings byte for byte");
}

/// A launcher that already is the Rust binary (a link), and an install
/// with nothing old: nothing moves.
#[test]
fn test_rust_install_left_alone() {
    let h = home();
    fs::create_dir_all(h.prefix.join("bin")).unwrap();
    write(&h.prefix.join("bin/sonata2-rs"), "ELF", 10);
    std::os::unix::fs::symlink("sonata2-rs", h.prefix.join("bin/sonata2")).unwrap();
    let r = install(&h.prefix, Some(&h.data), &h.backup).unwrap();
    assert_eq!(r, Report::default());
    assert!(h.prefix.join("bin/sonata2").symlink_metadata().unwrap().file_type().is_symlink());
    assert!(!h.backup.exists());
}

/// A --dev install's folder is a link to a clone: the link goes to the
/// backup, never the clone (its app folders are merged through it).
#[test]
fn test_dev_install_link_not_followed_into_the_clone() {
    let h = home();
    let clone = h.prefix.join("clone");
    write(&clone.join("sonata2/__init__.py"), "", 900);
    write(&clone.join("textedit/session.json"), "{\"tabs\": []}", 900);
    fs::create_dir_all(h.prefix.join("share")).unwrap();
    std::os::unix::fs::symlink(&clone, h.prefix.join("share/sonata2")).unwrap();
    install(&h.prefix, Some(&h.data), &h.backup).unwrap();
    assert!(clone.join("sonata2/__init__.py").exists() && clone.join("textedit/session.json").exists());
    assert_eq!(fs::read_link(h.backup.join("python-install")).unwrap(), clone);
    assert_eq!(read(&h.data.join("sonata2-data/textedit/session.json")), "{\"tabs\": []}");
}

/// Two runs in the same second never put one backup over the other.
#[test]
fn test_backup_never_overwritten() {
    let h = home();
    write(&h.cfg.join("x"), "1", 10);
    set_aside(&h.cfg.join("x"), &h.backup, Path::new("x")).unwrap();
    write(&h.cfg.join("x"), "2", 10);
    let second = set_aside(&h.cfg.join("x"), &h.backup, Path::new("x")).unwrap();
    assert_eq!((read(&h.backup.join("x")), read(&second)), ("1".into(), "2".into()));
}
