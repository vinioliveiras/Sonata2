//! Dock badges for apps that don't send one (sonata2/badges.py; Vini:
//! WhatsApp had none).
//!
//! An app's badge, first found wins:
//!
//!   1. the count the app sends itself (Unity LauncherEntry: Telegram,
//!      Thunderbird, Discord, Signal... -- the Dock)
//!   2. a web app's unread count from its page title: "(3) WhatsApp", the
//!      way WhatsApp, Gmail, Telegram Web, Slack... show it in the tab
//!   3. its notifications still in Notification Center that arrived after
//!      its window was last in front (opening the app clears it)
//!
//! The menu bar process (the notification server) publishes its list in
//! `$XDG_RUNTIME_DIR/sonata2-notes.json`; the Dock follows it. The web
//! app's own process publishes its page's title in
//! `$XDG_RUNTIME_DIR/sonata2-webapp-titles.json` (Chrome names an --app
//! window after the site, "web.whatsapp.com": no count there). The same
//! files as the Python Sonata's, so either side's Dock reads either side's
//! menu bar.
//!
//! ```text
//! publish(&notes, server, None)          // Notifications, whenever the list changes
//! load(None) -> (server, notes)          // [Note {id, desktop, app}]
//! watch(callback, None)                  // keep the returned monitor
//! note_counts(&notes, &tiles, &seen)     // {tile key: count}
//! title_count(&titles)                   // 3 for "(3) WhatsApp"
//! label(n)                               // "", "7", "99+"
//! publish_page_title(class, title, None) // the web app's process
//! page_titles(None)                      // the Dock
//! ```

use gio::prelude::*;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

/// The notification list the menu bar publishes (badges.STATE).
pub fn state_path() -> PathBuf {
    runtime_dir().join("sonata2-notes.json")
}

/// The web apps' page titles (badges.PAGE_TITLES).
pub fn page_titles_path() -> PathBuf {
    runtime_dir().join("sonata2-webapp-titles.json")
}

/// A web app's desktop id starts with this (webapps.DESKTOP_PREFIX).
pub const WEBAPP_DESKTOP_PREFIX: &str = "sonata2-webapp-";

/// webapps.is_webapp: "sonata2-webapp-<id>[.desktop]" (webapps.py isn't
/// ported; this is the one line badges needs).
pub fn is_webapp(desktop: &str) -> bool {
    norm_suffix(desktop).starts_with(WEBAPP_DESKTOP_PREFIX)
}

fn norm_suffix(s: &str) -> &str {
    s.strip_suffix(".desktop").unwrap_or(s)
}

fn norm(s: &str) -> String {
    norm_suffix(s).to_lowercase()
}

/// Settings > Notifications: Badge App Icons (all apps) and each app's own
/// Badge App Icon (Vini). cfg: notifications.json.
pub fn allowed(cfg: &Value, key: &str, name: &str) -> bool {
    if !cfg.get("badges").map(truthy).unwrap_or(true) {
        return false;
    }
    let (k, nm) = (norm(key), name.to_lowercase());
    if let Some(apps) = cfg.get("apps").and_then(Value::as_object) {
        for (akey, a) in apps {
            let Some(a) = a.as_object() else { continue };
            let same_name = !nm.is_empty() && a.get("name").and_then(Value::as_str).unwrap_or("").to_lowercase() == nm;
            if (norm(akey) == k || same_name) && !a.get("badge").map(truthy).unwrap_or(true) {
                return false;
            }
        }
    }
    true
}

/// Python's truth of a JSON value.
pub(crate) fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The badge's text: "" for none, "7", "99+".
pub fn label(n: i64) -> String {
    if n <= 0 {
        String::new()
    } else if n > 99 {
        "99+".into()
    } else {
        n.to_string()
    }
}

fn title_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^\s*[(\[](\d{1,5})\+?[)\]]\s").unwrap())
}

/// The biggest count in the titles' "(3) ..." / "[3] ...", 0 for none.
pub fn title_count<S: AsRef<str>>(titles: &[S]) -> i64 {
    titles.iter().filter_map(|t| title_re().captures(t.as_ref())?.get(1)?.as_str().parse::<i64>().ok()).max().unwrap_or(0).max(0)
}

/// Web pages put the count in their title; other apps' titles are their
/// own business (a document named "(1) draft").
pub fn counted_title(app_id: &str, key: &str) -> bool {
    let aid = app_id.to_lowercase();
    is_webapp(key) || ["chrome-", "chromium-", "brave-", "msedge-", "vivaldi-"].iter().any(|p| aid.starts_with(p))
}

// -- web apps' page titles, between processes ------------------------------------------------

/// The page's title of a web app (None: its window is gone).
pub fn publish_page_title(window_class: &str, title: Option<&str>, path: Option<&Path>) {
    let path = path.map(Path::to_path_buf).unwrap_or_else(page_titles_path);
    let mut data = match std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    };
    match title {
        None => {
            if data.remove(window_class).is_none() {
                return;
            }
        }
        Some(t) if data.get(window_class).and_then(Value::as_str) == Some(t) => return,
        Some(t) => {
            data.insert(window_class.into(), Value::String(t.into()));
        }
    }
    // several web apps write it: a temp file of this process's own
    let tmp = PathBuf::from(format!("{}.{}.new", path.display(), std::process::id()));
    if std::fs::write(&tmp, Value::Object(data).to_string()).is_ok() && std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

type PageCache = Option<((PathBuf, i128), HashMap<String, String>)>;
static PAGE_CACHE: Mutex<PageCache> = Mutex::new(None);

fn mtime_ns(path: &Path) -> Option<i128> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::metadata(path).ok()?;
    Some(m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128)
}

/// {window class: page title}; read again only when the file changed (the
/// Dock asks on every window event).
pub fn page_titles(path: Option<&Path>) -> HashMap<String, String> {
    let path = path.map(Path::to_path_buf).unwrap_or_else(page_titles_path);
    let Some(mtime) = mtime_ns(&path) else { return HashMap::new() };
    let mut cache = PAGE_CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let stamp = (path.clone(), mtime);
    if let Some((s, value)) = cache.as_ref() {
        if *s == stamp {
            return value.clone();
        }
    }
    let value: HashMap<String, String> = match std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => m.into_iter().filter_map(|(k, v)| Some((k, v.as_str()?.to_string()))).collect(),
        _ => HashMap::new(),
    };
    *cache = Some((stamp, value.clone()));
    value
}

/// The titles that count: each window's own and its web app's page title,
/// both -- the page's stood in for the window's, and a stale one (Chrome's
/// word at the last move: "Instagram", the address) hid the count the
/// window's title had ("(3) Instagram", Vini: no badge). wins: (app_id, title).
pub fn window_titles(wins: &[(&str, &str)], pages: Option<&HashMap<String, String>>) -> Vec<String> {
    let owned;
    let pages = match pages {
        Some(p) => p,
        None => {
            owned = page_titles(None);
            &owned
        }
    };
    let mut out = Vec::new();
    for (app_id, title) in wins {
        match pages.get(*app_id).filter(|p| !p.is_empty() && p.as_str() != *title) {
            Some(page) => out.extend([page.clone(), title.to_string()]),
            None => out.push(title.to_string()),
        }
    }
    out
}

// -- the notification list, between processes ----------------------------------------------

/// One notification as the Dock needs it.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct Note {
    pub id: i64,
    pub desktop: String,
    pub app: String,
}

/// The menu bar's list, for the Dock (server: the publishing process's pid).
pub fn publish(notes: &[Note], server: i64, path: Option<&Path>) {
    let data = json!({"server": server, "notes": notes.iter().map(|n| json!({"id": n.id, "desktop": n.desktop, "app": n.app})).collect::<Vec<_>>()});
    let path = path.map(Path::to_path_buf).unwrap_or_else(state_path);
    let tmp = PathBuf::from(format!("{}.new", path.display()));
    if std::fs::write(&tmp, data.to_string()).is_ok() && std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
}

/// (server, notes); (None, []) when there's nothing to read.
pub fn load(path: Option<&Path>) -> (Option<i64>, Vec<Note>) {
    let path = path.map(Path::to_path_buf).unwrap_or_else(state_path);
    let Some(Value::Object(data)) = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) else {
        return (None, Vec::new());
    };
    let notes = data
        .get("notes")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|n| {
                    let n = n.as_object()?;
                    let id = n.get("id").filter(|v| v.is_i64() || v.is_u64())?.as_i64()?;
                    let s = |k: &str| n.get(k).and_then(Value::as_str).unwrap_or("").to_string();
                    Some(Note { id, desktop: s("desktop"), app: s("app") })
                })
                .collect()
        })
        .unwrap_or_default();
    (data.get("server").and_then(Value::as_i64), notes)
}

/// callback() whenever the file changes. Keep the returned monitor.
pub fn watch<F: Fn() + 'static>(callback: F, path: Option<&Path>) -> Option<gio::FileMonitor> {
    let path = path.map(Path::to_path_buf).unwrap_or_else(state_path);
    let mon = gio::File::for_path(path).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE).ok()?;
    mon.connect_changed(move |_, _, _, _| callback());
    Some(mon)
}

/// The tile key a notification belongs to: by its desktop entry, else by
/// the app's name. tiles: (key, name), in the Dock's order.
pub fn owner<'a, I>(note: &Note, tiles: I) -> Option<&'a str>
where
    I: IntoIterator<Item = (&'a String, &'a String)> + Clone,
{
    let d = norm(&note.desktop);
    if !d.is_empty() {
        if let Some((k, _)) = tiles.clone().into_iter().find(|(k, _)| norm(k) == d) {
            return Some(k.as_str());
        }
    }
    let name = note.app.to_lowercase();
    if !name.is_empty() {
        if let Some((k, _)) = tiles.into_iter().find(|(_, t)| t.to_lowercase() == name) {
            return Some(k.as_str());
        }
    }
    None
}

/// {key: notifications newer than seen[key]} (ids only grow).
pub fn note_counts<'a, I>(notes: &[Note], tiles: I, seen: &HashMap<String, i64>) -> HashMap<String, i64>
where
    I: IntoIterator<Item = (&'a String, &'a String)> + Clone,
{
    let mut out = HashMap::new();
    for n in notes {
        if let Some(key) = owner(n, tiles.clone()) {
            if n.id > seen.get(key).copied().unwrap_or(0) {
                *out.entry(key.to_string()).or_insert(0) += 1;
            }
        }
    }
    out
}

/// The newest notification's id of `key`, 0 for none.
pub fn newest<'a, I>(notes: &[Note], tiles: I, key: &str) -> i64
where
    I: IntoIterator<Item = (&'a String, &'a String)> + Clone,
{
    notes.iter().filter(|n| owner(n, tiles.clone()) == Some(key)).map(|n| n.id).max().unwrap_or(0)
}

// -- who sent it ---------------------------------------------------------------------------------

/// The value of a `--user-data-dir=` in one argument: Chrome writes its
/// whole command line back as one string, spaces between ("chrome
/// --user-data-dir=... --app=..."): its own sender was never found by this
/// (its notifications went to "Google Chrome"). Up to the next " --".
fn user_data_dir(a: &str) -> Option<&str> {
    const FLAG: &str = "--user-data-dir=";
    let line = a.split('\n').next().unwrap_or("");
    let mut from = 0;
    while let Some(i) = line[from..].find(FLAG).map(|i| i + from) {
        let before_ok = i == 0 || line[..i].chars().next_back().is_some_and(char::is_whitespace);
        if before_ok {
            let rest = &line[i + FLAG.len()..];
            // lazy up to whitespace + "--", at least one character
            let mut end = rest.len();
            for (j, c) in rest.char_indices().skip(1) {
                if c.is_whitespace() && rest[j + c.len_utf8()..].starts_with("--") {
                    end = j;
                    break;
                }
            }
            if !rest.is_empty() {
                return Some(&rest[..end]);
            }
        }
        from = i + FLAG.len();
    }
    None
}

/// os.path.normpath for absolute or relative paths, enough for a profile folder.
fn normpath(p: &str) -> String {
    let abs = p.starts_with('/');
    let mut parts: Vec<&str> = Vec::new();
    for part in p.split('/') {
        match part {
            "" | "." => {}
            ".." if parts.last().is_some_and(|l| *l != "..") => {
                parts.pop();
            }
            ".." if abs => {}
            x => parts.push(x),
        }
    }
    let joined = parts.join("/");
    if abs {
        format!("/{joined}")
    } else if joined.is_empty() {
        ".".into()
    } else {
        joined
    }
}

fn split_path(p: &str) -> (&str, &str) {
    match p.rfind('/') {
        Some(0) => ("/", &p[1..]),
        Some(i) => (&p[..i], &p[i + 1..]),
        None => ("", p),
    }
}

/// A Sonata web app's id from its process's command line: Chromium with the
/// web app's profile (.../webapps/<id>/chromium), or `sonata2 webapp <id>`.
pub fn webapp_of<S: AsRef<str>>(argv: &[S]) -> String {
    for a in argv {
        if let Some(dir) = user_data_dir(a.as_ref()) {
            let path = normpath(dir);
            let (parent, leaf) = split_path(&path);
            let (root, app) = split_path(parent);
            let root_base = split_path(root).1;
            if leaf == "chromium" && root_base == "webapps" && !app.is_empty() {
                return app.to_string();
            }
        }
    }
    // sonata2 webapp <id>, python -m sonata2 webapp <id>
    for i in 1..argv.len().saturating_sub(1) {
        let prev = argv[i - 1].as_ref();
        if argv[i].as_ref() == "webapp" && split_path(prev).1.contains("sonata2") {
            return argv[i + 1].as_ref().to_string();
        }
    }
    String::new()
}

fn host(s: &str) -> String {
    let tags = {
        static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
        RE.get_or_init(|| regex::Regex::new(r"<[^>]+>").unwrap())
    };
    let mut s = tags.replace_all(s, "").trim().to_lowercase();
    if let Some(i) = s.find("://") {
        // urlsplit(s).hostname: between "//" and the path, no user or port
        let rest = &s[i + 3..];
        let net = &rest[..rest.find(['/', '?', '#']).unwrap_or(rest.len())];
        let net = net.rsplit('@').next().unwrap_or("");
        let h = if let Some(v6) = net.strip_prefix('[') { v6.split(']').next().unwrap_or("") } else { net.split(':').next().unwrap_or("") };
        s = h.to_string();
    }
    s.strip_prefix("www.").map(String::from).unwrap_or(s)
}

/// The web app (id) a browser notification came from, by the site line
/// Chrome puts first in its body ("web.whatsapp.com") -- when the sending
/// process can't tell (Chrome hands the notification to its own helper).
/// entries: webapps.json's "apps".
pub fn webapp_for_site(body: &str, entries: &Map<String, Value>) -> String {
    let first = body.trim().split('\n').next().unwrap_or("");
    let h = host(first);
    if h.is_empty() || h.contains(' ') || !h.contains('.') {
        return String::new();
    }
    for (app, entry) in entries {
        if let Some(e) = entry.as_object() {
            if host(e.get("url").and_then(Value::as_str).unwrap_or("")) == h {
                return app.clone();
            }
        }
    }
    String::new()
}

/// A process's command line ([] when it's gone).
pub fn cmdline(pid: i64) -> Vec<String> {
    std::fs::read(format!("/proc/{pid}/cmdline"))
        .map(|b| b.split(|c| *c == 0).filter(|p| !p.is_empty()).map(|p| String::from_utf8_lossy(p).into_owned()).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const WEB: &str = "sonata2-webapp-waee32c088c";

    #[test]
    fn test_title_count() {
        assert_eq!(title_count(&["(3) WhatsApp"]), 3);
        assert_eq!(title_count(&["WhatsApp", "(12) WhatsApp", "[2] x"]), 12);
        assert_eq!(title_count(&["WhatsApp", "Draft (1)"]), 0);
        assert_eq!(title_count::<&str>(&[]), 0);
    }

    #[test]
    fn test_counted_title_only_for_web_pages() {
        assert!(counted_title("chrome-web.whatsapp.com__-Default", WEB));
        assert!(counted_title("x", WEB));
        assert!(!counted_title("org.gnome.TextEditor", "org.gnome.TextEditor"));
    }

    /// Vini: a switch for badges in general and per app.
    #[test]
    fn test_allowed_all_and_per_app() {
        assert!(allowed(&json!({}), WEB, "WhatsApp"));
        assert!(!allowed(&json!({"badges": false}), WEB, "WhatsApp"));
        let off = json!({"apps": {format!("{WEB}.desktop"): {"name": "WhatsApp", "badge": false}}});
        assert!(!allowed(&off, WEB, "x"));
        assert!(!allowed(&json!({"apps": {"zz": {"name": "whatsapp", "badge": false}}}), "k", "WhatsApp"));
        assert!(allowed(&off, "discord", "Discord"));
    }

    #[test]
    fn test_label() {
        assert_eq!([0, 7, 120, -1].map(label), ["", "7", "99+", ""]);
    }

    fn tiles() -> BTreeMap<String, String> {
        [(WEB.to_string(), "WhatsApp".to_string()), ("x".into(), "X".into())].into_iter().collect()
    }

    fn note(id: i64, desktop: &str, app: &str) -> Note {
        Note { id, desktop: desktop.into(), app: app.into() }
    }

    #[test]
    fn test_note_counts_by_desktop_or_name_and_seen() {
        let notes = [note(1, &format!("{WEB}.desktop"), "WhatsApp"), note(2, "", "whatsapp"), note(3, "", "Other")];
        let t = tiles();
        assert_eq!(note_counts(&notes, &t, &HashMap::new()), HashMap::from([(WEB.to_string(), 2)]));
        assert_eq!(note_counts(&notes, &t, &HashMap::from([(WEB.to_string(), 1)])), HashMap::from([(WEB.to_string(), 1)]));
        assert_eq!(newest(&notes, &t, WEB), 2);
    }

    #[test]
    fn test_publish_load_roundtrip() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("n.json");
        publish(&[note(5, WEB, "WhatsApp")], 42, Some(&p));
        assert_eq!(load(Some(&p)), (Some(42), vec![note(5, WEB, "WhatsApp")]));
        assert_eq!(load(Some(&d.path().join("none"))), (None, vec![]));
        // what the Python menu bar writes reads the same
        std::fs::write(&p, r#"{"server": 7, "notes": [{"id": 1, "desktop": "a", "app": "A"}, {"id": "x"}, 3]}"#).unwrap();
        assert_eq!(load(Some(&p)), (Some(7), vec![note(1, "a", "A")]));
    }

    #[test]
    fn test_webapp_of_sender() {
        let root = "/home/v/.local/share/sonata2-data/webapps";
        assert_eq!(webapp_of(&["/opt/google/chrome/chrome".to_string(), format!("--user-data-dir={root}/waee32c088c/chromium"), "--app=https://web.whatsapp.com/".into()]), "waee32c088c");
        assert_eq!(webapp_of(&["python3", "-m", "sonata2", "webapp", "abc"]), "abc");
        assert_eq!(webapp_of(&["/opt/google/chrome/chrome", "--user-data-dir=/home/v/.config/chrome"]), "");
    }

    /// /proc/<pid>/cmdline of Chrome is one string, spaces between -- the
    /// web app that sent a notification was never found by its profile (it
    /// went to "Google Chrome").
    #[test]
    fn test_chrome_rewritten_command_line() {
        let root = "/home/v/.local/share/sonata2-data/webapps";
        let one = format!("/opt/google/chrome/chrome --user-data-dir={root}/w84b1822077/chromium --no-first-run --app=https://www.instagram.com/ --remote-debugging-pipe");
        assert_eq!(webapp_of(&[one]), "w84b1822077");
        assert_eq!(webapp_of(&["chrome --user-data-dir=/home/v/My Data/webapps/wab/chromium"]), "wab");
        assert_eq!(webapp_of(&["/opt/google/chrome/chrome --user-data-dir=/home/v/.config/chrome"]), "");
        assert_eq!(webapp_of(&["chrome --app=x --not-user-data-dir=/w/webapps/a/chromium"]), "");
    }

    /// Vini: WhatsApp's notifications went to Chrome (badge 8 on Chrome).
    #[test]
    fn test_site_line_finds_the_web_app() {
        let entries = json!({"waee32c088c": {"name": "WhatsApp", "url": "https://web.whatsapp.com/"}, "g1": {"name": "Gmail", "url": "https://mail.google.com/"}});
        let e = entries.as_object().unwrap();
        assert_eq!(webapp_for_site("web.whatsapp.com\n\nTava a dormir", e), "waee32c088c");
        assert_eq!(webapp_for_site("<a href=\"https://web.whatsapp.com/\">web.whatsapp.com</a>\n\nx", e), "waee32c088c");
        assert_eq!(webapp_for_site("www.mail.google.com\nhi", e), "g1");
        assert_eq!(webapp_for_site("Hello there.\nweb.whatsapp.com", e), "");
        assert_eq!(webapp_for_site("example.com\nhi", e), "");
    }

    #[test]
    fn test_published_and_counted() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("titles.json");
        let wm = "chrome-web.whatsapp.com__-Default";
        publish_page_title(wm, Some("(3) WhatsApp"), Some(&path));
        publish_page_title("chrome-other__-Default", Some("Other"), Some(&path));
        let pages = page_titles(Some(&path));
        assert_eq!(window_titles(&[(wm, "web.whatsapp.com")], Some(&pages)), ["(3) WhatsApp", "web.whatsapp.com"]);
        assert_eq!(title_count(&window_titles(&[(wm, "web.whatsapp.com")], Some(&pages))), 3);
        assert_eq!(window_titles(&[("org.gnome.App", "(1) draft")], Some(&pages)), ["(1) draft"]); // its own title
        publish_page_title(wm, None, Some(&path)); // its window gone
        let after = page_titles(Some(&path));
        assert!(!after.contains_key(wm));
        assert!(after.contains_key("chrome-other__-Default"));
    }

    /// Vini: a stale page title ("Instagram", the address) hid the count
    /// the window's own title had ("(3) Instagram"): both count.
    #[test]
    fn test_stale_page_title_hid_the_count() {
        let wm = "chrome-www.instagram.com__-Default";
        for stale in ["Instagram", "www.instagram.com"] {
            let pages = HashMap::from([(wm.to_string(), stale.to_string())]);
            assert_eq!(title_count(&window_titles(&[(wm, "(3) Instagram")], Some(&pages))), 3, "{stale}");
        }
        let pages = HashMap::from([(wm.to_string(), "(4) WhatsApp".to_string())]);
        assert_eq!(title_count(&window_titles(&[(wm, "web.whatsapp.com")], Some(&pages))), 4);
    }

    #[test]
    fn test_normpath() {
        assert_eq!(normpath("/a/./b//c/../d/"), "/a/b/d");
        assert_eq!(normpath("a/../.."), "..");
    }
}
