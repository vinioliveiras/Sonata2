//! Sonata's desktop settings (prefs.py): Dark Mode, wallpaper, fonts,
//! cursor, title bar buttons... in `~/.config/sonata2/system.json`, keyed
//! like the settings every Linux app knows
//! (`"org.gnome.desktop.interface/color-scheme": "prefer-dark"`). Apps get
//! them from Sonata's settings portal. The desktop's own setting: the
//! shared `sonata2` folder even on trial.

use serde_json::{Map, Value};

pub const I: &str = "org.gnome.desktop.interface";
pub const WM: &str = "org.gnome.desktop.wm.preferences";
pub const BG: &str = "org.gnome.desktop.background";
pub const P: &str = "org.gnome.desktop.privacy";

pub const BOOLS: [&str; 7] = ["enable-animations", "gtk-enable-primary-paste", "overlay-scrolling", "cursor-blink", "remember-recent-files", "remove-old-trash-files", "enabled"];
pub const INTS: [&str; 2] = ["cursor-size", "old-files-age"];
pub const DOUBLES: [&str; 1] = ["text-scaling-factor"];

fn wallpaper(name: &str) -> String {
    format!("file://{}", crate::data_path(&format!("wallpapers/{name}.jpg")).display())
}

/// Sonata's look (macOS), what a fresh session starts with.
pub fn defaults() -> Vec<(String, String)> {
    let i = |k: &str, v: &str| (format!("{I}/{k}"), v.to_string());
    vec![
        i("color-scheme", "default"),
        i("gtk-theme", "Sonata-Light"),
        i("icon-theme", "Sonata"),
        i("cursor-theme", "Sonata-Cursors"),
        i("cursor-size", "24"),
        i("accent-color", "blue"),
        i("text-scaling-factor", "1.0"),
        i("enable-animations", "true"),
        i("font-antialiasing", "grayscale"),
        i("font-hinting", "slight"),
        i("gtk-enable-primary-paste", "false"),
        i("overlay-scrolling", "true"),
        i("font-name", "Inter 10"),
        i("document-font-name", "Inter 10"),
        i("monospace-font-name", "Monospace 10"),
        i("clock-format", "24h"),
        i("cursor-blink", "true"),
        (format!("{WM}/button-layout"), "close,minimize,maximize:".into()),
        (format!("{WM}/action-double-click-titlebar"), "toggle-maximize".into()),
        (format!("{WM}/titlebar-font"), "Inter Variable Semi-Bold 10".into()),
        // Sonata's own "Mountains": snowy peaks in Light, under the stars in Dark
        (format!("{BG}/picture-uri"), wallpaper("snow-peaks")),
        (format!("{BG}/picture-uri-dark"), wallpaper("starry-night")),
        (format!("{BG}/picture-options"), "zoom".into()),
        (format!("{P}/remember-recent-files"), "true".into()),
        (format!("{P}/remove-old-trash-files"), "false".into()),
        (format!("{P}/old-files-age"), "30".into()),
        ("org.gnome.system.location/enabled".into(), "false".into()),
    ]
}

pub fn path() -> std::path::PathBuf {
    crate::config_home().join(crate::NAME).join("system.json")
}

/// system.json, read once by callers reading many keys.
pub fn load() -> Map<String, Value> {
    match std::fs::read(path()).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => m,
        _ => Map::new(),
    }
}

fn plain(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => crate::to_python_json(other),
    }
}

/// The value: the stored one, else Sonata's default, else `default`.
pub fn get(schema: &str, key: &str, default: &str, data: &Map<String, Value>) -> String {
    let k = format!("{schema}/{key}");
    if let Some(v) = data.get(&k) {
        return plain(v);
    }
    defaults().into_iter().find(|(d, _)| *d == k).map(|(_, v)| v).unwrap_or_else(|| default.to_string())
}

/// A setting typed for the portal.
#[derive(Debug, Clone, PartialEq)]
pub enum Typed {
    B(bool),
    I(i32),
    D(f64),
    S(String),
}

pub fn typed(schema: &str, key: &str, data: &Map<String, Value>) -> Typed {
    let v = get(schema, key, "", data);
    if BOOLS.contains(&key) {
        Typed::B(v == "true")
    } else if INTS.contains(&key) {
        Typed::I(v.parse::<f64>().map(|f| f as i32).unwrap_or(0))
    } else if DOUBLES.contains(&key) {
        Typed::D(v.parse().unwrap_or(1.0))
    } else {
        Typed::S(v)
    }
}

/// {schema: [keys]} Sonata knows, in order.
pub fn keys(data: &Map<String, Value>) -> Vec<(String, Vec<String>)> {
    let mut out: Vec<(String, Vec<String>)> = Vec::new();
    for k in defaults().into_iter().map(|(k, _)| k).chain(data.keys().cloned()) {
        let Some((schema, key)) = k.rsplit_once('/') else { continue };
        match out.iter_mut().find(|(s, _)| s == schema) {
            Some((_, ks)) => {
                if !ks.iter().any(|x| x == key) {
                    ks.push(key.to_string());
                }
            }
            None => out.push((schema.to_string(), vec![key.to_string()])),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefs_typed_from_stored_or_default() {
        let mut data = Map::new();
        data.insert(format!("{I}/color-scheme"), Value::String("prefer-dark".into()));
        data.insert(format!("{I}/cursor-size"), Value::String("32".into()));
        data.insert("x.y/z".into(), Value::String("v".into()));
        assert_eq!(typed(I, "color-scheme", &data), Typed::S("prefer-dark".into()));
        assert_eq!(typed(I, "cursor-size", &data), Typed::I(32));
        assert_eq!(typed(I, "enable-animations", &data), Typed::B(true));
        assert_eq!(typed(I, "text-scaling-factor", &data), Typed::D(1.0));
        let k = keys(&data);
        assert_eq!(k[0].0, I);
        assert!(k.iter().any(|(s, ks)| s == "x.y" && ks == &["z"]));
    }
}
