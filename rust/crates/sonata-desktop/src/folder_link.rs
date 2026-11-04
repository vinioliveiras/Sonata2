//! Folders linked between the Dock and Launchpad (sonata2/folder_link.py;
//! Vini): a folder dragged from one to the other stays the same folder
//! there -- renamed, or an app put in or taken out, the other follows. A
//! folder made in the Dock appears in Launchpad by itself (at the end; not
//! a locked one). (The Dock's lock stays the Dock's.)
//!
//! The link is a random id kept in both files: dock.json
//! folders[id]["link"] and the Launchpad folder's "link". Each side pushes
//! its folders after it saves; the other process reloads from its file
//! (sonata_config::watch). A file is written only when something differs,
//! so the two never answer each other in a loop. Folders saved before
//! links existed are linked when their name and apps match.
//!
//! ```text
//! new_link()
//! to_launchpad(&mut dock_folders) -> bool   // written?
//! to_dock(&mut launchpad_data) -> bool
//! ```

use serde_json::{json, Map, Value};

pub fn new_link() -> String {
    glib::uuid_string_random().replace('-', "")[..12].to_string()
}

fn raw(name: &str) -> Map<String, Value> {
    sonata_config::stored(name)
}

/// (page, index) of every Launchpad folder, in order.
fn lp_folders(data: &Value) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    if let Some(pages) = data.get("pages").and_then(Value::as_array) {
        for (p, page) in pages.iter().enumerate() {
            if let Some(page) = page.as_array() {
                for (i, it) in page.iter().enumerate() {
                    if it.as_object().is_some_and(|o| o.get("apps").is_some_and(Value::is_array)) {
                        out.push((p, i));
                    }
                }
            }
        }
    }
    out
}

fn item_mut(data: &mut Value, (p, i): (usize, usize)) -> &mut Map<String, Value> {
    data["pages"][p][i].as_object_mut().expect("a folder")
}

/// The Dock's folders' keys, in order.
fn dock_folders(folders: &Map<String, Value>) -> Vec<String> {
    folders.iter().filter(|(_, f)| f.as_object().is_some_and(|o| o.get("apps").is_some_and(Value::is_array))).map(|(k, _)| k.clone()).collect()
}

fn s<'a>(m: &'a Map<String, Value>, k: &str) -> Option<&'a str> {
    m.get(k).and_then(Value::as_str)
}

fn truthy(m: &Map<String, Value>, k: &str) -> bool {
    m.get(k).is_some_and(crate::badges::truthy)
}

fn apps(m: &Map<String, Value>) -> Vec<Value> {
    m.get("apps").and_then(Value::as_array).cloned().unwrap_or_default()
}

fn same_set(a: &[Value], b: &[Value]) -> bool {
    a.iter().all(|x| b.contains(x)) && b.iter().all(|x| a.contains(x))
}

/// Unlinked folders with the same name and apps on both sides: linked.
pub fn link_existing(dock: &mut Map<String, Value>, lp_data: &mut Value) -> bool {
    let mut changed = false;
    for key in dock_folders(dock) {
        let d = dock[&key].as_object().cloned().unwrap_or_default();
        if truthy(&d, "link") {
            continue;
        }
        for pos in lp_folders(lp_data) {
            let f = item_mut(lp_data, pos);
            if !truthy(f, "link") && f.get("folder") == d.get("name") && same_set(&apps(f), &apps(&d)) {
                let link = new_link();
                f.insert("link".into(), json!(link));
                dock[&key].as_object_mut().unwrap().insert("link".into(), json!(link));
                changed = true;
                break;
            }
        }
    }
    changed
}

/// The Dock's linked folders -> launchpad.json: name and apps. An app put
/// in leaves its other place in Launchpad; one taken out goes at the end.
/// Links made here are written into `dock_folders` (and dock.json).
pub fn to_launchpad(dock_folders_map: &mut Map<String, Value>) -> bool {
    let mut data = Value::Object(raw("launchpad"));
    if !data.get("pages").is_some_and(crate::badges::truthy) {
        return false;
    }
    let changed = launchpad_from(dock_folders_map, &mut data);
    if changed.0 {
        if let Value::Object(m) = &data {
            let _ = sonata_config::save("launchpad", m);
        }
    }
    if changed.1 {
        let _ = sonata_config::update("dock", &json!({"folders": dock_folders_map}));
    }
    changed.0
}

/// to_launchpad on data in hand: (Launchpad's changed, links made).
fn launchpad_from(dock: &mut Map<String, Value>, data: &mut Value) -> (bool, bool) {
    let mut linked_now = link_existing(dock, data);
    let hidden: Vec<Value> = data.get("hidden").and_then(Value::as_array).cloned().unwrap_or_default();
    // a folder made in the Dock shows in Launchpad too (Vini), linked; a
    // locked one keeps its apps private (Dock only)
    let last_page_ok = data["pages"].as_array().and_then(|p| p.last()).is_some_and(Value::is_array);
    for key in dock_folders(dock) {
        let d = dock[&key].as_object_mut().unwrap();
        if truthy(d, "link") || truthy(d, "locked") || !last_page_ok {
            continue;
        }
        let link = new_link();
        d.insert("link".into(), json!(link));
        let name = s(d, "name").filter(|n| !n.is_empty()).unwrap_or("Untitled Folder").to_string();
        let last = data["pages"].as_array_mut().unwrap().last_mut().unwrap().as_array_mut().unwrap();
        last.push(json!({"folder": name, "apps": [], "link": link}));
        linked_now = true;
    }
    let links: Vec<(Value, Map<String, Value>)> = dock_folders(dock)
        .into_iter()
        .filter_map(|k| dock[&k].as_object().filter(|d| truthy(d, "link")).map(|d| (d["link"].clone(), d.clone())))
        .collect();
    let mut changed = linked_now;
    // folders stay where they are below (only plain apps move): the k-th one is the same folder throughout
    let count = lp_folders(data).len();
    for k in 0..count {
        let pos = lp_folders(data)[k];
        let f = item_mut(data, pos);
        let Some(d) = f.get("link").and_then(|l| links.iter().find(|(dl, _)| dl == l)).map(|(_, d)| d.clone()) else { continue };
        if let Some(name) = s(&d, "name").filter(|n| !n.is_empty()) {
            if s(f, "folder") != Some(name) {
                f.insert("folder".into(), json!(name));
                changed = true;
            }
        }
        let mut want: Vec<Value> = Vec::new();
        for a in apps(&d) {
            if !want.contains(&a) && !hidden.contains(&a) {
                want.push(a);
            }
        }
        let have = apps(f);
        if want == have {
            continue;
        }
        let gone: Vec<Value> = have.iter().filter(|a| !want.contains(a)).cloned().collect();
        // an app put in: out of its other place
        for page in data["pages"].as_array_mut().unwrap() {
            if let Some(page) = page.as_array_mut() {
                page.retain(|it| !(it.is_string() && want.contains(it)));
            }
        }
        let pos = lp_folders(data)[k];
        for (j, other) in lp_folders(data).into_iter().enumerate() {
            if j != k {
                let o = item_mut(data, other);
                let kept: Vec<Value> = apps(o).into_iter().filter(|a| !want.contains(a)).collect();
                o.insert("apps".into(), Value::Array(kept));
            }
        }
        item_mut(data, pos).insert("apps".into(), Value::Array(want));
        // taken out: at the end (Launchpad repacks)
        if let Some(last) = data["pages"].as_array_mut().and_then(|p| p.last_mut()).and_then(Value::as_array_mut) {
            last.extend(gone);
        }
        changed = true;
    }
    (changed, linked_now)
}

/// Launchpad's linked folders -> dock.json: name and apps (apps hidden in
/// Launchpad stay in the Dock's copy). Links made here are written into
/// `lp_data` (and launchpad.json).
pub fn to_dock(lp_data: &mut Value) -> bool {
    let dock = raw("dock");
    let Some(Value::Object(mut folders)) = dock.get("folders").cloned() else { return false };
    if folders.is_empty() {
        return false;
    }
    if !lp_data.is_object() {
        *lp_data = json!({});
    }
    let (changed, linked) = dock_from(&mut folders, lp_data);
    if linked {
        if let Value::Object(m) = &*lp_data {
            let _ = sonata_config::save("launchpad", m);
        }
    }
    if changed {
        let _ = sonata_config::update("dock", &json!({"folders": folders}));
    }
    changed
}

/// to_dock on data in hand: (the Dock's changed, links made).
fn dock_from(folders: &mut Map<String, Value>, lp_data: &mut Value) -> (bool, bool) {
    let linked = link_existing(folders, lp_data);
    let mut changed = linked;
    let hidden: Vec<Value> = lp_data.get("hidden").and_then(Value::as_array).cloned().unwrap_or_default();
    for pos in lp_folders(lp_data) {
        let f = item_mut(lp_data, pos).clone();
        let Some(link) = f.get("link").filter(|l| crate::badges::truthy(l)) else { continue };
        let Some(key) = dock_folders(folders).into_iter().find(|k| folders[k].get("link") == Some(link)) else { continue };
        let d = folders[&key].as_object_mut().unwrap();
        if let Some(name) = s(&f, "folder").filter(|n| !n.is_empty()) {
            if s(d, "name") != Some(name) {
                d.insert("name".into(), json!(name));
                changed = true;
            }
        }
        let fa = apps(&f);
        let mut want = fa.clone();
        want.extend(apps(d).into_iter().filter(|a| hidden.contains(a) && !fa.contains(a)));
        if want != apps(d) {
            d.insert("apps".into(), Value::Array(want));
            changed = true;
        }
    }
    (changed, linked)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() -> Map<String, Value> {
        let lp = json!({"pages": [["x", {"folder": "Work", "apps": ["a", "b"], "link": "L1"}, "c", {"folder": "Other", "apps": ["d", "e"]}]], "hidden": ["h"]});
        sonata_config::save("launchpad", lp.as_object().unwrap()).unwrap();
        let dock = json!({"1": {"name": "Work", "apps": ["a", "b"], "link": "L1"}, "2": {"name": "Solo", "apps": ["d", "e"]}});
        sonata_config::save("dock", json!({"pinned": ["folder:1", "folder:2"], "folders": dock, "icon_size": 50}).as_object().unwrap()).unwrap();
        dock.as_object().unwrap().clone()
    }

    fn raw_v(name: &str) -> Value {
        Value::Object(raw(name))
    }

    fn lp_item(link: &str) -> Value {
        raw_v("launchpad")["pages"][0].as_array().unwrap().iter().find(|it| it.get("link").and_then(Value::as_str) == Some(link)).cloned().unwrap()
    }

    #[test]
    fn test_dock_rename_reaches_launchpad() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock["1"]["name"] = json!("Games");
        assert!(to_launchpad(&mut dock));
        assert_eq!(lp_item("L1")["folder"], "Games");
        assert!(!to_launchpad(&mut dock)); // nothing new: not written again
    }

    #[test]
    fn test_dock_app_in_and_out() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock.remove("2");
        dock["1"]["apps"] = json!(["a", "c", "d"]); // b out; c (top) and d (Other) in
        to_launchpad(&mut dock);
        let page = raw_v("launchpad")["pages"][0].clone();
        let page = page.as_array().unwrap();
        assert_eq!(lp_item("L1")["apps"], json!(["a", "c", "d"]));
        assert!(!page.contains(&json!("c"))); // left its own place
        let other = page.iter().find(|it| it.get("folder") == Some(&json!("Other"))).unwrap();
        assert_eq!(other["apps"], json!(["e"])); // (Launchpad dissolves it on load)
        assert_eq!(page.last().unwrap(), "b"); // taken out: at the end
    }

    #[test]
    fn test_hidden_apps_stay_hidden() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock["1"]["apps"] = json!(["a", "b", "h"]);
        to_launchpad(&mut dock);
        assert_eq!(lp_item("L1")["apps"], json!(["a", "b"]));
        assert_eq!(raw_v("launchpad")["hidden"], json!(["h"]));
    }

    #[test]
    fn test_launchpad_changes_reach_dock() {
        let _l = crate::test_lock();
        setup();
        let mut data = raw_v("launchpad");
        let f = data["pages"][0].as_array_mut().unwrap().iter_mut().find(|it| it.get("link") == Some(&json!("L1"))).unwrap();
        f["folder"] = json!("Games");
        f["apps"] = json!(["b", "a", "x"]);
        assert!(to_dock(&mut data));
        let d = raw_v("dock");
        assert_eq!(d["folders"]["1"], json!({"name": "Games", "apps": ["b", "a", "x"], "link": "L1"}));
        assert_eq!((d["pinned"].clone(), d["icon_size"].clone()), (json!(["folder:1", "folder:2"]), json!(50))); // the rest as stored
        assert!(!to_dock(&mut data));
    }

    #[test]
    fn test_dock_keeps_its_hidden_apps() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock["1"]["apps"] = json!(["a", "b", "h"]);
        sonata_config::update("dock", &json!({"folders": dock})).unwrap();
        let mut data = raw_v("launchpad");
        assert!(!to_dock(&mut data)); // h is hidden there, not removed
        assert_eq!(raw_v("dock")["folders"]["1"]["apps"], json!(["a", "b", "h"]));
    }

    #[test]
    fn test_old_folders_linked_by_name_and_apps() {
        let _l = crate::test_lock();
        let mut dock = setup();
        let mut data = raw_v("launchpad");
        for it in data["pages"][0].as_array_mut().unwrap() {
            if it.get("folder") == Some(&json!("Other")) {
                it["folder"] = json!("Solo");
            }
        }
        sonata_config::save("launchpad", data.as_object().unwrap()).unwrap();
        to_launchpad(&mut dock);
        let link = dock["2"]["link"].as_str().unwrap().to_string();
        assert!(!link.is_empty());
        assert_eq!(raw_v("dock")["folders"]["2"]["link"], json!(link));
        assert_eq!(lp_item(&link)["apps"], json!(["d", "e"]));
    }

    /// Vini: a folder made in the Dock shows in Launchpad too (linked).
    #[test]
    fn test_dock_made_folder_appears_in_launchpad() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock["2"]["name"] = json!("Games");
        to_launchpad(&mut dock);
        let link = dock["2"]["link"].as_str().unwrap().to_string();
        let item = lp_item(&link);
        assert_eq!((item["folder"].clone(), item["apps"].clone()), (json!("Games"), json!(["d", "e"])));
        assert_eq!(raw_v("dock")["folders"]["2"]["link"], json!(link)); // linked from now on
        assert!(!to_launchpad(&mut dock)); // added once
    }

    #[test]
    fn test_locked_dock_folder_stays_private() {
        let _l = crate::test_lock();
        let mut dock = setup();
        dock["2"]["locked"] = json!(true);
        to_launchpad(&mut dock);
        assert!(dock["2"].get("link").is_none());
        let names: Vec<Value> = raw_v("launchpad")["pages"][0].as_array().unwrap().iter().filter_map(|it| it.get("folder").cloned()).collect();
        assert_eq!(names, [json!("Work"), json!("Other")]);
    }

    #[test]
    fn test_new_link() {
        let (a, b) = (new_link(), new_link());
        assert_eq!(a.len(), 12);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }
}
