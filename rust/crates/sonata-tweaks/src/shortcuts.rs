//! Keyboard shortcuts Settings can show and change (sonata2/shortcuts.py;
//! Settings > Keyboard Shortcuts). They are Wayfire options -- key bindings
//! of the command, wm-actions, grid, scale, expo, vswitch and core plugins
//! -- so changing one is a write to Sonata's Wayfire overrides, applied at
//! once.
//!
//! A binding is Wayfire's activator text: alternatives split by "|", each
//! "<super> <shift> KEY_S", a modifier alone ("<super>": pressed and
//! released) or a gesture ("swipe up 3"). No GTK here.

use sonata_i18n::{fmt, gettext, n_, tr};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;

/// Wayfire's order when written
pub const MODS: [&str; 4] = ["<ctrl>", "<alt>", "<shift>", "<super>"];

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub struct Shortcut {
    pub id: &'static str,
    /// shown translated
    pub title: &'static str,
    pub group: &'static str,
    /// Wayfire plugin section
    pub section: &'static str,
    /// its option
    pub key: &'static str,
}

pub const GROUPS: [&str; 5] = [n_!("Apps and Search"), n_!("Windows"), n_!("Spaces"), n_!("Screenshots"), n_!("System")];

include!("shortcuts_tables.rs");

pub fn by_id(id: &str) -> Option<&'static Shortcut> {
    SHORTCUTS.iter().find(|s| s.id == id)
}

fn mod_label(m: &str) -> Option<&'static str> {
    match m {
        "<ctrl>" => Some("Ctrl"),
        "<alt>" => Some("Alt"),
        "<shift>" => Some("Shift"),
        "<super>" => Some("Super"),
        _ => None,
    }
}

fn mod_accel(m: &str) -> Option<&'static str> {
    match m {
        "<ctrl>" => Some("<Control>"),
        "<alt>" => Some("<Alt>"),
        "<shift>" => Some("<Shift>"),
        "<super>" => Some("<Super>"),
        _ => None,
    }
}

/// Sonata's own bindings: config/wayfire.ini next to the installed binary
/// (`<prefix>/share/sonata2-rs/config/wayfire.ini`), else this clone's.
pub fn default_ini() -> PathBuf {
    if let Some(prefix) = std::env::current_exe().ok().and_then(|e| e.parent()?.parent().map(PathBuf::from)) {
        let p = prefix.join("share/sonata2-rs/config/wayfire.ini");
        if p.exists() {
            return p;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../config/wayfire.ini")
}

type Parsed = (std::time::SystemTime, HashMap<(String, String), String>);

/// One value of an ini file, read once while the file doesn't change
/// (Settings > Keyboard asks for 32 of them: the whole file read 32 times).
fn ini_value(path: &std::path::Path, section: &str, key: &str) -> Option<String> {
    static PARSED: Mutex<Option<HashMap<PathBuf, Parsed>>> = Mutex::new(None);
    let mtime = std::fs::metadata(path).and_then(|m| m.modified()).ok()?;
    let mut all = PARSED.lock().unwrap_or_else(|e| e.into_inner());
    let all = all.get_or_insert_with(HashMap::new);
    if all.get(path).is_none_or(|(t, _)| *t != mtime) {
        let text = std::fs::read_to_string(path).ok()?;
        let mut values = HashMap::new();
        let mut cur: Option<String> = None;
        for line in text.lines() {
            let s = line.trim();
            if s.starts_with('[') && s.ends_with(']') {
                cur = Some(s[1..s.len() - 1].to_string());
            } else if let (Some(c), Some((k, v)), false) = (&cur, s.split_once('='), s.starts_with('#')) {
                values.entry((c.clone(), k.trim().to_string())).or_insert_with(|| v.trim().to_string()); // (the first one, as before)
            }
        }
        all.insert(path.to_path_buf(), (mtime, values));
    }
    all.get(path).and_then(|(_, v)| v.get(&(section.to_string(), key.to_string())).cloned())
}

/// Sonata's own binding (config/wayfire.ini).
pub fn default(s: &Shortcut) -> String {
    ini_value(&default_ini(), s.section, s.key).unwrap_or_default()
}

pub fn current(s: &Shortcut) -> String {
    sonata_backend::wfconfig::get(s.section, s.key, &default(s))
}

/// "<super> KEY_Q | <alt> KEY_F4" -> ["<super> KEY_Q", "<alt> KEY_F4"].
pub fn split(binding: &str) -> Vec<String> {
    binding.split('|').filter(|p| !p.trim().is_empty()).map(|p| p.split_whitespace().collect::<Vec<_>>().join(" ")).collect()
}

pub fn join(combos: &[String]) -> String {
    combos.iter().filter(|c| !c.is_empty()).cloned().collect::<Vec<_>>().join(" | ")
}

pub fn set_binding(s: &Shortcut, combos: &[String]) {
    sonata_backend::wfconfig::set(s.section, s.key, &join(combos));
}

pub fn reset(s: &Shortcut) {
    sonata_backend::wfconfig::set(s.section, s.key, &default(s));
}

/// The shortcut already using this combo, if any.
pub fn owner(combo: &str, but: Option<&Shortcut>) -> Option<&'static Shortcut> {
    SHORTCUTS.iter().find(|s| Some(*s) != but && split(&current(s)).iter().any(|c| c == combo))
}

pub const MODIFIER_KEYS: [&str; 12] = ["LEFTCTRL", "RIGHTCTRL", "LEFTSHIFT", "RIGHTSHIFT", "LEFTALT", "RIGHTALT", "LEFTMETA", "RIGHTMETA", "CAPSLOCK", "NUMLOCK", "SCROLLLOCK", "COMPOSE"];

/// Wayfire text for a key pressed with modifiers (keycode: the X / GDK
/// hardware keycode, evdev + 8); None for a modifier key alone.
pub fn combo_from_key(keycode: u32, ctrl: bool, shift: bool, alt: bool, super_: bool) -> Option<String> {
    let name = EVDEV.iter().find(|(c, _)| keycode >= 8 && *c == keycode - 8).map(|(_, n)| *n)?;
    if MODIFIER_KEYS.contains(&name) {
        return None;
    }
    let mut parts: Vec<String> = MODS.iter().zip([ctrl, alt, shift, super_]).filter(|(_, on)| *on).map(|(m, _)| m.to_string()).collect();
    parts.push(format!("KEY_{name}"));
    Some(parts.join(" "))
}

/// shown translated: gettext(label)
fn key_label(k: &str) -> Option<&'static str> {
    Some(match k {
        "LEFT" => "←",
        "RIGHT" => "→",
        "UP" => "↑",
        "DOWN" => "↓",
        "ESC" => "Esc",
        "DOT" => ".",
        "COMMA" => ",",
        "SPACE" => n_!("Space"),
        "TAB" => "Tab",
        "ENTER" => n_!("Return"),
        "BACKSPACE" => n_!("Backspace"),
        "DELETE" => n_!("Delete"),
        "SYSRQ" | "PRINT" => n_!("Print Screen"),
        "MINUS" => "-",
        "EQUAL" => "=",
        "SLASH" => "/",
        "SEMICOLON" => ";",
        "APOSTROPHE" => "'",
        "GRAVE" => "`",
        "BACKSLASH" => "\\",
        "LEFTBRACE" => "[",
        "RIGHTBRACE" => "]",
        "PAGEUP" => n_!("Page Up"),
        "PAGEDOWN" => n_!("Page Down"),
        "DASHBOARD" => n_!("Dashboard key"),
        _ => return None,
    })
}

/// a gesture's direction (Wayfire's word -> shown, translated)
fn way(w: &str) -> String {
    let t = match w {
        "up" => n_!("up"),
        "down" => n_!("down"),
        "left" => n_!("left"),
        "right" => n_!("right"),
        "in" => n_!("in"),
        "out" => n_!("out"),
        other => return gettext(other),
    };
    gettext(t)
}

/// Python's str.title() for a key name ("F1", "Kpenter" -> "Kpenter").
fn title(s: &str) -> String {
    let mut out = String::new();
    let mut prev_alpha = false;
    for c in s.chars() {
        out.extend(if prev_alpha { c.to_lowercase().collect::<Vec<_>>() } else { c.to_uppercase().collect() });
        prev_alpha = c.is_alphabetic();
    }
    out
}

/// Words for one combo: "Super+Shift+S", "Super", "Swipe up with 3 fingers".
pub fn describe(combo: &str) -> String {
    let parts: Vec<&str> = combo.split_whitespace().collect();
    if let Some(first) = parts.first().filter(|p| matches!(**p, "swipe" | "pinch" | "edge-swipe")) {
        let how = match *first {
            "swipe" => tr!("Swipe"),
            "pinch" => tr!("Pinch"),
            _ => tr!("Swipe from the edge"),
        };
        let rest = &parts[1..];
        let fingers = rest.last().filter(|f| !f.is_empty() && f.chars().all(|c| c.is_ascii_digit())).copied().unwrap_or("");
        let words = if fingers.is_empty() { rest } else { &rest[..rest.len() - 1] };
        let w = words.iter().map(|w| way(w)).collect::<Vec<_>>().join(" ");
        return fmt(&tr!("{how} {way} with {fingers} fingers"), &[("how", &how), ("way", &w), ("fingers", fingers)]).replace("  ", " ").trim().to_string();
    }
    let mut words: Vec<String> = parts.iter().filter_map(|p| mod_label(p)).map(String::from).collect();
    for p in &parts {
        if let Some(k) = p.strip_prefix("KEY_") {
            words.push(key_label(k).map(gettext).unwrap_or_else(|| gettext(&title(k))));
        }
    }
    words.join("+")
}

fn gtk_key(k: &str) -> Option<&'static str> {
    Some(match k {
        "LEFT" => "Left",
        "RIGHT" => "Right",
        "UP" => "Up",
        "DOWN" => "Down",
        "ESC" => "Escape",
        "DOT" => "period",
        "COMMA" => "comma",
        "SPACE" => "space",
        "TAB" => "Tab",
        "ENTER" => "Return",
        "BACKSPACE" => "BackSpace",
        "DELETE" => "Delete",
        "SYSRQ" | "PRINT" => "Print",
        "MINUS" => "minus",
        "EQUAL" => "equal",
        "SLASH" => "slash",
        "SEMICOLON" => "semicolon",
        "APOSTROPHE" => "apostrophe",
        "GRAVE" => "grave",
        "BACKSLASH" => "backslash",
        "LEFTBRACE" => "bracketleft",
        "RIGHTBRACE" => "bracketright",
        "PAGEUP" => "Page_Up",
        "PAGEDOWN" => "Page_Down",
        "HOME" => "Home",
        "END" => "End",
        "INSERT" => "Insert",
        _ => return None,
    })
}

/// GTK accelerator ("<Super>d") for a shortcut label, or None (gestures, a
/// modifier alone, keys GTK has no name for).
pub fn accelerator(combo: &str) -> Option<String> {
    let parts: Vec<&str> = combo.split_whitespace().collect();
    let keys: Vec<&str> = parts.iter().filter_map(|p| p.strip_prefix("KEY_")).collect();
    if keys.len() != 1 || parts.iter().any(|p| mod_accel(p).is_none() && !p.starts_with("KEY_")) {
        return None;
    }
    let k = keys[0];
    let name = match gtk_key(k) {
        Some(n) => n.to_string(),
        None if k.chars().count() == 1 => k.to_lowercase(),
        None if k.starts_with('F') && k.len() > 1 && k[1..].chars().all(|c| c.is_ascii_digit()) => k.to_string(),
        None => return None,
    };
    let mods: String = parts.iter().filter_map(|p| mod_accel(p)).collect();
    Some(mods + &name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn combos_from_keys_and_back_to_words() {
        assert_eq!(combo_from_key(32 + 8, true, false, false, true).as_deref(), Some("<ctrl> <super> KEY_D"));
        assert_eq!(combo_from_key(125 + 8, false, false, false, true), None); // a modifier alone
        assert_eq!(split("<super> KEY_Q |  <alt>  KEY_F4"), ["<super> KEY_Q", "<alt> KEY_F4"]);
        assert_eq!(join(&["a".into(), String::new(), "b".into()]), "a | b");
        assert_eq!(accelerator("<super> <shift> KEY_S").as_deref(), Some("<Super><Shift>s"));
        assert_eq!(accelerator("<super> KEY_F12").as_deref(), Some("<Super>F12"));
        assert_eq!(accelerator("<super> KEY_LEFT").as_deref(), Some("<Super>Left"));
        assert_eq!(accelerator("swipe up 3"), None);
        assert_eq!(accelerator("<super>"), None);
        assert_eq!(accelerator("<super> KEY_KPENTER"), None);
    }

    #[test]
    fn words_for_combos() {
        sonata_i18n::use_language("en");
        assert_eq!(describe("<super> <shift> KEY_S"), "Super+Shift+S");
        assert_eq!(describe("<super>"), "Super");
        assert_eq!(describe("<ctrl> KEY_LEFT"), "Ctrl+←");
        assert_eq!(describe("<super> KEY_KPENTER"), "Super+Kpenter");
        assert_eq!(describe("swipe up 3"), "Swipe up with 3 fingers");
        assert_eq!(describe("edge-swipe left 2"), "Swipe from the edge left with 2 fingers");
    }

    #[test]
    fn every_shortcut_has_sonatas_default() {
        assert_eq!(SHORTCUTS.len(), 32);
        assert!(default_ini().is_file());
        for s in SHORTCUTS.iter().filter(|s| s.section != "core") {
            assert!(GROUPS.contains(&s.group), "{}", s.id);
        }
        assert!(!default(by_id("search").unwrap()).is_empty());
        assert_eq!(by_id("search").unwrap().title, sonata_desktop::names::SEARCH);
    }
}
