//! The CSS of the parts already ported, taken from the Python modules
//! themselves (tools/export_tokens.py -> css.json): one source for both,
//! so a style changed in Python is the Rust one's after an export.
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
    T.get_or_init(|| serde_json::from_str(CSS_JSON).expect("css.json is generated: valid JSON"))
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
        eprintln!("sonata-ui: no CSS exported for {module} (tools/export_tokens.py)");
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
                if css.contains(&format!("%({k})s")) && !local.contains_key(k) {
                    local.insert(k.clone(), v.clone());
                }
            }
            (b["key"].as_str().unwrap_or(module).to_string(), css, local)
        })
        .collect()
}

/// Register every template of `module`, as its import does in Python.
pub fn register_module(module: &str) {
    for (key, css, local) in blocks(module) {
        crate::theme::register_with(&key, &css, local);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
                    let end = rest[i..].find(")s").map(|e| i + e).unwrap();
                    let name = &rest[i + 2..end];
                    assert!(known.contains_key(name) || local.contains_key(name), "{module} {key}: no {name}");
                    rest = &rest[end..];
                }
            }
        }
    }
}
