//! Design tokens -- the values of `sonata2/ui/tokens.py`, exported to
//! `tokens.json` by `tools/export_tokens.py`. tokens.py stays the one
//! place a value is changed (docs/RUST_PORT.md, "The design system").

use serde_json::Value;
use std::collections::HashMap;
use std::sync::OnceLock;

const TOKENS_JSON: &str = include_str!("../tokens.json");

pub type Values = HashMap<String, String>;

struct Table {
    light: Values,
    dark: Values,
    accents: HashMap<String, (Values, Values)>,
    animation_speed: f64,
}

fn strings(v: &Value) -> Values {
    v.as_object()
        .map(|m| {
            m.iter()
                .map(|(k, v)| (k.clone(), v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

fn table() -> &'static Table {
    static T: OnceLock<Table> = OnceLock::new();
    T.get_or_init(|| {
        let v: Value = serde_json::from_str(TOKENS_JSON).expect("tokens.json is generated: valid JSON");
        let accents = v["accents"]
            .as_object()
            .map(|m| m.iter().map(|(k, a)| (k.clone(), (strings(&a["light"]), strings(&a["dark"])))).collect())
            .unwrap_or_default();
        Table {
            light: strings(&v["light"]),
            dark: strings(&v["dark"]),
            accents,
            animation_speed: v["animation_speed"].as_f64().unwrap_or(1.0),
        }
    })
}

/// All tokens of the theme for one appearance (tokens.palette, the theme's own values).
pub fn palette(dark: bool) -> Values {
    if dark { table().dark.clone() } else { table().light.clone() }
}

/// Sonata's animation speed (tokens.ANIMATION_SPEED).
pub fn animation_speed() -> f64 {
    table().animation_speed
}

/// A designed duration (ms) at Sonata's animation speed (tokens.ms).
pub fn ms(duration: f64) -> u32 {
    if duration > 0.0 {
        ((duration / animation_speed()).round_ties_even() as u32).max(1)   // Python's round()
    } else {
        0
    }
}

/// A colour the user picked ("#rrggbb", Settings > Appearance > Accent
/// colour > the custom dot), normalised; None for a named accent.
pub fn custom_accent(name: &str) -> Option<String> {
    if !name.starts_with('#') {
        return None;
    }
    let h = name.trim().trim_start_matches('#');
    let h: String = if h.len() == 3 { h.chars().flat_map(|c| [c, c]).collect() } else { h.to_string() };
    if h.len() != 6 || !h.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    Some(format!("#{}", h.to_lowercase()))
}

fn rgb(hex: &str) -> [u8; 3] {
    let h = hex.trim_start_matches('#');
    let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0);
    [p(0), p(2), p(4)]
}

fn hex(c: [u8; 3]) -> String {
    format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2])
}

fn darker(hex_color: &str, f: f64) -> String {
    let c = rgb(hex_color);
    hex([(c[0] as f64 * f) as u8, (c[1] as f64 * f) as u8, (c[2] as f64 * f) as u8])
}

fn luminance(hex_color: &str) -> f64 {
    let ch = rgb(hex_color).map(|v| {
        let c = v as f64 / 255.0;
        if c <= 0.03928 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
    });
    0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

pub fn contrast(a: &str, b: &str) -> f64 {
    let (la, lb) = (luminance(a), luminance(b));
    let (hi, lo) = if la >= lb { (la, lb) } else { (lb, la) };
    (hi + 0.05) / (lo + 0.05)
}

const DARK_BG: &str = "#1e1e1e";
const INK_MIN: f64 = 3.0;

/// `hex_color` mixed with white just enough to reach INK_MIN contrast with
/// a dark background -- the same hue, lighter.
fn lighter_until(hex_color: &str) -> String {
    let c = rgb(hex_color);
    for step in (0..=100).step_by(2) {
        let t = step as f64 / 100.0;
        // (round_ties_even: Python's round())
        let mixed = c.map(|v| (v as f64 + (255.0 - v as f64) * t).round_ties_even() as u8);
        let h = hex(mixed);
        if contrast(&h, DARK_BG) >= INK_MIN {
            return h;
        }
    }
    "#ffffff".to_string()
}

/// Token overrides for an accent colour (empty for the default blue), as tokens.accent_tokens.
pub fn accent_tokens(name: &str, dark: bool) -> Values {
    let mut out = Values::new();
    let Some(c) = custom_accent(name) else {
        if let Some((light, dk)) = table().accents.get(name) {
            return if dark { dk.clone() } else { light.clone() };
        }
        return out;
    };
    out.insert("accent".into(), c.clone());
    out.insert("accent_selected".into(), if dark { c.clone() } else { darker(&c, 0.86) });
    let ink = if dark && contrast(&c, DARK_BG) < INK_MIN { lighter_until(&c) } else { c };
    out.insert("accent_ink".into(), ink);
    out
}

/// Fill a CSS template's `%(token)s` / `%(token)d` placeholders (Python's
/// `%` with a dict; `%%` is a literal %). A token that doesn't exist is left empty
/// and named on stderr, where Python would have raised KeyError.
pub fn fill(template: &str, values: &Values) -> String {
    let mut out = String::with_capacity(template.len() + 256);
    let mut rest = template;
    while let Some(i) = rest.find('%') {
        out.push_str(&rest[..i]);
        let tail = &rest[i..];
        if let Some(t) = tail.strip_prefix("%%") {
            out.push('%');
            rest = t;
        } else if let Some(t) = tail.strip_prefix("%(") {
            match t.find(')').filter(|e| matches!(t.as_bytes().get(e + 1), Some(b's') | Some(b'd'))) {
                Some(end) => {
                    let key = &t[..end];
                    match values.get(key) {
                        Some(v) => out.push_str(v),
                        None => eprintln!("sonata-ui: no token {key:?}"),
                    }
                    rest = &t[end + 2..];
                }
                None => {
                    out.push('%');
                    rest = &tail[1..];
                }
            }
        } else {
            out.push('%');
            rest = &tail[1..];
        }
    }
    out.push_str(rest);
    out
}

/// Every `<number>ms` in CSS at Sonata's animation speed (theme._MS): a
/// number not glued to a word or a dot before it, `ms` ending a word.
pub fn scale_durations(css: &str) -> String {
    let b = css.as_bytes();
    let mut out = String::with_capacity(css.len());
    let mut i = 0;
    let mut copied = 0;
    while i < b.len() {
        let starts = b[i].is_ascii_digit()
            && (i == 0 || !(b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_' || b[i - 1] == b'.'));
        if !starts {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < b.len() && b[j].is_ascii_digit() {
            j += 1;
        }
        if j + 1 < b.len() && b[j] == b'.' && b[j + 1].is_ascii_digit() {
            j += 1;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
        }
        let is_ms = css[j..].starts_with("ms")
            && !b.get(j + 2).is_some_and(|c| c.is_ascii_alphanumeric() || *c == b'_');
        if is_ms {
            let n: f64 = css[i..j].parse().unwrap_or(0.0);
            out.push_str(&css[copied..i]);
            out.push_str(&format!("{}ms", ms(n)));
            copied = j + 2;
            i = j + 2;
        } else {
            i = j.max(i + 1);
        }
    }
    out.push_str(&css[copied..]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn palettes_have_the_same_tokens_in_light_and_dark() {
        let (l, d) = (palette(false), palette(true));
        assert!(l.len() > 50);
        let mut lk: Vec<_> = l.keys().collect();
        let mut dk: Vec<_> = d.keys().collect();
        lk.sort();
        dk.sort();
        assert_eq!(lk, dk);
        assert_eq!(l["calc_key_op"], "#ff9f0a");
    }

    #[test]
    fn accents_match_tokens_py() {
        assert!(accent_tokens("blue", false).is_empty());
        assert!(accent_tokens("nonsense", true).is_empty());
        assert_eq!(accent_tokens("red", false)["accent_selected"], "#c03035");
        // custom: tokens.accent_tokens("#000", True) -- black's ink lightened for Dark Mode
        let black = accent_tokens("#000", true);
        assert_eq!(black["accent"], "#000000");
        assert_eq!(black["accent_ink"], "#6b6b6b");
        assert_eq!(accent_tokens("#ABCDEF", false)["accent_selected"], "#93b0cd");
        assert!(custom_accent("#12345").is_none());
    }

    #[test]
    fn fill_is_pythons_percent_with_a_dict() {
        let mut v = Values::new();
        v.insert("bg".into(), "red".into());
        assert_eq!(fill("a { background: %(bg)s; width: 50%%; }", &v), "a { background: red; width: 50%; }");
        assert_eq!(fill("%(missing)s!", &v), "!");
    }

    /// Spotlight's `%(open_ms)d` ran on to the next `)s` and swallowed
    /// the CSS in between: `d` placeholders are filled too.
    #[test]
    fn test_d_placeholders_filled() {
        let mut v = Values::new();
        v.insert("bg".into(), "red".into());
        v.insert("open_ms".into(), "200".into());
        assert_eq!(fill("a { animation: x %(open_ms)dms; color: %(bg)s; }", &v), "a { animation: x 200ms; color: red; }");
    }

    #[test]
    fn durations_follow_the_animation_speed() {
        let s = animation_speed();
        let want = |n: f64| format!("{}ms", ((n / s).round_ties_even() as u32).max(1));
        assert_eq!(scale_durations("transition: 90ms ease"), format!("transition: {} ease", want(90.0)));
        assert_eq!(scale_durations("a 1.5ms b"), format!("a {} b", want(1.5)));
        // not a duration: glued to a word or a dot, or not ending the word
        assert_eq!(scale_durations("x90ms .5ms 90msx 10px"), "x90ms .5ms 90msx 10px");
    }
}
