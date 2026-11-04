//! The window frame as the user set it (ui/tokens.py's frame(),
//! button_layout, user_button_size, wayfire_color...), without GTK: the
//! title bars, traffic lights, Steam's buttons and prefs read it.
//! The theme's own values come from sonata-ui's tokens.json ("frame").

use serde_json::{Map, Value};

/// The traffic lights' colours (tokens.TL_COLORS).
pub const TL_COLORS: [(&str, &str); 4] = [("close", "#ff5f57"), ("minimize", "#febc2e"), ("maximize", "#28c840"), ("restore", "#28c840")];
pub const BUTTONS: [&str; 3] = ["close", "minimize", "maximize"];
pub const BUTTON_SIZE_RANGE: (i64, i64) = (10, 20);
/// tokens.FRAME's values (tokens.json "frame").
pub const DOT: i64 = 12;
pub const DOT_GAP: i64 = 8;
pub const DOT_LEFT: f64 = 13.0;
pub const DOT_TOP: f64 = 14.0;
pub const RADIUS: i64 = 10;
pub const TITLE_FONT: &str = "Inter Variable Semi-Bold 10";

pub fn tl_color(name: &str) -> &'static str {
    TL_COLORS.iter().find(|(n, _)| *n == name).map(|(_, c)| *c).unwrap_or("#28c840")
}

#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    pub radius: i64,
    pub buttons_side: String,
    /// from the window's edge inwards
    pub buttons: Vec<String>,
    pub dot: i64,
    pub dot_gap: i64,
    pub dot_left: f64,
    pub dot_top: f64,
}

/// appearance.json as stored (no defaults filled in).
pub fn appearance() -> Map<String, Value> {
    sonata_config::stored("appearance")
}

/// A stored order of the window buttons: close, minimize and maximize, each
/// once; anything else is macOS' order.
pub fn buttons_order(value: Option<&Value>) -> Vec<String> {
    let default: Vec<String> = BUTTONS.iter().map(|s| s.to_string()).collect();
    let Some(arr) = value.and_then(Value::as_array) else { return default };
    let order: Vec<String> = arr.iter().map(|v| v.as_str().map(String::from).unwrap_or_else(|| v.to_string())).collect();
    let (mut a, mut b) = (order.clone(), default.clone());
    a.sort();
    b.sort();
    if order.len() == default.len() && a == b { order } else { default }
}

pub fn buttons_side(cfg: &Map<String, Value>) -> String {
    match cfg.get("buttons_side").and_then(Value::as_str) {
        Some(s @ ("left" | "right")) => s.to_string(),
        _ => "left".into(),
    }
}

/// The window buttons' size in effect ("button_size"; the theme's when not picked).
pub fn button_size(cfg: &Map<String, Value>) -> i64 {
    match cfg.get("button_size") {
        Some(Value::Number(n)) => n.as_f64().map(|b| (b.round_ties_even() as i64).clamp(BUTTON_SIZE_RANGE.0, BUTTON_SIZE_RANGE.1)).unwrap_or(DOT),
        _ => DOT,
    }
}

pub fn user_button_size() -> i64 {
    button_size(&appearance())
}

/// The window radius in effect (appearance.json "radius" > "window").
pub fn window_radius(cfg: &Map<String, Value>) -> i64 {
    cfg.get("radius").and_then(|r| r.get("window")).and_then(Value::as_f64).map(|r| (r.round_ties_even() as i64).clamp(0, 24)).unwrap_or(RADIUS)
}

/// How much further in what sits by a corner goes when it is rounder than designed.
pub fn corner_extra(radius: f64, designed: f64) -> f64 {
    ((radius - designed) / 2.0).max(0.0)
}

pub fn frame_from(cfg: &Map<String, Value>) -> Frame {
    let r = window_radius(cfg);
    let size = button_size(cfg);
    let mv = corner_extra(r as f64, RADIUS as f64) + (size - DOT) as f64 / 2.0;
    Frame {
        radius: r,
        buttons_side: buttons_side(cfg),
        buttons: buttons_order(cfg.get("buttons_order")),
        dot: size,
        dot_gap: ((DOT_GAP * size) as f64 / DOT as f64).round_ties_even() as i64,
        dot_left: DOT_LEFT + mv,
        dot_top: DOT_TOP + mv,
    }
}

pub fn frame() -> Frame {
    frame_from(&appearance())
}

/// GTK's "button-layout": "close,minimize,maximize:" on the left,
/// ":maximize,minimize,close" on the right.
pub fn button_layout(f: &Frame) -> String {
    if f.buttons_side == "left" {
        format!("{}:", f.buttons.join(","))
    } else {
        format!(":{}", f.buttons.iter().rev().cloned().collect::<Vec<_>>().join(","))
    }
}

/// 'rgba(r, g, b, a)' or '#rrggbb' -> Wayfire's '#rrggbbaa' (premultiplied for pixdecor).
pub fn wayfire_color(css: &str, premultiplied: bool) -> String {
    let css = css.trim();
    if css.starts_with('#') {
        return if css.len() == 7 { format!("{css}ff") } else { css.to_string() };
    }
    let inner = css.split_once('(').and_then(|(_, r)| r.split_once(')')).map(|(i, _)| i).unwrap_or("");
    let n: Vec<f64> = inner.split(',').map(|x| x.trim().parse().unwrap_or(0.0)).collect();
    let g = |i: usize| n.get(i).copied().unwrap_or(0.0);
    let a = if n.len() > 3 { g(3) } else { 1.0 };
    let k = if premultiplied { a } else { 1.0 };
    let b = |v: f64| v.round_ties_even() as i64;
    format!("#{:02x}{:02x}{:02x}{:02x}", b(g(0) * k), b(g(1) * k), b(g(2) * k), b(255.0 * a))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn frame_follows_the_users_side_order_size_and_radius() {
        let f = frame_from(&Map::new());
        assert_eq!((f.dot, f.dot_gap, f.dot_left, f.buttons_side.as_str()), (12, 8, 13.0, "left"));
        assert_eq!(button_layout(&f), "close,minimize,maximize:");
        let cfg = json!({"buttons_side": "right", "buttons_order": ["minimize", "close", "maximize"], "button_size": 16, "radius": {"window": 14}});
        let f = frame_from(cfg.as_object().unwrap());
        assert_eq!(button_layout(&f), ":maximize,close,minimize");
        assert_eq!((f.dot, f.dot_gap, f.dot_left, f.dot_top), (16, 11, 17.0, 18.0));
        assert_eq!(buttons_order(Some(&json!(["close", "close", "maximize"]))), BUTTONS);
        assert_eq!(button_size(json!({"button_size": 99}).as_object().unwrap()), 20);
    }

    #[test]
    fn wayfire_colors() {
        assert_eq!(wayfire_color("#ff5f57", false), "#ff5f57ff");
        assert_eq!(wayfire_color("rgba(255, 255, 255, 0.14)", false), "#ffffff24");
        assert_eq!(wayfire_color("rgba(200, 100, 0, 0.5)", true), "#64320080");
        assert_eq!(wayfire_color("rgb(9, 9, 11)", false), "#09090bff");
    }
}
