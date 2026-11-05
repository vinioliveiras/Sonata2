//! config.py's behaviour, in a temporary config folder (never the real
//! ~/.config: see tests/__init__.py for why).

use super::*;
use serde_json::json;
use std::sync::{Mutex, MutexGuard};

static ENV: Mutex<()> = Mutex::new(());

/// A temporary XDG_CONFIG_HOME for one test (tests share the process
/// environment: one at a time).
struct Env {
    dir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

fn env(side_by_side: bool) -> Env {
    let guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("XDG_CONFIG_HOME", dir.path());
    std::env::set_var("SONATA2_RS_SIDE_BY_SIDE", if side_by_side { "1" } else { "0" });
    Env { dir, _guard: guard }
}

impl Env {
    fn write(&self, folder: &str, name: &str, text: &str) {
        let d = self.dir.path().join(folder);
        fs::create_dir_all(&d).unwrap();
        fs::write(d.join(format!("{name}.json")), text).unwrap();
    }
    fn read(&self, folder: &str, name: &str) -> Option<String> {
        fs::read_to_string(self.dir.path().join(folder).join(format!("{name}.json"))).ok()
    }
}

#[test]
fn missing_file_gives_the_defaults() {
    let _e = env(false);
    let got = load("dock", &json!({"size": 48, "autohide": false}));
    assert_eq!(Value::Object(got), json!({"size": 48, "autohide": false}));
}

#[test]
fn stored_values_win_and_unknown_keys_are_left_out() {
    let e = env(false);
    e.write("sonata2", "dock", r#"{"size": 60, "old_key": 1}"#);
    let got = load("dock", &json!({"size": 48, "autohide": false}));
    assert_eq!(Value::Object(got), json!({"size": 60, "autohide": false}));
}

#[test]
fn broken_or_non_object_files_read_as_nothing_stored() {
    let e = env(false);
    for text in ["{not json", "[1, 2]", "\"text\"", ""] {
        e.write("sonata2", "dock", text);
        assert_eq!(Value::Object(load("dock", &json!({"size": 48}))), json!({"size": 48}), "{text:?}");
    }
}

#[test]
fn update_keeps_every_other_key() {
    let e = env(false);
    e.write("sonata2", "dock", r#"{"size": 60, "pinned": ["a", "b"]}"#);
    update("dock", &json!({"size": 40})).unwrap();
    let back: Value = serde_json::from_str(&e.read("sonata2", "dock").unwrap()).unwrap();
    assert_eq!(back, json!({"size": 40, "pinned": ["a", "b"]}));
}

#[test]
fn written_text_matches_python_json_dumps_indent_2() {
    // json.dumps({"a": 1, "b": [True, None], "c": {}, "d": "ção", "e": 1.5}, indent=2)
    let v = json!({"a": 1, "b": [true, null], "c": {}, "d": "ção", "e": 1.5});
    assert_eq!(
        to_python_json(&v),
        "{\n  \"a\": 1,\n  \"b\": [\n    true,\n    null\n  ],\n  \"c\": {},\n  \"d\": \"\\u00e7\\u00e3o\",\n  \"e\": 1.5\n}"
    );
    // a character outside the BMP: a surrogate pair, as Python writes it
    assert_eq!(to_python_json(&json!("🎵")), "\"\\ud83c\\udfb5\"");
}

#[test]
fn trial_build_reads_python_settings_but_never_writes_them() {
    let e = env(true);
    e.write("sonata2", "appearance", r#"{"accent": "red", "theme": "mac"}"#);
    // reads the Python Sonata's file...
    assert_eq!(load("appearance", &json!({"accent": "blue"}))["accent"], json!("red"));
    // ...writes its own, starting from what was stored
    update("appearance", &json!({"accent": "green"})).unwrap();
    assert_eq!(e.read("sonata2", "appearance").unwrap(), r#"{"accent": "red", "theme": "mac"}"#);
    let mine: Value = serde_json::from_str(&e.read("sonata2-rs", "appearance").unwrap()).unwrap();
    assert_eq!(mine, json!({"accent": "green", "theme": "mac"}));
    // and from then on its own file is the one read
    assert_eq!(load("appearance", &json!({"accent": "blue"}))["accent"], json!("green"));
    // no lock file left in the Python Sonata's folder either
    assert!(!e.dir.path().join("sonata2").join(".appearance.lock").exists());
}

#[test]
fn final_mode_reads_and_writes_the_shared_folder() {
    let e = env(false);
    update("dock", &json!({"size": 50})).unwrap();
    assert!(e.read("sonata2", "dock").is_some());
    assert!(e.read("sonata2-rs", "dock").is_none());
}

#[test]
fn atomic_write_leaves_no_temp_files() {
    let e = env(false);
    let p = e.dir.path().join("x").join("f.json");
    atomic_write(&p, b"one", true).unwrap();
    atomic_write(&p, b"two", false).unwrap();
    assert_eq!(fs::read_to_string(&p).unwrap(), "two");
    let names: Vec<_> = fs::read_dir(p.parent().unwrap()).unwrap().map(|d| d.unwrap().file_name()).collect();
    assert_eq!(names, vec![std::ffi::OsString::from("f.json")]);
}

#[test]
fn indent_one_as_the_apps_data_files() {
    // json.dumps({"alarms": [{"id": "a", "repeat": []}]}, indent=1)
    let v = json!({"alarms": [{"id": "a", "repeat": []}]});
    assert_eq!(to_python_json_indent(&v, 1), "{\n \"alarms\": [\n  {\n   \"id\": \"a\",\n   \"repeat\": []\n  }\n ]\n}");
}

/// Notes' timestamps came back one bit off after a save: serde_json's fast
/// float parser read "1793814913.3558567" as 1793814913.355857, so every
/// number the Python Sonata wrote could change on the Rust side's next
/// write (and the Notes store's round-trip test failed now and then).
#[test]
fn test_floats_read_back_exactly() {
    for text in ["1793814913.3558567", "1793814913.355857", "0.1", "1e-7", "123456789.12345679"] {
        let v: serde_json::Value = serde_json::from_str(text).unwrap();
        let want: f64 = text.parse().unwrap();
        assert_eq!(v.as_f64().unwrap().to_bits(), want.to_bits(), "{text}");
        assert_eq!(to_python_json(&v), format!("{want:?}"));
    }
}

/// Notes and calendars kept in the old ~/.local/share/sonata2/<app> (where
/// install.sh puts Sonata itself) never moved to sonata2-data in the Rust
/// apps: they opened empty. Python moves them over the first time.
#[test]
fn test_old_data_folder_was_not_moved_over() {
    let d = tempfile::tempdir().unwrap();
    let old = d.path().join("sonata2").join("notes");
    fs::create_dir_all(old.join("sub")).unwrap();
    fs::write(old.join("notes.json"), "[1]").unwrap();
    fs::write(old.join("sub").join("a"), "x").unwrap();
    assert!(migrate_data(d.path(), "notes"));
    let new = d.path().join("sonata2-data").join("notes");
    assert_eq!(fs::read_to_string(new.join("notes.json")).unwrap(), "[1]");
    assert_eq!(fs::read_to_string(new.join("sub").join("a")).unwrap(), "x");
    assert!(!old.exists());
    // only once: a new folder that exists already is kept as it is
    fs::create_dir_all(&old).unwrap();
    fs::write(old.join("notes.json"), "[2]").unwrap();
    assert!(!migrate_data(d.path(), "notes"));
    assert_eq!(fs::read_to_string(new.join("notes.json")).unwrap(), "[1]");
    // nothing old: nothing to do
    assert!(!migrate_data(d.path(), "calendar"));
    // a dev install's link: what it points to moves
    let clone = d.path().join("clone-notes");
    fs::create_dir_all(&clone).unwrap();
    fs::write(clone.join("t"), "y").unwrap();
    std::os::unix::fs::symlink(&clone, d.path().join("sonata2").join("textedit")).unwrap();
    assert!(migrate_data(d.path(), "textedit"));
    assert_eq!(fs::read_to_string(d.path().join("sonata2-data").join("textedit").join("t")).unwrap(), "y");
}

/// data_dir does the move itself, the first time.
#[test]
fn test_data_dir_left_the_old_folder_behind() {
    let _e = env(false);
    let d = tempfile::tempdir().unwrap();
    let prev = std::env::var_os("XDG_DATA_HOME");
    std::env::set_var("XDG_DATA_HOME", d.path());
    fs::create_dir_all(d.path().join("sonata2").join("calendar")).unwrap();
    fs::write(d.path().join("sonata2").join("calendar").join("c.ics"), "z").unwrap();
    let got = data_dir("calendar");
    match prev {
        Some(v) => std::env::set_var("XDG_DATA_HOME", v),
        None => std::env::remove_var("XDG_DATA_HOME"),
    }
    assert_eq!(got, d.path().join("sonata2-data").join("calendar"));
    assert_eq!(fs::read_to_string(got.join("c.ics")).unwrap(), "z");
}
