//! Location Services (backend/location.py; Vini): one switch for every app,
//! and a switch per app installed from packages.
//!
//! Apps get your location from GeoClue -- directly, or through the portal
//! for Flatpak apps. Sonata writes GeoClue's own settings,
//! /etc/geoclue/conf.d/90-sonata.conf (pkexec: the password is asked):
//! Location Services off turns every source off; an app turned off gets
//! its own section, allowed=false.

use serde_json::{json, Value};
use std::io::Write;

pub const NAME: &str = "location";
pub const CONF_DIR: &str = "/etc/geoclue/conf.d";
pub const CONF: &str = "/etc/geoclue/conf.d/90-sonata.conf";
pub const SOURCES: [&str; 7] = ["wifi", "3g", "cdma", "modem-gps", "network-nmea", "compass", "static-source"];

pub fn defaults() -> Value {
    json!({"enabled": true, "denied": []})
}

/// GeoClue installed
pub fn available() -> bool {
    ["/etc/geoclue/geoclue.conf", "/usr/lib/geoclue-2.0/geoclue", "/usr/libexec/geoclue", "/usr/lib/geoclue"].iter().any(|p| std::path::Path::new(p).exists())
}

fn load() -> serde_json::Map<String, Value> {
    sonata_config::load(NAME, &defaults())
}

pub fn enabled() -> bool {
    // bool(get("enabled", True)), as the Python reads it
    load().get("enabled").is_none_or(|v| crate::py::truthy(Some(v)))
}

pub fn denied() -> Vec<String> {
    let mut got: Vec<String> = load().get("denied").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str()).filter(|s| !s.is_empty()).map(String::from).collect()).unwrap_or_default();
    got.sort();
    got.dedup();
    got
}

/// GeoClue's name for an app: its desktop id without .desktop.
pub fn app_key(app_id: &str) -> &str {
    app_id.strip_suffix(".desktop").unwrap_or(app_id)
}

pub fn allowed(app_id: &str) -> bool {
    !denied().iter().any(|d| d == app_key(app_id))
}

pub fn render(on: bool, deny: &[String]) -> String {
    let mut out: Vec<String> = vec!["# Written by Sonata (Settings > Apps): Location Services and the apps".into(), "# turned off there. Changed from Sonata; a hand edit is replaced.".into(), String::new()];
    if !on {
        for src in SOURCES {
            out.extend([format!("[{src}]"), "enable=false".into(), String::new()]);
        }
    }
    let mut apps: Vec<&String> = deny.iter().collect();
    apps.sort();
    apps.dedup();
    for app in apps {
        // never a broken file
        if !app.is_empty() && !app.contains(['[', ']', '\n']) {
            out.extend([format!("[{app}]"), "allowed=false".into(), "system=false".into(), "users=".into(), String::new()]);
        }
    }
    out.join("\n")
}

/// How the root command is run: (argv, its input) -> ok.
pub type Runner<'a> = &'a dyn Fn(&[&str], &str) -> bool;

fn pkexec_run(cmd: &[&str], data: &str) -> bool {
    let child = std::process::Command::new(cmd[0]).args(&cmd[1..]).stdin(std::process::Stdio::piped()).spawn();
    let Ok(mut child) = child else { return false };
    if let Some(mut i) = child.stdin.take() {
        let _ = i.write_all(data.as_bytes());
    }
    child.wait().is_ok_and(|s| s.success())
}

/// The file, as root (pkexec), then GeoClue started again to read it.
fn write(text: &str, run: Runner) -> bool {
    let script = format!("mkdir -p {CONF_DIR} && cat > {CONF}.new && mv -f {CONF}.new {CONF} && (systemctl try-restart geoclue.service 2>/dev/null || true)");
    run(&["pkexec", "sh", "-c", &script], text)
}

/// Blocking (pkexec asks the password).
pub fn set_enabled(on: bool) -> bool {
    set_enabled_with(on, &pkexec_run)
}

pub fn set_enabled_with(on: bool, run: Runner) -> bool {
    if !write(&render(on, &denied()), run) {
        return false; // (cancelled: nothing changes)
    }
    let _ = sonata_config::update(NAME, &json!({"enabled": on}));
    true
}

pub fn set_app(app_id: &str, on: bool) -> bool {
    set_app_with(app_id, on, &pkexec_run)
}

pub fn set_app_with(app_id: &str, on: bool, run: Runner) -> bool {
    let key = app_key(app_id).to_string();
    let mut deny: Vec<String> = denied().into_iter().filter(|a| *a != key).collect();
    if !on {
        deny.push(key);
    }
    if !write(&render(enabled(), &deny), run) {
        return false;
    }
    deny.sort();
    deny.dedup();
    let _ = sonata_config::update(NAME, &json!({"denied": deny}));
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn sections(text: &str) -> Vec<String> {
        text.lines().filter(|l| l.starts_with('[')).map(String::from).collect()
    }

    #[test]
    fn off_turns_every_source_off_and_apps_turned_off() {
        let t = render(false, &[]);
        for src in ["wifi", "3g", "cdma", "modem-gps", "network-nmea", "static-source"] {
            assert!(t.contains(&format!("[{src}]\nenable=false\n")), "{src}");
        }
        assert!(sections(&render(true, &[])).is_empty());
        assert!(render(true, &["org.gnome.Maps".into()]).contains("[org.gnome.Maps]\nallowed=false\nsystem=false\nusers=\n"));
        assert!(sections(&render(true, &["bad]name".into(), "x\ny".into()])).is_empty()); // never a broken file
    }

    /// Vini: a Location switch for every app, written through pkexec.
    #[test]
    fn set_app_and_enabled_through_pkexec() {
        let _l = crate::test_lock();
        let _ = sonata_config::save(NAME, &Default::default());
        let writes: RefCell<Vec<(Vec<String>, String)>> = RefCell::default();
        let ok = |cmd: &[&str], data: &str| {
            writes.borrow_mut().push((cmd.iter().map(|s| s.to_string()).collect(), data.into()));
            true
        };
        assert!(set_app_with("org.gnome.Maps.desktop", false, &ok));
        assert!(!allowed("org.gnome.Maps.desktop"));
        {
            let w = writes.borrow();
            let (cmd, data) = w.last().unwrap();
            assert_eq!(cmd[..3], ["pkexec", "sh", "-c"]);
            assert!(cmd[3].contains(CONF));
            assert!(data.contains("[org.gnome.Maps]"));
        }
        assert!(set_enabled_with(false, &ok));
        assert!(!enabled());
        assert!(writes.borrow().last().unwrap().1.contains("[org.gnome.Maps]")); // both kept in one file
        assert!(set_app_with("org.gnome.Maps", true, &ok));
        assert!(allowed("org.gnome.Maps"));
        // a cancelled password changes nothing
        let no = |_: &[&str], _: &str| false;
        let _ = sonata_config::save(NAME, &Default::default());
        assert!(!set_enabled_with(false, &no));
        assert!(enabled());
        assert!(!set_app_with("x", false, &no));
        assert!(allowed("x"));
    }
}
