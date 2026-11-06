//! TextEdit's saved session (sonata2/textedit/session.py: macOS state
//! restoration / Sublime's hot exit): every window and tab, with the text of
//! untitled and unsaved documents, so quitting, closing the last window or
//! rebooting loses nothing.
//!
//!   <data>/textedit/session.json      {"version": 1, "windows": [window, ...]}
//!   <data>/textedit/buffers/<id>.txt  text of a tab with unsaved changes (UTF-8)
//!
//! On trial the Rust TextEdit keeps its own (`sonata2-rs-data`): two
//! TextEdits writing one session would undo each other's windows.

use serde_json::{json, Value};
use std::path::PathBuf;

pub const VERSION: u32 = 1;

pub fn data_dir() -> PathBuf {
    sonata_config::data_dir("textedit")
}

fn buffers() -> PathBuf {
    data_dir().join("buffers")
}

/// A tab id: 8 to 64 lowercase hex digits.
pub fn valid_id(id: &str) -> bool {
    (8..=64).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The saved windows (none when there is no usable session).
pub fn load() -> Vec<Value> {
    let Some(data) = std::fs::read(data_dir().join("session.json")).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) else {
        return Vec::new();
    };
    data.get("windows")
        .and_then(Value::as_array)
        .map(|w| w.iter().filter(|w| w.get("tabs").is_some_and(Value::is_array)).cloned().collect())
        .unwrap_or_default()
}

/// Write session.json and delete the buffer files no tab refers to. Written
/// from a main-loop timer every few seconds: no fsync (the rename still
/// makes it all-or-nothing).
pub fn save(windows: &[Value]) -> std::io::Result<()> {
    let text = sonata_config::to_python_json_indent(&json!({"version": VERSION, "windows": windows}), 1);
    sonata_config::atomic_write(&data_dir().join("session.json"), text.as_bytes(), false)?;
    let keep: Vec<&str> = windows
        .iter()
        .flat_map(|w| w.get("tabs").and_then(Value::as_array).into_iter().flatten())
        .filter(|t| t.get("backup").and_then(Value::as_bool).unwrap_or(false))
        .filter_map(|t| t.get("id").and_then(Value::as_str))
        .collect();
    if let Ok(rd) = std::fs::read_dir(buffers()) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if let Some(stem) = name.strip_suffix(".txt") {
                if valid_id(stem) && !keep.contains(&stem) {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
    }
    Ok(())
}

pub fn write_buffer(id: &str, text: &str) -> std::io::Result<()> {
    if !valid_id(id) {
        return Ok(());
    }
    sonata_config::atomic_write(&buffers().join(format!("{id}.txt")), text.as_bytes(), false)
}

pub fn read_buffer(id: &str) -> Option<String> {
    if !valid_id(id) {
        return None;
    }
    std::fs::read(buffers().join(format!("{id}.txt"))).ok().map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// Every saved window as one: their tabs side by side, the frontmost
/// window's first (documents come back as tabs, never as a pile of
/// windows). A file in two windows comes back once; an empty Untitled tab
/// (no text, no file) is left out. None when nothing is left.
pub fn merged(states: &[Value]) -> Option<Value> {
    let mut tabs: Vec<Value> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let mut active_id = None;
    for (i, st) in states.iter().enumerate() {
        let ts: Vec<&Value> = st.get("tabs").and_then(Value::as_array).map(|t| t.iter().filter(|t| t.is_object()).collect()).unwrap_or_default();
        if i == 0 && !ts.is_empty() {
            let a = st.get("active").and_then(Value::as_i64).unwrap_or(0).clamp(0, ts.len() as i64 - 1) as usize;
            active_id = ts[a].get("id").cloned();
        }
        for t in ts {
            let uri = t.get("uri").and_then(Value::as_str).filter(|u| !u.is_empty());
            let backup = t.get("backup").and_then(Value::as_bool).unwrap_or(false);
            if uri.is_none() && !backup {
                continue;
            }
            let key = match uri {
                Some(u) => format!("uri:{u}"),
                None => format!("id:{}", t.get("id").map(Value::to_string).unwrap_or_default()),
            };
            if seen.contains(&key) {
                continue;
            }
            seen.push(key);
            tabs.push(t.clone());
        }
    }
    if tabs.is_empty() {
        return None;
    }
    let active = tabs.iter().position(|t| active_id.as_ref().is_some_and(|a| t.get("id") == Some(a))).unwrap_or(0);
    let mut out = states[0].clone();
    out["tabs"] = Value::Array(tabs);
    out["active"] = json!(active);
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merged_session_skips_duplicates_and_empty_untitled() {
        let st = merged(&[
            json!({"active": 1, "tabs": [{"id": "11111111", "uri": "file:///x"}, {"id": "22222222", "uri": null, "backup": true}]}),
            json!({"tabs": [{"id": "33333333", "uri": "file:///x"}, {"id": "44444444", "uri": null}]}),
        ])
        .unwrap();
        let ids: Vec<&str> = st["tabs"].as_array().unwrap().iter().map(|t| t["id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["11111111", "22222222"]);
        assert_eq!(st["active"], json!(1));
        assert!(merged(&[json!({"tabs": [{"id": "44444444", "uri": null}]})]).is_none());
    }

    #[test]
    fn save_keeps_only_the_buffers_tabs_refer_to() {
        let _g = crate::TEST_ENV.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("XDG_DATA_HOME", dir.path());
        write_buffer("aaaaaaaa", "kept").unwrap();
        write_buffer("bbbbbbbb", "dropped").unwrap();
        write_buffer("../evil", "never").unwrap(); // not an id: not written
        save(&[json!({"tabs": [{"id": "aaaaaaaa", "backup": true}]})]).unwrap();
        assert_eq!(read_buffer("aaaaaaaa").as_deref(), Some("kept"));
        assert_eq!(read_buffer("bbbbbbbb"), None);
        assert_eq!(load().len(), 1);
        assert!(!dir.path().join("evil.txt").exists());
    }
}
