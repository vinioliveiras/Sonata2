//! What a notification is and what happens to it, without a widget: the
//! list Notification Center keeps, the per-app settings (Settings >
//! Notifications), the web apps' and locked folders' rules, the list kept
//! for a restarted menu bar. The server (server.rs) drives it; the tests
//! use it directly.

use regex::Regex;
use serde_json::{json, Map, Value};
use sonata_desktop::badges;
use std::cell::RefCell;
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::OnceLock;

pub const BANNER_W: i32 = 344;
pub const BANNER_MS: u32 = 5000;
pub const MAX_BANNERS: usize = 3;
/// kept in the Notification Center
pub const MAX_NOTES: usize = 100;
/// the newest ones it shows (a card each)
pub const SHOWN: usize = 40;
/// a notification's picture: a square this size (macOS), every card alike
pub const THUMB: i32 = 40;
pub const BODY_LINES: i32 = 3;
/// Notification Center's column slides in from off the right edge: its
/// width and its margins (CSS `nc_slide`)
pub const NC_SLIDE_PX: i32 = BANNER_W + 40;
/// in the runtime folder: this session only
pub const HISTORY: &str = "sonata2-notification-center.json";
/// notifications.json
pub const CONFIG: &str = "notifications";
pub const BROWSERS: [&str; 6] = ["chrome", "chromium", "brave", "edge", "vivaldi", "opera"];

/// The reasons of NotificationClosed.
pub const EXPIRED: u32 = 1;
pub const DISMISSED: u32 = 2;
pub const CLOSED_BY_APP: u32 = 3;

pub fn defaults() -> Value {
    json!({"dnd": false, "badges": true, "apps": {}})
}

/// Per app (Settings > Notifications): key = desktop entry or app name.
pub fn app_defaults() -> Map<String, Value> {
    match json!({"name": "", "allow": true, "style": "banners", "center": true, "sound": true, "badge": true}) {
        Value::Object(m) => m,
        _ => Map::new(),
    }
}

/// The menu bar's height (shell/menubar_size.py; the menu bar's crate has
/// its own copy, this one is what the banners and Notification Center sit
/// under): 32 px by default, 24 px with "Taller menu bar" off.
pub fn menubar_height() -> i32 {
    let cfg = sonata_config::load("topbar", &json!({"tall": true}));
    if cfg.get("tall").and_then(Value::as_bool).unwrap_or(true) {
        32
    } else {
        24
    }
}

pub fn app_key(desktop: &str, app_name: &str) -> String {
    (if !desktop.is_empty() { desktop } else { app_name }).trim().to_string()
}

/// One app's settings, its defaults filled in.
pub fn app_settings(cfg: &Map<String, Value>, key: &str) -> Map<String, Value> {
    let mut out = app_defaults();
    if let Some(Value::Object(own)) = cfg.get("apps").and_then(|a| a.get(key)) {
        for (k, v) in own {
            out.insert(k.clone(), v.clone());
        }
    }
    out
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

// -- a notification ------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Note {
    pub id: u32,
    pub app: String,
    pub icon: String,
    pub summary: String,
    pub body: String,
    /// (key, label)
    pub actions: Vec<(String, String)>,
    pub desktop: String,
    pub urgency: i64,
    pub timeout: i32,
    /// picture shown on the card's right (screenshots, album art)
    pub image: String,
    /// when it came, seconds since the epoch
    pub at: f64,
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

impl Note {
    pub fn new(id: u32, app: &str, summary: &str, body: &str) -> Self {
        Note {
            id,
            app: app.into(),
            icon: String::new(),
            summary: summary.into(),
            body: body.into(),
            actions: Vec::new(),
            desktop: String::new(),
            urgency: 1,
            timeout: -1,
            image: String::new(),
            at: now(),
        }
    }

    /// As the Python Note's asdict(): the history file is read by either
    /// side's menu bar, so exactly its keys.
    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id, "app": self.app, "icon": self.icon, "summary": self.summary, "body": self.body,
            "actions": self.actions.iter().map(|(k, l)| json!([k, l])).collect::<Vec<_>>(),
            "desktop": self.desktop, "urgency": self.urgency, "timeout": self.timeout, "image": self.image, "at": self.at,
        })
    }

    pub fn from_json(v: &Value) -> Option<Self> {
        let o = v.as_object()?;
        let s = |k: &str| o.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let actions = o
            .get("actions")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|p| {
                        let p = p.as_array()?;
                        Some((p.first()?.as_str()?.to_string(), p.get(1)?.as_str()?.to_string()))
                    })
                    .collect()
            })
            .unwrap_or_default();
        Some(Note {
            id: u32::try_from(o.get("id")?.as_u64()?).ok()?,
            app: s("app"),
            icon: s("icon"),
            summary: s("summary"),
            body: s("body"),
            actions,
            desktop: s("desktop"),
            urgency: o.get("urgency").and_then(Value::as_i64).unwrap_or(1),
            timeout: o.get("timeout").and_then(Value::as_i64).and_then(|t| i32::try_from(t).ok()).unwrap_or(-1),
            image: s("image"),
            at: o.get("at").and_then(Value::as_f64).unwrap_or_else(now),
        })
    }

    pub fn has_default(&self) -> bool {
        self.actions.iter().any(|(k, _)| k == "default")
    }
}

// -- a card's text -------------------------------------------------------------------------

fn host_line() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^\s*(?:<a [^>]*>)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:</a>)?\s*(?:\n|$)").unwrap())
}

/// (body, actions) as a card shows them: no blank lines; a web app's
/// without the browser's site line ("web.whatsapp.com"); no browser's own
/// Settings button (Vini: not needed -- Settings > Notifications is ours).
pub fn tidy(body: &str, actions: &[(String, String)], web: bool, browser: bool) -> (String, Vec<(String, String)>) {
    let body = if web { host_line().replace(body, "").into_owned() } else { body.to_string() };
    let actions = if web || browser {
        actions.iter().filter(|(k, l)| k.to_lowercase() != "settings" && l.to_lowercase() != "settings").cloned().collect()
    } else {
        actions.to_vec()
    };
    let lines: Vec<&str> = body.split(['\n', '\r']).filter(|l| !l.trim().is_empty()).collect();
    (lines.join("\n"), actions)
}

/// "now", "5m ago", "2h ago", else the day ("04 Nov").
pub fn ago(t: f64, now: f64) -> String {
    let s = now - t;
    if s < 60.0 {
        return sonata_i18n::tr!("now");
    }
    if s < 3600.0 {
        return sonata_i18n::fmt(&sonata_i18n::tr!("{n}m ago"), &[("n", &((s / 60.0).floor() as i64).to_string())]);
    }
    if s < 86400.0 {
        return sonata_i18n::fmt(&sonata_i18n::tr!("{n}h ago"), &[("n", &((s / 3600.0).floor() as i64).to_string())]);
    }
    glib::DateTime::from_unix_local(t as i64).ok().and_then(|d| d.format("%d %b").ok()).map(|s| s.to_string()).unwrap_or_default()
}

/// Notification bodies may carry simple markup (<b>, <i>, <a>); keep what
/// Pango understands, escape the rest.
pub fn markup(text: &str) -> String {
    if gtk::pango::parse_markup(text, '\0').is_ok() {
        text.to_string()
    } else {
        glib::markup_escape_text(text).to_string()
    }
}

// -- the list, kept for a restarted menu bar ------------------------------------------------

pub fn history_path() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp")).join(HISTORY)
}

/// Notification Center's list, kept for a menu bar started again during
/// the session (gone at logout, as the list always was).
pub fn save_history(notes: &[Note]) {
    let data = Value::Array(notes.iter().map(Note::to_json).collect());
    let _ = sonata_config::atomic_write(&history_path(), data.to_string().as_bytes(), false);
}

pub fn load_history() -> Vec<Note> {
    let Some(Value::Array(raw)) = std::fs::read(history_path()).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) else {
        return Vec::new();
    };
    let notes: Vec<Note> = raw.iter().filter_map(Note::from_json).collect();
    let skip = notes.len().saturating_sub(MAX_NOTES);
    notes.into_iter().skip(skip).collect()
}

// -- what the server asks of the desktop ----------------------------------------------------

/// What the server looks up outside itself: installed apps, web apps,
/// windows. The real one is [`SystemEnv`]; the tests fake it.
pub trait Env {
    /// an installed app with this desktop id
    fn app_exists(&self, desktop: &str) -> bool {
        self.app_name(desktop).is_some()
    }
    /// its name, None when it isn't installed
    fn app_name(&self, desktop: &str) -> Option<String>;
    /// apps.match_app_id: the desktop id of a window's app-id
    fn match_app_id(&self, app_id: &str) -> Option<String>;
    /// webapps.json's "apps"
    fn webapps(&self) -> Map<String, Value>;
    /// restore and focus the app's window; false when it has none open
    fn bring_forward(&self, desktop: &str, app: &str) -> bool;
    /// open the app
    fn launch(&self, desktop: &str) -> bool;
}

pub struct SystemEnv;

thread_local! {
    static APPS: RefCell<std::collections::HashMap<String, (Option<gio::DesktopAppInfo>, std::time::Instant)>> = RefCell::default();
}

/// apps.lookup, remembered for a minute: a miss walks every app folder,
/// and a card is made for each notification of an app with no entry.
pub fn lookup_app(desktop: &str) -> Option<gio::DesktopAppInfo> {
    if desktop.is_empty() {
        return None;
    }
    APPS.with(|c| {
        let mut c = c.borrow_mut();
        if let Some((info, at)) = c.get(desktop) {
            if at.elapsed() < std::time::Duration::from_secs(60) {
                return info.clone();
            }
        }
        if c.len() > 500 {
            c.clear();
        }
        let info = sonata_ui::apps::lookup(desktop);
        c.insert(desktop.to_string(), (info.clone(), std::time::Instant::now()));
        info
    })
}

impl Env for SystemEnv {
    fn app_exists(&self, desktop: &str) -> bool {
        lookup_app(desktop).is_some()
    }

    fn app_name(&self, desktop: &str) -> Option<String> {
        lookup_app(desktop).map(sonata_ui::apps::name) // ("Spotify", not "Spotify (Launcher)")
    }

    fn match_app_id(&self, app_id: &str) -> Option<String> {
        sonata_desktop::appmatch::match_app_id(app_id)
    }

    fn webapps(&self) -> Map<String, Value> {
        webapps()
    }

    fn bring_forward(&self, desktop: &str, app: &str) -> bool {
        use sonata_backend::wfipc;
        let Some(views) = wfipc::call("window-rules/list-views", json!({})) else { return false };
        let Some(vid) = window_of(&views, desktop, app, self) else { return false };
        wfipc::call("wm-actions/set-minimized", json!({"view_id": vid, "state": false}));
        wfipc::call("window-rules/focus-view", json!({"id": vid}));
        true
    }

    fn launch(&self, desktop: &str) -> bool {
        // Sonata's launch path: the display's context (the app comes to the
        // front), its GPU, its own scope, the app lock -- as in the Dock
        match lookup_app(desktop) {
            Some(info) => sonata_ui::apps::launch(&info, &[], None).is_ok(),
            None => false,
        }
    }
}

// -- web apps (webapps/__init__.py isn't ported: the few lines needed here) ------------------

/// webapps.json's "apps": {id: {"name", "url"...}}.
pub fn webapps() -> Map<String, Value> {
    match sonata_config::load("webapps", &json!({"apps": {}})).remove("apps") {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

/// webapps.desktop_id
pub fn webapp_desktop_id(app: &str) -> String {
    format!("{}{app}", badges::WEBAPP_DESKTOP_PREFIX)
}

fn webapp_name(entries: &Map<String, Value>, app: &str) -> String {
    entries.get(app).and_then(|e| e.get("name")).and_then(Value::as_str).unwrap_or("").to_string()
}

/// (desktop id, name) of the Sonata web app whose process has this
/// command line (WhatsApp notifies as "Google Chrome": its badge, icon
/// and click belong to WhatsApp), else None.
pub fn webapp_of_argv<S: AsRef<str>>(argv: &[S], env: &dyn Env) -> Option<(String, String)> {
    let app = badges::webapp_of(argv);
    if app.is_empty() || !env.app_exists(&webapp_desktop_id(&app)) {
        return None;
    }
    Some((webapp_desktop_id(&app), webapp_name(&env.webapps(), &app)))
}

// -- locked folders ------------------------------------------------------------------------

/// Desktop ids of apps kept in a locked folder: the Dock's locked folders
/// and Apps' hidden (locked) ones.
pub fn locked_ids() -> HashSet<String> {
    let mut ids = HashSet::new();
    let mut take = |f: &Value| {
        if f.get("locked").is_some_and(|v| truthy(Some(v))) {
            for a in f.get("apps").and_then(Value::as_array).into_iter().flatten() {
                if let Some(a) = a.as_str() {
                    ids.insert(a.to_string());
                }
            }
        }
    };
    if let Some(Value::Object(folders)) = sonata_config::load("dock", &json!({"folders": {}})).get("folders") {
        folders.values().for_each(&mut take);
    }
    let pad = sonata_config::load("launchpad", &json!({"pages": [], "hidden": []}));
    if let Some(Value::Array(pages)) = pad.get("pages") {
        for page in pages {
            for it in page.as_array().into_iter().flatten() {
                if it.is_object() {
                    take(it);
                }
            }
        }
    }
    for a in pad.get("hidden").and_then(Value::as_array).into_iter().flatten() {
        if let Some(a) = a.as_str() {
            ids.insert(a.to_string());
        }
    }
    ids
}

#[derive(Default)]
struct Locked {
    stamp: Option<Vec<i128>>,
    ids: HashSet<String>,
    names: HashSet<String>,
}

thread_local! {
    static LOCKED: RefCell<Locked> = RefCell::default();
}

/// Read the locked folders again at the next notification (tests).
pub fn forget_locked() {
    LOCKED.with(|l| l.borrow_mut().stamp = None);
}

fn mtime_ns(p: &std::path::Path) -> i128 {
    std::fs::metadata(p).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_nanos() as i128).unwrap_or(0)
}

/// A notification from an app in a locked folder: shown nowhere (Vini) --
/// the folder keeps its apps private. Matched by the sender's desktop
/// entry, else by the app's name.
pub fn locked(desktop: &str, app_name: &str, env: &dyn Env) -> bool {
    let stamp: Vec<i128> = ["dock", "launchpad"].iter().map(|n| mtime_ns(&sonata_config::path(n))).collect();
    LOCKED.with(|l| {
        let mut l = l.borrow_mut();
        if l.stamp.as_ref() != Some(&stamp) {
            // the folders changed: read them again
            let ids = locked_ids();
            let names = ids.iter().filter_map(|i| env.app_name(i)).map(|n| n.to_lowercase()).filter(|n| !n.is_empty()).collect();
            *l = Locked { stamp: Some(stamp), ids: ids.iter().map(|i| i.to_lowercase()).collect(), names };
        }
        let d = desktop.to_lowercase(); // "org.telegram.desktop" is an id itself
        let short = d.strip_suffix(".desktop").unwrap_or(&d).to_string();
        let by_id = [d, short].iter().any(|i| !i.is_empty() && l.ids.contains(i));
        by_id || (!app_name.is_empty() && l.names.contains(&app_name.to_lowercase()))
    })
}

// -- the notifying app's window ------------------------------------------------------------

/// The id of the notifying app's most recently used window in Wayfire's
/// list-views, None when it has none.
pub fn window_of(views: &Value, desktop: &str, app_name: &str, env: &dyn Env) -> Option<i64> {
    let d = desktop.to_lowercase();
    let ds: HashSet<String> = [d.clone(), d.strip_suffix(".desktop").unwrap_or(&d).to_string()].into_iter().filter(|s| !s.is_empty()).collect();
    let name = app_name.to_lowercase();
    let mut best: Option<(&Value, i64)> = None;
    for v in views.as_array().into_iter().flatten() {
        if !v.is_object() || v.get("type").and_then(Value::as_str).unwrap_or("toplevel") != "toplevel" {
            continue;
        }
        let Some(aid) = v.get("app-id").and_then(Value::as_str).filter(|a| !a.is_empty()) else { continue };
        let mine = if !ds.is_empty() {
            ds.contains(&aid.to_lowercase()) || env.match_app_id(aid).is_some_and(|m| ds.contains(&m.to_lowercase()))
        } else if !name.is_empty() {
            env.match_app_id(aid).and_then(|did| env.app_name(&did)).is_some_and(|n| n.to_lowercase() == name)
        } else {
            false
        };
        let ts = v.get("last-focus-timestamp").and_then(Value::as_i64).unwrap_or(0);
        if mine && best.map_or(true, |(_, b)| ts > b) {
            best = Some((v, ts));
        }
    }
    best.and_then(|(v, _)| v.get("id")).and_then(Value::as_i64)
}

// -- the list and its rules ----------------------------------------------------------------

/// Notify's hints, the ones Sonata reads.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Hints {
    /// 0 low, 1 normal, 2 critical; None when not given or not a number
    pub urgency: Option<i64>,
    pub desktop_entry: String,
    pub image_path: String,
}

/// One Notify call.
#[derive(Clone, Debug, Default)]
pub struct Request {
    pub app_name: String,
    pub replaces_id: u32,
    pub app_icon: String,
    pub summary: String,
    pub body: String,
    /// [key, label, key, label...]
    pub actions: Vec<String>,
    pub hints: Hints,
    pub expire_timeout: i32,
}

/// What became of a notification.
#[derive(Clone, Debug, PartialEq)]
pub struct Outcome {
    pub id: u32,
    /// it went into the list (Notification Center)
    pub listed: bool,
    /// its banner, unless Do Not Disturb or Notification Center open
    pub banner: Option<Note>,
}

/// The server's state: the list (newest last), the next id, the settings.
pub struct Core {
    pub notes: Vec<Note>,
    pub next: u32,
    pub cfg: Map<String, Value>,
}

impl Default for Core {
    fn default() -> Self {
        Core { notes: Vec::new(), next: 1, cfg: load_config() }
    }
}

pub fn load_config() -> Map<String, Value> {
    sonata_config::load(CONFIG, &defaults())
}

impl Core {
    pub fn dnd(&self) -> bool {
        truthy(self.cfg.get("dnd"))
    }

    pub fn note(&self, id: u32) -> Option<&Note> {
        self.notes.iter().find(|n| n.id == id)
    }

    pub fn notify(&mut self, r: Request, env: &dyn Env, at: f64) -> Outcome {
        // any id handed out before is replaced in place, listed or not (a banner-only
        // app's updates must not stack, and its CloseNotification must find it)
        let nid = if 0 < r.replaces_id && r.replaces_id < self.next { r.replaces_id } else { self.next };
        if nid == self.next {
            self.next += 1;
        }
        let pairs: Vec<(String, String)> = r.actions.chunks_exact(2).map(|p| (p[0].clone(), p[1].clone())).collect();
        let mut image = r.hints.image_path.clone();
        if image.starts_with("file://") {
            image = glib::filename_from_uri(&image).map(|(p, _)| p.to_string_lossy().into_owned()).unwrap_or_default();
        }
        let mut n = Note {
            id: nid,
            app: r.app_name,
            icon: r.app_icon,
            summary: r.summary,
            body: r.body,
            actions: pairs,
            desktop: r.hints.desktop_entry,
            urgency: r.hints.urgency.unwrap_or(1),
            timeout: r.expire_timeout,
            image: if image.starts_with('/') { image } else { String::new() },
            at,
        };
        let browser = BROWSERS.iter().any(|b| format!("{} {}", n.desktop, n.app).to_lowercase().contains(b));
        if browser && !badges::is_webapp(&n.desktop) {
            // (Vini: WhatsApp's went to Chrome)
            let entries = env.webapps();
            let web = badges::webapp_for_site(&n.body, &entries);
            if !web.is_empty() && env.app_exists(&webapp_desktop_id(&web)) {
                n.desktop = webapp_desktop_id(&web);
                let name = webapp_name(&entries, &web);
                if !name.is_empty() {
                    n.app = name;
                }
            }
        }
        let (body, actions) = tidy(&n.body, &n.actions, badges::is_webapp(&n.desktop), browser);
        n.body = body;
        n.actions = actions;
        if locked(&n.desktop, &n.app, env) {
            // its folder is locked: no banner, not in the list
            return Outcome { id: nid, listed: false, banner: None };
        }
        let key = app_key(&n.desktop, &n.app);
        let per = app_settings(&self.cfg, &key);
        let known = self.cfg.get("apps").and_then(Value::as_object).is_some_and(|a| a.contains_key(&key));
        if !key.is_empty() && !known {
            // listed in Settings > Notifications. From the file, not memory:
            // an older copy saved here turned Do Not Disturb (just set in
            // Settings) back off
            self.cfg = load_config();
            let mut entry = app_defaults();
            entry.insert("name".into(), json!(if n.app.is_empty() { key.clone() } else { n.app.clone() }));
            if !self.cfg.get("apps").is_some_and(Value::is_object) {
                self.cfg.insert("apps".into(), json!({}));
            }
            if let Some(Value::Object(apps)) = self.cfg.get_mut("apps") {
                apps.insert(key.clone(), Value::Object(entry));
            }
            let _ = sonata_config::save(CONFIG, &self.cfg);
        }
        if !truthy(per.get("allow")) {
            return Outcome { id: nid, listed: false, banner: None };
        }
        let listed = truthy(per.get("center"));
        if listed {
            self.notes.retain(|x| x.id != nid);
            self.notes.push(n.clone());
            let skip = self.notes.len().saturating_sub(MAX_NOTES);
            self.notes.drain(..skip);
        }
        let style = per.get("style").and_then(Value::as_str).unwrap_or("banners");
        let banner = (style != "none" && !self.dnd()).then_some(n);
        Outcome { id: nid, listed, banner }
    }

    /// A web app taking its notification back (WhatsApp does as its window
    /// is focused) only hides the banner: it stays in Notification Center
    /// until it is read there -- clicked or dismissed (Vini).
    pub fn kept_when_withdrawn(&self, id: u32) -> bool {
        self.note(id).is_some_and(|n| !n.desktop.is_empty() && badges::is_webapp(&n.desktop))
    }

    /// Out of the list; true when it was there.
    pub fn remove(&mut self, id: u32) -> bool {
        let before = self.notes.len();
        self.notes.retain(|n| n.id != id);
        self.notes.len() != before
    }
}
