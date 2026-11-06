//! The menu bar's height (shell/menubar_size.py): 32 px like the menu bar
//! of the MacBooks with a notch (the default, Vini), or 24 px (Big Sur) --
//! Settings > Menu Bar > "Taller menu bar". Read once by each process that
//! lays things out under it (menu bar, notifications, desktop icons);
//! Settings restarts the menu bar on a change.

use serde_json::json;

pub const STANDARD_H: i32 = 24;
pub const TALL_H: i32 = 32;

pub fn height() -> i32 {
    let cfg = sonata_config::load("topbar", &json!({"tall": true}));
    if cfg.get("tall").is_none_or(truthy) { TALL_H } else { STANDARD_H }
}

/// Python's truth: a hand-edited `"tall": 0` or `""` is off too.
fn truthy(v: &serde_json::Value) -> bool {
    match v {
        serde_json::Value::Null => false,
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::Number(n) => n.as_f64() != Some(0.0),
        serde_json::Value::String(s) => !s.is_empty(),
        serde_json::Value::Array(a) => !a.is_empty(),
        serde_json::Value::Object(o) => !o.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_height_from_the_setting() {
        let _env = crate::testenv::env();
        assert_eq!(height(), 32); // taller by default (Vini)
        sonata_config::update("topbar", &json!({"tall": false})).unwrap();
        assert_eq!(height(), 24);
        sonata_config::update("topbar", &json!({"tall": true})).unwrap();
        assert_eq!(height(), 32);
    }
}
