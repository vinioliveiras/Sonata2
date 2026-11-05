//! An app that doesn't open is opened again (sonata2/launchwatch.py;
//! Vini: Steam above all).
//!
//! Opened from the Dock, Launchpad or Files and no window of it this long
//! after: its processes are killed (appscope::kill) and it is opened again,
//! up to RETRIES times; then a notification says it couldn't open. An app
//! that can't be started at all says so at once.
//!
//! ```text
//! launchwatch::open(&info, Rc::new(move || info.launch(&[], ctx)), 0, None)  // Launchpad, Files
//! launchwatch::has_window(Some(&info), None, None)                           // a window of it is open
//! ```
//!
//! The Dock keeps its own watch (it already follows every window and
//! bounces the icon): it uses the same timings and failed().

use crate::{appmatch, appscope};
use gio::prelude::*;
use serde_json::{json, Value};
use sonata_i18n::tr;
use std::path::Path;
use std::rc::Rc;

pub const WATCH_S: u32 = 30;
/// Apps that update themselves first: longer.
pub const SLOW_S: [(&str, u32); 3] = [("steam", 90), ("steam-native", 90), ("steam-runtime", 90)];
pub const RETRIES: u32 = 3;
pub const RELAUNCH_MS: u32 = 900;

/// What starts the app (`info.launch(...)`).
pub type Launch = Rc<dyn Fn() -> Result<(), glib::Error>>;

/// The desktop id without .desktop ("" for none).
pub fn key_of(info: Option<&gio::AppInfo>) -> String {
    let id = info.and_then(|i| i.id()).map(|s| s.to_string()).unwrap_or_default();
    id.strip_suffix(".desktop").unwrap_or(&id).to_string()
}

/// How long to wait for its window, in seconds.
pub fn wait_s(info: Option<&gio::AppInfo>) -> u32 {
    let key = key_of(info);
    SLOW_S.iter().find(|(k, _)| *k == key).map_or(WATCH_S, |(_, s)| *s)
}

/// has_window from what it needs: the app's key, Wayfire's views, its
/// launch scope's unit, how app ids are matched and where /proc is.
pub fn has_window_in(key: &str, views: &Value, unit: Option<&str>, matcher: &dyn Fn(&str) -> Option<String>, proc: &Path) -> bool {
    let Some(views) = views.as_array() else { return false };
    for v in views {
        let Some(v) = v.as_object() else { continue };
        if v.get("type").and_then(Value::as_str).unwrap_or("toplevel") != "toplevel" {
            continue;
        }
        let aid = v.get("app-id").and_then(Value::as_str).unwrap_or("");
        if !aid.is_empty() && (aid == key || matcher(aid).is_some_and(|m| m.strip_suffix(".desktop").unwrap_or(&m) == key)) {
            return true;
        }
        if let (Some(unit), Some(pid)) = (unit.filter(|u| !u.is_empty()), v.get("pid").and_then(Value::as_i64).filter(|p| *p > 0)) {
            if std::fs::read_to_string(proc.join(pid.to_string()).join("cgroup")).is_ok_and(|c| c.contains(unit)) {
                return true;
            }
        }
    }
    false
}

/// A window of the app is open: its app id names the app's entry, or its
/// process is in the app's launch scope (`unit`). views None: Wayfire's
/// list now -- and when it can't be read, true: never kill on a guess.
pub fn has_window(info: Option<&gio::AppInfo>, views: Option<&Value>, unit: Option<&str>) -> bool {
    let owned;
    let views = match views {
        Some(v) => v,
        None => match sonata_backend::wfipc::call("window-rules/list-views", json!({})) {
            Some(v) => {
                owned = v;
                &owned
            }
            None => return true,
        },
    };
    has_window_in(&key_of(info), views, unit, &appmatch::match_app_id, Path::new("/proc"))
}

/// The notification: it couldn't open.
pub fn failed(info: Option<&gio::AppInfo>, why: &str) {
    let name = info.map(|i| sonata_ui::apps::display_name(i)).unwrap_or_else(|| tr!("The app"));
    let icon = info.and_then(|i| i.icon()).and_then(|i| IconExt::to_string(&i)).map(|s| s.to_string()).unwrap_or_default();
    let desktop = info.and_then(|i| i.id()).map(|s| s.to_string()).unwrap_or_default();
    let summary = sonata_i18n::fmt(&tr!("“{name}” couldn't open"), &[("name", &name)]);
    crate::notify::send(&summary, &format!("{why} {}", tr!("Try opening it again in a moment.")), "Sonata", &icon, &desktop, false);
}

/// What happens when the wait is over.
#[derive(Debug, PartialEq, Eq)]
pub enum Step {
    /// its window came: done
    Opened,
    /// killed, opened again (this is the next try's number)
    Again(u32),
    /// killed; tried RETRIES times: the notification
    GiveUp,
}

/// After the wait: a window, or the next try, or give up.
pub fn next_step(has_window: bool, tries: u32) -> Step {
    if has_window {
        Step::Opened
    } else if tries >= RETRIES {
        Step::GiveUp
    } else {
        Step::Again(tries + 1)
    }
}

/// Run `launch()` (it starts `info`), then watch it. False: it couldn't be
/// started (the notification is out). already_open: Some(true) when the app
/// had a window before (a file handed to it: nothing to watch); None: asked.
pub fn open(info: &gio::AppInfo, launch: Launch, tries: u32, already_open: Option<bool>) -> bool {
    let already_open = match already_open {
        Some(a) => a,
        None if tries == 0 => has_window(Some(info), None, None),
        None => false,
    };
    if let Err(e) = launch() {
        failed(Some(info), e.message());
        return false;
    }
    if !already_open {
        let info = info.clone();
        glib::timeout_add_seconds_local_once(wait_s(Some(&info)), move || check(&info, launch, tries));
    }
    true
}

fn check(info: &gio::AppInfo, launch: Launch, tries: u32) {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    let step = next_step(has_window(Some(info), None, appscope::launched(&id).as_deref()), tries);
    if step == Step::Opened {
        return;
    }
    appscope::kill(&id, Some(info), Path::new("/proc"));
    match step {
        Step::GiveUp => {
            let why = sonata_i18n::fmt(&tr!("It was closed and opened again {n} times and still didn't show a window."), &[("n", &RETRIES.to_string())]);
            failed(Some(info), &why);
        }
        Step::Again(next) => {
            eprintln!("sonata2: {} showed no window in {} s: killed, opening it again ({next}/{RETRIES})", key_of(Some(info)), wait_s(Some(info)));
            let info = info.clone();
            glib::timeout_add_local_once(std::time::Duration::from_millis(RELAUNCH_MS as u64), move || {
                open(&info, launch, next, Some(false));
            });
        }
        Step::Opened => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn by_name(a: &str) -> Option<String> {
        (a == "Steam").then(|| "steam".to_string())
    }

    #[test]
    fn test_by_app_id() {
        let proc = Path::new("/nonexistent");
        assert!(has_window_in("steam", &json!([{"app-id": "steam", "type": "toplevel"}]), None, &by_name, proc));
        assert!(has_window_in("steam", &json!([{"app-id": "Steam", "type": "toplevel"}]), None, &by_name, proc));
        assert!(!has_window_in("steam", &json!([{"app-id": "firefox", "type": "toplevel"}]), None, &by_name, proc));
        assert!(!has_window_in("steam", &json!([{"app-id": "steam", "type": "background"}]), None, &by_name, proc));
        assert!(has_window_in("steam", &json!([{"app-id": "steam"}]), None, &by_name, proc)); // no type: a toplevel
    }

    #[test]
    fn test_by_launch_scope() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir(d.path().join("77")).unwrap();
        std::fs::write(d.path().join("77/cgroup"), "0::/user.slice/sonata-apps.slice/app-sonata2-steam-5.scope\n").unwrap();
        let views = json!([{"app-id": "steamwebhelper", "pid": 77}]);
        assert!(has_window_in("steam", &views, Some("app-sonata2-steam-5.scope"), &by_name, d.path()));
        assert!(!has_window_in("steam", &views, Some("app-sonata2-steam-6.scope"), &by_name, d.path()));
        assert!(!has_window_in("steam", &views, None, &by_name, d.path()));
    }

    /// Wayfire's list can't be read: never kill on a guess.
    #[test]
    fn test_unknown_means_open() {
        assert!(has_window(None, None, None));
    }

    #[test]
    fn test_three_retries_then_notified() {
        let mut tries = 0;
        let mut launches = 1; // the first open
        loop {
            match next_step(false, tries) {
                Step::Again(n) => {
                    tries = n;
                    launches += 1;
                }
                Step::GiveUp => break,
                Step::Opened => unreachable!(),
            }
        }
        assert_eq!(launches, RETRIES + 1);
        assert_eq!(next_step(true, 2), Step::Opened); // stops when a window comes
    }

    #[test]
    fn test_waits() {
        assert_eq!(wait_s(None), WATCH_S);
        assert_eq!(key_of(None), "");
    }
}
