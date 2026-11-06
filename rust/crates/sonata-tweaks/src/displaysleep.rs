//! When the display goes dark (sonata2/displaysleep.py; Settings > Battery
//! > "Turn display off after").
//!
//! Sonata darkens it itself (the shell's idle policy: black and the
//! backlight at zero on a laptop; any key or move brings it back).
//! Wayfire's own display timeout stays off (idle/dpms_timeout = -1): it
//! powered the panel off, and on Vini's laptop (NVIDIA) it never came back
//! -- "Atomic commit failed: Permission denied" -- until the lid was
//! closed and opened.

use serde_json::{json, Value};
use sonata_backend::wfconfig;

const NAME: &str = "display";
pub const DEFAULT: i64 = 600;

fn as_int(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    }
}

/// The first time: Wayfire's value (from before the lock while locked), 0 counting as unset.
pub fn first_value(saved: Option<&str>, wayfire: &str) -> i64 {
    let raw = saved.filter(|s| !s.is_empty()).unwrap_or(if wayfire.is_empty() { "600" } else { wayfire });
    match raw.trim().parse::<i64>() {
        Ok(0) | Err(_) => DEFAULT,
        Ok(v) => v,
    }
}

/// Seconds without input before the display goes dark; <= 0 never. The
/// first time: what Wayfire's timeout was set to (Settings wrote it there).
pub fn seconds() -> i64 {
    let cfg = crate::util::load(NAME, json!({"off_after": null}));
    match cfg.get("off_after") {
        Some(Value::Null) | None => {}
        Some(v) => return as_int(v).unwrap_or(DEFAULT),
    }
    let saved = sonata_shell::lockdisplay::saved();
    let saved = saved.get("idle/dpms_timeout").map(|v| v.as_str().map(String::from).unwrap_or_else(|| v.to_string()));
    let old = first_value(saved.as_deref(), &wfconfig::get("idle", "dpms_timeout", &DEFAULT.to_string()));
    let _ = sonata_config::update(NAME, &json!({"off_after": old}));
    old
}

pub fn set_seconds(value: i64) {
    let _ = sonata_config::update(NAME, &json!({"off_after": value}));
    compositor_off();
    // (the login screen going dark after the same time is shared with it by
    // Settings > Displays: monitors.share_with_login_screen)
}

/// Wayfire never powers the display off itself; true when it had to be changed.
pub fn compositor_off() -> bool {
    seconds(); // (the old value kept first)
    if wfconfig::get("idle", "dpms_timeout", "").trim() == "-1" {
        return false;
    }
    wfconfig::set("idle", "dpms_timeout", "-1");
    true
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_first_value_comes_from_wayfire() {
        use super::first_value;
        assert_eq!(first_value(None, "300"), 300);
        assert_eq!(first_value(Some("120"), "-1"), 120);
        assert_eq!(first_value(None, "0"), 600);
        assert_eq!(first_value(None, "x"), 600);
        assert_eq!(first_value(None, "-1"), -1);
    }
}
