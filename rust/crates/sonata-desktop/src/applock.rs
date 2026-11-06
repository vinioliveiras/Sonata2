//! Locked apps (sonata2/applock.py; Vini): "Lock App" in an app's Dock or
//! Launchpad menu, and opening it asks for the login password first --
//! only when it opens; a running app isn't touched. Every launch Sonata
//! makes asks gate() first.
//!
//! Not asked: apps reopened at login (autostart) or after a crash
//! (open_apps) -- those run inside trusted().
//!
//! ```text
//! locked("spotify.desktop")           // or "spotify"
//! set_locked("spotify", true)
//! trusted(|| info.launch(&[], None))
//! ```
//!
//! applock.json through sonata-config (~/.config/sonata2).

use gio::prelude::*;
use serde_json::{json, Value};
use sonata_i18n::tr;
use std::cell::Cell;

pub const NAME: &str = "applock";

thread_local! {
    static TRUSTED: Cell<u32> = const { Cell::new(0) };
}

fn full_id(app_id: &str) -> String {
    if app_id.ends_with(".desktop") {
        app_id.to_string()
    } else {
        format!("{app_id}.desktop")
    }
}

/// The locked apps' desktop ids ("spotify.desktop").
pub fn locked_apps() -> Vec<String> {
    match sonata_config::load(NAME, &json!({"apps": []})).get("apps") {
        Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        _ => Vec::new(),
    }
}

pub fn locked(app_id: &str) -> bool {
    !app_id.is_empty() && locked_apps().contains(&full_id(app_id))
}

pub fn set_locked(app_id: &str, on: bool) {
    let id = full_id(app_id);
    let mut keep: Vec<String> = locked_apps().into_iter().filter(|a| *a != id).collect();
    if on {
        keep.push(id);
    }
    let mut data = serde_json::Map::new();
    data.insert("apps".into(), json!(keep));
    if let Err(e) = sonata_config::save(NAME, &data) {
        eprintln!("sonata2: app lock: {e}");
    }
}

/// Launches in here never ask (autostart, reopening after a crash).
pub fn trusted<R>(f: impl FnOnce() -> R) -> R {
    struct Guard;
    impl Drop for Guard {
        fn drop(&mut self) {
            TRUSTED.with(|t| t.set(t.get().saturating_sub(1)));
        }
    }
    TRUSTED.with(|t| t.set(t.get() + 1));
    let _g = Guard;
    f()
}

/// Inside trusted() now.
pub fn is_trusted() -> bool {
    TRUSTED.with(|t| t.get() > 0)
}

/// True: `launch` ran or waits for the password (the caller does nothing
/// more); false: not locked, the caller launches.
pub fn gate(info: &impl IsA<gio::AppInfo>, launch: impl Fn() + 'static) -> bool {
    let app_id = info.as_ref().id().map(|s| s.to_string()).unwrap_or_default();
    if is_trusted() || !locked(&app_id) {
        return false;
    }
    if !gtk::is_initialized_main_thread() || gtk::gdk::Display::default().is_none() {
        return true; // nowhere to ask: never opens unasked
    }
    let name = sonata_ui::apps::display_name(info);
    let heading = sonata_i18n::fmt(&tr!("“{name}” is locked"), &[("name", &name)]);
    // (the launch comes back through gate: opened inside trusted())
    sonata_shell::authsheet::ask(&heading, &tr!("Enter your password to open it."), Some(&tr!("Open")), None, None, move |_pw| trusted(&launch), || {});
    true
}

/// Unlocking asks the password too (like a locked folder).
pub fn ask_unlock(info: &impl IsA<gio::AppInfo>, done: Option<Box<dyn Fn()>>) {
    let name = sonata_ui::apps::display_name(info);
    let id = info.as_ref().id().map(|s| s.to_string()).unwrap_or_default();
    let heading = sonata_i18n::fmt(&tr!("Unlock “{name}”?"), &[("name", &name)]);
    sonata_shell::authsheet::ask(&heading, &tr!("Enter your password to stop asking when it opens."), Some(&tr!("Unlock")), None, None, move |_pw| {
        set_locked(&id, false);
        if let Some(d) = &done {
            d();
        }
    }, || {});
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lock_and_unlock() {
        let _l = crate::test_lock();
        sonata_config::save(NAME, &serde_json::Map::new()).unwrap();
        assert!(!locked("spotify.desktop"));
        set_locked("spotify.desktop", true);
        assert!(locked("spotify.desktop"));
        assert!(locked("spotify")); // either form of the id
        set_locked("spotify", false);
        assert!(!locked("spotify.desktop"));
        assert!(!locked(""));
    }

    #[test]
    fn test_bad_file_is_nothing_locked() {
        let _l = crate::test_lock();
        let mut m = serde_json::Map::new();
        m.insert("apps".into(), json!("spotify.desktop"));
        sonata_config::save(NAME, &m).unwrap();
        assert!(!locked("spotify.desktop"));
        sonata_config::save(NAME, &serde_json::Map::new()).unwrap();
    }

    #[test]
    fn test_trusted_nests_and_ends() {
        assert!(!is_trusted());
        trusted(|| {
            assert!(is_trusted());
            trusted(|| assert!(is_trusted()));
            assert!(is_trusted());
        });
        assert!(!is_trusted());
    }

    #[test]
    fn test_unlocked_and_trusted_dont_ask() {
        let _l = crate::test_lock();
        let mut m = serde_json::Map::new();
        m.insert("apps".into(), json!(["spotify.desktop"]));
        sonata_config::save(NAME, &m).unwrap();
        let kf = glib::KeyFile::new();
        kf.load_from_data("[Desktop Entry]\nType=Application\nName=X\nExec=true\n", glib::KeyFileFlags::NONE).unwrap();
        let info = gio::DesktopAppInfo::from_keyfile(&kf).unwrap(); // no id: never locked
        assert!(!gate(&info, || {}));
        sonata_config::save(NAME, &serde_json::Map::new()).unwrap();
    }
}
