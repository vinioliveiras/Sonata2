//! Glass (frosted, see-through materials), set per part of the desktop
//! (sonata2/ui/glass.py): Settings > Appearance > Glass & Transparency,
//! appearance.json "glass". Each part is on (blurred, see-through) or
//! solid, with how see-through ("alpha"; None = the theme's own). These
//! become the material tokens (panel_material, sidebar_material...).

use crate::tokens::Values;
use serde_json::{json, Map, Value};

pub const ITEMS: [&str; 5] = ["dock", "menubar", "menus", "windows", "launchpad"];
const ALPHA_RANGE: (f64, f64) = (0.0, 0.97);

/// glass token -> solid token, per part
fn materials(item: &str) -> (&'static str, &'static str) {
    match item {
        "dock" => ("glass_tint", "solid_tint"),
        "menubar" => ("bar_bg", "window_bg"),
        "windows" => ("window_glass", "sidebar_bg"),
        _ => ("glass_tint", "menu_bg"), // menus, launchpad
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Part {
    pub on: bool,
    pub alpha: Option<f64>,
}

fn clamp_alpha(a: f64) -> f64 {
    (a.clamp(ALPHA_RANGE.0, ALPHA_RANGE.1) * 1000.0).round_ties_even() / 1000.0
}

/// Each part's setting, from appearance.json's content. Before this setting
/// existed one switch (dock.json "glass") made every part solid: that
/// choice stands until a part is set.
pub fn settings_from(appearance: &Map<String, Value>, legacy_on: bool) -> Vec<(&'static str, Part)> {
    let empty = Map::new();
    let raw = appearance.get("glass").and_then(Value::as_object).unwrap_or(&empty);
    ITEMS
        .iter()
        .map(|item| {
            let part = raw.get(*item).and_then(Value::as_object).unwrap_or(&empty);
            let on = part.get("on").map(truthy).unwrap_or(legacy_on);
            let alpha = part.get("alpha").and_then(Value::as_f64).map(clamp_alpha);
            (*item, Part { on, alpha })
        })
        .collect()
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The settings as stored now.
pub fn settings() -> Vec<(&'static str, Part)> {
    let appearance = sonata_config::stored("appearance");
    let legacy = sonata_config::load("dock", &json!({"glass": true}));
    settings_from(&appearance, legacy.get("glass").map(truthy).unwrap_or(true))
}

/// 'rgba(r, g, b, a)' (or rgb / #rrggbb) with another alpha.
pub fn with_alpha(css: &str, alpha: f64) -> String {
    let css = css.trim();
    let (r, g, b) = if let Some(h) = css.strip_prefix('#') {
        let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0) as f64;
        (p(0), p(2), p(4))
    } else {
        let inner = css.split_once('(').and_then(|(_, r)| r.split_once(')')).map(|(i, _)| i).unwrap_or("");
        let n: Vec<f64> = inner.split(',').take(3).map(|x| x.trim().parse().unwrap_or(0.0)).collect();
        (n.first().copied().unwrap_or(0.0), n.get(1).copied().unwrap_or(0.0), n.get(2).copied().unwrap_or(0.0))
    };
    format!("rgba({}, {}, {}, {alpha:.3})", r.round_ties_even(), g.round_ties_even(), b.round_ties_even())
}

/// The colour of a part's background: its glass (with the chosen alpha)
/// when on and the compositor blurs (`glass_ok`), else its solid one.
pub fn material(item: &str, tokens: &Values, glass_ok: bool, parts: &[(&str, Part)]) -> String {
    let part = parts.iter().find(|(i, _)| *i == item).map(|(_, p)| *p).unwrap_or(Part { on: true, alpha: None });
    let (glass, solid) = materials(item);
    let get = |k: &str| tokens.get(k).cloned().unwrap_or_default();
    if !(glass_ok && part.on) {
        return get(solid);
    }
    match part.alpha {
        Some(a) => with_alpha(&get(glass), a),
        None => get(glass),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn with_alpha_matches_glass_py() {
        assert_eq!(with_alpha("rgba(30, 30, 32, 0.72)", 0.5), "rgba(30, 30, 32, 0.500)");
        assert_eq!(with_alpha("#ff9f0a", 0.25), "rgba(255, 159, 10, 0.250)");
        assert_eq!(with_alpha("rgb(1.4, 2.5, 3.6)", 1.0), "rgba(1, 2, 4, 1.000)");
    }

    #[test]
    fn settings_fill_defaults_and_follow_the_old_switch() {
        let cfg = json!({"glass": {"dock": {"on": false, "alpha": 2.0}, "menus": {"alpha": 0.4}}});
        let s = settings_from(cfg.as_object().unwrap(), true);
        assert_eq!(s[0], ("dock", Part { on: false, alpha: Some(0.97) }));
        assert_eq!(s[2], ("menus", Part { on: true, alpha: Some(0.4) }));
        let old = settings_from(&Map::new(), false);
        assert!(old.iter().all(|(_, p)| !p.on));
    }

    #[test]
    fn solid_without_the_compositors_blur() {
        let mut t = Values::new();
        t.insert("glass_tint".into(), "rgba(1, 2, 3, 0.5)".into());
        t.insert("menu_bg".into(), "#eeeeee".into());
        let parts = settings_from(&Map::new(), true);
        assert_eq!(material("menus", &t, false, &parts), "#eeeeee");
        assert_eq!(material("menus", &t, true, &parts), "rgba(1, 2, 3, 0.5)");
    }
}
