//! Sonata's web apps (sonata2/webapps/__init__.py; Vini): a website in a
//! window of its own -- its own Dock icon, its own login (cookies and
//! storage apart from every other web app), in Launchpad like any app.
//! Made with "New Web App…" (right-click on the background of Apps, full
//! screen or the Apps Menu).
//!
//! Each one: config webapps.json {"apps": {id: {"name", "url"}}}, a desktop
//! entry sonata2-webapp-<id>.desktop (`sonata2 webapp <id>`, StartupWMClass
//! = its own app id, so the Dock tells them apart), its data in
//! ~/.local/share/sonata2-data/webapps/<id>/ and its icon there (icon.png:
//! the site's own, fetched when it's made; the window updates it from the
//! page).
//!
//! ```ignore
//! create(name, url, ...) -> id      remove(id)
//! apps() -> {id: {...}}             normalize_url("web.whatsapp.com") -> "https://web.whatsapp.com/"
//! open_new()                        // the "New Web App" form (its own process)
//! ```

pub mod browsergpu;
pub mod chromeguard;
pub mod links;
pub mod webkit;
pub mod window;

use serde_json::{json, Map, Value};
use std::ffi::{c_void, CStr, CString};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub use sonata_desktop::badges::is_webapp;

pub const NAME: &str = "webapps";
pub const APP_ID_PREFIX: &str = sonata_desktop::appmatch::WEBAPP_APP_ID_PREFIX;
pub const DESKTOP_PREFIX: &str = sonata_desktop::badges::WEBAPP_DESKTOP_PREFIX;
pub const FALLBACK_ICON: &str = "web-browser";
/// smaller site icons look blurry on the Dock: kept only without a better one
pub const ICON_MIN: i32 = 48;
pub const FETCH_TIMEOUT: u32 = 8;
pub const UA: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";

/// webapps.json, with an "apps" object of its own.
fn load() -> Map<String, Value> {
    sonata_config::load(NAME, &json!({"apps": {}}))
}

fn save(data: &Map<String, Value>) {
    let _ = sonata_config::save(NAME, data);
}

/// Every web app: {id: {"name", "url", ...}}.
pub fn apps() -> Map<String, Value> {
    match load().remove("apps") {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

pub fn get(app: &str) -> Option<Map<String, Value>> {
    apps().get(app).and_then(Value::as_object).cloned()
}

fn text<'a>(entry: &'a Map<String, Value>, key: &str) -> &'a str {
    entry.get(key).and_then(Value::as_str).unwrap_or("")
}

pub fn app_id(app: &str) -> String {
    format!("{APP_ID_PREFIX}{app}")
}

pub fn desktop_id(app: &str) -> String {
    format!("{DESKTOP_PREFIX}{app}")
}

pub fn id_of(desktop: &str) -> String {
    let d = desktop.strip_suffix(".desktop").unwrap_or(desktop);
    d.get(DESKTOP_PREFIX.len()..).unwrap_or("").to_string()
}

/// ~/.local/share/sonata2-data/webapps/<id> (userdata: never Sonata's own
/// folder -- a dev install links that to the git clone, and the logins
/// landed in the repository). Not created: the caller makes it when it writes.
pub fn data_dir(app: &str) -> PathBuf {
    webapps_root().join(app)
}

/// userdata.folder("webapps"): created, with the old folder's content moved in.
fn webapps_root() -> PathBuf {
    let new = data_home().join("sonata2-data").join("webapps");
    if !new.is_dir() {
        migrate(&data_home().join("sonata2").join("webapps"), &new);
    }
    sonata_config::data_dir("webapps")
}

/// userdata.migrate: ~/.local/share/sonata2/webapps (or wherever it points:
/// a dev install's git clone) moved to the data folder, unless that exists.
fn migrate(old: &Path, new: &Path) -> bool {
    if std::fs::symlink_metadata(new).is_ok() || !old.is_dir() {
        return false;
    }
    let real = std::fs::canonicalize(old).unwrap_or_else(|_| old.to_path_buf());
    if let Some(parent) = new.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::rename(&real, new).is_ok()
}

fn data_home() -> PathBuf {
    glib::user_data_dir()
}

fn applications_dir() -> PathBuf {
    data_home().join("applications")
}

/// A picture chosen before the data moved (userdata.migrate): App Icons
/// follows it to the new folder.
fn fix_moved_icon(app: &str) {
    let folder = data_dir(app); // (moves the old folder over first)
    let prefs = sonata_config::load("icons", &json!({"shape": "squircle", "source": "auto", "apps": {}}));
    let path = prefs.get("apps").and_then(|a| a.get(desktop_id(app))).and_then(|p| p.get("path")).and_then(Value::as_str).unwrap_or("").to_string();
    if !path.is_empty() && !Path::new(&path).exists() && path.contains(&format!("/webapps/{app}/")) {
        let moved = folder.join(Path::new(&path).file_name().unwrap_or_default());
        if moved.exists() {
            set_app_pref(&desktop_id(app), &[("path", Some(json!(moved.to_string_lossy())))]);
        }
    }
}

/// icons.set_app_pref (icons.py isn't ported): change one app's choices in
/// icons.json (None removes a key; nothing left: back to auto).
fn set_app_pref(did: &str, values: &[(&str, Option<Value>)]) {
    let data = sonata_config::load("icons", &json!({"shape": "squircle", "source": "auto", "apps": {}}));
    let mut apps = data.get("apps").and_then(Value::as_object).cloned().unwrap_or_default();
    let mut cur = apps.get(did).and_then(Value::as_object).cloned().unwrap_or_default();
    for (k, v) in values {
        match v {
            None => {
                cur.remove(*k);
            }
            Some(v) => {
                cur.insert(k.to_string(), v.clone());
            }
        }
    }
    if cur.get("source").is_some() && cur.get("source") == data.get("source") {
        cur.remove("source"); // the same as all apps': nothing of its own
    }
    if cur.is_empty() {
        apps.remove(did);
    } else {
        apps.insert(did.into(), Value::Object(cur));
    }
    let _ = sonata_config::update("icons", &json!({"apps": apps}));
}

pub fn icon_path(app: &str) -> PathBuf {
    data_dir(app).join("icon.png")
}

// -- addresses (urllib.parse's few rules the web apps use) --------------------------------------

/// urllib.parse.urlsplit's five parts.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SplitUrl {
    pub scheme: String,
    pub netloc: String,
    pub path: String,
    pub query: String,
    pub fragment: String,
}

impl SplitUrl {
    /// The host, lowercase, without user or port (None when empty).
    pub fn hostname(&self) -> Option<String> {
        let at = self.netloc.rsplit_once('@').map_or(self.netloc.as_str(), |x| x.1);
        let host = if at.contains('[') {
            at.split_once('[').map(|x| x.1).unwrap_or("").split(']').next().unwrap_or("")
        } else {
            at.split(':').next().unwrap_or("")
        };
        (!host.is_empty()).then(|| host.to_lowercase())
    }
}

/// urllib.parse.urlsplit.
pub fn urlsplit(url: &str) -> SplitUrl {
    let url: String = url.trim_start_matches(|c: char| c <= ' ').chars().filter(|c| !matches!(c, '\t' | '\r' | '\n')).collect();
    let mut rest = url.as_str();
    let mut out = SplitUrl::default();
    if let Some(i) = rest.find(':') {
        let s = &rest[..i];
        if i > 0 && s.starts_with(|c: char| c.is_ascii_alphabetic()) && s.chars().all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c)) {
            out.scheme = s.to_ascii_lowercase();
            rest = &rest[i + 1..];
        }
    }
    if let Some(r) = rest.strip_prefix("//") {
        let end = r.find(['/', '?', '#']).unwrap_or(r.len());
        out.netloc = r[..end].to_string();
        rest = &r[end..];
    }
    if let Some((a, f)) = rest.split_once('#') {
        out.fragment = f.to_string();
        rest = a;
    }
    if let Some((a, q)) = rest.split_once('?') {
        out.query = q.to_string();
        rest = a;
    }
    out.path = rest.to_string();
    out
}

/// A typed address -> a full https URL, None when it isn't one.
pub fn normalize_url(text: &str) -> Option<String> {
    let mut text = text.trim().to_string();
    if text.is_empty() || text.contains(' ') {
        return None;
    }
    let has_scheme = text.split_once("://").is_some_and(|(s, _)| s.starts_with(|c: char| c.is_ascii_alphabetic()) && s.chars().all(|c| c.is_ascii_alphanumeric() || "+.-".contains(c)));
    if !has_scheme {
        text = format!("https://{text}");
    }
    let u = urlsplit(&text);
    let host = u.hostname().unwrap_or_default();
    if !matches!(u.scheme.as_str(), "http" | "https") || host.is_empty() || (!host.contains('.') && host != "localhost") {
        return None;
    }
    let mut out = format!("{}://{}{}", u.scheme, u.netloc, if u.path.is_empty() { "/" } else { &u.path });
    if !u.query.is_empty() {
        out += &format!("?{}", u.query);
    }
    if !u.fragment.is_empty() {
        out += &format!("#{}", u.fragment);
    }
    Some(out)
}

/// "https://web.whatsapp.com/" -> "Whatsapp" (what the form suggests).
pub fn default_name(url: &str) -> String {
    let host = urlsplit(url).hostname().unwrap_or_default();
    let host = host.strip_prefix("www.").unwrap_or(&host).to_string();
    let parts: Vec<&str> = host.split('.').filter(|p| !["web", "app", "m"].contains(p)).collect();
    capitalize(parts.first().copied().unwrap_or(&host))
}

/// str.capitalize: the first letter up, the rest down.
fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().chain(c.flat_map(char::to_lowercase)).collect(),
        None => String::new(),
    }
}

/// How the desktop entries start this binary (__main__.self_command):
/// `$SONATA2_RS_LAUNCHER` or this executable.
fn launcher() -> String {
    std::env::var("SONATA2_RS_LAUNCHER")
        .ok()
        .filter(|l| Path::new(l).exists())
        .or_else(|| std::env::current_exe().ok().map(|p| p.display().to_string()))
        .unwrap_or_else(|| "sonata2-rs".into())
}

/// Desktop Entry quoting for one Exec argument (sonata2-rs/desktop.rs's
/// own, which isn't public: the same few lines).
fn quote(arg: &str) -> String {
    let a = arg.replace('%', "%%");
    if !a.chars().any(|c| " \t\n\"'\\><~|&;$*?#()`".contains(c)) {
        return a;
    }
    let mut q = String::from("\"");
    for c in a.chars() {
        if "\"`$\\".contains(c) {
            q.push('\\');
        }
        q.push(c);
    }
    q.push('"');
    q.replace('\\', "\\\\")
}

fn command() -> String {
    quote(&launcher())
}

/// WebKitGTK 6 can be opened (it's loaded at run time).
pub fn webkit_available() -> bool {
    webkit::available()
}

// -- the engine ---------------------------------------------------------------------------------
// The engine each web app runs on (its form): Chromium (a browser of that
// family, as an app window with a profile of its own: sites made for Chrome
// just work -- WhatsApp's videos) or WebKit (Sonata's own window, lighter).
pub const ENGINES: [&str; 2] = ["chromium", "webkit"];
const CHROMIUM_BROWSERS: [(&str, &str); 8] = [
    ("google-chrome-stable", "Google Chrome"),
    ("google-chrome", "Google Chrome"),
    ("chromium", "Chromium"),
    ("chromium-browser", "Chromium"),
    ("brave", "Brave"),
    ("brave-browser", "Brave"),
    ("microsoft-edge-stable", "Microsoft Edge"),
    ("vivaldi-stable", "Vivaldi"),
];
/// a browser picked in the form (entry["browser"]): its key, the prefix of
/// the app id its --app windows get, and the profile folder (Chrome's and
/// Chromium's stay "chromium", where web apps kept theirs before)
const BROWSER_KEYS: [(&str, &str); 5] = [("Google Chrome", "chrome"), ("Chromium", "chromium"), ("Brave", "brave"), ("Microsoft Edge", "edge"), ("Vivaldi", "vivaldi")];
/// the same browsers as Flatpaks (Vini's Brave is one): run with `flatpak
/// run`, their profiles in the app's own folder (no access to the rest of home)
const CHROMIUM_FLATPAKS: [(&str, &str); 5] = [("com.google.Chrome", "Google Chrome"), ("org.chromium.Chromium", "Chromium"), ("com.brave.Browser", "Brave"), ("com.microsoft.Edge", "Microsoft Edge"), ("com.vivaldi.Vivaldi", "Vivaldi")];
/// a browser "path" that is a Flatpak: "flatpak:com.brave.Browser"
pub const FLATPAK: &str = "flatpak:";
const FLATPAK_APPS: [&str; 2] = ["/var/lib/flatpak/app", "~/.local/share/flatpak/app"];

/// A Chromium-family browser installed: (command path, name, key).
#[derive(Debug, Clone, PartialEq)]
pub struct Browser {
    pub path: String,
    pub name: &'static str,
    pub key: &'static str,
}

fn key_of(name: &str) -> &'static str {
    BROWSER_KEYS.iter().find(|b| b.0 == name).map_or("chrome", |b| b.1)
}

fn is_browser_key(key: &str) -> bool {
    BROWSER_KEYS.iter().any(|b| b.1 == key)
}

fn expanduser(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => glib::home_dir().join(rest),
        None => PathBuf::from(p),
    }
}

/// Every Chromium-family browser installed, one per browser (Vini: Brave to
/// choose when it's there, not only the first one found).
pub fn chromium_browsers() -> Vec<Browser> {
    #[cfg(test)]
    if let Some(found) = tests::FOUND.with(|f| f.borrow().clone()) {
        return found;
    }
    browsers_with(&|c| sonata_backend::run::which(c).map(|p| p.display().to_string()), &|p| p.is_dir())
}

pub(crate) fn browsers_with(which: &dyn Fn(&str) -> Option<String>, isdir: &dyn Fn(&Path) -> bool) -> Vec<Browser> {
    let mut found: Vec<Browser> = Vec::new();
    for (cmd, name) in CHROMIUM_BROWSERS {
        if let Some(path) = which(cmd) {
            if !found.iter().any(|b| b.name == name) {
                found.push(Browser { path, name, key: key_of(name) });
            }
        }
    }
    let flatpak = which("flatpak").is_some();
    for (fid, name) in CHROMIUM_FLATPAKS {
        // (installed natively too: that one)
        if flatpak && !found.iter().any(|b| b.name == name) && FLATPAK_APPS.iter().any(|d| isdir(&expanduser(d).join(fid))) {
            found.push(Browser { path: format!("{FLATPAK}{fid}"), name, key: key_of(name) });
        }
    }
    found
}

/// The browser a web app runs in: the one its form picked if it's still
/// installed, else the first one installed; None: none.
pub fn chromium_browser(entry: Option<&Map<String, Value>>) -> Option<Browser> {
    let found = chromium_browsers();
    let want = entry.map(|e| text(e, "browser")).unwrap_or("");
    found.iter().find(|b| !want.is_empty() && b.key == want).or(found.first()).cloned()
}

pub fn browser_key(entry: Option<&Map<String, Value>>) -> &'static str {
    chromium_browser(entry).map_or("chrome", |b| key_of(b.name))
}

pub fn default_engine() -> &'static str {
    if chromium_browser(None).is_some() {
        "chromium"
    } else {
        "webkit"
    }
}

/// A web app's engine: what its form says (made before the choice: WebKit,
/// its login is there); Chromium without a browser for it: WebKit.
pub fn engine(entry: Option<&Map<String, Value>>) -> &'static str {
    let e = entry.map(|e| text(e, "engine")).unwrap_or("");
    if e == "chromium" && chromium_browser(None).is_some() {
        "chromium"
    } else {
        "webkit"
    }
}

/// The Wayland app id Chrome gives an --app window ("--class" is ignored):
/// https://web.whatsapp.com/ -> chrome-web.whatsapp.com__-Default (Brave's
/// start with "brave-").
pub fn chromium_app_id(url: &str, key: &str) -> String {
    let u = urlsplit(url);
    let prefix = if key == "brave" { "brave-" } else { "chrome-" };
    let path = if u.path.is_empty() { "/" } else { &u.path };
    format!("{prefix}{}_{}-Default", u.hostname().unwrap_or_default(), path.replace('/', "_"))
}

/// The app id its windows have (the desktop entry's StartupWMClass: the Dock's icon).
pub fn window_class(app: &str, entry: &Map<String, Value>) -> String {
    if engine(Some(entry)) == "chromium" {
        return chromium_app_id(text(entry, "url"), browser_key(Some(entry)));
    }
    app_id(app)
}

/// The desktop id of the web app whose windows have this app id (apps.py's
/// match_app_id for a Chromium web app, which sonata-desktop's appmatch
/// can't do: it can't depend on this crate). For the Dock and the menu bar.
pub fn desktop_for_window_class(window_app_id: &str) -> Option<String> {
    if let Some(wid) = window_app_id.strip_prefix(APP_ID_PREFIX) {
        return Some(desktop_id(wid));
    }
    apps().iter().find(|(wid, e)| e.as_object().is_some_and(|e| window_class(wid, e) == window_app_id)).map(|(wid, _)| desktop_id(wid))
}

/// The browser's profile for this web app: one per browser (a Brave and a
/// Chrome profile in one folder would clash). A Flatpak's is in its own
/// folder (~/.var/app/<id>): it can't reach the rest of home.
pub fn profile_dir(app: &str, entry: &Map<String, Value>) -> PathBuf {
    if let Some(fid) = chromium_browser(Some(entry)).and_then(|b| b.path.strip_prefix(FLATPAK).map(String::from)) {
        return glib::home_dir().join(format!(".var/app/{fid}/data/sonata2-webapps/{app}"));
    }
    let key = browser_key(Some(entry));
    data_dir(app).join(if key == "chrome" || key == "chromium" { "chromium" } else { key })
}

pub const DEFAULT_SIZE: (i32, i32) = (1200, 820);
pub const MIN_SIZE: (i32, i32) = (420, 360);
pub const SIZE_POLL_S: u32 = 2;

pub fn size_key(app: &str) -> String {
    format!("webapp-{app}")
}

/// The size its window opens at: the last one it had (windows.json), else DEFAULT_SIZE.
pub fn window_size(app: &str) -> (i32, i32) {
    let (w, h) = sonata_ui::window::saved_size(&size_key(app)).map_or(DEFAULT_SIZE, |(w, h, _)| (w, h));
    (w.max(MIN_SIZE.0), h.max(MIN_SIZE.1))
}

/// (w, h) of its window in Wayfire's list (not when maximized, tiled or full screen).
pub fn view_size(views: &Value, wm_class: &str) -> Option<(i32, i32)> {
    for v in views.as_array()? {
        let Some(v) = v.as_object() else { continue };
        if v.get("app-id").and_then(Value::as_str) != Some(wm_class) || v.get("role").and_then(Value::as_str).unwrap_or("toplevel") != "toplevel" {
            continue;
        }
        let set = |k: &str| v.get(k).is_some_and(|x| !matches!(x, Value::Null | Value::Bool(false)) && x.as_i64() != Some(0));
        if set("fullscreen") || set("maximized") || set("tiled-edges") {
            return None;
        }
        let g = v.get("geometry");
        let num = |k: &str| g.and_then(|g| g.get(k)).and_then(Value::as_i64).unwrap_or(0);
        if num("width") > 0 && num("height") > 0 {
            return Some((num("width") as i32, num("height") as i32));
        }
    }
    None
}

/// The browser as this web app's window: its own profile, its own app id,
/// its own Vulkan / WebGPU choice (browsergpu::web_app_command).
/// --test-type: no "unsupported command-line flag" bar over the page (Vini:
/// WhatsApp), whatever flags reach the browser (the user's own, a
/// launcher's, its own WebGPU).
pub fn chromium_command(app: &str, entry: &Map<String, Value>, browser: &str) -> Vec<String> {
    let (w, h) = window_size(app);
    let common = [
        format!("--user-data-dir={}", profile_dir(app, entry).display()),
        "--no-first-run".into(),
        "--no-default-browser-check".into(),
        "--test-type".into(),
        "--ozone-platform-hint=auto".into(),
    ];
    let tail = [format!("--window-size={w},{h}"), format!("--app={}", text(entry, "url"))];
    if let Some(fid) = browser.strip_prefix(FLATPAK) {
        // (its flag files are inside the Flatpak)
        let mut cmd = vec!["flatpak".to_string(), "run".into(), fid.into()];
        cmd.extend(common);
        cmd.extend(tail);
        return cmd;
    }
    let start = browsergpu::web_app_command(app, browser, None);
    let mut cmd = vec![start[0].clone()];
    cmd.extend(common);
    cmd.extend(start[1..].iter().cloned());
    cmd.extend(tail);
    cmd
}

/// (heading, body) said when WebKitGTK 6 is missing.
pub fn missing_webkit() -> (String, String) {
    (
        sonata_i18n::tr!("Web apps need WebKitGTK 6."),
        sonata_i18n::tr!("Install it with your package manager (Arch / CachyOS: sudo pacman -S webkitgtk-6.0; Ubuntu: gir1.2-webkit-6.0; Fedora: webkitgtk6.0), then open the web app again."),
    )
}

// -- icons --------------------------------------------------------------------------------------

/// Theme icon names a web app called `name` may have: "WhatsApp" ->
/// whatsapp; "Google Calendar" -> google-calendar, googlecalendar...
pub fn icon_names(name: &str, url: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for t in [name.to_string(), default_name(url)] {
        let mut slug = String::new();
        let mut gap = false;
        for c in t.to_lowercase().chars() {
            if c.is_ascii_lowercase() || c.is_ascii_digit() {
                if gap && !slug.is_empty() {
                    slug.push('-');
                }
                gap = false;
                slug.push(c);
            } else {
                gap = true;
            }
        }
        if !slug.is_empty() {
            for s in [slug.clone(), slug.replace('-', ""), slug.replace('-', "_")] {
                if !out.contains(&s) {
                    out.push(s);
                }
            }
        }
    }
    out
}

/// The icon theme's own icon with the web app's name (Vini: the icon pack
/// first, when it has one), None without one -- or without a display.
pub fn theme_icon(name: &str, url: &str) -> Option<String> {
    let display = no_display_or()?;
    let theme = gtk::IconTheme::for_display(&display);
    icon_names(name, url).into_iter().find(|n| theme.has_icon(n))
}

fn no_display_or() -> Option<gtk::gdk::Display> {
    if !gtk::is_initialized_main_thread() {
        return None;
    }
    gtk::gdk::Display::default()
}

/// Look the theme icon up again (main thread: GTK) and keep it in the entry.
pub fn update_theme_icon(app: &str) {
    let mut data = load();
    let Some(entry) = data.get_mut("apps").and_then(|a| a.get_mut(app)).and_then(Value::as_object_mut) else { return };
    let found = theme_icon(text(entry, "name"), text(entry, "url"));
    if found.is_none() && no_display_or().is_none() {
        return; // no display: keep what was found before
    }
    if entry.get("theme_icon").and_then(Value::as_str) != found.as_deref() {
        match found {
            Some(f) => {
                entry.insert("theme_icon".into(), json!(f));
            }
            None => {
                entry.remove("theme_icon");
            }
        }
        save(&data);
    }
}

/// A desktop entry string value: line breaks can't add keys, and a
/// backslash is escaped (the spec reads \s, \n... as escapes).
fn desktop_value(t: &str) -> String {
    let t = t.replace('\\', "\\\\");
    let mut out = String::new();
    let mut gap = false;
    for c in t.chars() {
        if (c as u32) < 0x20 {
            gap = true;
        } else {
            if gap {
                out.push(' ');
                gap = false;
            }
            out.push(c);
        }
    }
    if gap {
        out.push(' ');
    }
    out
}

pub fn desktop_text(app: &str, entry: &Map<String, Value>, command: Option<&str>) -> String {
    // the icon pack's icon with its name, else the site's, else a generic one
    let icon = match entry.get("theme_icon").and_then(Value::as_str).filter(|s| !s.is_empty()) {
        Some(t) => t.to_string(),
        None if icon_path(app).is_file() => icon_path(app).display().to_string(),
        None => FALLBACK_ICON.into(),
    };
    let name = desktop_value(Some(text(entry, "name")).filter(|n| !n.is_empty()).unwrap_or("Web App"));
    let cmd = command.map(String::from).unwrap_or_else(self::command);
    format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment={}\nIcon={icon}\nCategories=Network;WebApps;\nKeywords=web;app;site;\n\
         StartupWMClass={}\nStartupNotify=true\nX-Sonata-WebApp={app}\nExec={cmd} webapp {app}\n",
        desktop_value(text(entry, "url")),
        window_class(app, entry)
    )
}

/// Its desktop entry (apps.write_desktop_file: only when it changed,
/// written beside it then moved in, so the folder changes and Launchpad
/// reads it again). The
/// name is the user's own: no translations to add.
pub fn write_desktop(app: &str, command: Option<&str>) -> Option<PathBuf> {
    let entry = get(app)?;
    let t = desktop_text(app, &entry, command);
    let path = applications_dir().join(format!("{}.desktop", desktop_id(app)));
    if std::fs::read_to_string(&path).ok().as_deref() == Some(t.as_str()) {
        return Some(path);
    }
    let _ = std::fs::create_dir_all(applications_dir());
    let tmp = PathBuf::from(format!("{}.new", path.display()));
    std::fs::write(&tmp, &t).ok()?;
    std::fs::rename(&tmp, &path).ok()?;
    Some(path)
}

fn new_id() -> String {
    let hex: String = glib::uuid_string_random().chars().filter(char::is_ascii_hexdigit).take(10).collect();
    format!("w{hex}")
}

/// The web app made (its id), or the one already made for that site in
/// that browser. Err: not a web address.
pub fn create(name: &str, url: &str, command: Option<&str>, fetch: bool, background: bool, engine_name: Option<&str>, browser: Option<&str>) -> Result<String, &'static str> {
    let url = normalize_url(url).ok_or("not a web address")?;
    // the same site twice in one browser is one web app (Vini: WhatsApp twice
    // -- Chrome gives both one app id, the Dock can't tell their windows
    // apart, and opening one opened the site in Chrome): that one, not a
    // copy. (WebKit web apps have an app id each: a second is fine.)
    let eng = ENGINES.iter().find(|e| Some(**e) == engine_name).copied().unwrap_or_else(default_engine);
    let browser = browser.filter(|b| is_browser_key(b));
    let mut new = Map::new();
    new.insert("url".into(), json!(url));
    new.insert("engine".into(), json!(eng));
    if let Some(b) = browser {
        new.insert("browser".into(), json!(b));
    }
    if engine(Some(&new)) == "chromium" {
        let cls = window_class("", &new);
        for (other, entry) in apps() {
            if let Some(e) = entry.as_object() {
                if engine(Some(e)) == "chromium" && window_class(&other, e) == cls {
                    return Ok(other);
                }
            }
        }
    }
    let app = new_id();
    let mut data = load();
    let shown = Some(name.trim()).filter(|n| !n.is_empty()).map(String::from).unwrap_or_else(|| default_name(&url));
    let mut entry = Map::new();
    entry.insert("name".into(), json!(shown));
    entry.insert("url".into(), json!(url));
    if background {
        entry.insert("background".into(), json!(true));
    }
    entry.insert("engine".into(), json!(eng));
    if let Some(b) = browser {
        entry.insert("browser".into(), json!(b));
    }
    if !data.get("apps").is_some_and(Value::is_object) {
        data.insert("apps".into(), json!({}));
    }
    data["apps"].as_object_mut().map(|a| a.insert(app.clone(), Value::Object(entry)));
    save(&data);
    let _ = std::fs::create_dir_all(data_dir(&app));
    update_theme_icon(&app);
    write_desktop(&app, command);
    if fetch {
        let (a, c) = (app.clone(), command.map(String::from));
        std::thread::spawn(move || {
            if fetch_icon(&a, &http_get) {
                write_desktop(&a, c.as_deref());
            }
        });
    }
    Ok(app)
}

/// What the form changes; None: left as it is.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Changes {
    pub name: Option<String>,
    pub url: Option<String>,
    pub background: Option<bool>,
    pub engine: Option<String>,
    pub browser: Option<String>,
}

/// Edit a web app (its form): name, address, keep running, engine. Its
/// login stays (each engine keeps its own: switching asks to sign in once).
pub fn update(app: &str, changes: &Changes, command: Option<&str>) -> bool {
    let mut data = load();
    let Some(entry) = data.get_mut("apps").and_then(|a| a.get_mut(app)).and_then(Value::as_object_mut) else { return false };
    if let Some(n) = changes.name.as_deref().map(str::trim).filter(|n| !n.is_empty()) {
        entry.insert("name".into(), json!(n));
    }
    if let Some(u) = changes.url.as_deref().and_then(normalize_url) {
        entry.insert("url".into(), json!(u));
    }
    match changes.background {
        Some(true) => {
            entry.insert("background".into(), json!(true));
        }
        Some(false) => {
            entry.remove("background");
        }
        None => {}
    }
    if let Some(e) = changes.engine.as_deref().filter(|e| ENGINES.contains(e)) {
        entry.insert("engine".into(), json!(e));
    }
    if let Some(b) = changes.browser.as_deref().filter(|b| is_browser_key(b)) {
        entry.insert("browser".into(), json!(b));
    }
    save(&data);
    update_theme_icon(app); // a new name may have its own icon in the pack
    write_desktop(app, command);
    true
}

pub fn rename(app: &str, name: &str) {
    update(app, &Changes { name: Some(name.into()), ..Default::default() }, None);
}

/// A picture the user chose: copied next to the web app's data (the
/// original may move) and set as its icon in App Icons (icons.json).
pub fn set_custom_icon(app: &str, picture: &Path) -> std::io::Result<PathBuf> {
    let ext = picture.extension().map(|e| format!(".{}", e.to_string_lossy().to_lowercase())).unwrap_or_else(|| ".png".into());
    let dir = data_dir(app);
    std::fs::create_dir_all(&dir)?;
    for old in std::fs::read_dir(&dir)?.flatten() {
        if old.file_name().to_string_lossy().starts_with("custom-icon") {
            let _ = std::fs::remove_file(old.path());
        }
    }
    let dest = dir.join(format!("custom-icon{ext}"));
    std::fs::copy(picture, &dest)?;
    set_app_pref(&desktop_id(app), &[("source", Some(json!("file"))), ("path", Some(json!(dest.to_string_lossy())))]);
    Ok(dest)
}

/// "w" and ten hex digits: never a path from elsewhere.
fn is_own_id(app: &str) -> bool {
    app.len() == 11 && app.starts_with('w') && app[1..].chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
}

/// The web app, its desktop entry and its data (login, storage).
pub fn remove(app: &str) {
    let mut data = load();
    if let Some(a) = data.get_mut("apps").and_then(Value::as_object_mut) {
        a.remove(app);
    }
    save(&data);
    let _ = std::fs::remove_file(applications_dir().join(format!("{}.desktop", desktop_id(app))));
    if is_own_id(app) {
        let _ = std::fs::remove_dir_all(data_dir(app));
        set_app_pref(&desktop_id(app), &[("source", None), ("path", None), ("name", None), ("shape", None), ("scale", None)]);
    }
}

// -- the site's icon ----------------------------------------------------------------------------

/// The attributes of each <link> tag in a page (html.parser's, enough for
/// icons: names lowercase, values unquoted and unescaped, the last of a name wins).
fn link_tags(html: &str) -> Vec<Map<String, Value>> {
    let b = html.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(off) = html[i..].find('<') {
        i += off + 1;
        let name_end = html[i..].find(|c: char| !c.is_ascii_alphanumeric()).map_or(html.len(), |e| i + e);
        if !html[i..name_end].eq_ignore_ascii_case("link") {
            continue;
        }
        let mut attrs = Map::new();
        let mut j = name_end;
        loop {
            while j < b.len() && (b[j].is_ascii_whitespace() || b[j] == b'/') {
                j += 1;
            }
            if j >= b.len() || b[j] == b'>' {
                break;
            }
            let start = j;
            while j < b.len() && !b[j].is_ascii_whitespace() && !matches!(b[j], b'=' | b'>' | b'/') {
                j += 1;
            }
            let key = html[start..j].to_ascii_lowercase();
            while j < b.len() && b[j].is_ascii_whitespace() {
                j += 1;
            }
            let mut value = Value::Null;
            if j < b.len() && b[j] == b'=' {
                j += 1;
                while j < b.len() && b[j].is_ascii_whitespace() {
                    j += 1;
                }
                if j < b.len() && (b[j] == b'"' || b[j] == b'\'') {
                    let q = b[j];
                    let end = html[j + 1..].find(q as char).map_or(html.len(), |e| j + 1 + e);
                    value = json!(unescape(&html[j + 1..end]));
                    j = (end + 1).min(html.len());
                } else {
                    let start = j;
                    while j < b.len() && !b[j].is_ascii_whitespace() && b[j] != b'>' {
                        j += 1;
                    }
                    value = json!(unescape(&html[start..j]));
                }
            }
            if !key.is_empty() {
                attrs.insert(key, value);
            } else {
                j += 1;
            }
        }
        out.push(attrs);
        i = j.min(html.len());
    }
    out
}

/// html.unescape for the entities hrefs carry.
fn unescape(s: &str) -> String {
    if !s.contains('&') {
        return s.to_string();
    }
    let mut out = String::new();
    let mut rest = s;
    while let Some(at) = rest.find('&') {
        out.push_str(&rest[..at]);
        rest = &rest[at..];
        let end = rest.find(';').filter(|e| *e <= 10);
        let ent = end.map(|e| &rest[1..e]);
        let ch = ent.and_then(|e| match e {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            _ => {
                let n = e.strip_prefix("#x").or_else(|| e.strip_prefix("#X")).map(|h| u32::from_str_radix(h, 16)).or_else(|| e.strip_prefix('#').map(|d| d.parse::<u32>()))?;
                n.ok().and_then(char::from_u32)
            }
        });
        match (ch, end) {
            (Some(c), Some(e)) => {
                out.push(c);
                rest = &rest[e + 1..];
            }
            _ => {
                out.push('&');
                rest = &rest[1..];
            }
        }
    }
    out.push_str(rest);
    out
}

/// urllib.parse.urljoin (GLib's RFC 3986 resolution).
fn urljoin(base: &str, href: &str) -> String {
    glib::Uri::resolve_relative(Some(base), href, glib::UriFlags::NONE).map(|s| s.to_string()).unwrap_or_else(|_| href.to_string())
}

/// The page's icons, biggest first, then /favicon.ico.
pub fn icon_candidates(page_url: &str, html: &str) -> Vec<String> {
    let mut found: Vec<(i64, String)> = Vec::new();
    for a in link_tags(html) {
        let get = |k: &str| a.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let rel: Vec<String> = get("rel").to_lowercase().split_whitespace().map(String::from).collect();
        let href = get("href");
        let touch = rel.iter().any(|r| r == "apple-touch-icon" || r == "apple-touch-icon-precomposed");
        if href.is_empty() || !(touch || rel.iter().any(|r| r == "icon")) {
            continue;
        }
        let mut size = 0;
        for s in get("sizes").split_whitespace() {
            let digits: String = s.chars().take_while(char::is_ascii_digit).collect();
            let rest = &s[digits.len()..];
            if !digits.is_empty() && rest.starts_with('x') && rest[1..].starts_with(|c: char| c.is_ascii_digit()) {
                size = size.max(digits.parse().unwrap_or(0));
            }
        }
        if touch && size == 0 {
            size = 180;
        }
        if (get("type").ends_with("svg") || href.split('?').next().unwrap_or("").ends_with(".svg")) && size == 0 {
            size = 512;
        }
        found.push((size, href));
    }
    found.sort_by_key(|f| -f.0); // stable: the page's order among equals
    let mut urls: Vec<String> = found.iter().map(|(_, h)| urljoin(page_url, h)).collect();
    urls.push(urljoin(page_url, "/favicon.ico"));
    let mut out: Vec<String> = Vec::new();
    for u in urls {
        if !out.contains(&u) {
            out.push(u);
        }
    }
    out
}

/// One of libsoup 3's functions, the library opened at run time (as WebKit
/// is: one binary, every library -- docs/RUST_PORT.md, risks).
pub(crate) fn soup_sym(name: &CStr) -> Option<*mut c_void> {
    static LIB: OnceLock<usize> = OnceLock::new();
    // SAFETY: dlopen / dlsym with valid C strings; the library stays loaded
    unsafe {
        let lib = *LIB.get_or_init(|| libc::dlopen(c"libsoup-3.0.so.0".as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) as usize);
        if lib == 0 {
            return None;
        }
        let f = libc::dlsym(lib as *mut c_void, name.as_ptr());
        (!f.is_null()).then_some(f)
    }
}

/// The page or picture at `url` (urllib's GET with Sonata's user agent, at
/// most 2 MB; None on any error, an HTTP error too). Blocking: a thread's.
pub fn http_get(url: &str) -> Option<Vec<u8>> {
    type New = unsafe extern "C" fn() -> *mut glib::gobject_ffi::GObject;
    type SetStr = unsafe extern "C" fn(*mut glib::gobject_ffi::GObject, *const std::ffi::c_char);
    type SetUint = unsafe extern "C" fn(*mut glib::gobject_ffi::GObject, u32);
    type MsgNew = unsafe extern "C" fn(*const std::ffi::c_char, *const std::ffi::c_char) -> *mut glib::gobject_ffi::GObject;
    type Send = unsafe extern "C" fn(*mut glib::gobject_ffi::GObject, *mut glib::gobject_ffi::GObject, *mut c_void, *mut *mut glib::ffi::GError) -> *mut glib::ffi::GBytes;
    type Status = unsafe extern "C" fn(*mut glib::gobject_ffi::GObject) -> u32;
    const LIMIT: usize = 2 << 20;
    // SAFETY: libsoup 3's own signatures (soup-session.h, soup-message.h); every
    // object made here is unreffed before returning
    unsafe {
        let session_new: New = std::mem::transmute(soup_sym(c"soup_session_new")?);
        let set_ua: SetStr = std::mem::transmute(soup_sym(c"soup_session_set_user_agent")?);
        let set_timeout: SetUint = std::mem::transmute(soup_sym(c"soup_session_set_timeout")?);
        let msg_new: MsgNew = std::mem::transmute(soup_sym(c"soup_message_new")?);
        let send: Send = std::mem::transmute(soup_sym(c"soup_session_send_and_read")?);
        let status: Status = std::mem::transmute(soup_sym(c"soup_message_get_status")?);
        let c_url = CString::new(url).ok()?;
        let ua = CString::new(UA).ok()?;
        let msg = msg_new(c"GET".as_ptr(), c_url.as_ptr());
        if msg.is_null() {
            return None;
        }
        let session = session_new();
        set_ua(session, ua.as_ptr());
        set_timeout(session, FETCH_TIMEOUT);
        let mut err: *mut glib::ffi::GError = std::ptr::null_mut();
        let bytes = send(session, msg, std::ptr::null_mut(), &mut err);
        let code = status(msg);
        glib::gobject_ffi::g_object_unref(msg as *mut _);
        glib::gobject_ffi::g_object_unref(session as *mut _);
        if !err.is_null() {
            glib::ffi::g_error_free(err);
        }
        if bytes.is_null() {
            return None;
        }
        let mut len = 0usize;
        let ptr = glib::ffi::g_bytes_get_data(bytes, &mut len) as *const u8;
        let data = if ptr.is_null() { Vec::new() } else { std::slice::from_raw_parts(ptr, len.min(LIMIT)).to_vec() };
        glib::ffi::g_bytes_unref(bytes);
        (200..400).contains(&code).then_some(data)
    }
}

/// Any picture GdkPixbuf reads (png, ico, svg...) -> icon.png (at most 256 px).
pub fn save_icon(app: &str, raw: &[u8]) -> bool {
    use gtk::gdk_pixbuf::prelude::*;
    use gtk::gdk_pixbuf::{InterpType, Pixbuf, PixbufLoader};
    let loader = PixbufLoader::new();
    if loader.write(raw).is_err() || loader.close().is_err() {
        return false;
    }
    let Some(mut pix) = loader.pixbuf() else { return false };
    let side = pix.width().max(pix.height());
    if side > 256 {
        match pix.scale_simple(pix.width() * 256 / side, pix.height() * 256 / side, InterpType::Bilinear) {
            Some(p) => pix = p,
            None => return false,
        }
    }
    let old = icon_path(app);
    if old.is_file() {
        if let Some((_, w, _)) = Pixbuf::file_info(&old) {
            if w >= pix.width() {
                return false; // the one we have is as good (never a smaller one)
            }
        }
    }
    let Ok(png) = pix.save_to_bufferv("png", &[]) else { return false };
    sonata_config::atomic_write(&old, &png[..], false).is_ok()
}

/// The site's own icon, fetched with `download` (http_get).
pub fn fetch_icon(app: &str, download: &dyn Fn(&str) -> Option<Vec<u8>>) -> bool {
    let Some(entry) = get(app) else { return false };
    let url = text(&entry, "url").to_string();
    let html = download(&url).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
    icon_candidates(&url, &html).iter().any(|c| download(c).is_some_and(|raw| save_icon(app, &raw)))
}

// -- other processes ----------------------------------------------------------------------------

fn spawn(args: &[&str]) {
    use std::os::unix::process::CommandExt;
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("sonata2-rs"));
    let mut cmd = std::process::Command::new(exe);
    cmd.arg("webapp").args(args).stdin(std::process::Stdio::null());
    // SAFETY: setsid is async-signal-safe (start_new_session)
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    if let Ok(c) = cmd.spawn() {
        sonata_backend::run::reap(c); // (else a zombie in the Dock once it quits)
    }
}

/// The "New Web App" form, in a process of its own (it takes the keyboard;
/// the Dock and the Desktop can't).
pub fn open_new() {
    spawn(&["new"]);
}

pub fn launch(app: &str) {
    spawn(&[app]);
}

/// The form, filled in (its own process: it takes the keyboard).
pub fn edit(app: &str) {
    spawn(&["edit", app]);
}

/// Every web app's desktop entry (the Dock at login: Sonata's command may
/// have moved). Nothing written on trial (see write_desktop).
pub fn write_all(command: Option<&str>) {
    for app in apps().keys() {
        fix_moved_icon(app);
        update_theme_icon(app); // (an icon pack installed or changed since)
        write_desktop(app, command);
    }
}

// -- the memory cap -----------------------------------------------------------------------------
pub const SCOPED_ENV: &str = "SONATA_WEBAPP_SCOPED";
// A web app (with its WebKit processes) runs in a systemd scope whose memory
// cap follows what the computer has free: the cap is what it uses plus what
// is free, less RESERVE_MB for the rest of the system, never under FLOOR_MB.
// A site that runs away (WhatsApp decoding a video: 50 GB) is killed alone
// instead of freezing the computer; with memory to spare it is never in the way.
pub const RESERVE_MB: i64 = 1536;
pub const FLOOR_MB: i64 = 1024;
pub const GUARD_S: u32 = 2;
/// smaller moves are not written (no cgroup write every tick)
pub const STEP_MB: i64 = 128;

/// MemAvailable in MB, from /proc/meminfo.
fn mem_available(path: &Path) -> Option<i64> {
    let t = std::fs::read_to_string(path).ok()?;
    t.lines().find_map(|l| l.strip_prefix("MemAvailable:")).and_then(|v| v.split_whitespace().next()?.parse::<i64>().ok()).map(|kb| kb / 1024)
}

/// The web app's cap in MB: its use plus what is free, less the reserve.
pub fn memory_cap(used_mb: i64, available_mb: i64) -> i64 {
    FLOOR_MB.max(used_mb + available_mb - RESERVE_MB)
}

fn own_cgroup() -> PathBuf {
    let t = std::fs::read_to_string("/proc/self/cgroup").unwrap_or_default();
    t.lines().find_map(|l| l.strip_prefix("0::")).map(|p| PathBuf::from(format!("/sys/fs/cgroup{}", p.trim()))).unwrap_or_default()
}

/// Moves the scope's memory.max with the free memory (every GUARD_S).
pub struct MemoryGuard {
    pub cgroup: PathBuf,
    pub last: Option<i64>,
}

impl MemoryGuard {
    pub fn new(cgroup: Option<PathBuf>) -> Self {
        MemoryGuard { cgroup: cgroup.unwrap_or_else(own_cgroup), last: None }
    }

    pub fn ok(&self) -> bool {
        let max = self.cgroup.join("memory.max");
        let c = CString::new(max.as_os_str().as_encoded_bytes()).unwrap_or_default();
        // SAFETY: a valid C string
        self.cgroup.to_string_lossy().ends_with(".scope") && unsafe { libc::access(c.as_ptr(), libc::W_OK) } == 0
    }

    pub fn tick(&mut self) -> bool {
        self.tick_with(mem_available(Path::new("/proc/meminfo")))
    }

    pub(crate) fn tick_with(&mut self, available: Option<i64>) -> bool {
        let Some(used) = std::fs::read_to_string(self.cgroup.join("memory.current")).ok().and_then(|t| t.trim().parse::<i64>().ok()).map(|b| b / (1024 * 1024)) else {
            return false;
        };
        let Some(avail) = available else { return true };
        let cap = memory_cap(used, avail);
        if self.last.is_none_or(|l| (cap - l).abs() >= STEP_MB) {
            if std::fs::write(self.cgroup.join("memory.max"), (cap * 1024 * 1024).to_string()).is_err() {
                return false;
            }
            self.last = Some(cap);
        }
        true
    }

    pub fn start(mut self) -> bool {
        if !self.ok() || !self.tick() {
            return false;
        }
        glib::timeout_add_seconds_local(GUARD_S, move || if self.tick() { glib::ControlFlow::Continue } else { glib::ControlFlow::Break });
        true
    }
}

// WebKit's video through the GPU's decoders failed ("Media failed to decode":
// WhatsApp could not read a screen recording to send it); web apps decode
// video on the CPU. GStreamer's ranks: 0 = never picked.
pub const HW_VIDEO_DECODERS: [&str; 15] = [
    "nvh264dec", "nvh265dec", "nvav1dec", "nvvp9dec", "nvvp8dec", "nvmpeg2videodec", "vulkanh264dec", "vulkanh265dec", "vulkanav1dec", "vah264dec", "vah265dec", "vaav1dec", "vavp9dec", "vavp8dec", "vampeg2dec",
];

/// GST_PLUGIN_FEATURE_RANK with the GPU decoders off (what was set stays).
pub fn gst_ranks(existing: &str) -> String {
    let parts: Vec<&str> = existing.split(',').filter(|p| !p.is_empty()).collect();
    let have: Vec<&str> = parts.iter().map(|p| p.split(':').next().unwrap_or("")).collect();
    let mut out: Vec<String> = parts.iter().map(|p| p.to_string()).collect();
    out.extend(HW_VIDEO_DECODERS.iter().filter(|d| !have.contains(d)).map(|d| format!("{d}:0")));
    out.join(",")
}

/// The command that runs this web app in a systemd scope of its own.
pub fn scoped_command(argv: &[String], exe: &str) -> Vec<String> {
    let mut cmd: Vec<String> = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=sonata-apps.slice", "-p", "MemorySwapMax=0"].iter().map(|s| s.to_string()).collect();
    cmd.push(exe.into());
    cmd.push("webapp".into());
    cmd.extend(argv.iter().cloned());
    cmd
}

/// A web app (not its form) not yet in its scope goes there first.
pub fn needs_scope(argv: &[String], scoped: bool, has_systemd_run: bool) -> bool {
    argv.first().is_some_and(|a| a != "new" && a != "edit") && !scoped && has_systemd_run
}

/// `sonata2-rs webapp new | edit <id> | <id>`.
pub fn run(args: &[String]) -> glib::ExitCode {
    std::env::set_var("GST_PLUGIN_FEATURE_RANK", gst_ranks(&std::env::var("GST_PLUGIN_FEATURE_RANK").unwrap_or_default()));
    let scoped = std::env::var_os(SCOPED_ENV).is_some_and(|v| !v.is_empty());
    if needs_scope(args, scoped, sonata_backend::run::which("systemd-run").is_some()) {
        use std::os::unix::process::CommandExt;
        std::env::set_var(SCOPED_ENV, "1");
        let exe = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into());
        let cmd = scoped_command(args, &exe);
        let _ = std::process::Command::new(&cmd[0]).args(&cmd[1..]).exec(); // no user systemd: run as is
    }
    if std::env::var_os(SCOPED_ENV).is_some_and(|v| !v.is_empty()) {
        MemoryGuard::new(None).start();
    }
    if let Some(app) = args.first().filter(|a| *a != "new" && *a != "edit") {
        if engine(get(app).as_ref()) == "chromium" {
            return glib::ExitCode::from(run_chromium(app));
        }
    }
    window::main(args)
}

/// Write all of `data` to `fd` (the browser's pipe; a gone browser: dropped).
fn write_fd(fd: i32, mut data: &[u8]) {
    while !data.is_empty() {
        // SAFETY: a valid buffer; fd is ours
        let n = unsafe { libc::write(fd, data.as_ptr() as *const c_void, data.len()) };
        if n <= 0 {
            if n < 0 && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return;
        }
        data = &data[n as usize..];
    }
}

/// The browser as the web app's window; this process stays (its scope's
/// memory guard) until the browser is gone.
pub fn run_chromium(app: &str) -> i32 {
    use std::cell::{Cell, RefCell};
    use std::os::unix::process::CommandExt;
    use std::rc::Rc;
    let Some(entry) = get(app) else { return 1 };
    let Some(browser) = chromium_browser(Some(&entry)) else { return 1 };
    // a Flatpak gets no pipe: `flatpak run` doesn't hand fds 3 and 4 into its
    // sandbox, and a browser asked for a pipe it hasn't got won't start (its
    // other windows then stay in it, not in the default browser)
    let flat = browser.path.starts_with(FLATPAK);
    let mut cmd_line = chromium_command(app, &entry, &browser.path);
    if !flat {
        cmd_line.push("--remote-debugging-pipe".into());
    }
    let mut cmd = std::process::Command::new(&cmd_line[0]);
    cmd.args(&cmd_line[1..]).stdin(std::process::Stdio::null());
    // DevTools' pipe (fd 3: to the browser, fd 4: from it): new tabs and
    // windows go to the default browser (chromeguard.rs)
    let mut fds = [-1i32; 4]; // to_r, to_w, from_r, from_w
    if !flat {
        let (mut to, mut from) = ([0i32; 2], [0i32; 2]);
        // SAFETY: plain pipe / fcntl calls on fresh fds
        unsafe {
            if libc::pipe2(to.as_mut_ptr(), libc::O_CLOEXEC) != 0 || libc::pipe2(from.as_mut_ptr(), libc::O_CLOEXEC) != 0 {
                return 1;
            }
            // kept above 3 and 4 (and closed on exec): the child puts them there itself
            let hi_r = libc::fcntl(to[0], libc::F_DUPFD_CLOEXEC, 20);
            let hi_w = libc::fcntl(from[1], libc::F_DUPFD_CLOEXEC, 20);
            libc::close(to[0]);
            libc::close(from[1]);
            fds = [hi_r, to[1], from[0], hi_w];
        }
        let (to_r, from_w) = (fds[0], fds[3]);
        // SAFETY: dup2 is async-signal-safe
        unsafe {
            cmd.pre_exec(move || {
                if libc::dup2(to_r, 3) < 0 || libc::dup2(from_w, 4) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("sonata2 webapp: {e}");
            return 1;
        }
    };
    let pid = child.id() as i32;
    drop(child); // (reaped by the child watch below)
    let wm = window_class(app, &entry);
    if !flat {
        // SAFETY: the child's ends, ours to close
        unsafe {
            libc::close(fds[0]);
            libc::close(fds[3]);
        }
        let (to_w, from_r) = (fds[1], fds[2]);
        let (log_app, wm2) = (app.to_string(), wm.clone());
        // its page's title for the Dock's count (Chrome titles the window with the site's name)
        let guard = chromeguard::Guard::new(text(&entry, "url"), move |b| write_fd(to_w, b))
            .with_log(move |m| println!("sonata2 webapp {log_app}: {m}"))
            .with_name(text(&entry, "name"))
            .with_on_title(move |t| sonata_desktop::badges::publish_page_title(&wm2, Some(t), None));
        let guard = RefCell::new(guard);
        glib::source::unix_fd_add_local(from_r, glib::IOCondition::IN | glib::IOCondition::HUP, move |fd, _| {
            let mut buf = vec![0u8; 65536];
            // SAFETY: a valid buffer of that size
            let n = unsafe { libc::read(fd, buf.as_mut_ptr() as *mut c_void, buf.len()) };
            if n <= 0 {
                return glib::ControlFlow::Break;
            }
            guard.borrow_mut().feed(&buf[..n as usize]);
            glib::ControlFlow::Continue
        });
    }
    let main_loop = glib::MainLoop::new(None, false);
    let last: Rc<Cell<Option<(i32, i32)>>> = Rc::default();
    let key = size_key(app);
    let (l, w2) = (last.clone(), wm.clone());
    let key2 = key.clone();
    // its window's size, kept while it's open (saved once it's gone: Chrome
    // doesn't keep an --app window's size itself)
    let poll = glib::timeout_add_seconds_local(SIZE_POLL_S, move || {
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({}));
        let size = views.as_ref().and_then(|v| view_size(v, &w2));
        if size.is_some() && size != l.get() {
            l.set(size);
            if let Some((w, h)) = size {
                sonata_ui::window::save_size(&key2, w, h, false); // kept now: a restart ends it without a goodbye
            }
        }
        glib::ControlFlow::Continue
    });
    let code = Rc::new(Cell::new(0));
    let poll = RefCell::new(Some(poll));
    let (ml, l, c, key3) = (main_loop.clone(), last.clone(), code.clone(), key.clone());
    glib::child_watch_add_local(glib::Pid(pid), move |_, status| {
        if let Some(p) = poll.borrow_mut().take() {
            sonata_ui::cancel(p);
        }
        sonata_desktop::badges::publish_page_title(&wm, None, None);
        if let Some((w, h)) = l.get() {
            sonata_ui::window::save_size(&key3, w, h, false);
        }
        c.set(if libc::WIFEXITED(status) { libc::WEXITSTATUS(status) } else { 0 });
        ml.quit();
    });
    // closed by Sonata (quit on last window): the size kept, the browser asked to quit
    glib::unix_signal_add_local(libc::SIGTERM, move || {
        if let Some((w, h)) = last.get() {
            sonata_ui::window::save_size(&key, w, h, false);
        }
        // SAFETY: signalling our own child
        unsafe { libc::kill(pid, libc::SIGTERM) };
        glib::ControlFlow::Continue
    });
    main_loop.run();
    code.get()
}

#[cfg(test)]
mod tests;
