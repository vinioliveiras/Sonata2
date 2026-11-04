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
