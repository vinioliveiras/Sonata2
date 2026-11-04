//! The menu bar's settings and what Settings reads of them, without the
//! menu bar itself (shell/topbar_prefs.py): Settings > Menu Bar and Date &
//! Time need these, not the whole menu bar.

use serde_json::{json, Map, Value};

/// topbar.json's defaults, with a show / style pair for every figure this
/// machine can show (`kinds`: stats::kinds(), gpu_<card> per card when
/// there are two or more).
pub fn defaults_for(kinds: &[String]) -> Value {
    let mut d = json!({
        "battery_percent": false, "clock_format": "%a %-d %b  %H:%M", "show_bluetooth": true,
        "show_sound": false, "show_now_playing": false, "show_tray": true,
        // every status item can be taken out of the menu bar (Settings > Menu Bar, Vini);
        // Control Center and the clock always stay, like macOS
        "show_wifi": true, "show_battery": true, "show_spotlight": true, "show_input": true,
        // performance figures (statsui), off by default; each as "text" or "graph"
        "show_cpu": false, "show_gpu": false, "show_ram": false, "show_net": false, "show_fps": false,
        "cpu_style": "text", "gpu_style": "text", "ram_style": "text", "net_style": "text",
        "fps_style": "text",
        "autohide": false, // Settings > Menu Bar: hide it like the Dock (Vini)
        "tall": true,      // the taller menu bar of new MacBooks (menubar_size; on by default, Vini)
    });
    let m: &mut Map<String, Value> = d.as_object_mut().unwrap();
    for k in kinds {
        m.entry(format!("show_{k}")).or_insert(json!(false));
        m.entry(format!("{k}_style")).or_insert(json!("text"));
    }
    d
}

/// topbar.json's defaults on this machine.
pub fn defaults() -> Value {
    defaults_for(crate::statsui::kinds())
}

/// The menu bar's settings, missing keys from the defaults.
pub fn load() -> Map<String, Value> {
    sonata_config::load("topbar", &defaults())
}

/// `%` + an optional flag (`-_0^#`) + one of `letters` somewhere in `fmt`
/// (what the Python side matches with `%[-_0^#]?[...]`).
fn has_directive(fmt: &str, letters: &[char]) -> bool {
    let c: Vec<char> = fmt.chars().collect();
    (0..c.len()).any(|i| {
        if c[i] != '%' {
            return false;
        }
        let mut j = i + 1;
        if c.get(j).is_some_and(|f| "-_0^#".contains(*f)) {
            j += 1;
        }
        c.get(j).is_some_and(|l| letters.contains(l))
    })
}

/// The clock format shows the date (%-d, %d, %e: the day of the month).
/// Vini: Settings' "Show the date" looked for "%d" only and stayed off
/// with the default "%-d".
pub fn shows_date(fmt: &str) -> bool {
    has_directive(fmt, &['d', 'e'])
}

/// The clock format is 24-hour (%H, %-H, %k).
pub fn is_24h(fmt: &str) -> bool {
    has_directive(fmt, &['H', 'k'])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// settings/app.py's clock_format: the format for Date & Time's two switches.
    fn clock_format(date: bool, h24: bool) -> String {
        format!("{}{}", if date { "%a %-d %b  " } else { "%a " }, if h24 { "%H:%M" } else { "%-I:%M %p" })
    }

    #[test]
    fn test_default_shows_date_in_24_hours() {
        let d = defaults_for(&[]);
        let fmt = d["clock_format"].as_str().unwrap();
        assert!(shows_date(fmt) && is_24h(fmt));
    }

    #[test]
    fn test_every_format_settings_writes_reads_back() {
        for date in [true, false] {
            for h24 in [true, false] {
                let fmt = clock_format(date, h24);
                assert_eq!((shows_date(&fmt), is_24h(&fmt)), (date, h24), "{fmt}");
            }
        }
    }

    #[test]
    fn test_other_spellings() {
        assert!(shows_date("%e %b %k:%M"));
        assert!(is_24h("%e %b %k:%M"));
        assert!(!shows_date("%a %-I:%M %p"));
        assert!(!is_24h("%a %-I:%M %p"));
        assert!(!shows_date(""));
        assert!(!shows_date("%"));
        assert!(shows_date("%_d"));
    }

    #[test]
    fn each_card_gets_its_own_switch() {
        let d = defaults_for(&["cpu".into(), "gpu_amd".into(), "gpu_nvidia".into()]);
        assert_eq!(d["show_gpu_nvidia"], json!(false));
        assert_eq!(d["gpu_amd_style"], json!("text"));
        assert_eq!(d["tall"], json!(true));
        assert_eq!(d["show_tray"], json!(true));
    }
}
