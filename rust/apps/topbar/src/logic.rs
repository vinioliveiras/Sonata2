//! The menu bar's rules without widgets (topbar.py's module functions and
//! the parts of Bar that decide what an item shows), so they're tested
//! as they are in Python.

use sonata_i18n::tr;
use sonata_i18n::fmt;
use std::collections::HashMap;

/// Wi-Fi as system.wifi_status() gives it: (hardware there, radio on, (joined network, signal, wired)).
pub type WifiStatus = (bool, bool, (String, i64, bool));

/// The Wi-Fi item's icon, or None: hidden (no Wi-Fi hardware and no cable).
pub fn wifi_icon(res: &WifiStatus) -> Option<String> {
    let (avail, on, (ssid, sig, wired)) = res;
    Some(if *wired && ssid.is_empty() {
        "sonata-network-symbolic".into() // a cable connection: the network globe, as in Settings (Vini)
    } else if !avail {
        return None;
    } else if !on {
        "sonata-wifi-off-symbolic".into()
    } else if ssid.is_empty() {
        "sonata-wifi-0-symbolic".into()
    } else {
        format!("sonata-wifi-{}-symbolic", signal_level(*sig))
    })
}

/// 1..3 bars for a signal strength (percent).
pub fn signal_level(sig: i64) -> i32 {
    if sig > 60 {
        3
    } else if sig > 30 {
        2
    } else {
        1
    }
}

/// backend/power.py's icon_name, the one battery icon rule: charging
/// (bolt), on power but holding -- full or a charge limit -- (plug), Low
/// Power Mode (yellow), low (red in the icon); `missing` when there's a
/// battery but no reading.
pub fn battery_icon(pct: Option<i64>, status: &str, ac: bool, profile: Option<&str>) -> String {
    let Some(pct) = pct else { return "sonata-battery-missing-symbolic".into() };
    let level = ((pct + 5).div_euclid(10) * 10).clamp(0, 100);
    let state = if status == "Charging" {
        "-charging"
    } else if ac {
        "-plugged"
    } else if profile == Some("power-saver") {
        "-saver"
    } else {
        ""
    };
    format!("sonata-battery-{level}{state}-symbolic")
}

/// The Sound item's icon for (volume, muted).
pub fn sound_icon(vol: i64, muted: bool) -> String {
    if muted || vol == 0 {
        return "sonata-volume-muted-symbolic".into();
    }
    format!("sonata-volume-{}-symbolic", if vol > 66 { 3 } else if vol > 33 { 2 } else { 1 })
}

/// [(layout, [keyboard names])]: the connected keyboards by the layout
/// they type, in their order; layout None for the ones following Input
/// Sources (own[name] ""), that group first.
pub fn keyboard_groups(keyboards: &[String], own: &HashMap<String, String>) -> Vec<(Option<String>, Vec<String>)> {
    let mut groups: Vec<(Option<String>, Vec<String>)> = Vec::new();
    for k in keyboards {
        let layout = own.get(k).filter(|l| !l.is_empty()).cloned();
        match groups.iter_mut().find(|g| g.0 == layout) {
            Some(g) => g.1.push(k.clone()),
            None => groups.push((layout, vec![k.clone()])),
        }
    }
    groups.sort_by_key(|g| g.0.is_some()); // (stable: their order otherwise)
    groups
}

/// The menu bar's short name of a layout: "br(abnt2)" -> "BR", "us(intl)" -> "US".
pub fn layout_badge(layout: &str) -> String {
    let base = layout.split('(').next().unwrap_or("");
    let base = if base.is_empty() { "?" } else { base };
    base.to_uppercase().chars().take(3).collect()
}

/// A layout's name ("us(intl)" -> "English (US, intl.)"), translated; the code itself when unknown.
pub fn layout_name(layout: &str) -> String {
    sonata_backend::system::XKB_LAYOUTS.iter().find(|(k, _)| *k == layout).map_or_else(|| layout.to_string(), |(_, n)| sonata_i18n::gettext(n))
}

/// The input sources with the i-th one first (Wayland has no "switch
/// layout" request: the chosen one becomes the first of the list, which
/// Wayfire applies at once).
pub fn use_layout(mut lays: Vec<String>, i: usize) -> Vec<String> {
    if i < lays.len() {
        let l = lays.remove(i);
        lays.insert(0, l);
    }
    lays
}

/// A device icon from its name (BlueZ's "Icon" needs another call).
pub fn bt_icon(name: &str) -> &'static str {
    let n = name.to_lowercase();
    let table: [(&[&str], &str); 6] = [
        (&["airpods", "buds", "headphone", "headset", "wh-", "wf-"], "audio-headphones-symbolic"),
        (&["speaker", "soundbar", "jbl", "boom"], "audio-speakers-symbolic"),
        (&["mouse", "mx master", "trackpad"], "input-mouse-symbolic"),
        (&["keyboard", "keys"], "input-keyboard-symbolic"),
        (&["controller", "gamepad", "xbox", "dualsense", "dualshock", "joy-con"], "input-gaming-symbolic"),
        (&["phone", "iphone", "galaxy", "pixel"], "phone-symbolic"),
    ];
    table.iter().find(|(keys, _)| keys.iter().any(|k| n.contains(k))).map_or("sonata-bluetooth-symbolic", |t| t.1)
}

/// The in-use dots: (camera dot, microphone dot, the tooltip) -- green
/// while the camera records, orange the microphone; who, on hover. macOS:
/// the camera's dot covers both.
pub fn inuse(camera: &[String], mic: &[String]) -> (bool, bool, String) {
    let mut lines = Vec::new();
    if !camera.is_empty() {
        lines.push(fmt(&tr!("Camera: {apps}"), &[("apps", &camera.join(", "))]));
    }
    if !mic.is_empty() {
        lines.push(fmt(&tr!("Microphone: {apps}"), &[("apps", &mic.join(", "))]));
    }
    (!camera.is_empty(), !mic.is_empty() && camera.is_empty(), lines.join("\n"))
}

/// The battery menu's power source line: "Power Source: Battery · Charging".
pub fn power_source(ac: bool, status: &str) -> String {
    let source = if ac { tr!("Power Adapter") } else { tr!("Battery") };
    let text = fmt(&tr!("Power Source: {source}"), &[("source", &source)]);
    if !status.is_empty() && status != "Discharging" && status != "Unknown" {
        format!("{text} · {}", sonata_i18n::gettext(status))
    } else {
        text
    }
}

/// The clock's text now in `fmt` (strftime as GLib reads it); a format
/// GLib can't read shows the default instead of nothing.
pub fn clock_text(t: &glib::DateTime, fmt: &str) -> String {
    t.format(fmt).ok().filter(|s| !s.is_empty()).or_else(|| t.format("%a %H:%M").ok()).map(|s| s.to_string()).unwrap_or_default()
}

/// Every text the clock shows on `day` in `fmt`: each hour at :59:59, AM
/// and PM. The widest of them is the clock's width all day (Vini: a room
/// for the widest date of the whole year left a gap beside the clock most
/// days; now the clock keeps one width all day and may change it at
/// midnight, like macOS).
pub fn clock_texts(day: &glib::DateTime, fmt: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for h in 0..24 {
        if let Ok(t) = glib::DateTime::new(&day.timezone(), day.year(), day.month(), day.day_of_month(), h, 59, 59.0) {
            let s = clock_text(&t, fmt);
            if !out.contains(&s) {
                out.push(s);
            }
        }
    }
    out
}

/// Wi-Fi's networks for its menu: the joined one, then up to ten others.
pub fn split_networks(networks: &[sonata_backend::system::WifiNetwork]) -> (Option<&sonata_backend::system::WifiNetwork>, Vec<&sonata_backend::system::WifiNetwork>) {
    (networks.iter().find(|n| n.connected), networks.iter().filter(|n| !n.connected).take(10).collect())
}

/// A connectivity toggle's caption: On / Off / Unavailable (no such radio).
pub fn on_off(state: Option<bool>) -> String {
    match state {
        Some(true) => tr!("On"),
        Some(false) => tr!("Off"),
        None => tr!("Unavailable"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LAPTOP: &str = "AT Translated Set 2 keyboard";
    const HYPERX: &str = "HyperX Alloy Origins";
    const ITE: &str = "ITE Tech. Inc. ITE Device(8910) Keyboard";
    const MOUSE: &str = "Logitech G502 Keyboard";

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn test_by_layout() {
        let vini: HashMap<String, String> = [(MOUSE, "us(intl)"), (HYPERX, "us(intl)"), (ITE, "us(intl)"), (LAPTOP, "pt")].iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let kbs = s(&[MOUSE, HYPERX, ITE, LAPTOP]);
        assert_eq!(keyboard_groups(&kbs, &vini), vec![(Some("us(intl)".into()), s(&[MOUSE, HYPERX, ITE])), (Some("pt".into()), s(&[LAPTOP]))]);
        assert_eq!(keyboard_groups(&kbs, &HashMap::new()), vec![(None, kbs.clone())]); // all on Input Sources
        let own = HashMap::from([(HYPERX.to_string(), "us".to_string())]);
        assert_eq!(keyboard_groups(&s(&[LAPTOP, HYPERX]), &own), vec![(None, s(&[LAPTOP])), (Some("us".into()), s(&[HYPERX]))]); // Input Sources first
        assert_eq!(keyboard_groups(&[], &vini), vec![]);
    }

    #[test]
    fn test_badges() {
        assert_eq!(["br(abnt2)", "us(intl)", "pt", ""].map(layout_badge), ["BR", "US", "PT", "?"]);
    }

    /// Ctrl+Space: the one used before comes first, the rest keep their order.
    #[test]
    fn test_ctrl_space_goes_back_to_the_previous_layout() {
        assert_eq!(use_layout(s(&["pt", "us(intl)", "br"]), 1), s(&["us(intl)", "pt", "br"]));
        assert_eq!(use_layout(s(&["pt"]), 3), s(&["pt"]));
    }

    /// On a cable the menu bar showed no network at all (no Wi-Fi hardware) or an empty Wi-Fi fan.
    #[test]
    fn test_menu_bar_on_a_cable() {
        let wired = Some("sonata-network-symbolic".to_string());
        assert_eq!(wifi_icon(&(false, false, (String::new(), 0, true))), wired);
        assert_eq!(wifi_icon(&(true, true, (String::new(), 0, true))), wired);
        assert_eq!(wifi_icon(&(false, false, (String::new(), 0, false))), None);
        assert_eq!(wifi_icon(&(true, false, (String::new(), 0, false))).unwrap(), "sonata-wifi-off-symbolic");
        assert_eq!(wifi_icon(&(true, true, (String::new(), 0, false))).unwrap(), "sonata-wifi-0-symbolic");
        assert_eq!(wifi_icon(&(true, true, ("Home".into(), 80, false))).unwrap(), "sonata-wifi-3-symbolic");
        assert_eq!(wifi_icon(&(true, true, ("Home".into(), 31, true))).unwrap(), "sonata-wifi-2-symbolic");
    }

    #[test]
    fn battery_icons() {
        assert_eq!(battery_icon(None, "", false, None), "sonata-battery-missing-symbolic");
        assert_eq!(battery_icon(Some(64), "Discharging", false, None), "sonata-battery-60-symbolic");
        assert_eq!(battery_icon(Some(95), "Charging", true, None), "sonata-battery-100-charging-symbolic");
        assert_eq!(battery_icon(Some(80), "Not charging", true, None), "sonata-battery-80-plugged-symbolic");
        assert_eq!(battery_icon(Some(14), "Discharging", false, Some("power-saver")), "sonata-battery-10-saver-symbolic");
        assert_eq!(battery_icon(Some(3), "Discharging", false, None), "sonata-battery-0-symbolic");
    }

    #[test]
    fn sound_icons() {
        assert_eq!(sound_icon(70, false), "sonata-volume-3-symbolic");
        assert_eq!(sound_icon(50, false), "sonata-volume-2-symbolic");
        assert_eq!(sound_icon(10, false), "sonata-volume-1-symbolic");
        assert_eq!(sound_icon(0, false), "sonata-volume-muted-symbolic");
        assert_eq!(sound_icon(70, true), "sonata-volume-muted-symbolic");
    }

    #[test]
    fn bluetooth_device_icons() {
        assert_eq!(bt_icon("Vini's AirPods Pro"), "audio-headphones-symbolic");
        assert_eq!(bt_icon("Sony WH-1000XM4"), "audio-headphones-symbolic");
        assert_eq!(bt_icon("DualSense Wireless Controller"), "input-gaming-symbolic");
        assert_eq!(bt_icon("MX Master 3"), "input-mouse-symbolic");
        assert_eq!(bt_icon("Something"), "sonata-bluetooth-symbolic");
    }

    /// The camera's green dot covers both; the tooltip names who.
    #[test]
    fn in_use_dots() {
        let _e = crate::testenv::env();
        assert_eq!(inuse(&[], &[]), (false, false, String::new()));
        assert_eq!(inuse(&[], &s(&["Firefox"])), (false, true, "Microphone: Firefox".into()));
        assert_eq!(inuse(&s(&["Zoom"]), &s(&["Zoom", "OBS"])), (true, false, "Camera: Zoom\nMicrophone: Zoom, OBS".into()));
    }

    #[test]
    fn power_source_line() {
        let _e = crate::testenv::env();
        assert_eq!(power_source(true, "Charging"), "Power Source: Power Adapter · Charging");
        assert_eq!(power_source(false, "Discharging"), "Power Source: Battery");
    }

    /// The clock keeps one width all day: every hour of the day measured, each text once.
    #[test]
    fn clock_texts_of_a_day() {
        let tz = glib::TimeZone::utc();
        let day = glib::DateTime::new(&tz, 2026, 11, 4, 9, 3, 0.0).unwrap();
        let t = clock_texts(&day, "%H:%M");
        assert_eq!(t.len(), 24);
        assert!(t.contains(&"00:59".to_string()) && t.contains(&"23:59".to_string()));
        assert_eq!(clock_texts(&day, "%a").len(), 1);
        assert_eq!(clock_text(&day, "%H:%M"), "09:03");
    }
}
