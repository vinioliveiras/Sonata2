//! What __main__.py sets in every component's process before GTK starts:
//! no GTK_THEME of the session's, the renderer picked in Settings > General
//! > Graphics, and the shell (and the portal itself) never waiting on the
//! settings portal.

use serde_json::{json, Value};

/// The commands __main__.py runs before any of this (no GTK: nothing to set).
const EARLY: &[&str] = &["keep", "keep-all", "key", "screenshot", "doctor", "restart", "keyring", "share-picker", "show-desktop", "webapp", "lock-wait", "self-update", "--handles", "-h", "--help", "help", "desktop-files", "prefs", "xsettings"];

/// The shell reads Sonata's settings itself (prefs): it never waits on the
/// settings portal, even while that is still starting (__main__.SHELL_NO_PORTAL;
/// the Dock does it in its own run(), autostart starts no GTK).
pub const SHELL_NO_PORTAL: &[&str] = &["topbar", "wallpaper", "launchpad", "spotlight", "polkit", "welcome", "lock", "greeter"];

/// GSK_RENDERER for appearance.json "renderer": OpenGL by default -- GTK's
/// Vulkan path also loads every implicit Vulkan layer installed (overlays,
/// frame generators), slow on hybrid laptops.
pub fn renderer(appearance: &serde_json::Map<String, Value>) -> &'static str {
    match appearance.get("renderer").and_then(Value::as_str) {
        Some("vulkan") => "vulkan",
        Some("software") => "cairo",
        _ => "gl",
    }
}

/// GDK_DEBUG's flags with "no-portals" and without "portals".
pub fn no_portals(gdk_debug: &str) -> String {
    gdk_debug.split(',').filter(|f| !f.is_empty() && *f != "portals" && *f != "no-portals").chain(["no-portals"]).collect::<Vec<_>>().join(",")
}

/// Set the process up for `component`; true when GDK_DEBUG is to be put
/// back once GTK has read it ([`restore_gdk_debug`]): the apps opened from
/// the shell get the session's.
pub fn setup(component: &str) -> bool {
    if EARLY.contains(&component) {
        return false;
    }
    // Sonata's own UI never takes the GTK theme meant for other apps
    std::env::remove_var("GTK_THEME");
    // the session's value, kept across the layer-shell re-exec
    if std::env::var_os("SONATA2_SESSION_GDK_DEBUG").is_none() {
        std::env::set_var("SONATA2_SESSION_GDK_DEBUG", std::env::var("GDK_DEBUG").unwrap_or_default());
    }
    let session = std::env::var("SONATA2_SESSION_GDK_DEBUG").unwrap_or_default();
    let shell = SHELL_NO_PORTAL.contains(&component);
    if shell {
        std::env::set_var("GDK_DEBUG", no_portals(&session));
        std::env::set_var("ADW_DISABLE_PORTAL", "1"); // libadwaita's own settings read
    }
    if component == "portal" {
        // started by D-Bus, maybe without the session's variables: the
        // Open/Save panels are Files windows and get its glass
        if std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default().split(':').any(|d| d == "Sonata") && std::env::var_os("SONATA_GLASS").is_none() {
            std::env::set_var("SONATA_GLASS", "1");
        }
        // the portal backend is what xdg-desktop-portal waits for while it
        // starts: GTK asking that same portal for settings deadlocked the
        // two (every Sonata surface then waited 2 x 30 s at login -- black screen)
        std::env::set_var("GDK_DEBUG", no_portals(&std::env::var("GDK_DEBUG").unwrap_or_default()));
        std::env::set_var("ADW_DISABLE_PORTAL", "1");
        std::env::remove_var("GTK_USE_PORTAL");
    }
    // GPU drawing for every Sonata surface (Settings > General > Graphics)
    if std::env::var_os("GSK_RENDERER").is_none() {
        let a = sonata_config::load("appearance", &json!({"renderer": "gl"}));
        std::env::set_var("GSK_RENDERER", renderer(&a));
    }
    shell
}

/// GDK_DEBUG as the session had it (GTK read ours at init).
pub fn restore_gdk_debug() {
    match std::env::var("SONATA2_SESSION_GDK_DEBUG").ok().filter(|d| !d.is_empty()) {
        Some(d) => std::env::set_var("GDK_DEBUG", d),
        None => std::env::remove_var("GDK_DEBUG"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Settings > General > Graphics did nothing in the Rust session: no
    /// component read appearance.json "renderer" (__main__.py sets
    /// GSK_RENDERER from it).
    #[test]
    fn test_graphics_setting_was_ignored() {
        let a = |v: Value| v.as_object().cloned().unwrap();
        assert_eq!(renderer(&a(json!({}))), "gl");
        assert_eq!(renderer(&a(json!({"renderer": "vulkan"}))), "vulkan");
        assert_eq!(renderer(&a(json!({"renderer": "software"}))), "cairo");
        assert_eq!(renderer(&a(json!({"renderer": 3}))), "gl");
    }

    /// The portal and the shell asked the settings portal while it was
    /// starting (the black screen at login, 2 x 30 s, in Python before the
    /// fix): no-portals for them, the session's other flags kept.
    #[test]
    fn test_shell_waited_on_the_settings_portal() {
        assert_eq!(no_portals(""), "no-portals");
        assert_eq!(no_portals("portals,gl-debug"), "gl-debug,no-portals");
        assert_eq!(no_portals("no-portals"), "no-portals");
        assert!(SHELL_NO_PORTAL.contains(&"topbar") && !SHELL_NO_PORTAL.contains(&"dock"));
    }
}
