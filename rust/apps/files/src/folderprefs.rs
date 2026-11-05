//! Per-folder view settings (files/folderprefs.py; Finder keeps them per
//! folder too): the view (icons / list / columns), the list's sort column
//! and direction, and whether hidden files show -- saved only for folders
//! where you chose them. Others use the default view and Name, ascending.
//!
//! files-folders.json {"folders": {uri: {"view": "list", "sort": ["Size", true]}}};
//! the most recently changed folders are kept, past MAX the oldest go.

use serde_json::{json, Map, Value};

const NAME: &str = "files-folders";
const MAX: usize = 1000;
pub const DEFAULT_SORT: (&str, bool) = ("Name", false);
pub const VIEWS: [&str; 3] = ["icons", "list", "columns"];

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Prefs {
    pub view: Option<String>,
    pub hidden: Option<bool>,
    /// (column title, descending)
    pub sort: Option<(String, bool)>,
}

fn all() -> Map<String, Value> {
    sonata_config::load(NAME, &json!({"folders": {}})).get("folders").and_then(Value::as_object).cloned().unwrap_or_default()
}

/// Python's bool(): what an older or hand-edited file holds reads the same.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// What was chosen for this folder.
pub fn get(uri: &str) -> Prefs {
    let all = all();
    let Some(p) = all.get(uri).and_then(Value::as_object) else { return Prefs::default() };
    Prefs {
        view: p.get("view").and_then(Value::as_str).filter(|v| VIEWS.contains(v)).map(str::to_string),
        hidden: p.get("hidden").and_then(Value::as_bool),
        sort: p.get("sort").and_then(Value::as_array).filter(|a| a.len() == 2).and_then(|a| Some((a[0].as_str()?.to_string(), truthy(&a[1])))),
    }
}

/// A folder renamed: its settings follow it.
pub fn moved(old: &str, new: &str) {
    let mut folders = all();
    // (shift_remove, not remove: serde_json's remove swaps the last folder
    // into the gap, and the oldest-first order Python keeps -- what MAX
    // trims by -- came out shuffled)
    if let Some(v) = folders.shift_remove(old) {
        folders.insert(new.into(), v);
        let _ = sonata_config::save(NAME, json!({"folders": folders}).as_object().unwrap());
    }
}

/// Save view= / hidden= / sort= for this folder (moved to the newest end).
pub fn remember(uri: &str, values: &[(&str, Value)]) {
    if uri.is_empty() {
        return;
    }
    let mut folders = all();
    let mut p = folders.shift_remove(uri).and_then(|v| v.as_object().cloned()).unwrap_or_default();
    for (k, v) in values {
        p.insert(k.to_string(), v.clone());
    }
    folders.insert(uri.into(), Value::Object(p));
    while folders.len() > MAX {
        let first = folders.keys().next().cloned().unwrap();
        folders.shift_remove(&first);
    }
    let _ = sonata_config::save(NAME, json!({"folders": folders}).as_object().unwrap());
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The folder you changed last went to the end, but the folder that was
    /// last before it jumped into its old place: the order Python keeps
    /// (oldest first, what MAX trims) came out shuffled, and a full list
    /// forgot a recent folder's view instead of the oldest one.
    #[test]
    fn test_folder_settings_reordered_on_change() {
        let _ = std::fs::remove_file(sonata_config::write_dir().join("files-folders.json"));
        for u in ["file:///a", "file:///b", "file:///c"] {
            remember(u, &[("view", json!("list"))]);
        }
        remember("file:///a", &[("sort", json!(["Size", 1.0]))]);
        assert_eq!(all().keys().collect::<Vec<_>>(), ["file:///b", "file:///c", "file:///a"]);
        assert_eq!(get("file:///a").sort, Some(("Size".to_string(), true))); // bool(1.0), as Python reads it
        moved("file:///b", "file:///d");
        assert_eq!(all().keys().collect::<Vec<_>>(), ["file:///c", "file:///a", "file:///d"]);
    }
}
