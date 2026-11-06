//! Sonata's CSS templates: `css.json` next to this crate, one list of
//! blocks per module. The keys are the Python files they were exported
//! from ("clock/window.py"...) -- kept as names, since every
//! register_module() call uses them. css.json is where a style is changed
//! now (docs/GUIDE.md, "Design tokens and CSS"); `%(token)s` placeholders
//! are filled from tokens.json.
//!
//! ```no_run
//! sonata_ui::css::register_module("clock/pages.py");
//! ```

use crate::tokens::Values;
use serde_json::Value;
use std::sync::OnceLock;

const CSS_JSON: &str = include_str!("../css.json");
const TOKENS_JSON: &str = include_str!("../tokens.json");

fn table() -> &'static Value {
    static T: OnceLock<Value> = OnceLock::new();
    T.get_or_init(|| serde_json::from_str(CSS_JSON).expect("css.json: valid JSON"))
}

/// FRAME's numbers as CSS lengths ("52px"), for a template's own placeholders.
fn frame() -> &'static Values {
    static F: OnceLock<Values> = OnceLock::new();
    F.get_or_init(|| {
        let v: Value = serde_json::from_str(TOKENS_JSON).unwrap_or_default();
        v["frame"]
            .as_object()
            .map(|m| {
                m.iter()
                    .map(|(k, x)| (k.clone(), if x.is_number() { format!("{x}px") } else { x.as_str().unwrap_or("").to_string() }))
                    .collect()
            })
            .unwrap_or_default()
    })
}

/// The templates of `module` ("ui/controls.py"...): (key, css, local placeholders).
pub fn blocks(module: &str) -> Vec<(String, String, Values)> {
    let Some(list) = table()[module].as_array() else {
        eprintln!("sonata-ui: no CSS for {module} in css.json");
        return Vec::new();
    };
    list.iter()
        .map(|b| {
            let css = b["css"].as_str().unwrap_or("").to_string();
            let mut local: Values = b["local"]
                .as_object()
                .map(|m| m.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string())).collect())
                .unwrap_or_default();
            for (k, v) in frame() {
                if (css.contains(&format!("%({k})s")) || css.contains(&format!("%({k})d"))) && !local.contains_key(k) {
                    local.insert(k.clone(), v.clone());
                }
            }
            (b["key"].as_str().unwrap_or(module).to_string(), css, local)
        })
        .collect()
}

/// Register every template of `module`, as its import does in Python.
pub fn register_module(module: &str) {
    register_module_with(module, &[]);
}

/// Placeholders the exporter can't fill (a computed keyword: `fade_ms=
/// ui.tokens.ms(FADE_MS)`, `bar_h=BAR_H`): the Rust component passes
/// them to register_module_with. The test below keeps this list honest.
pub const FILLED_BY_CALLER: &[(&str, &str)] = &[
    ("shell/launchpad_window.py", "open_ms"),
    ("shell/launchpad_window.py", "close_ms"),
    ("shell/launchpad_window.py", "fade_ms"),
    ("shell/notifications.py", "nc_slide"),
    ("shell/gamebar.py", "dim"),
    ("shell/launchpad.py", "fold_in"),
    ("shell/launchpad.py", "fold_out"),
    ("shell/topbar.py", "bar_h"),
    ("shell/topbar.py", "item_h"),
];

/// register_module, with values for the template's own computed
/// placeholders (`("fade_ms", tokens::ms(150).to_string())`).
pub fn register_module_with(module: &str, extra: &[(&str, String)]) {
    for (key, css, mut local) in blocks(module) {
        for (k, v) in extra {
            local.insert(k.to_string(), v.clone());
        }
        crate::theme::register_with(&key, &css, local);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: the colour tags' dots in Files turned into squares -- files/
    /// tags.py registers its CSS inside a function, which the exporter
    /// skipped, so the Rust Files had no border-radius for them.
    #[test]
    fn test_tag_dots_drawn_square() {
        let blocks = blocks("files/tags.py");
        assert!(blocks.iter().any(|(_, css, _)| css.contains(".fs-tag {") && css.contains("border-radius: 99px")));
    }

    #[test]
    fn every_placeholder_has_a_value() {
        let mut known: Values = crate::tokens::palette(false);
        for k in ["sidebar_material", "panel_material", "dock_material", "bar_material", "launchpad_material"] {
            known.insert(k.into(), String::new());
        }
        for module in table().as_object().unwrap().keys() {
            for (key, css, local) in blocks(module) {
                let mut rest = css.as_str();
                while let Some(i) = rest.find("%(") {
                    let end = rest[i..].find(')').map(|e| i + e).unwrap();
                    let name = &rest[i + 2..end];
                    let caller = FILLED_BY_CALLER.contains(&(module.as_str(), name));
                    assert!(known.contains_key(name) || local.contains_key(name) || caller, "{module} {key}: no {name}");
                    rest = &rest[end..];
                }
            }
        }
    }
}
