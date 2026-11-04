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

/// What was chosen for this folder.
pub fn get(uri: &str) -> Prefs {
    let all = all();
    let Some(p) = all.get(uri).and_then(Value::as_object) else { return Prefs::default() };
    Prefs {
        view: p.get("view").and_then(Value::as_str).filter(|v| VIEWS.contains(v)).map(str::to_string),
        hidden: p.get("hidden").and_then(Value::as_bool),
        sort: p.get("sort").and_then(Value::as_array).filter(|a| a.len() == 2).and_then(|a| Some((a[0].as_str()?.to_string(), a[1].as_bool().unwrap_or(a[1].as_i64().unwrap_or(0) != 0)))),
    }
}

/// A folder renamed: its settings follow it.
pub fn moved(old: &str, new: &str) {
    let mut folders = all();
    if let Some(v) = folders.remove(old) {
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
    let mut p = folders.remove(uri).and_then(|v| v.as_object().cloned()).unwrap_or_default();
    for (k, v) in values {
        p.insert(k.to_string(), v.clone());
    }
    folders.insert(uri.into(), Value::Object(p));
    while folders.len() > MAX {
        let first = folders.keys().next().cloned().unwrap();
        folders.remove(&first);
    }
    let _ = sonata_config::save(NAME, json!({"folders": folders}).as_object().unwrap());
}
