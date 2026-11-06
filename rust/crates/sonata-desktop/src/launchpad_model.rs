//! Launchpad layout: pages of apps and folders (sonata2/launchpad_model.py;
//! no GTK here; unit-tested).
//!
//! Stored in launchpad.json as
//! `{"pages": [["firefox", {"folder": "Games", "apps": ["steam", ...]}, ...], ...]}`.
//! Items are desktop ids (without .desktop) or folders. Pages hold up to
//! cols x rows items; overflow cascades to the next page (macOS behaviour
//! when an item is dropped on a full page).
//!
//! Python finds a folder by identity (`it is folder`); here a folder is
//! found by its contents (the first equal one), which is the same folder
//! unless two are exactly alike.

use serde_json::{json, Map, Value};
use sonata_i18n::{n_, tr};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};

static COLS: AtomicUsize = AtomicUsize::new(7);
static ROWS: AtomicUsize = AtomicUsize::new(5);

/// launchpad.json's keys (sonata_config::load keeps only these).
pub fn defaults() -> Value {
    json!({"pages": [], "hidden": [], "grid": []})
}

pub fn cols() -> usize {
    COLS.load(Ordering::Relaxed)
}

pub fn rows() -> usize {
    ROWS.load(Ordering::Relaxed)
}

/// Items on a full page.
pub fn per_page() -> usize {
    cols() * rows()
}

/// Page size for this display (responsive: 7 columns like macOS, fewer on
/// narrow or portrait screens; as many rows as fit, 3 to 8). True if it changed.
pub fn set_grid(cols: i64, rows: i64) -> bool {
    let (c, r) = (cols.clamp(3, 8) as usize, rows.clamp(3, 8) as usize);
    if (c, r) == (self::cols(), self::rows()) {
        return false;
    }
    COLS.store(c, Ordering::Relaxed);
    ROWS.store(r, Ordering::Relaxed);
    true
}

/// Folder names from freedesktop main categories (macOS names its new
/// folders after the apps' App Store category).
pub const CATEGORY_NAMES: [(&str, &str); 13] = [
    ("Game", n_!("Games")),
    ("Development", n_!("Developer Tools")),
    ("Office", n_!("Productivity")),
    ("Graphics", n_!("Creativity")),
    ("AudioVideo", n_!("Entertainment")),
    ("Audio", n_!("Music")),
    ("Video", n_!("Video")),
    ("Network", n_!("Internet")),
    ("Education", n_!("Education")),
    ("Science", n_!("Science")),
    ("Settings", n_!("Settings")),
    ("System", n_!("System")),
    ("Utility", n_!("Utilities")),
];

/// A folder's other keys kept (folder_link: the Dock's copy; Lock Folder).
pub const KEPT_KEYS: [&str; 2] = ["link", "secure"];

/// Name for a new folder made of two apps (shared category, else first's).
pub fn folder_name<A: AsRef<str>, B: AsRef<str>>(categories_a: &[A], categories_b: &[B]) -> String {
    let a: Vec<&str> = categories_a.iter().map(AsRef::as_ref).collect();
    let b: Vec<&str> = categories_b.iter().map(AsRef::as_ref).collect();
    let both: Vec<&str> = a.iter().copied().filter(|c| b.contains(c)).collect();
    for cats in [&both, &a, &b] {
        for (key, name) in CATEGORY_NAMES {
            if cats.contains(&key) {
                return sonata_i18n::gettext(name); // a new folder's name, in the session's language
            }
        }
    }
    tr!("Untitled Folder")
}

/// A folder: its name, apps and kept keys (KEPT_KEYS).
#[derive(Clone, Debug, PartialEq)]
pub struct Folder {
    pub name: String,
    pub apps: Vec<String>,
    pub kept: Map<String, Value>,
}

impl Folder {
    pub fn new(name: &str, apps: Vec<String>) -> Self {
        Folder { name: name.into(), apps, kept: Map::new() }
    }

    /// Its Dock link ("" for none).
    pub fn link(&self) -> &str {
        self.kept.get("link").and_then(Value::as_str).unwrap_or("")
    }

    pub fn to_json(&self) -> Value {
        let mut m = Map::new();
        m.insert("folder".into(), json!(self.name));
        m.insert("apps".into(), json!(self.apps));
        for (k, v) in &self.kept {
            m.insert(k.clone(), v.clone());
        }
        Value::Object(m)
    }
}

/// An app (its desktop id) or a folder.
#[derive(Clone, Debug, PartialEq)]
pub enum Item {
    App(String),
    Folder(Folder),
}

impl Item {
    pub fn is_folder(&self) -> bool {
        matches!(self, Item::Folder(_))
    }

    pub fn to_json(&self) -> Value {
        match self {
            Item::App(a) => json!(a),
            Item::Folder(f) => f.to_json(),
        }
    }
}

impl From<&str> for Item {
    fn from(s: &str) -> Self {
        Item::App(s.into())
    }
}

/// An item as stored, leniently: a string is an app, an object a folder
/// (only its string apps; a name that isn't one is fixed by reconcile),
/// anything else nothing.
fn parse_item(v: &Value) -> Option<Item> {
    match v {
        Value::String(s) => Some(Item::App(s.clone())),
        Value::Object(o) => {
            let apps = o.get("apps").and_then(Value::as_array).map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
            let mut kept = Map::new();
            for k in KEPT_KEYS {
                if let Some(v) = o.get(k).filter(|v| crate::badges::truthy(v)) {
                    kept.insert(k.into(), v.clone());
                }
            }
            Some(Item::Folder(Folder { name: o.get("folder").and_then(Value::as_str).unwrap_or("").into(), apps, kept }))
        }
        _ => None,
    }
}

pub struct Model {
    /// desktop id -> display name (apps that should be shown).
    pub installed: BTreeMap<String, String>,
    pub pages: Vec<Vec<Item>>,
    pub hidden: Vec<String>,
    /// The page size the pages were filled for ((cols, rows); None = unknown).
    pub grid: Option<(usize, usize)>,
}

impl Model {
    pub fn new(data: &Value, installed: BTreeMap<String, String>) -> Self {
        // a hand-edited / older launchpad.json: anything that isn't a list is dropped
        let pages = data.get("pages").and_then(Value::as_array).map(|p| p.iter().filter_map(Value::as_array).map(|pg| pg.iter().filter_map(parse_item).collect()).collect()).unwrap_or_default();
        let hidden = data.get("hidden").and_then(Value::as_array).map(|h| h.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
        let grid = match data.get("grid").and_then(Value::as_array).map(|g| g.as_slice()) {
            Some([c, r]) => c.as_u64().zip(r.as_u64()).map(|(c, r)| (c as usize, r as usize)),
            _ => None,
        };
        let mut m = Model { installed, pages, hidden, grid };
        m.reconcile();
        m
    }

    // -- persistence -------------------------------------------------------------

    pub fn to_json(&self) -> Value {
        let grid = self.grid.map(|(c, r)| json!([c, r])).unwrap_or_else(|| json!([]));
        json!({"pages": self.pages.iter().map(|p| p.iter().map(Item::to_json).collect::<Vec<_>>()).collect::<Vec<_>>(), "hidden": self.hidden, "grid": grid})
    }

    /// Drop uninstalled apps, dissolve 1-app folders, append new apps
    /// (alphabetically) at the end, fix overflow.
    pub fn reconcile(&mut self) {
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut pages: Vec<Vec<Item>> = Vec::new();
        for page in &self.pages {
            let mut out = Vec::new();
            for item in page {
                match item {
                    Item::Folder(f) => {
                        let mut apps: Vec<String> = Vec::new();
                        for a in &f.apps {
                            // (twice in one folder: kept twice, as launchpad_model.py does)
                            if self.installed.contains_key(a) && !seen.contains(a) {
                                apps.push(a.clone());
                            }
                        }
                        seen.extend(apps.iter().cloned());
                        if apps.len() > 1 {
                            let name = if f.name.is_empty() { tr!("Untitled Folder") } else { f.name.clone() };
                            out.push(Item::Folder(Folder { name, apps, kept: f.kept.clone() })); // the Dock link, the lock
                        } else if let Some(a) = apps.pop() {
                            out.push(Item::App(a));
                        }
                    }
                    Item::App(a) => {
                        if self.installed.contains_key(a) && seen.insert(a.clone()) {
                            out.push(Item::App(a.clone()));
                        }
                    }
                }
            }
            pages.push(out);
        }
        let mut new: Vec<&String> = self.installed.keys().filter(|a| !seen.contains(*a) && !self.hidden.contains(a)).collect();
        new.sort_by_key(|a| self.installed[*a].to_lowercase());
        if pages.is_empty() {
            pages.push(Vec::new());
        }
        pages.last_mut().unwrap().extend(new.into_iter().map(|a| Item::App(a.clone())));
        self.pages = pages;
        self.normalize();
    }

    /// Pages refilled in order to the current size (after the page grew):
    /// no empty rows left at the bottom of a page.
    pub fn repack(&mut self) {
        let items: Vec<Item> = self.pages.drain(..).flatten().collect();
        let per = per_page();
        self.pages = items.chunks(per).map(|c| c.to_vec()).collect();
        if self.pages.is_empty() {
            self.pages.push(Vec::new());
        }
        self.grid = Some((cols(), rows()));
    }

    /// A page before the last one with empty places (apps moved off it).
    pub fn has_gaps(&self) -> bool {
        let n = self.pages.len();
        self.pages.iter().take(n.saturating_sub(1)).any(|p| p.len() < per_page())
    }

    /// The "•••" menu's Clean Up (Vini): every app moved up into the empty
    /// places, in the order they're in, page after page. True if anything moved.
    pub fn close_gaps(&mut self) -> bool {
        if !self.has_gaps() {
            return false;
        }
        self.repack();
        true
    }

    /// Cascade overflow to the next pages; drop empty pages (keep one).
    pub fn normalize(&mut self) {
        let per = per_page();
        let mut i = 0;
        while i < self.pages.len() {
            if self.pages[i].len() > per {
                let extra: Vec<Item> = self.pages[i].drain(per..).collect();
                if i + 1 == self.pages.len() {
                    self.pages.push(Vec::new());
                }
                self.pages[i + 1].splice(0..0, extra);
            }
            i += 1;
        }
        self.pages.retain(|p| !p.is_empty());
        if self.pages.is_empty() {
            self.pages.push(Vec::new());
        }
    }

    // -- lookups -----------------------------------------------------------------

    /// (page, index, the folder it's in or None) of an app.
    pub fn find(&self, app_id: &str) -> Option<(usize, usize, Option<&Folder>)> {
        for (p, page) in self.pages.iter().enumerate() {
            for (i, item) in page.iter().enumerate() {
                match item {
                    Item::App(a) if a == app_id => return Some((p, i, None)),
                    Item::Folder(f) if f.apps.iter().any(|a| a == app_id) => return Some((p, i, Some(f))),
                    _ => {}
                }
            }
        }
        None
    }

    pub fn all_apps(&self) -> Vec<String> {
        let mut out = Vec::new();
        for item in self.pages.iter().flatten() {
            match item {
                Item::App(a) => out.push(a.clone()),
                Item::Folder(f) => out.extend(f.apps.iter().cloned()),
            }
        }
        out
    }

    fn position(&self, item: &Item) -> Option<(usize, usize)> {
        self.pages.iter().enumerate().find_map(|(p, page)| page.iter().position(|it| it == item).map(|i| (p, i)))
    }

    fn insert_at(&mut self, page: usize, index: usize, item: Item) {
        while page >= self.pages.len() {
            self.pages.push(Vec::new());
        }
        let pg = &mut self.pages[page];
        pg.insert(index.min(pg.len()), item);
    }

    // -- edits -------------------------------------------------------------------

    /// Move a top-level item (an app or a folder) to page/index (Model.move).
    pub fn move_item(&mut self, item: &Item, page: usize, index: usize) {
        self.remove_top(item);
        self.insert_at(page, index, item.clone());
        self.normalize();
    }

    fn remove_top(&mut self, item: &Item) {
        if let Some((p, i)) = self.position(item) {
            self.pages[p].remove(i);
        }
    }

    /// Drop app `dragged` on `target` (an app or a folder): the folder
    /// they're in now, None when `target` isn't on any page.
    pub fn make_folder(&mut self, target: &Item, dragged: &str, name: &str) -> Option<Folder> {
        if let Item::Folder(f) = target {
            if f.apps.iter().any(|a| a == dragged) {
                self.normalize();
                return Some(f.clone());
            }
            self.remove_app_anywhere(dragged);
            let (p, i) = self.position(target)?;
            let Item::Folder(f) = &mut self.pages[p][i] else { return None };
            f.apps.push(dragged.to_string());
            let out = f.clone();
            self.normalize();
            return Some(out);
        }
        self.remove_app_anywhere(dragged);
        let (p, i) = self.position(target)?;
        let Item::App(t) = &self.pages[p][i] else { return None };
        let folder = Folder::new(name, vec![t.clone(), dragged.to_string()]);
        self.pages[p][i] = Item::Folder(folder.clone());
        self.normalize();
        Some(folder)
    }

    /// An app out of wherever it is; a folder left with one app becomes that app.
    pub fn remove_app_anywhere(&mut self, app_id: &str) {
        let Some((p, i, folder)) = self.find(app_id) else { return };
        if folder.is_none() {
            self.pages[p].remove(i);
            return;
        }
        if let Item::Folder(f) = &mut self.pages[p][i] {
            if let Some(j) = f.apps.iter().position(|a| a == app_id) {
                f.apps.remove(j);
            }
            if f.apps.len() == 1 {
                // a folder needs 2 apps
                self.pages[p][i] = Item::App(f.apps[0].clone());
            }
        }
    }

    /// An app dragged out of `folder` to page/index.
    pub fn take_out_of_folder(&mut self, folder: &Folder, app_id: &str, page: usize, index: usize) {
        if let Some((p, i)) = self.position(&Item::Folder(folder.clone())) {
            if let Item::Folder(f) = &mut self.pages[p][i] {
                if let Some(j) = f.apps.iter().position(|a| a == app_id) {
                    f.apps.remove(j);
                }
                if f.apps.len() == 1 {
                    self.pages[p][i] = Item::App(f.apps[0].clone());
                }
            }
        }
        self.insert_at(page, index, Item::App(app_id.to_string()));
        self.normalize();
    }

    /// Ungroup (the folder's menu): its apps take its place, in its order;
    /// what no longer fits the page flows on (normalize). False: no such folder.
    pub fn ungroup(&mut self, folder: &Folder) -> bool {
        let Some((p, i)) = self.position(&Item::Folder(folder.clone())) else { return false };
        let apps: Vec<Item> = folder.apps.iter().map(|a| Item::App(a.clone())).collect();
        self.pages[p].splice(i..i + 1, apps);
        self.normalize();
        true
    }

    /// A folder dropped in from the Dock: its apps (installed, not hidden)
    /// leave where they were and come together at page/index. One app alone
    /// is placed as itself. A folder with the same link (dragged back) is
    /// replaced: its other apps stay where it was. Returns what was placed
    /// (None: nothing).
    pub fn add_folder(&mut self, name: &str, app_ids: &[String], page: usize, index: usize, link: &str) -> Option<Item> {
        let mut index = index as i64;
        if !link.is_empty() {
            for (p, pg) in self.pages.iter_mut().enumerate() {
                let mut i = 0;
                while i < pg.len() {
                    let rest = match &pg[i] {
                        Item::Folder(f) if f.link() == link => f.apps.iter().filter(|a| !app_ids.contains(a)).map(|a| Item::App(a.clone())).collect::<Vec<_>>(),
                        _ => {
                            i += 1;
                            continue;
                        }
                    };
                    let n = rest.len();
                    pg.splice(i..i + 1, rest);
                    if p == page && (i as i64) < index {
                        index += n as i64 - 1;
                    }
                    i += n;
                }
            }
        }
        let mut keep: Vec<String> = Vec::new();
        for a in app_ids {
            if self.installed.contains_key(a) && !self.hidden.contains(a) && !keep.contains(a) {
                keep.push(a.clone());
            }
        }
        if keep.is_empty() {
            return None;
        }
        for a in &keep {
            self.remove_app_anywhere(a);
        }
        let item = if keep.len() > 1 {
            let mut f = Folder::new(name, keep);
            if f.name.is_empty() {
                f.name = tr!("Untitled Folder");
            }
            if !link.is_empty() {
                f.kept.insert("link".into(), json!(link));
            }
            Item::Folder(f)
        } else {
            Item::App(keep.remove(0))
        };
        self.insert_at(page, index.max(0) as usize, item.clone());
        self.normalize();
        Some(item)
    }

    pub fn hide(&mut self, app_id: &str) {
        self.remove_app_anywhere(app_id);
        if !self.hidden.iter().any(|h| h == app_id) {
            self.hidden.push(app_id.to_string());
        }
        self.normalize();
    }
}

// -- folders dragged between Launchpad and the Dock (two processes) -----------
// The drag carries the folder as text: a string value (Dock -> Launchpad) or
// one URI in a text/uri-list (Launchpad -> Dock, whose drop targets read uris).
pub const FOLDER_SCHEME: &str = "sonata2-folder:";

/// urllib.parse.quote(text, safe=""): everything but A-Z a-z 0-9 _.-~ as %XX.
fn quote(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"_.-~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// urllib.parse.unquote: %XX decoded as UTF-8 (bad bytes replaced).
fn unquote(s: &str) -> String {
    let b = s.as_bytes();
    let hex = |c: u8| (c as char).to_digit(16).map(|d| d as u8);
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex(b[i + 1]), hex(b[i + 2])) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub fn encode_folder(name: &str, app_ids: &[String], link: &str) -> String {
    let mut data = json!({"folder": name, "apps": app_ids});
    if !link.is_empty() {
        data["link"] = json!(link);
    }
    format!("{FOLDER_SCHEME}{}", quote(&data.to_string()))
}

/// The folder from encode_folder's text (its link, if any, in `kept`), else None.
pub fn decode_folder(text: &str) -> Option<Folder> {
    let rest = text.strip_prefix(FOLDER_SCHEME)?;
    let data: Value = serde_json::from_str(&unquote(rest)).ok()?;
    let name = data.get("folder")?.as_str()?;
    let apps = data.get("apps")?.as_array()?.iter().map(|a| a.as_str().map(String::from)).collect::<Option<Vec<_>>>()?;
    let mut f = Folder::new(name, apps);
    if let Some(link) = data.get("link").and_then(Value::as_str).filter(|l| !l.is_empty()) {
        f.kept.insert("link".into(), json!(link));
    }
    Some(f)
}

/// installed_meta: id -> (name, extra searchable text). Prefix matches on
/// the name first, then word prefixes, then substrings anywhere. limit
/// None (or 0): a page's worth.
pub fn search(installed_meta: &BTreeMap<String, (String, String)>, query: &str, limit: Option<usize>) -> Vec<String> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Vec::new();
    }
    let mut ranked: Vec<(u8, String, &String)> = Vec::new();
    for (app_id, (name, extra)) in installed_meta {
        let n = name.to_lowercase();
        let rank = if n.starts_with(&q) {
            0
        } else if n.split_whitespace().any(|w| w.starts_with(&q)) {
            1
        } else if n.contains(&q) {
            2
        } else if extra.to_lowercase().contains(&q) {
            3
        } else {
            continue;
        };
        ranked.push((rank, n, app_id));
    }
    ranked.sort();
    let limit = limit.filter(|l| *l > 0).unwrap_or_else(per_page);
    ranked.into_iter().take(limit).map(|(_, _, a)| a.clone()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn installed(n: usize) -> BTreeMap<String, String> {
        (0..n).map(|i| (format!("app{i:03}"), format!("App {i:03}"))).collect()
    }

    fn app(s: &str) -> Item {
        Item::App(s.into())
    }

    fn apps(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    fn lens(m: &Model) -> Vec<usize> {
        m.pages.iter().map(Vec::len).collect()
    }

    #[test]
    fn test_new_apps_fill_pages() {
        let m = Model::new(&json!({}), installed(80));
        assert_eq!(lens(&m), [35, 35, 10]);
        assert_eq!(m.pages[0][0], app("app000"));
    }

    #[test]
    fn test_reconcile_drops_missing_and_dissolves() {
        let data = json!({"pages": [["gone", {"folder": "F", "apps": ["app001", "gone2"]}, "app002"]]});
        let m = Model::new(&data, installed(3));
        assert_eq!(m.pages, [vec![app("app001"), app("app002"), app("app000")]]);
    }

    #[test]
    fn test_overflow_cascades() {
        let mut m = Model::new(&json!({}), installed(35));
        m.pages.push(vec![app("x")]);
        m.installed.insert("x".into(), "X".into());
        m.move_item(&app("x"), 0, 0);
        assert_eq!(m.pages[0][0], app("x"));
        assert_eq!(m.pages[0].len(), 35);
        assert_eq!(m.pages[1], [app("app034")]);
    }

    /// Vini: a folder in Apps had no Ungroup in its menu (the Dock's do).
    /// Its apps take its place, in order; nothing else moves.
    #[test]
    fn test_no_ungroup_in_apps_folders() {
        let mut m = Model::new(&json!({}), installed(5));
        let f = m.make_folder(&app("app001"), "app003", "Games").unwrap();
        let before: Vec<Item> = m.pages[0].clone();
        let at = before.iter().position(|it| *it == Item::Folder(f.clone())).unwrap();
        assert!(m.ungroup(&f));
        assert_eq!(m.pages[0][at], app("app001"));
        assert_eq!(m.pages[0][at + 1], app("app003"));
        assert!(!m.pages.iter().flatten().any(|it| matches!(it, Item::Folder(_))));
        let mut all = m.all_apps();
        all.sort();
        assert_eq!(all, installed(5).into_keys().collect::<Vec<_>>());
        assert!(!m.ungroup(&f)); // gone already
    }

    #[test]
    fn test_make_and_dissolve_folder() {
        let mut m = Model::new(&json!({}), installed(4));
        let f = m.make_folder(&app("app000"), "app003", "Games").unwrap();
        assert_eq!(m.pages[0][0].to_json(), json!({"folder": "Games", "apps": ["app000", "app003"]}));
        assert!(!m.pages[0][1..].contains(&app("app003")));
        let f = m.make_folder(&Item::Folder(f), "app001", "x").unwrap();
        assert_eq!(f.apps, apps(&["app000", "app003", "app001"]));
        m.take_out_of_folder(&f, "app001", 0, 5);
        let Item::Folder(f) = m.pages[0][0].clone() else { panic!() };
        m.take_out_of_folder(&f, "app003", 0, 5);
        assert_eq!(m.pages[0][0], app("app000")); // 1 app left -> plain app again
        let mut all = m.all_apps();
        all.sort();
        assert_eq!(all, installed(4).into_keys().collect::<Vec<_>>());
    }

    #[test]
    fn test_hide() {
        let mut m = Model::new(&json!({}), installed(3));
        m.hide("app001");
        assert!(!m.all_apps().contains(&"app001".to_string()));
        let m2 = Model::new(&m.to_json(), installed(3)); // stays hidden after reload
        assert!(!m2.all_apps().contains(&"app001".to_string()));
    }

    #[test]
    fn test_folder_name() {
        assert_eq!(folder_name(&["Game", "Network"], &["Game"]), sonata_i18n::gettext("Games"));
        assert_eq!(folder_name(&["Utility"], &["Office"]), sonata_i18n::gettext("Utilities"));
        assert_eq!(folder_name::<&str, &str>(&[], &[]), tr!("Untitled Folder"));
    }

    #[test]
    fn test_search_ranking() {
        let meta: BTreeMap<String, (String, String)> = [("a", "Firefox", "web browser"), ("b", "GNOME Web", "browser"), ("c", "Files", "file manager"), ("d", "Terminal", "")]
            .into_iter()
            .map(|(k, n, e)| (k.to_string(), (n.to_string(), e.to_string())))
            .collect();
        assert_eq!(search(&meta, "fi", None), ["c", "a"]);
        assert_eq!(search(&meta, "web", None), ["b", "a"]);
        assert_eq!(search(&meta, "browser", None), ["a", "b"]);
        assert!(search(&meta, " ", None).is_empty());
        assert_eq!(search(&meta, "fi", Some(1)), ["c"]);
    }

    #[test]
    fn test_round_trip() {
        let t = encode_folder("Games & \u{e9} \"x\"", &apps(&["a.desktop", "b"]), "");
        assert!(t.starts_with(FOLDER_SCHEME));
        assert!(!t.contains(' ')); // one URI line in a uri-list
        assert_eq!(decode_folder(&t), Some(Folder::new("Games & \u{e9} \"x\"", apps(&["a.desktop", "b"]))));
        // what the Python Launchpad sends: json.dumps, quoted
        let py = "sonata2-folder:%7B%22folder%22%3A%20%22W%22%2C%20%22apps%22%3A%20%5B%22a%22%5D%7D";
        assert_eq!(decode_folder(py), Some(Folder::new("W", apps(&["a"]))));
    }

    #[test]
    fn test_decode_rejects() {
        let bad_apps = encode_folder("x", &[], "").replace("apps", "nope");
        for bad in ["", "org.app.desktop", "file:///x", "sonata2-folder:nope", "sonata2-folder:%5B1%5D", bad_apps.as_str(), "sonata2-folder:%7B%22folder%22%3A1%2C%22apps%22%3A%5B%5D%7D", "sonata2-folder:%7B%22folder%22%3A%22x%22%2C%22apps%22%3A%5B1%5D%7D"] {
            assert_eq!(decode_folder(bad), None, "{bad}");
        }
    }

    #[test]
    fn test_add_folder_gathers_apps() {
        let mut m = Model::new(&json!({"pages": [["app000", {"folder": "F", "apps": ["app001", "app002", "app003"]}, "app004"]]}), installed(6));
        let item = m.add_folder("Work", &apps(&["app000", "app002", "gone", "app000"]), 0, 1, "").unwrap();
        assert_eq!(item.to_json(), json!({"folder": "Work", "apps": ["app000", "app002"]}));
        assert!(m.pages[0].contains(&item));
        let mut all = m.all_apps();
        all.sort();
        assert_eq!(all, (0..6).map(|i| format!("app{i:03}")).collect::<Vec<_>>()); // nothing lost or doubled
        assert_eq!(m.find("app001").unwrap().2.unwrap().apps, apps(&["app001", "app003"]));
    }

    #[test]
    fn test_add_folder_single_and_none() {
        let mut m = Model::new(&json!({}), installed(3));
        assert_eq!(m.add_folder("X", &apps(&["app001", "gone"]), 0, 0, ""), Some(app("app001")));
        assert_eq!(m.pages[0][0], app("app001"));
        assert_eq!(m.add_folder("X", &apps(&["gone"]), 0, 0, ""), None);
        m.hidden.push("app002".into());
        assert_eq!(m.add_folder("X", &apps(&["app002", "app000"]), 0, 0, ""), Some(app("app000"))); // hidden stays hidden
    }

    #[test]
    fn test_link_kept_through_reconcile() {
        let installed: BTreeMap<String, String> = [("a", "A"), ("b", "B")].into_iter().map(|(k, v)| (k.into(), v.into())).collect();
        let m = Model::new(&json!({"pages": [[{"folder": "W", "apps": ["a", "b"], "link": "L1", "secure": false}]]}), installed);
        let Item::Folder(f) = &m.pages[0][0] else { panic!() };
        assert_eq!(f.link(), "L1");
        assert_eq!(m.to_json()["pages"][0][0], json!({"folder": "W", "apps": ["a", "b"], "link": "L1"}));
    }

    #[test]
    fn test_drag_carries_the_link() {
        let f = decode_folder(&encode_folder("W", &apps(&["a", "b"]), "L1")).unwrap();
        assert_eq!(f.to_json(), json!({"folder": "W", "apps": ["a", "b"], "link": "L1"}));
        assert!(decode_folder(&encode_folder("W", &apps(&["a", "b"]), "")).unwrap().kept.get("link").is_none());
    }

    #[test]
    fn test_dragged_back_replaces_its_copy() {
        let inst: BTreeMap<String, String> = "abcx".chars().map(|k| (k.to_string(), k.to_uppercase().to_string())).collect();
        let mut m = Model::new(&json!({"pages": [["x", {"folder": "W", "apps": ["a", "b", "c"], "link": "L1"}]]}), inst);
        m.add_folder("W2", &apps(&["a", "b"]), 0, 0, "L1");
        let page = &m.pages[0];
        let folders: Vec<&Item> = page.iter().filter(|it| it.is_folder()).collect();
        assert_eq!(folders.len(), 1);
        assert_eq!(folders[0].to_json(), json!({"folder": "W2", "apps": ["a", "b"], "link": "L1"}));
        assert!(page.contains(&app("c"))); // its other app stays
    }

    /// Vini: the fifth row of the Apps page stayed empty -- the pages were
    /// filled for a 7x4 grid, and the 7x5 grid (the default) changed nothing.
    #[test]
    fn test_pages_remember_their_grid() {
        set_grid(7, 5);
        let names: BTreeMap<String, String> = (0..52).map(|i| (format!("a{i:02}"), format!("A{i:02}"))).collect();
        let ids: Vec<String> = names.keys().cloned().collect();
        let mut m = Model::new(&json!({"pages": [ids[..28], ids[28..]]}), names.clone());
        assert_eq!(m.grid, None); // an old file: size unknown
        m.repack();
        assert_eq!(lens(&m), [35, 17]);
        assert_eq!(m.to_json()["grid"], json!([7, 5]));
        let again = Model::new(&m.to_json(), names);
        assert_eq!(again.grid, Some((7, 5))); // kept: no repack next time
        assert!(defaults().get("grid").is_some()); // sonata_config::load keeps it
    }

    /// Vini: moving apps between pages leaves empty places; the "•••"
    /// menu's Clean Up moves the apps up into them, in their order.
    #[test]
    fn test_gaps_closed_in_order() {
        set_grid(7, 5);
        let names = installed(60);
        let ids: Vec<String> = names.keys().cloned().collect();
        let folder = json!({"folder": "Games", "apps": [ids[58], ids[59]]});
        let mut p1: Vec<Value> = ids[20..50].iter().map(|s| json!(s)).collect();
        p1.push(folder.clone());
        let mut m = Model::new(&json!({"pages": [ids[..20], p1, ids[50..58]], "grid": [7, 5]}), names);
        assert!(m.has_gaps());
        assert!(m.close_gaps());
        assert_eq!(lens(&m), [35, 24]);
        let flat: Vec<Value> = m.pages.iter().flatten().map(Item::to_json).collect();
        assert_eq!(flat[..50], ids[..50].iter().map(|s| json!(s)).collect::<Vec<_>>()[..]); // the same order
        assert_eq!(flat[50], folder); // folders move as they are
        assert!(!m.has_gaps());
        assert!(!m.close_gaps()); // nothing to do: nothing changes
    }

    #[test]
    fn test_a_short_last_page_is_no_gap() {
        let m = Model::new(&json!({}), installed(40));
        assert_eq!(lens(&m), [35, 5]);
        assert!(!m.has_gaps());
    }

    #[test]
    fn test_set_grid_clamps() {
        assert!(!set_grid(7, 5));
        assert_eq!((cols(), rows()), (7, 5));
    }

    #[test]
    fn test_quote() {
        assert_eq!(quote("a b/é"), "a%20b%2F%C3%A9");
        assert_eq!(unquote("a%20b%2F%C3%A9%"), "a b/é%");
    }
}
