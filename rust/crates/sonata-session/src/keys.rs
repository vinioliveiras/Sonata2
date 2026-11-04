//! Wayfire's key bindings that need no window (sonata2/__main__.py's
//! `key` and `screenshot`): the volume, brightness and media keys, and
//! screenshots when the menu bar isn't there to take them. No GTK here.

use gio::prelude::*;
use sonata_backend::{sounds, system, wfipc};
use sonata_i18n::tr;

/// The Python shell's app ids: the menu bar and Files aren't ported, so
/// these are theirs on trial too.
fn python_app_id(component: &str) -> String {
    format!("io.github.vinioliveiras.sonata2.{component}")
}

enum Key {
    Volume(i64),
    Brightness(i64),
    Media(&'static str),
}

/// media keys: what changes, and the step (macOS uses 16 steps)
fn key_of(name: &str) -> Option<Key> {
    Some(match name {
        "volume-up" => Key::Volume(6),
        "volume-down" => Key::Volume(-6),
        "volume-mute" => Key::Volume(0),
        "brightness-up" => Key::Brightness(6),
        "brightness-down" => Key::Brightness(-6),
        "play-pause" => Key::Media("PlayPause"),
        "next" => Key::Media("Next"),
        "previous" => Key::Media("Previous"),
        _ => return None,
    })
}

fn session_bus() -> Option<gio::DBusConnection> {
    gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok()
}

/// Run an action of a running Sonata process (org.freedesktop.Application).
pub fn app_action(app_id: &str, name: &str, param: Option<&str>) -> bool {
    let Some(bus) = session_bus() else { return false };
    let params: Vec<glib::Variant> = param.map(|p| vec![p.to_variant()]).unwrap_or_default();
    let platform: std::collections::HashMap<String, glib::Variant> = Default::default();
    let args = glib::Variant::tuple_from_iter([name.to_variant(), params.to_variant(), platform.to_variant()]);
    bus.call_sync(Some(app_id), &format!("/{}", app_id.replace('.', "/")), "org.freedesktop.Application", "ActivateAction", Some(&args), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE)
        .is_ok()
}

/// The player to steer: the one playing, else the first.
pub fn pick_player(players: &[(String, String)]) -> Option<&str> {
    players.iter().find(|p| p.1 == "Playing").or(players.first()).map(|p| p.0.as_str())
}

/// Play/pause, next, previous: the player that is playing (else the first one) over MPRIS.
fn media(method: &str) -> i32 {
    let Some(bus) = session_bus() else { return 1 };
    let Ok(names) = bus.call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None, None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE) else { return 1 };
    let names: Vec<String> = names.child_value(0).get().unwrap_or_default();
    let status = |n: &str| -> String {
        bus.call_sync(Some(n), "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get", Some(&("org.mpris.MediaPlayer2.Player", "PlaybackStatus").to_variant()), None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.child_value(0).as_variant())
            .and_then(|v| v.get::<String>())
            .unwrap_or_default()
    };
    let players: Vec<(String, String)> = names.into_iter().filter(|n| n.starts_with("org.mpris.MediaPlayer2.")).map(|n| { let s = status(&n); (n, s) }).collect();
    let Some(target) = pick_player(&players) else { return 1 };
    let r = bus.call_sync(Some(target), "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", method, None, None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE);
    if r.is_ok() { 0 } else { 1 }
}

/// The new level after a key: within 0..100.
pub fn stepped(current: i64, step: i64) -> i64 {
    (current + step).clamp(0, 100)
}

/// `sonata2-rs key volume-up` (Wayfire key bindings): change the level,
/// then ask the menu bar process to show the HUD.
pub fn key(name: &str) -> i32 {
    let (kind, level, muted) = match key_of(name) {
        None => return 2,
        Some(Key::Media(method)) => return media(method),
        Some(Key::Volume(step)) => {
            let (cur, was_muted) = system::volume().unwrap_or((0, false));
            if step == 0 {
                system::set_volume(None, Some(!was_muted));
                ("volume", cur, !was_muted)
            } else {
                let level = stepped(cur, step);
                system::set_volume(Some(level), Some(false));
                sounds::play("volume"); // macOS: feedback when the volume changes
                ("volume", level, false)
            }
        }
        Some(Key::Brightness(step)) => {
            // the display with the focus: an external monitor over DDC/CI
            // when it answers, else the laptop panel
            let mut out = wfipc::focused_output();
            let mut cur = if system::is_builtin(out.as_deref()) { None } else { system::brightness(out.as_deref()) };
            if cur.is_none() {
                out = None;
                cur = system::brightness(None);
            }
            let level = stepped(cur.unwrap_or(50), step);
            system::set_brightness(level, out.as_deref());
            ("brightness", level, false)
        }
    };
    // the per-device volume is saved a moment later: before this process ends
    sonata_backend::devicevolume::flush(None);
    // no menu bar: the level still changed
    app_action(&python_app_id("topbar"), "osd", Some(&format!("{kind}:{level}:{}", muted as i32)));
    0
}

/// "Screenshot 2026-09-29 at 01.52.10.png" (the name the menu bar gives too).
pub fn screenshot_name(now: &glib::DateTime) -> String {
    now.format("Screenshot %Y-%m-%d at %H.%M.%S.png").map(|s| s.to_string()).unwrap_or_else(|_| "Screenshot.png".into())
}

/// macOS screenshots. The menu bar does it when it runs (floating
/// thumbnail, Super+Shift+5 toolbar, recording); otherwise a file on the
/// Desktop and a notification.
pub fn screenshot(mode: &str) -> i32 {
    if app_action(&python_app_id("topbar"), "capture", Some(mode)) {
        return 0;
    }
    if !["screen", "area", "edit"].contains(&mode) || sonata_backend::run::which("grim").is_none() {
        return 1;
    }
    let desk = glib::user_special_dir(glib::UserDirectory::Desktop).unwrap_or_else(glib::home_dir);
    let name = screenshot_name(&glib::DateTime::now_local().unwrap_or_else(|_| glib::DateTime::now_utc().expect("a clock")));
    let path = desk.join(&name).display().to_string();
    let mut cmd = std::process::Command::new("grim");
    if mode == "area" {
        if sonata_backend::run::which("slurp").is_none() {
            return 1;
        }
        let Ok(sel) = std::process::Command::new("slurp").stderr(std::process::Stdio::null()).output() else { return 1 };
        let geometry = String::from_utf8_lossy(&sel.stdout).trim().to_string();
        if !sel.status.success() || geometry.is_empty() {
            return 0; // Escape: cancelled
        }
        cmd.args(["-g", &geometry]);
    }
    if !cmd.arg(&path).status().is_ok_and(|s| s.success()) {
        return 1;
    }
    if mode == "edit" {
        // Print Screen without the menu bar: still the editor
        let argv = crate::keep::command_for(&["preview".into(), "--markup".into(), path]);
        let _ = std::process::Command::new(&argv[0]).args(&argv[1..]).spawn();
        return 0;
    }
    if let Some(bus) = session_bus() {
        let hints: std::collections::HashMap<String, glib::Variant> = [("desktop-entry".to_string(), python_app_id("files").to_variant()), ("image-path".to_string(), path.to_variant())].into();
        let title = tr!("Screenshot");
        let actions: Vec<String> = Vec::new();
        let args = glib::Variant::tuple_from_iter([title.to_variant(), 0u32.to_variant(), path.to_variant(), title.to_variant(), name.to_variant(), actions.to_variant(), hints.to_variant(), (-1i32).to_variant()]);
        let _ = bus.call_sync(Some("org.freedesktop.Notifications"), "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify", Some(&args), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn levels_stay_in_range() {
        assert_eq!(stepped(97, 6), 100);
        assert_eq!(stepped(3, -6), 0);
        assert_eq!(stepped(50, 6), 56);
    }

    #[test]
    fn the_playing_player_first() {
        let p = vec![("org.mpris.MediaPlayer2.a".to_string(), "Paused".to_string()), ("org.mpris.MediaPlayer2.b".to_string(), "Playing".to_string())];
        assert_eq!(pick_player(&p), Some("org.mpris.MediaPlayer2.b"));
        assert_eq!(pick_player(&p[..1]), Some("org.mpris.MediaPlayer2.a"));
        assert_eq!(pick_player(&[]), None);
        assert!(key_of("volume-up").is_some() && key_of("nope").is_none());
    }

    #[test]
    fn screenshot_names() {
        let t = glib::DateTime::from_local(2026, 9, 29, 1, 52, 10.0).unwrap();
        assert_eq!(screenshot_name(&t), "Screenshot 2026-09-29 at 01.52.10.png");
    }
}
