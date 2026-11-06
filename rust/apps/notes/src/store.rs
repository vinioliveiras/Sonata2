//! Notes and Reminders data (sonata2/notes/store.py): notes.json and
//! reminders.json, each rewritten atomically after every change.
//!
//! ```text
//! notes.json      {"folders": [{id, name}],
//!                  "notes": [{id, folder, body, created, modified, pinned, deleted}]}
//! reminders.json  {"lists": [{id, name, color}],
//!                  "reminders": [{id, list, title, notes, due, priority, flagged,
//!                                 completed, completed_at, created}]}
//! ```
//!
//! Times are epoch seconds; a reminder's `due` is "YYYY-MM-DD" (all day) or
//! "YYYY-MM-DDTHH:MM" (local time). A deleted note keeps its folder and gets
//! `deleted` (the time): it shows in Recently Deleted for 30 days, then goes.
//!
//! Records are kept as JSON objects, so keys this version doesn't know stay.
//!
//! On trial the Rust Notes keeps its own copy (`sonata2-rs-data/notes`),
//! filled from the Python Notes' data the first time: two Notes writing
//! the same files at once would undo each other's edits.

use crate::markup;
use serde_json::{json, Map, Value};
use sonata_i18n::{gettext, n_};
use std::path::{Path, PathBuf};

pub const ALL: &str = "all";
pub const DELETED: &str = "deleted";
pub const DEFAULT_FOLDER: &str = "notes";
pub const DEFAULT_LIST: &str = "reminders";
pub const SMART: [&str; 4] = ["today", "scheduled", "all", "completed"];
pub const KEEP_DELETED: f64 = 30.0 * 86400.0;
pub const LIST_COLORS: [&str; 9] = ["blue", "red", "orange", "green", "purple", "pink", "teal", "indigo", "graphite"];
/// all-day reminders alert at 9:00 (macOS)
pub const ALL_DAY_HOUR: u32 = 9;

pub type Rec = Map<String, Value>;

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// The folder: ~/.local/share/sonata2-data/notes.
pub fn data_dir() -> PathBuf {
    sonata_config::data_dir("notes")
}

pub fn write_json(path: &Path, data: &Value) -> std::io::Result<()> {
    sonata_config::atomic_write(path, sonata_config::to_python_json_utf8(data, 1).as_bytes(), true)
}

fn read(path: &Path) -> Rec {
    let Ok(bytes) = std::fs::read(path) else { return Rec::new() };
    if let Ok(Value::Object(m)) = serde_json::from_slice::<Value>(&bytes) {
        return m;
    }
    // damaged or wrongly shaped: keep it aside so the next save can't destroy it
    let aside = format!("{}.corrupt-{}", path.display(), now() as u64);
    let _ = std::fs::rename(path, aside);
    Rec::new()
}

pub fn s<'a>(r: &'a Rec, k: &str) -> &'a str {
    r.get(k).and_then(Value::as_str).unwrap_or("")
}

pub fn f(r: &Rec, k: &str) -> f64 {
    r.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

/// Python's truthiness of a key.
pub fn b(r: &Rec, k: &str) -> bool {
    match r.get(k) {
        Some(Value::Bool(x)) => *x,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(x)) => !x.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        _ => false,
    }
}

/// A folder's or list's name as shown: "Notes" and "Reminders" (the
/// built-in ones, never renamed) in the session's language.
pub fn display_name(x: Option<&Rec>) -> String {
    let Some(x) = x else { return String::new() };
    let name = s(x, "name");
    let builtin = match s(x, "id") {
        DEFAULT_FOLDER => Some(n_!("Notes")),
        DEFAULT_LIST => Some(n_!("Reminders")),
        _ => None,
    };
    if builtin == Some(name) { gettext(name) } else { name.to_string() }
}

pub fn new_id() -> String {
    let mut bytes = [0u8; 6];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = f.read_exact(&mut bytes);
    }
    bytes.iter().map(|x| format!("{x:02x}")).collect()
}

// -- dates (no time zone: local wall time, as Python's naive datetime) -----------------------------
/// (year, month, day, hour, minute): orders as time does.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Moment(pub i32, pub u32, pub u32, pub u32, pub u32);

pub fn days_in_month(y: i32, m: u32) -> u32 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        2 => 28,
        _ => 0,
    }
}

impl Moment {
    pub fn date(&self) -> (i32, u32, u32) {
        (self.0, self.1, self.2)
    }

    /// Days since 1970-01-01 of a date (civil calendar).
    pub fn day_number((y, m, d): (i32, u32, u32)) -> i64 {
        let (y, m) = (if m <= 2 { y - 1 } else { y } as i64, m as i64);
        let era = y.div_euclid(400);
        let yoe = y - era * 400;
        let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d as i64 - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146097 + doe - 719468
    }
}

fn parse_due(due: &str) -> Option<(Moment, bool)> {
    let num = |s: &str, len: usize| (s.len() == len && s.bytes().all(|b| b.is_ascii_digit())).then(|| s.parse::<u32>().ok()).flatten();
    let (date, time) = match due.split_once('T') {
        Some((d, t)) => (d, Some(t)),
        None => (due, None),
    };
    let mut p = date.split('-');
    let (y, m, d) = (num(p.next()?, 4)? as i32, num(p.next()?, 2)?, num(p.next()?, 2)?);
    if p.next().is_some() || !(1..=12).contains(&m) || d == 0 || d > days_in_month(y, m) {
        return None;
    }
    match time {
        None => Some((Moment(y, m, d, ALL_DAY_HOUR, 0), false)),
        Some(t) => {
            let (h, mi) = t.split_once(':')?;
            let (h, mi) = (num(h, 2)?, num(mi, 2)?);
            (h < 24 && mi < 60).then_some((Moment(y, m, d, h, mi), true))
        }
    }
}

/// The moment a reminder is due (all-day: 9:00), or None.
pub fn due_datetime(r: &Rec) -> Option<Moment> {
    parse_due(s(r, "due")).map(|x| x.0)
}

pub fn due_date(r: &Rec) -> Option<(i32, u32, u32)> {
    due_datetime(r).map(|m| m.date())
}

pub fn has_time(r: &Rec) -> bool {
    s(r, "due").contains('T')
}

pub fn make_due(date: Option<(i32, u32, u32)>, time: Option<(u32, u32)>) -> Option<String> {
    let (y, m, d) = date?;
    Some(match time {
        None => format!("{y:04}-{m:02}-{d:02}"),
        Some((h, mi)) => format!("{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}"),
    })
}

fn by_due(r: &Rec) -> (bool, Moment, i64) {
    let dt = due_datetime(r);
    (dt.is_none(), dt.unwrap_or(Moment(i32::MAX, 12, 31, 23, 59)), (f(r, "created") * 1e6) as i64)
}

/// today: due today or overdue; scheduled: every dated one; all: every open
/// one; completed: the done ones (latest first).
pub fn smart_list(reminders: &[Rec], kind: &str, today: (i32, u32, u32)) -> Vec<Rec> {
    let open: Vec<Rec> = reminders.iter().filter(|r| !b(r, "completed")).cloned().collect();
    let mut out: Vec<Rec> = match kind {
        "today" => open.into_iter().filter(|r| due_date(r).is_some_and(|d| d <= today)).collect(),
        "scheduled" => open.into_iter().filter(|r| due_date(r).is_some()).collect(),
        "all" => open,
        "completed" => reminders.iter().filter(|r| b(r, "completed")).cloned().collect(),
        _ => Vec::new(),
    };
    match kind {
        "today" | "scheduled" => out.sort_by_key(by_due),
        "all" => out.sort_by(|a, b| f(a, "created").total_cmp(&f(b, "created"))),
        _ => out.sort_by(|a, b| f(b, "completed_at").total_cmp(&f(a, "completed_at"))),
    }
    out
}

pub fn search_reminders(reminders: &[Rec], query: &str) -> Vec<Rec> {
    let words: Vec<String> = query.to_lowercase().split_whitespace().map(String::from).collect();
    reminders
        .iter()
        .filter(|r| {
            let text = format!("{}\n{}", s(r, "title"), s(r, "notes")).to_lowercase();
            words.iter().all(|w| text.contains(w))
        })
        .cloned()
        .collect()
}

/// Pinned first, then by date edited (newest first).
pub fn sort_notes(notes: &mut [Rec]) {
    notes.sort_by(|a, x| (!b(a, "pinned")).cmp(&!b(x, "pinned")).then(f(x, "modified").total_cmp(&f(a, "modified"))));
}

/// Notes whose text contains every word of `query` (case-insensitive).
pub fn search(notes: &[Rec], query: &str) -> Vec<Rec> {
    let words: Vec<String> = query.to_lowercase().split_whitespace().map(String::from).collect();
    if words.is_empty() {
        return notes.to_vec();
    }
    notes
        .iter()
        .filter(|n| {
            let text = markup::plain(s(n, "body")).join("\n").to_lowercase();
            words.iter().all(|w| text.contains(w))
        })
        .cloned()
        .collect()
}

pub struct Store {
    pub dir: PathBuf,
    pub notes_path: PathBuf,
    pub reminders_path: PathBuf,
    pub folders: Vec<Rec>,
    pub notes: Vec<Rec>,
    pub lists: Vec<Rec>,
    pub reminders: Vec<Rec>,
    /// notifier.py's ReminderFile: reminders.json only, never written (the
    /// menu bar's reminder alerts: Notes is the only writer -- no purge, no
    /// save, a damaged file is not set aside).
    pub read_only: bool,
}

fn objects(v: Option<&Value>) -> Vec<Rec> {
    v.and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_object()).filter(|x| b(x, "id")).cloned().collect())
        .unwrap_or_default()
}

fn obj(v: Value) -> Rec {
    match v {
        Value::Object(m) => m,
        _ => Rec::new(),
    }
}

impl Store {
    pub fn open(dir: PathBuf) -> Self {
        let mut st = Store {
            notes_path: dir.join("notes.json"),
            reminders_path: dir.join("reminders.json"),
            dir,
            folders: Vec::new(),
            notes: Vec::new(),
            lists: Vec::new(),
            reminders: Vec::new(),
            read_only: false,
        };
        st.load();
        st
    }

    /// reminders.json read-only (notifier.py's ReminderFile).
    pub fn open_read_only(dir: PathBuf) -> Self {
        let mut st = Store {
            notes_path: dir.join("notes.json"),
            reminders_path: dir.join("reminders.json"),
            dir,
            folders: Vec::new(),
            notes: Vec::new(),
            lists: Vec::new(),
            reminders: Vec::new(),
            read_only: true,
        };
        st.load();
        st
    }

    pub fn new() -> Self {
        Self::open(data_dir())
    }

    pub fn load(&mut self) {
        if self.read_only {
            // mid-replace or damaged: keep what we had
            let data = std::fs::read(&self.reminders_path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok());
            if let Some(Value::Object(r)) = data {
                self.lists = objects(r.get("lists"));
                self.reminders = objects(r.get("reminders"));
            }
            return;
        }
        let n = read(&self.notes_path);
        self.folders = objects(n.get("folders"));
        if !self.folders.iter().any(|x| s(x, "id") == DEFAULT_FOLDER) {
            self.folders.insert(0, obj(json!({"id": DEFAULT_FOLDER, "name": "Notes"})));
        }
        self.notes = objects(n.get("notes"));
        let r = read(&self.reminders_path);
        self.lists = objects(r.get("lists"));
        if !self.lists.iter().any(|x| s(x, "id") == DEFAULT_LIST) {
            self.lists.insert(0, obj(json!({"id": DEFAULT_LIST, "name": "Reminders", "color": "blue"})));
        }
        self.reminders = objects(r.get("reminders"));
        if self.purge(now()) {
            self.save_notes();
        }
    }

    pub fn save_notes(&self) {
        if self.read_only {
            return;
        }
        let data = json!({"version": 1, "folders": self.folders, "notes": self.notes});
        if let Err(e) = write_json(&self.notes_path, &data) {
            eprintln!("sonata2-rs notes: notes.json: {e}");
        }
    }

    pub fn save_reminders(&self) {
        if self.read_only {
            return;
        }
        let data = json!({"version": 1, "lists": self.lists, "reminders": self.reminders});
        if let Err(e) = write_json(&self.reminders_path, &data) {
            eprintln!("sonata2-rs notes: reminders.json: {e}");
        }
    }

    // -- notes ----------------------------------------------------------------------------------
    pub fn note_index(&self, id: &str) -> Option<usize> {
        self.notes.iter().position(|n| s(n, "id") == id)
    }

    pub fn note(&self, id: &str) -> Option<&Rec> {
        self.notes.iter().find(|n| s(n, "id") == id)
    }

    fn note_mut(&mut self, id: &str) -> Option<&mut Rec> {
        self.notes.iter_mut().find(|n| s(n, "id") == id)
    }

    pub fn notes_in(&self, folder: &str) -> Vec<Rec> {
        let mut out: Vec<Rec> = if folder == DELETED {
            self.notes.iter().filter(|n| b(n, "deleted")).cloned().collect()
        } else {
            self.notes.iter().filter(|n| !b(n, "deleted") && (folder == ALL || s(n, "folder") == folder)).cloned().collect()
        };
        sort_notes(&mut out);
        out
    }

    pub fn new_note(&mut self, folder: &str, body: &str, at: Option<f64>) -> String {
        let t = at.unwrap_or_else(now);
        let folder = if folder == ALL || folder == DELETED || self.folder(folder).is_none() { DEFAULT_FOLDER } else { folder };
        let id = new_id();
        self.notes.push(obj(json!({"id": id, "folder": folder, "body": body, "created": t, "modified": t, "pinned": false, "deleted": null})));
        self.save_notes();
        id
    }

    /// The note's new text; false when it didn't change.
    pub fn update_note(&mut self, id: &str, body: &str, at: Option<f64>) -> bool {
        let Some(n) = self.note_mut(id) else { return false };
        if s(n, "body") == body {
            return false;
        }
        n.insert("body".into(), json!(body));
        n.insert("modified".into(), json!(at.unwrap_or_else(now)));
        self.save_notes();
        true
    }

    pub fn set_pinned(&mut self, id: &str, pinned: bool) {
        if let Some(n) = self.note_mut(id) {
            n.insert("pinned".into(), json!(pinned));
            self.save_notes();
        }
    }

    pub fn move_note(&mut self, id: &str, folder: &str) {
        if let Some(n) = self.note_mut(id) {
            n.insert("folder".into(), json!(folder));
            self.save_notes();
        }
    }

    /// To Recently Deleted (or for good when it's already there).
    pub fn delete_note(&mut self, id: &str, at: Option<f64>) {
        let Some(n) = self.note_mut(id) else { return };
        if b(n, "deleted") {
            self.destroy_note(id);
            return;
        }
        n.insert("deleted".into(), json!(at.unwrap_or_else(now)));
        n.insert("pinned".into(), json!(false));
        self.save_notes();
    }

    pub fn recover_note(&mut self, id: &str) {
        let folder_ok = self.note(id).map(|n| s(n, "folder").to_string()).is_some_and(|fid| self.folder(&fid).is_some());
        let Some(n) = self.note_mut(id) else { return };
        n.insert("deleted".into(), Value::Null);
        if !folder_ok {
            n.insert("folder".into(), json!(DEFAULT_FOLDER));
        }
        self.save_notes();
    }

    pub fn destroy_note(&mut self, id: &str) {
        self.notes.retain(|n| s(n, "id") != id);
        self.save_notes();
    }

    /// Forget notes deleted more than 30 days ago.
    pub fn purge(&mut self, at: f64) -> bool {
        let before = self.notes.len();
        self.notes.retain(|n| !b(n, "deleted") || at - f(n, "deleted") < KEEP_DELETED);
        before != self.notes.len()
    }

    // -- folders ----------------------------------------------------------------------------------
    pub fn folder(&self, id: &str) -> Option<&Rec> {
        self.folders.iter().find(|x| s(x, "id") == id)
    }

    pub fn unique_name(base: &str, names: &[String]) -> String {
        let taken: Vec<String> = names.iter().map(|n| n.to_lowercase()).collect();
        if !taken.contains(&base.to_lowercase()) {
            return base.to_string();
        }
        let mut i = 2;
        while taken.contains(&format!("{base} {i}").to_lowercase()) {
            i += 1;
        }
        format!("{base} {i}")
    }

    pub fn add_folder(&mut self, name: &str) -> String {
        let names: Vec<String> = self.folders.iter().map(|x| s(x, "name").to_string()).collect();
        let id = new_id();
        self.folders.push(obj(json!({"id": id, "name": Self::unique_name(name, &names)})));
        self.save_notes();
        id
    }

    /// The name it gets (made unique), or None when nothing changed.
    pub fn rename_folder(&mut self, id: &str, name: &str) -> Option<String> {
        let name = name.trim();
        let names: Vec<String> = self.folders.iter().filter(|x| s(x, "id") != id).map(|x| s(x, "name").to_string()).collect();
        let x = self.folders.iter_mut().find(|x| s(x, "id") == id)?;
        if name.is_empty() || name == s(x, "name") {
            return None;
        }
        let unique = Self::unique_name(name, &names);
        x.insert("name".into(), json!(unique));
        self.save_notes();
        Some(unique)
    }

    /// Its notes go to Recently Deleted (in Notes when recovered).
    pub fn delete_folder(&mut self, id: &str, at: Option<f64>) {
        if id == DEFAULT_FOLDER {
            return;
        }
        let t = at.unwrap_or_else(now);
        for n in self.notes.iter_mut().filter(|n| s(n, "folder") == id) {
            n.insert("folder".into(), json!(DEFAULT_FOLDER));
            if !b(n, "deleted") {
                n.insert("deleted".into(), json!(t));
                n.insert("pinned".into(), json!(false));
            }
        }
        self.folders.retain(|x| s(x, "id") != id);
        self.save_notes();
    }

    pub fn folder_count(&self, id: &str) -> usize {
        self.notes_in(id).len()
    }

    // -- reminders ----------------------------------------------------------------------------------
    pub fn rlist(&self, id: &str) -> Option<&Rec> {
        self.lists.iter().find(|x| s(x, "id") == id)
    }

    pub fn reminder(&self, id: &str) -> Option<&Rec> {
        self.reminders.iter().find(|r| s(r, "id") == id)
    }

    /// A smart list (today/scheduled/all/completed) or a user list's open ones.
    pub fn reminders_in(&self, key: &str, today: (i32, u32, u32)) -> Vec<Rec> {
        if SMART.contains(&key) {
            return smart_list(&self.reminders, key, today);
        }
        let mut out: Vec<Rec> = self.reminders.iter().filter(|r| s(r, "list") == key && !b(r, "completed")).cloned().collect();
        out.sort_by(|a, x| f(a, "created").total_cmp(&f(x, "created")));
        out
    }

    pub fn new_reminder(&mut self, list: &str, title: &str, due: Option<String>, at: Option<f64>) -> String {
        let list = if self.rlist(list).is_some() { list } else { DEFAULT_LIST };
        let id = new_id();
        self.reminders.push(obj(json!({"id": id, "list": list, "title": title, "notes": "", "due": due, "priority": 0,
            "flagged": false, "completed": false, "completed_at": null, "created": at.unwrap_or_else(now)})));
        self.save_reminders();
        id
    }

    /// Change some keys; re-completing a done reminder keeps its original
    /// completion time. False when nothing changed.
    pub fn update_reminder(&mut self, id: &str, changes: Rec) -> bool {
        let Some(r) = self.reminders.iter_mut().find(|r| s(r, "id") == id) else { return false };
        let mut changes = changes;
        if let Some(c) = changes.get("completed").cloned() {
            let done = c.as_bool().unwrap_or(false);
            let at = if !done {
                Value::Null
            } else if b(r, "completed") {
                r.get("completed_at").cloned().unwrap_or(Value::Null)
            } else {
                json!(now())
            };
            changes.insert("completed_at".into(), at);
        }
        if changes.iter().all(|(k, v)| r.get(k).unwrap_or(&Value::Null) == v) {
            return false;
        }
        for (k, v) in changes {
            r.insert(k, v);
        }
        self.save_reminders();
        true
    }

    pub fn delete_reminder(&mut self, id: &str) {
        self.reminders.retain(|r| s(r, "id") != id);
        self.save_reminders();
    }

    pub fn add_list(&mut self, name: &str) -> String {
        let used: Vec<&str> = self.lists.iter().map(|x| s(x, "color")).collect();
        let color = *LIST_COLORS.iter().min_by_key(|c| (used.iter().filter(|u| **u == **c).count(), LIST_COLORS.iter().position(|x| x == *c))).unwrap_or(&"blue");
        let names: Vec<String> = self.lists.iter().map(|x| s(x, "name").to_string()).collect();
        let id = new_id();
        self.lists.push(obj(json!({"id": id, "name": Self::unique_name(name, &names), "color": color})));
        self.save_reminders();
        id
    }

    pub fn rename_list(&mut self, id: &str, name: &str) -> Option<String> {
        let name = name.trim();
        let names: Vec<String> = self.lists.iter().filter(|x| s(x, "id") != id).map(|x| s(x, "name").to_string()).collect();
        let x = self.lists.iter_mut().find(|x| s(x, "id") == id)?;
        if name.is_empty() || name == s(x, "name") {
            return None;
        }
        let unique = Self::unique_name(name, &names);
        x.insert("name".into(), json!(unique));
        self.save_reminders();
        Some(unique)
    }

    /// The list and its reminders (the default list stays).
    pub fn delete_list(&mut self, id: &str) {
        if id == DEFAULT_LIST {
            return;
        }
        self.reminders.retain(|r| s(r, "list") != id);
        self.lists.retain(|x| s(x, "id") != id);
        self.save_reminders();
    }

    /// Open reminders falling due in (start, end].
    pub fn due_between(&self, start: Moment, end: Moment) -> Vec<Rec> {
        self.reminders.iter().filter(|r| !b(r, "completed") && due_datetime(r).is_some_and(|d| start < d && d <= end)).cloned().collect()
    }

    pub fn next_due_after(&self, t: Moment) -> Option<Moment> {
        self.reminders.iter().filter(|r| !b(r, "completed")).filter_map(due_datetime).filter(|d| *d > t).min()
    }
}

impl Default for Store {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> (tempfile::TempDir, Store) {
        let dir = tempfile::tempdir().unwrap();
        let st = Store::open(dir.path().to_path_buf());
        (dir, st)
    }

    fn ch(v: Value) -> Rec {
        obj(v)
    }

    #[test]
    fn round_trip() {
        let (dir, mut st) = store();
        let fid = st.add_folder("Work");
        let nid = st.new_note(&fid, "# Plan\nship it", None);
        st.set_pinned(&nid, true);
        let lid = st.add_list("Groceries");
        let rid = st.new_reminder(&lid, "Milk", Some("2026-10-01T09:30".into()), None);
        st.update_reminder(&rid, ch(json!({"flagged": true, "priority": 2})));
        let st2 = Store::open(dir.path().to_path_buf());
        assert_eq!(st2.folders, st.folders);
        assert_eq!(st2.notes, st.notes);
        assert_eq!(st2.lists, st.lists);
        assert_eq!(st2.reminders, st.reminders);
        assert!(!std::fs::read_dir(dir.path()).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().ends_with(".tmp")));
        assert_eq!(due_datetime(&st2.reminders[0]), Some(Moment(2026, 10, 1, 9, 30)));
    }

    #[test]
    fn deleted_notes() {
        let (dir, mut st) = store();
        let t = now();
        let a = st.new_note(DEFAULT_FOLDER, "old", Some(t));
        let bb = st.new_note(DEFAULT_FOLDER, "fresh", Some(t));
        st.delete_note(&a, Some(t - 31.0 * 86400.0));
        st.delete_note(&bb, Some(t));
        assert_eq!(st.notes_in(DELETED).len(), 2);
        assert!(st.notes_in(ALL).is_empty());
        let mut st2 = Store::open(dir.path().to_path_buf()); // older than 30 days: gone
        assert_eq!(st2.notes_in(DELETED).iter().map(|n| s(n, "id").to_string()).collect::<Vec<_>>(), [bb.clone()]);
        st2.recover_note(&bb);
        assert_eq!(st2.notes_in(DEFAULT_FOLDER).len(), 1);
    }

    #[test]
    fn folders() {
        let (_d, mut st) = store();
        let a = st.add_folder("New Folder");
        let g = st.add_folder("New Folder");
        assert_eq!((s(st.folder(&a).unwrap(), "name"), s(st.folder(&g).unwrap(), "name")), ("New Folder", "New Folder 2"));
        st.new_note(&a, "x", None);
        st.delete_folder(&a, None);
        assert!(st.folder(&a).is_none());
        assert_eq!(st.notes_in(DELETED).len(), 1);
    }

    #[test]
    fn sort_and_search() {
        let (_d, mut st) = store();
        let a = st.new_note(DEFAULT_FOLDER, "# Alpha\nred apples", Some(100.0));
        let bb = st.new_note(DEFAULT_FOLDER, "# Beta\ngreen **pears**", Some(200.0));
        let c = st.new_note(DEFAULT_FOLDER, "# Gamma\nred pears", Some(50.0));
        st.set_pinned(&c, true);
        let ids = |v: Vec<Rec>| v.iter().map(|n| s(n, "id").to_string()).collect::<Vec<_>>();
        assert_eq!(ids(st.notes_in(ALL)), [c.clone(), bb, a.clone()]);
        let mut red = ids(search(&st.notes_in(ALL), "RED"));
        red.sort();
        let mut want = vec![a, c.clone()];
        want.sort();
        assert_eq!(red, want);
        assert_eq!(ids(search(&st.notes_in(ALL), "red pears")), [c]);
        assert!(search(&st.notes_in(ALL), "**").is_empty()); // markers aren't text
        assert_eq!(search(&st.notes_in(ALL), "").len(), 3);
    }

    #[test]
    fn smart_lists() {
        let (_d, mut st) = store();
        let today = (2026, 9, 30);
        let over = st.new_reminder(DEFAULT_LIST, "overdue", Some("2026-09-28".into()), Some(1.0));
        let now_ = st.new_reminder(DEFAULT_LIST, "today", Some("2026-09-30T15:00".into()), Some(2.0));
        let later = st.new_reminder(DEFAULT_LIST, "later", Some("2026-10-05".into()), Some(3.0));
        let none = st.new_reminder(DEFAULT_LIST, "undated", None, Some(4.0));
        let done = st.new_reminder(DEFAULT_LIST, "done", Some("2026-09-30".into()), Some(5.0));
        st.update_reminder(&done, ch(json!({"completed": true})));
        let ids = |v: Vec<Rec>| v.iter().map(|n| s(n, "id").to_string()).collect::<Vec<_>>();
        assert_eq!(ids(st.reminders_in("today", today)), [over.clone(), now_.clone()]);
        assert_eq!(ids(st.reminders_in("scheduled", today)), [over.clone(), now_.clone(), later.clone()]);
        assert_eq!(ids(st.reminders_in("all", today)), [over, now_.clone(), later.clone(), none]);
        assert_eq!(ids(st.reminders_in("completed", today)), [done]);
        assert_eq!(st.reminders_in(DEFAULT_LIST, today).len(), 4);
        assert_eq!(ids(search_reminders(&st.reminders, "LATE")), [later]);
        assert_eq!(ids(st.due_between(Moment(2026, 9, 30, 14, 0), Moment(2026, 9, 30, 16, 0))), [now_]);
    }

    #[test]
    fn make_due_round_trips_and_bad_dues_are_no_date() {
        assert_eq!(make_due(Some((2026, 3, 7)), None).as_deref(), Some("2026-03-07"));
        assert_eq!(make_due(Some((2026, 3, 7)), Some((8, 5))).as_deref(), Some("2026-03-07T08:05"));
        assert_eq!(make_due(None, Some((9, 0))), None);
        let r = |d: &str| ch(json!({"due": d}));
        assert_eq!(due_datetime(&r("2026-03-07")), Some(Moment(2026, 3, 7, ALL_DAY_HOUR, 0)));
        assert!(has_time(&r("2026-03-07T00:00"))); // midnight is still a time
        for bad in ["", "tomorrow", "2026-13-01", "2026-02-30", "2026-10-10T25:00", "2026-10-10 14:00"] {
            assert_eq!(due_datetime(&r(bad)), None, "{bad}");
        }
        assert_eq!(due_datetime(&Rec::new()), None);
    }

    #[test]
    fn alerts_and_completion() {
        let (_d, mut st) = store();
        let a = st.new_reminder(DEFAULT_LIST, "a", Some("2026-10-10T12:00".into()), None);
        let bb = st.new_reminder(DEFAULT_LIST, "b", Some("2026-10-10T12:05".into()), None);
        let noon = Moment(2026, 10, 10, 12, 0);
        let first = st.due_between(Moment(2026, 10, 10, 11, 55), noon);
        let second = st.due_between(noon, Moment(2026, 10, 10, 12, 5));
        assert_eq!(s(&first[0], "id"), a);
        assert_eq!(s(&second[0], "id"), bb);
        let x = st.new_reminder(DEFAULT_LIST, "x", None, None);
        st.update_reminder(&x, ch(json!({"completed": true})));
        let first_at = st.reminder(&x).unwrap()["completed_at"].clone();
        assert!(!first_at.is_null());
        st.update_reminder(&x, ch(json!({"completed": true})));
        assert_eq!(st.reminder(&x).unwrap()["completed_at"], first_at);
        st.update_reminder(&x, ch(json!({"completed": false})));
        assert!(st.reminder(&x).unwrap()["completed_at"].is_null());
        assert_eq!(st.next_due_after(Moment(2026, 10, 10, 12, 1)), Some(Moment(2026, 10, 10, 12, 5)));
    }

    #[test]
    fn day_numbers() {
        assert_eq!(Moment::day_number((1970, 1, 1)), 0);
        assert_eq!(Moment::day_number((2027, 1, 1)) - Moment::day_number((2026, 12, 31)), 1);
        assert_eq!(Moment::day_number((2024, 3, 1)) - Moment::day_number((2024, 2, 28)), 2);
    }
}
