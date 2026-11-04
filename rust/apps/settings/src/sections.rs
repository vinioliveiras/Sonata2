//! The sections of Settings (app.py's SECTIONS, PARTS, KEYWORDS,
//! PAGE_CONFIGS): the macOS System Settings sidebar, with Vini's split --
//! Linux (Wi-Fi ... Battery: read and written through the system's
//! services), input, Sonata (its own settings, ~/.config/sonata2/*.json,
//! which the shell watches), system, About.

use sonata_desktop::names;
use sonata_i18n::n_;

/// (id, title, icon, badge colour, group). The title is a msgid
/// ("Wi-Fi", "Bluetooth" and "Dock" stay as they are): see `title()`.
/// Colours varied, not mostly grey (Vini).
pub const SECTIONS: [(&str, &str, &str, &str, &str); 25] = [
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "blue", "linux"),
    ("network", n_!("Network"), "sonata-network-symbolic", "indigo", "linux"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "blue", "linux"),
    ("sound", n_!("Sound"), "audio-volume-high-symbolic", "pink", "linux"),
    ("displays", n_!("Displays"), "video-display-symbolic", "teal", "linux"),
    ("battery", n_!("Battery"), "battery-full-symbolic", "green", "linux"),
    ("keyboard", n_!("Keyboard"), "input-keyboard-symbolic", "graphite", "input"),
    ("mouse", n_!("Mouse & Trackpad"), "input-mouse-symbolic", "gray", "input"),
    ("gamepad", n_!("Game Controllers"), "input-gaming-symbolic", "orange", "input"),
    ("printers", n_!("Printers & Scanners"), "printer-symbolic", "gray", "input"),
    ("appearance", n_!("Appearance"), "preferences-desktop-appearance-symbolic", "indigo", "sonata"),
    ("appicons", n_!("App Icons"), "applications-graphics-symbolic", "pink", "sonata"),
    // Dock, Menu Bar, Control Center, Desktop & Windows: what each does (Vini);
    // how they look (glass, transparency, corners) stays in Appearance
    ("dock", "Dock", "view-grid-symbolic", "black", "sonata"),
    ("menubar", n_!("Menu Bar"), "panel-top-symbolic", "blue", "sonata"),
    ("controlcenter", n_!("Control Center"), "sonata-control-center-symbolic", "purple", "sonata"),
    ("desktop", n_!("Desktop & Windows"), "user-desktop-symbolic", "teal", "sonata"),
    ("launchpad", names::APPS, "view-app-grid-symbolic", "graphite", "sonata"),
    ("notifications", n_!("Notifications"), "preferences-system-notifications-symbolic", "red", "sonata"),
    ("users", n_!("Users & Groups"), "system-users-symbolic", "orange", "system"),
    ("defaults", n_!("Default Apps"), "emblem-default-symbolic", "purple", "system"),
    ("privacy", n_!("Security & Privacy"), "security-high-symbolic", "indigo", "system"),
    ("accessibility", n_!("Accessibility"), "preferences-desktop-accessibility-symbolic", "blue", "system"),
    ("language", n_!("Language & Region"), "preferences-desktop-locale-symbolic", "blue", "system"),
    ("datetime", n_!("Date & Time"), "preferences-system-time-symbolic", "green", "system"),
    ("about", n_!("About"), "help-about-symbolic", "gray", "about"),
];

/// Sections made of several parts (each part one `page_<part>` builder,
/// shown one after the other). The parts' old ids still open their section
/// (--page wallpaper, "Change Desktop Background…", "Software Update").
pub const PARTS: [(&str, &[&str]); 6] = [
    ("displays", &["displays", "wallpaper"]),
    ("keyboard", &["keyboard", "shortcuts"]),
    ("mouse", &["trackpad", "mouse"]),
    ("launchpad", &["launchpad", "hidden", "apps"]), // Apps: the grid, hidden apps, installed apps (Vini)
    ("privacy", &["privacy", "sharing"]),
    ("about", &["about", "sonataupdate", "updates"]), // Sonata's own update, then the system's (Vini)
];

pub const PART_TITLES: [(&str, &str); 8] = [
    ("wallpaper", n_!("Wallpaper")),
    ("shortcuts", n_!("Keyboard Shortcuts")),
    ("trackpad", n_!("Trackpad")),
    ("hidden", n_!("Hidden & Protected Apps")),
    ("apps", n_!("Installed Apps")),
    ("sharing", n_!("Sharing")),
    ("updates", n_!("Software Update")),
    ("sonataupdate", n_!("Sonata Update")),
];

/// The section showing `sid` (itself, or the one it was merged into).
pub fn section_of(sid: &str) -> &str {
    PARTS.iter().find(|(_, parts)| parts.contains(&sid)).map(|(sec, _)| *sec).unwrap_or(sid)
}

/// The parts a section is built from (a plain section: itself).
pub fn parts_of(sid: &str) -> Vec<&str> {
    PARTS.iter().find(|(sec, _)| *sec == sid).map(|(_, p)| p.to_vec()).unwrap_or_else(|| vec![sid])
}

/// The section's static id (None: no such section).
pub fn id(sid: &str) -> Option<&'static str> {
    SECTIONS.iter().find(|s| s.0 == sid).map(|s| s.0)
}

/// The section's title as the sidebar shows it.
pub fn title(sid: &str) -> String {
    match SECTIONS.iter().find(|s| s.0 == section_of(sid)) {
        Some((_, t, ..)) if ["Wi-Fi", "Bluetooth", "Dock"].contains(t) => t.to_string(),
        Some((_, t, ..)) => sonata_i18n::gettext(t),
        None => String::new(),
    }
}

/// Search (the sidebar's field, macOS Ventura): words that find a section besides its title.
const KEYWORDS: [(&str, &str); 31] = [
    ("wifi", "wireless network internet ssid password"),
    ("network", "ethernet vpn proxy wired ip"),
    ("bluetooth", "devices headphones mouse keyboard pair"),
    ("printers", "printer scanner cups print"),
    ("sound", "volume output input microphone speakers headphones effects alert equalizer eq bass treble"),
    ("displays", "screen monitor resolution refresh rate hz scale brightness night shift main display  arrange arrangement main display positionrounded corners"),
    ("battery", "power energy low power mode charge sleep display off"),
    ("wallpaper", "background desktop picture photo mountains"),
    ("keyboard", "layout input source repeat shortcuts"),
    ("trackpad", "touchpad tap click scroll gestures"),
    ("mouse", "pointer speed scroll natural"),
    ("shortcuts", "keyboard shortcuts keys hotkeys windows super win snap desktop lock screenshot"),
    ("gamepad", "game controller gamepad xbox playstation dualsense joystick steam"),
    ("datetime", "clock time zone date"),
    ("notifications", "do not disturb alerts banners"),
    ("language", "language idioma locale region formats date time numbers currency calendar first day week portuguese português english translation"),
    ("users", "account password picture avatar login items"),
    ("privacy", "security lock screen location trash"),
    ("sharing", "file sharing remote"),
    ("accessibility", "zoom contrast reduce transparency motion graphics gpu hardware acceleration renderer"),
    ("appearance", "app icons regenerate frame generated dark light mode accent color theme icons font glass transparency translucent blur frosted title bars corners radius"),
    ("dock", "magnification size position autohide recent apps displays indicators bounce edge"),
    ("desktop", "windows minimize genie scale resize resizing window contents title bar double-click zoom quit close background last window desktop icons sort name kind date"),
    ("controlcenter", "control center modules controls layout add remove reset cpu gpu memory network fps temperature video memory vram mixer"),
    ("defaults", "default apps open with web browser chrome firefox mail email calendar music player video photos pictures images viewer pdf text editor folders file manager"),
    ("apps", "apps applications uninstall remove delete clear data storage size permissions camera microphone network location background notifications flatpak lock"),
    ("appicons", "icon icons app shape squircle circle rounded custom picture image package theme"),
    ("menubar", "clock battery percentage bluetooth sound now playing logo text automatically hide show wifi wi-fi search input source keyboard background apps cpu gpu memory ram network fps performance"),
    ("launchpad", "apps grid folders launchpad"),
    ("hidden", "hide hidden protected private lock password apps"),
    ("updates", "software update upgrade packages"),
];
const MORE_KEYWORDS: [(&str, &str); 2] =
    [("about", "computer system version restart sonata"), ("sonataupdate", "sonata update new version release what's new")];

fn own_keywords(sid: &str) -> &'static str {
    KEYWORDS.iter().chain(MORE_KEYWORDS.iter()).find(|(k, _)| *k == sid).map(|(_, w)| *w).unwrap_or("")
}

/// A section's search words: a merged section is found by its parts' words
/// and titles (as Python builds them, the titles translated when read).
pub fn keywords(sid: &str) -> String {
    match PARTS.iter().find(|(sec, _)| *sec == sid) {
        None => own_keywords(sid).to_string(),
        Some((_, parts)) => {
            let mut words: Vec<String> = parts.iter().map(|p| own_keywords(p).to_string()).collect();
            words.extend(parts.iter().map(|p| PART_TITLES.iter().find(|(k, _)| k == p).map(|(_, t)| sonata_i18n::gettext(t)).unwrap_or_default()));
            words.join(" ").trim().to_string()
        }
    }
}

/// The sidebar's filter: every word of `q` (already casefolded) in the
/// section's title or its keywords.
pub fn matches(sid: &str, title: &str, q: &str) -> bool {
    let words = format!("{title} {}", keywords(sid)).to_lowercase();
    q.split_whitespace().all(|w| words.contains(w))
}

/// Sections showing settings other places change too (Control Center, the
/// menu bar, the Setup Assistant, another section here): built again when
/// shown if one of these files changed since (no stale switches).
const PAGE_CONFIGS: [(&str, &[&str]); 13] = [
    ("sound", &["sounds"]),
    ("displays", &["displays", "nightshift"]),
    ("wallpaper", &["system"]),
    ("datetime", &["topbar"]),
    ("notifications", &["notifications"]),
    ("privacy", &["security", "system"]),
    ("accessibility", &["appearance", "system"]),
    ("appearance", &["appearance", "dock", "system"]),
    ("dock", &["dock", "system"]),
    ("menubar", &["topbar", "appearance"]),
    ("gamepad", &["gamepad"]),
    ("desktop", &["dock", "desktop", "system"]),
    ("controlcenter", &["controlcenter"]),
];

fn own_configs(part: &str) -> &'static [&'static str] {
    PAGE_CONFIGS.iter().find(|(k, _)| *k == part).map(|(_, c)| *c).unwrap_or(&[])
}

/// The config files a section shows (a merged section: every part's, once).
pub fn page_configs(sid: &str) -> Vec<&'static str> {
    let mut out: Vec<&'static str> = Vec::new();
    for p in parts_of(sid) {
        for n in own_configs(p) {
            if !out.contains(n) {
                out.push(n);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_old_id_opens_its_section() {
        let ids: Vec<&str> = SECTIONS.iter().map(|s| s.0).collect();
        for (sec, parts) in PARTS {
            assert!(ids.contains(&sec));
            for p in parts {
                assert_eq!(section_of(p), sec);
                assert!(ids.contains(&section_of(p)), "{p}");
            }
        }
        assert_eq!(section_of("wallpaper"), "displays");
        assert_eq!(section_of("updates"), "about");
        assert_eq!(section_of("wifi"), "wifi");
        assert_eq!(parts_of("about"), vec!["about", "sonataupdate", "updates"]);
        assert_eq!(parts_of("wifi"), vec!["wifi"]);
    }

    #[test]
    fn a_merged_section_is_found_by_its_parts() {
        assert!(matches("displays", "Displays", "mountains")); // (Wallpaper's own words)
        assert!(matches("about", "About", "software update"));
        assert!(matches("keyboard", "Keyboard", "hotkeys"));
        assert!(!matches("wifi", "Wi-Fi", "hotkeys"));
        assert_eq!(page_configs("displays"), vec!["displays", "nightshift", "system"]);
        assert_eq!(page_configs("wifi"), Vec::<&str>::new());
    }
}
