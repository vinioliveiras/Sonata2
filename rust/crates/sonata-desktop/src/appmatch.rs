//! A window's app: the desktop id for a Wayland app_id (sonata2/apps.py's
//! match_app_id, refresh). Not in sonata-ui's apps.rs yet; it lives here
//! until it moves there (launchwatch, open_apps and the Dock need it).
//!
//! Exact first (the id itself), then an index of every entry's names:
//! StartupWMClass, the id, its last reverse-DNS part, the executable; then
//! the same without a wrapper suffix ("spotify-launcher" -> "spotify"),
//! only where no entry claims that name exactly.
//!
//! Not ported: apps.py's look through webapps.json for a Chromium web
//! app's window class -- the web app's own entry carries that class as its
//! StartupWMClass, which the index finds.

use gio::prelude::*;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// An unknown app_id rebuilds the index at most this often.
pub const INDEX_RETRY: Duration = Duration::from_secs(5);
/// app_id -> when it last matched nothing. A Steam game or a Wine program
/// never has a .desktop: the Dock asks on every window event. Retried after this.
pub const MISS_RETRY: Duration = Duration::from_secs(60);
/// A match is kept a while too: each one read and parsed its .desktop again.
pub const HIT_KEEP: Duration = Duration::from_secs(10);
/// A desktop entry that starts a launcher or wrapper: its window has the
/// app's own id (spotify-launcher.desktop -> app_id "spotify").
pub const WRAPPER_SUFFIXES: [&str; 7] = ["-launcher", "-bin", "-wrapper", "-desktop", "-stable", "-wayland", "-x11"];
/// webapps.APP_ID_PREFIX: a Sonata web app's window (WebKit engine).
pub const WEBAPP_APP_ID_PREFIX: &str = "io.github.vinioliveiras.sonata2.webapp.";

#[derive(Default)]
struct State {
    index: Option<(HashMap<String, String>, Instant)>,
    misses: HashMap<String, Instant>,
    hits: HashMap<String, (String, Instant)>,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

/// One entry's names for the index: (desktop id without .desktop, shown,
/// StartupWMClass, executable's file name).
pub struct Entry {
    pub id: String,
    pub shown: bool,
    pub wm_class: String,
    pub exe: String,
}

/// Lower-cased app_id candidates -> desktop id (without .desktop). Exact
/// names first; then the same without a wrapper suffix -- only where no
/// entry claims that name exactly.
pub fn build_index(mut entries: Vec<Entry>) -> HashMap<String, String> {
    // entries meant to be shown claim their names first: Vini's Chrome got a
    // second Dock icon -- an update added a hidden com.google.Chrome.desktop
    // (NoDisplay) with the same window class, and it was listed first
    entries.sort_by_key(|e| !e.shown);
    let mut idx: HashMap<String, String> = HashMap::new();
    let mut loose: Vec<(String, String)> = Vec::new();
    for e in &entries {
        let mut keys = vec![e.id.clone(), e.id.rsplit('.').next().unwrap_or("").to_string()];
        if !e.wm_class.is_empty() {
            keys.insert(0, e.wm_class.clone());
        }
        if !e.exe.is_empty() {
            keys.push(e.exe.clone());
        }
        for k in keys {
            let low = k.to_lowercase();
            idx.entry(low.clone()).or_insert_with(|| e.id.clone());
            for suf in WRAPPER_SUFFIXES {
                if low.ends_with(suf) && k.len() > suf.len() {
                    loose.push((low[..low.len() - suf.len()].to_string(), e.id.clone()));
                }
            }
        }
    }
    for (k, did) in loose {
        idx.entry(k).or_insert(did);
    }
    idx
}

fn entries() -> Vec<Entry> {
    gio::AppInfo::all()
        .into_iter()
        .filter_map(|info| {
            let did = info.id()?.to_string();
            let id = did.strip_suffix(".desktop")?.to_string();
            let desk = info.downcast_ref::<gio::DesktopAppInfo>();
            let wm_class = desk.and_then(|d| d.startup_wm_class()).map(|s| s.to_string()).unwrap_or_default();
            // the program Exec starts, its file name
            let exe = info.executable().to_string_lossy().into_owned();
            let exe = exe.split(' ').next().unwrap_or("").rsplit('/').next().unwrap_or("").to_string();
            Some(Entry { id, shown: info.should_show(), wm_class, exe })
        })
        .collect()
}

fn find(idx: &HashMap<String, String>, a: &str) -> Option<String> {
    idx.get(a).or_else(|| idx.get(a.rsplit('.').next().unwrap_or(""))).cloned()
}

fn known(did: &str) -> bool {
    sonata_ui::apps::lookup(did).is_some()
}

/// Desktop id (without .desktop) for a Wayland app_id, or None when no
/// .desktop matches.
pub fn match_app_id(app_id: &str) -> Option<String> {
    if app_id.is_empty() {
        return None;
    }
    {
        let mut st = STATE.lock().unwrap_or_else(|e| e.into_inner());
        let st = st.get_or_insert_with(State::default);
        if st.misses.get(app_id).is_some_and(|t| t.elapsed() < MISS_RETRY) {
            return None;
        }
        if let Some((did, t)) = st.hits.get(app_id) {
            if t.elapsed() < HIT_KEEP {
                return Some(did.clone());
            }
        }
    }
    let found = lookup_match(app_id);
    let mut st = STATE.lock().unwrap_or_else(|e| e.into_inner());
    let st = st.get_or_insert_with(State::default);
    match &found {
        None => {
            st.misses.insert(app_id.to_string(), Instant::now());
            st.hits.remove(app_id);
        }
        Some(did) => {
            st.misses.remove(app_id);
            st.hits.insert(app_id.to_string(), (did.clone(), Instant::now()));
        }
    }
    found
}

fn lookup_match(app_id: &str) -> Option<String> {
    if known(app_id) {
        return Some(app_id.strip_suffix(".desktop").unwrap_or(app_id).to_string());
    }
    // a Sonata web app: its own entry, by its id
    if let Some(wid) = app_id.strip_prefix(WEBAPP_APP_ID_PREFIX) {
        let did = format!("{}{wid}", crate::badges::WEBAPP_DESKTOP_PREFIX);
        if known(&did) {
            return Some(did);
        }
    }
    let a = app_id.to_lowercase();
    let current = {
        let mut st = STATE.lock().unwrap_or_else(|e| e.into_inner());
        st.get_or_insert_with(State::default).index.clone()
    };
    let (mut idx, mut at) = current.unwrap_or_else(|| (build_index(entries()), Instant::now()));
    let mut found = find(&idx, &a);
    if found.as_deref().is_some_and(|f| !known(f)) {
        // an entry gone since the index was built (Vini: a web app made again
        // has a new id -- the menu bar named WhatsApp by the old entry's id):
        // built again now
        (idx, at) = (build_index(entries()), Instant::now());
        found = find(&idx, &a).filter(|f| known(f));
    }
    if found.is_none() && at.elapsed() > INDEX_RETRY {
        // an app installed since the index was built (Vini: a new web app's
        // window had no icon or name in the Dock): built again, once in a while
        (idx, at) = (build_index(entries()), Instant::now());
        found = find(&idx, &a);
    }
    let mut st = STATE.lock().unwrap_or_else(|e| e.into_inner());
    st.get_or_insert_with(State::default).index = Some((idx, at));
    found
}

/// Forget the app_id index (call when apps are installed/removed).
pub fn refresh() {
    *STATE.lock().unwrap_or_else(|e| e.into_inner()) = None;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn e(id: &str, shown: bool, wm: &str, exe: &str) -> Entry {
        Entry { id: id.into(), shown, wm_class: wm.into(), exe: exe.into() }
    }

    #[test]
    fn test_index_names() {
        let idx = build_index(vec![e("org.mozilla.firefox", true, "firefox", "firefox"), e("spotify-launcher", true, "", "spotify-launcher"), e("org.gnome.Nautilus", true, "", "nautilus")]);
        assert_eq!(find(&idx, "firefox").as_deref(), Some("org.mozilla.firefox"));
        assert_eq!(find(&idx, "nautilus").as_deref(), Some("org.gnome.Nautilus"));
        assert_eq!(find(&idx, "org.gnome.nautilus").as_deref(), Some("org.gnome.Nautilus"));
        assert_eq!(find(&idx, "spotify").as_deref(), Some("spotify-launcher")); // the wrapper's app
        assert_eq!(find(&idx, "steam_app_1"), None);
    }

    /// Vini's Chrome got a second Dock icon: a hidden entry with the same
    /// window class, listed first, took the name.
    #[test]
    fn test_hidden_entry_took_the_window_class() {
        let idx = build_index(vec![e("com.google.Chrome", false, "Google-chrome", ""), e("google-chrome", true, "Google-chrome", "")]);
        assert_eq!(find(&idx, "google-chrome").as_deref(), Some("google-chrome"));
    }

    #[test]
    fn test_exact_name_beats_a_wrapper() {
        let idx = build_index(vec![e("foo-bin", true, "", ""), e("foo", true, "", "")]);
        assert_eq!(find(&idx, "foo").as_deref(), Some("foo"));
    }

    #[test]
    fn test_empty_matches_nothing() {
        assert_eq!(match_app_id(""), None);
    }
}
