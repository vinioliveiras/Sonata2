//! What to call a window's app when no desktop entry matches its app_id
//! (sonata2/windowapps.py) -- one answer for the Dock, the app switcher and
//! the menu bar (Vini: an installer run with Faugus showed as
//! "steam_app_default"; it must work for any app, not only Steam).
//!
//! ```text
//! let (name, icon, dynamic) = describe(app_id, title);
//! ```
//!
//! In order:
//! 1. A Steam game ("steam_app_<id>"): its name and picture from Steam's library.
//! 2. A Windows program run with Proton or Wine outside Steam
//!    ("steam_app_default", "wine", "something.exe"...): a launcher's
//!    shortcut named like the window (Faugus, Lutris, Bottles... make them:
//!    "FL Studio 2026" for the window "FL Studio 2026 - untitled.flp"), else
//!    the window's title -- dynamic: it follows the title as it changes
//!    (the installer, then the program).
//! 3. Any other app: its app_id made readable ("org.example.CoolApp" ->
//!    "CoolApp", "my-tool" -> "My Tool"), and an icon of that name if the
//!    theme has one.
//!
//! `dynamic` is true when the name comes from the window's title: callers
//! ask again when the title changes.

use crate::steamgames;
use gio::prelude::*;
use std::cell::RefCell;
use std::time::{Duration, Instant};

/// app_ids that say nothing about the program behind them: a Windows
/// program run with Proton without a Steam game id, or Wine's own.
pub const GENERIC: [&str; 6] = ["steam_app_default", "steam_app_0", "wine", "wine64", "explorer.exe", ""];
pub const LAUNCHER_WORDS: [&str; 7] = ["faugus", "umu", "proton", "wine", "lutris", "bottles", "heroic"];
pub const WINDOWS_ICONS: [&str; 5] = ["faugus-launcher", "io.github.Faugus.faugus-launcher", "wine", "application-x-ms-dos-executable", "application-x-executable"];

pub fn is_windows_program(app_id: &str) -> bool {
    let a = app_id.to_lowercase();
    GENERIC.contains(&a.as_str()) || a.ends_with(".exe")
}

fn ends_exe(s: &str) -> bool {
    s.to_lowercase().ends_with(".exe")
}

/// "org.example.CoolApp" -> "CoolApp", "my-tool" -> "My Tool".
pub fn readable(app_id: &str) -> String {
    let mut a = app_id.trim();
    if a.contains('.') && !ends_exe(a) {
        a = a.rsplit('.').next().unwrap_or(a);
    }
    if ends_exe(a) {
        a = &a[..a.len() - 4];
    }
    let words: Vec<&str> = a.split(['-', '_', ' ']).filter(|w| !w.is_empty()).collect();
    let out = if a == a.to_lowercase() { words.iter().map(|w| capitalize(w)).collect::<Vec<_>>().join(" ") } else { words.join(" ") };
    if out.is_empty() {
        app_id.to_string()
    } else {
        out
    }
}

/// str.capitalize: the first letter upper, the rest lower.
fn capitalize(w: &str) -> String {
    let mut c = w.chars();
    match c.next() {
        Some(f) => f.to_uppercase().chain(c.flat_map(char::to_lowercase)).collect(),
        None => String::new(),
    }
}

/// Of (name, command line) pairs, the index of the launcher's shortcut
/// whose name begins the window's title (the longest one: the most specific).
pub fn shortcut_index(title: &str, entries: &[(String, String)]) -> Option<usize> {
    let low = title.trim().to_lowercase();
    if low.is_empty() {
        return None;
    }
    let mut best: Option<(usize, usize)> = None;
    for (i, (name, cmd)) in entries.iter().enumerate() {
        let name = name.trim();
        let cmd = cmd.to_lowercase();
        if !name.is_empty() && low.starts_with(&name.to_lowercase()) && LAUNCHER_WORDS.iter().any(|w| cmd.contains(w)) {
            let len = name.chars().count();
            if best.is_none_or(|(_, l)| len > l) {
                best = Some((i, len));
            }
        }
    }
    best.map(|(i, _)| i)
}

thread_local! {
    // the installed apps, looked through on every window event: read again
    // after a while (apps.py keeps its own scan the same way)
    static SCAN: RefCell<Option<(Vec<gio::DesktopAppInfo>, Instant)>> = const { RefCell::new(None) };
}
const SCAN_KEEP: Duration = Duration::from_secs(10);

fn installed() -> Vec<gio::DesktopAppInfo> {
    SCAN.with(|s| {
        let mut s = s.borrow_mut();
        if let Some((apps, when)) = s.as_ref() {
            if when.elapsed() < SCAN_KEEP {
                return apps.clone();
            }
        }
        let apps: Vec<gio::DesktopAppInfo> = sonata_ui::apps::scan().into_values().collect();
        *s = Some((apps.clone(), Instant::now()));
        apps
    })
}

fn shortcut_for(title: &str) -> Option<gio::DesktopAppInfo> {
    let apps = installed();
    let pairs: Vec<(String, String)> = apps.iter().map(|i| (sonata_ui::apps::display_name(i), i.commandline().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default())).collect();
    shortcut_index(title, &pairs).map(|i| apps[i].clone())
}

/// The theme names to look an unknown app's icon up by.
pub fn icon_names(app_id: &str) -> Vec<String> {
    let mut names: Vec<String> = [app_id.to_string(), app_id.to_lowercase(), app_id.rsplit('.').next().unwrap_or("").to_lowercase()].into_iter().filter(|n| !n.is_empty()).collect();
    names.push("application-x-executable".into());
    names
}

/// The name, and whether it follows the title, of a window whose app_id
/// is no Steam game and has no launcher's shortcut (the rest of describe).
pub fn plain_name(app_id: &str, title: &str) -> (String, bool) {
    let title = title.trim();
    if is_windows_program(app_id) {
        let exe = if ends_exe(app_id) { readable(app_id) } else { String::new() };
        let name = [title, exe.as_str()].into_iter().find(|s| !s.is_empty()).unwrap_or("Windows App").to_string();
        return (name, exe.is_empty() || !title.is_empty());
    }
    let r = readable(app_id);
    let name = [r.as_str(), title].into_iter().find(|s| !s.is_empty()).unwrap_or("App").to_string();
    (name, false)
}

fn themed(names: &[String]) -> gio::Icon {
    let refs: Vec<&str> = names.iter().map(String::as_str).collect();
    gio::ThemedIcon::from_names(&refs).upcast()
}

/// (name, icon, dynamic) for a window whose app_id has no desktop entry.
pub fn describe(app_id: &str, title: &str) -> (String, gio::Icon, bool) {
    let title = title.trim();
    if let Some(aid) = steamgames::appid(app_id) {
        let found = steamgames::name(&aid);
        if let Some((name, icon)) = steamgames::shown(app_id, title) {
            return (name, icon, found.is_none());
        }
    }
    if is_windows_program(app_id) {
        if let Some(info) = shortcut_for(title) {
            return (sonata_ui::apps::display_name(&info), sonata_ui::icons::app_icon(info.upcast_ref()), false);
        }
        let (name, dynamic) = plain_name(app_id, title);
        let names: Vec<String> = WINDOWS_ICONS.iter().map(|s| s.to_string()).collect();
        return (name, themed(&names), dynamic);
    }
    let (name, dynamic) = plain_name(app_id, title);
    (name, themed(&icon_names(app_id)), dynamic)
}

/// The title to name an app by: its focused window's, else the first
/// one's. windows: (title, activated).
pub fn window_title(windows: &[(&str, bool)]) -> String {
    let act = windows.iter().find(|w| w.1);
    act.into_iter().chain(windows.iter()).find(|w| !w.0.is_empty()).map(|w| w.0.to_string()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_windows_program_named_by_its_title() {
        assert_eq!(plain_name("steam_app_default", "FL Studio 2026 Setup"), ("FL Studio 2026 Setup".into(), true)); // follows the title
        assert_eq!(plain_name("steam_app_default", "").0, "Windows App");
        assert_eq!(plain_name("wine", "Notepad++").0, "Notepad++");
        assert_eq!(plain_name("FL64.exe", ""), ("FL64".into(), false));
        assert!(WINDOWS_ICONS.contains(&"wine"));
    }

    #[test]
    fn test_launcher_shortcut() {
        let scan = [("FL Studio 2026", "faugus-launcher --game fl-studio-2026"), ("FL", "lutris lutris:rungame/fl"), ("FL Studio 2026", "flatpak run something")].map(|(n, c)| (n.to_string(), c.to_string()));
        assert_eq!(shortcut_index("FL Studio 2026 - untitled.flp", &scan), Some(0));
        assert_eq!(shortcut_index("", &scan), None);
        assert_eq!(shortcut_index("Firefox", &[("Firefox".into(), "firefox %u".into())]), None); // no launcher
    }

    #[test]
    fn test_any_other_app() {
        assert_eq!(plain_name("org.example.CoolApp", "doc.txt").0, "CoolApp");
        assert_eq!(plain_name("my-tool", ""), ("My Tool".into(), false));
        assert!(icon_names("my-tool").contains(&"my-tool".to_string()));
        assert_eq!(readable("Some_App"), "Some App");
        assert_eq!(readable(""), "");
    }

    #[test]
    fn test_window_title() {
        assert_eq!(window_title(&[("a", false), ("b", true)]), "b");
        assert_eq!(window_title(&[("", false), ("c", false)]), "c");
        assert_eq!(window_title(&[]), "");
    }
}
