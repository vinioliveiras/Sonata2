//! Design tokens: `tokens.json` next to this crate is where a colour,
//! radius or speed is changed (light, dark, accents, the window frame). It
//! was exported from the Python Sonata's ui/tokens.py while both existed;
//! the Rust build is the only Sonata now, so the JSON is the source
//! (docs/GUIDE.md, "Design tokens and CSS").

use serde_json::{Map, Value};
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
        let v: Value = serde_json::from_str(TOKENS_JSON).expect("tokens.json: valid JSON");
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

/// The tokens in effect with the user's appearance.json (`cfg`, as
/// stored): the theme's, with the background colour, the corner radii and
/// the window buttons' place the user picked (tokens.palette).
pub fn user_palette(dark: bool, cfg: &Map<String, Value>) -> Values {
    let mut v = palette(dark);
    let picked = user_backgrounds(cfg).into_iter().find(|(m, _)| *m == if dark { "dark" } else { "light" });
    if let Some((_, c)) = picked {
        let moved = background_tokens(&v, &c, dark);
        v.extend(moved);
    }
    v.extend(radius_tokens(&user_radii(cfg)));
    v.extend(traffic_tokens(&crate::frame::frame_from(cfg)));
    v
}

// -- corner radii (Settings > Appearance > Corners; appearance.json "radius") ----------------
/// Windows (their title bars too), the Dock's plate, menus and panels.
/// Small parts follow their group's ratio (tokens.RADIUS_DEFAULTS).
pub const RADIUS_DEFAULTS: [(&str, i64); 3] = [("window", crate::frame::RADIUS), ("dock", 18), ("menu", 7)];
pub const RADIUS_RANGE: [(&str, (i64, i64)); 3] = [("window", (0, 24)), ("dock", (0, 32)), ("menu", (0, 16))];

/// The radii in effect: (window, dock, menu), each in its range.
pub fn user_radii(cfg: &Map<String, Value>) -> [(&'static str, i64); 3] {
    let got = cfg.get("radius").and_then(Value::as_object);
    let mut out = RADIUS_DEFAULTS;
    for (slot, (k, (lo, hi))) in out.iter_mut().zip(RADIUS_RANGE) {
        // (a bool is no number, as in Python's isinstance check of a JSON value)
        if let Some(n) = got.and_then(|g| g.get(k)).and_then(Value::as_f64) {
            slot.1 = (n.round_ties_even() as i64).clamp(lo, hi);
        }
    }
    out
}

/// The r_* tokens for these radii (the defaults give the designed values).
pub fn radius_tokens(r: &[(&str, i64); 3]) -> Values {
    let get = |k: &str| r.iter().find(|(n, _)| *n == k).map_or(0, |(_, v)| *v);
    let scaled = |base: i64, value: i64, ratio: i64| {
        let v = if base != 0 { ((value * ratio) as f64 / base as f64).round_ties_even().max(0.0) as i64 } else { 0 };
        format!("{v}px")
    };
    let (w, d, m) = (get("window"), get("dock"), get("menu"));
    [
        ("r_window", format!("{w}px")),
        ("r_button", scaled(10, w, 5)),
        ("r_plate", format!("{d}px")),
        ("r_menu", format!("{m}px")),
        ("r_menu_row", scaled(7, m, 4)),
        ("r_label", scaled(7, m, 6)),
        ("r_dialog", scaled(7, m, 12)),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect()
}

/// A GTK header bar's own padding around the traffic lights (Sonata's theme).
pub const HEADERBAR_PAD: (f64, f64) = (5.0, 6.0);

/// A length as Python's `f"{v:g}px"` writes it (3.0 -> "3px", 3.5 -> "3.5px").
fn px_g(v: f64) -> String {
    format!("{}px", if v == v.trunc() { format!("{}", v as i64) } else { format!("{v}") })
}

/// CSS values for the traffic lights (window.rs) from a frame: tokens, so
/// a new window radius or button size moves them with the rest of the theme.
pub fn traffic_tokens(f: &crate::frame::Frame) -> Values {
    let half = f.dot as f64 / 2.0;
    let gap = f.dot_gap as f64;
    let left = f.dot_left - half - gap / 2.0; // the box's margin: the first button adds half a gap
    let top = f.dot_top - half;
    [
        ("tl_dot", px_g(f.dot as f64)),
        ("tl_half_gap", px_g(gap / 2.0)),
        ("tl_margin_left", px_g(left)),
        ("tl_margin_top", px_g(top)),
        ("tl_hb_left", px_g(left - HEADERBAR_PAD.0)),
        ("tl_hb_top", px_g(top - HEADERBAR_PAD.1)),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect()
}

// -- background colour per appearance (Settings > Appearance, like the accent) --------------
/// A few to pick from -- the first is the theme's own -- or any colour.
/// The names are shown translated (tr! of the name; marked with n_!).
pub const BACKGROUNDS_LIGHT: [(&str, &str); 8] = [
    (sonata_i18n::n_!("Default"), "#ececec"), (sonata_i18n::n_!("Paper"), "#f5f3ee"), (sonata_i18n::n_!("Sand"), "#efe8dc"),
    (sonata_i18n::n_!("Mist"), "#e4e9f0"), (sonata_i18n::n_!("Mint"), "#e3eee7"), (sonata_i18n::n_!("Rose"), "#f1e5e8"),
    (sonata_i18n::n_!("Lavender"), "#e9e5f2"), (sonata_i18n::n_!("Butter"), "#f2eedb"),
];
pub const BACKGROUNDS_DARK: [(&str, &str); 8] = [
    (sonata_i18n::n_!("Default"), "#18181a"), (sonata_i18n::n_!("Black"), "#0b0b0c"), (sonata_i18n::n_!("Graphite"), "#26262a"),
    (sonata_i18n::n_!("Navy"), "#121826"), (sonata_i18n::n_!("Forest"), "#121c16"), (sonata_i18n::n_!("Plum"), "#1d1420"),
    (sonata_i18n::n_!("Espresso"), "#1f1915"), (sonata_i18n::n_!("Teal"), "#0f1e1f"),
];
/// Surfaces that follow the background (their own alpha, if any, is kept).
pub const BACKGROUND_FOLLOWERS: [&str; 6] = ["window_bg", "content_bg", "pane_bg", "sidebar_bg", "menu_bg", "solid_tint"];
/// Glass (Dock, menu bar, menus and panels, sidebars, glass title bars;
/// Vini: "the colour changed only where it isn't see-through"): moved by
/// the same amount *as seen* -- the shift over its alpha, since only that
/// much of the tint shows over what is behind.
pub const BACKGROUND_GLASS: [&str; 6] = ["glass_tint", "bar_bg", "window_glass", "titlebar_glass", "titlebar_glass_inactive", "calc_bg"];
/// Legible text: a light background stays this light, a dark one this dark (relative luminance).
pub const LIGHT_BG_MIN: f64 = 0.55;
pub const DARK_BG_MAX: f64 = 0.045;

/// '#rrggbb' / 'rgb(...)' / 'rgba(...)' -> ([r, g, b], alpha or None).
fn parse_rgb(c: &str) -> ([i64; 3], Option<f64>) {
    let c = c.trim();
    if c.starts_with('#') {
        let h = rgb(c);
        return ([h[0] as i64, h[1] as i64, h[2] as i64], None);
    }
    let inner = c.split_once('(').and_then(|(_, r)| r.split_once(')')).map_or("", |(i, _)| i);
    let v: Vec<f64> = inner.split(',').map(|x| x.trim().parse().unwrap_or(0.0)).collect();
    let g = |i: usize| v.get(i).copied().unwrap_or(0.0).round_ties_even() as i64;
    ([g(0), g(1), g(2)], v.get(3).copied())
}

/// A float as Python's str() writes it (1.0 -> "1.0", 0.97 -> "0.97").
fn py_float(v: f64) -> String {
    if v == v.trunc() { format!("{v:.1}") } else { format!("{v}") }
}

/// `hex_color` moved towards white (light) or black (dark), just enough
/// for the theme's text to stay readable on it.
pub fn legible_background(hex_color: &str, dark: bool) -> String {
    let Some(c) = custom_accent(hex_color) else {
        return (if dark { BACKGROUNDS_DARK[0].1 } else { BACKGROUNDS_LIGHT[0].1 }).to_string();
    };
    let v = rgb(&c);
    let target = if dark { 0.0 } else { 255.0 };
    for step in (0..=100).step_by(2) {
        let t = step as f64 / 100.0;
        let out = hex(v.map(|x| (x as f64 + (target - x as f64) * t).round_ties_even() as u8));
        let lum = luminance(&out);
        if (dark && lum <= DARK_BG_MAX) || (!dark && lum >= LIGHT_BG_MIN) {
            return out;
        }
    }
    (if dark { "#000000" } else { "#ffffff" }).to_string()
}

/// ("light" | "dark", "#rrggbb") as picked (appearance.json "background");
/// a missing one is the theme's own.
pub fn user_backgrounds(cfg: &Map<String, Value>) -> Vec<(&'static str, String)> {
    let Some(raw) = cfg.get("background").and_then(Value::as_object) else { return Vec::new() };
    ["light", "dark"]
        .into_iter()
        .filter_map(|m| raw.get(m).and_then(Value::as_str).and_then(custom_accent).map(|c| (m, c)))
        .collect()
}

/// A translucent colour composited over an opaque one -> 'rgb(...)' (tokens.over).
pub fn over(css: &str, base: &str) -> String {
    let parts = |c: &str| -> [f64; 4] {
        let c = c.trim();
        if c.starts_with('#') {
            let h = rgb(c);
            return [h[0] as f64, h[1] as f64, h[2] as f64, 1.0];
        }
        let inner = c.split_once('(').and_then(|(_, r)| r.split_once(')')).map_or("", |(i, _)| i);
        let v: Vec<f64> = inner.split(',').map(|x| x.trim().parse().unwrap_or(0.0)).collect();
        let g = |i: usize| v.get(i).copied().unwrap_or(0.0);
        [g(0), g(1), g(2), v.get(3).copied().unwrap_or(1.0)]
    };
    let [r, g, b, a] = parts(css);
    let [br, bg, bb, _] = parts(base);
    let mix = |c: f64, under: f64| (c * a + under * (1.0 - a)).round_ties_even() as i64;
    format!("rgb({}, {}, {})", mix(r, br), mix(g, bg), mix(b, bb))
}

/// The tokens a picked background changes: the window's own colour, and
/// every surface in BACKGROUND_FOLLOWERS moved by the same amount; the
/// opaque title bars made again over it (their glass over the window).
pub fn background_tokens(tokens: &Values, picked: &str, dark: bool) -> Values {
    let mut out = Values::new();
    let (Some(win), true) = (tokens.get("window_bg"), !picked.is_empty()) else { return out };
    let new = parse_rgb(&legible_background(picked, dark)).0;
    let base = parse_rgb(win).0;
    let delta = [new[0] - base[0], new[1] - base[1], new[2] - base[2]];
    for k in BACKGROUND_FOLLOWERS.iter().chain(BACKGROUND_GLASS.iter()) {
        let Some(t) = tokens.get(*k) else { continue };
        let (c, a) = parse_rgb(t);
        // (clear glass: never a glare)
        let gain = match a {
            Some(a) if a != 0.0 && BACKGROUND_GLASS.contains(k) => 1.0 / a.max(0.35),
            _ => 1.0,
        };
        let moved: Vec<i64> = (0..3).map(|i| (c[i] as f64 + delta[i] as f64 * gain).round_ties_even().clamp(0.0, 255.0) as i64).collect();
        let v = match a {
            None => format!("#{:02x}{:02x}{:02x}", moved[0], moved[1], moved[2]),
            Some(a) => format!("rgba({}, {}, {}, {})", moved[0], moved[1], moved[2], py_float(a)),
        };
        out.insert(k.to_string(), v);
    }
    for (bar, glass) in [("titlebar_bg", "titlebar_glass"), ("titlebar_bg_inactive", "titlebar_glass_inactive")] {
        // the moved glass over the theme's window: as seen, the bar moved like the window
        let Some(g) = out.get(glass).or_else(|| tokens.get(glass)).cloned() else { continue };
        out.insert(bar.to_string(), over(&g, win));
    }
    out
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

    /// Vini: several animations of the Rust session ran slower than
    /// Python's (the Dock's hide, magnification and bounce-in, the
    /// switcher's glide, Launchpad's zoom, Notification Center's banners,
    /// the OSD's fade, every stack and revealer...). Python scales every
    /// Adw.TimedAnimation and every Gtk.Stack/Revealer transition by
    /// ANIMATION_SPEED (theme._animation_speed); Rust can't patch GTK, so
    /// each call site passes its designed duration through tokens::ms --
    /// on the same line, so this check can see it.
    #[test]
    fn test_animations_ignored_the_animation_speed() {
        fn walk(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
            for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
                let p = e.path();
                if p.is_dir() {
                    if p.file_name().is_some_and(|n| n != "target") {
                        walk(&p, out);
                    }
                } else if p.extension().is_some_and(|x| x == "rs") {
                    out.push(p);
                }
            }
        }
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let mut files = Vec::new();
        for d in ["apps", "crates"] {
            walk(&root.join(d), &mut files);
        }
        assert!(files.len() > 100, "the workspace's sources not found from {}", root.display());
        let mut bad = Vec::new();
        for f in files.iter().filter(|f| !f.ends_with("sonata-ui/src/tokens.rs")) {
            let src = std::fs::read_to_string(f).unwrap_or_default();
            for (i, line) in src.lines().enumerate() {
                let code = line.split("//").next().unwrap_or("");
                let sets = code.contains("TimedAnimation::new(")
                    || code.contains("set_duration(")
                    || code.contains("transition_duration(") && !code.contains("transition_duration()") && !code.contains("transition_duration(0)");
                if sets && !code.contains("tokens::ms(") && !code.contains("fn ") {
                    bad.push(format!("{}:{}: {}", f.display(), i + 1, line.trim()));
                }
            }
        }
        assert!(bad.is_empty(), "durations not at Sonata's animation speed (tokens::ms):\n{}", bad.join("\n"));
    }

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

    fn cfg(v: serde_json::Value) -> Map<String, Value> {
        v.as_object().cloned().unwrap()
    }

    fn lum_of(c: &str) -> f64 {
        let (v, _) = parse_rgb(c);
        luminance(&hex([v[0] as u8, v[1] as u8, v[2] as u8]))
    }

    /// Nothing picked: exactly the theme's tokens (tokens.json was made
    /// with no appearance.json).
    #[test]
    fn test_default_unchanged() {
        for dark in [false, true] {
            assert_eq!(user_palette(dark, &Map::new()), palette(dark), "dark {dark}");
        }
        assert_eq!(BACKGROUNDS_LIGHT[0].1, palette(false)["window_bg"]); // the first is the theme's own
        assert_eq!(BACKGROUNDS_DARK[0].1, palette(true)["window_bg"]);
    }

    #[test]
    fn test_defaults_are_the_designed_values() {
        let pal = user_palette(false, &Map::new());
        for (k, v) in [("r_window", "10px"), ("r_button", "5px"), ("r_plate", "18px"), ("r_menu", "7px"), ("r_menu_row", "4px"), ("r_label", "6px"), ("r_dialog", "12px")] {
            assert_eq!(pal[k], v, "{k}");
        }
    }

    #[test]
    fn test_user_values_reach_every_place() {
        let c = cfg(serde_json::json!({"radius": {"window": 16, "dock": 4, "menu": 99}}));
        assert_eq!(user_radii(&c), [("window", 16), ("dock", 4), ("menu", 16)]); // clamped
        let pal = user_palette(true, &c);
        assert_eq!((pal["r_window"].as_str(), pal["r_plate"].as_str(), pal["r_menu"].as_str()), ("16px", "4px", "16px"));
        assert_eq!(pal["r_button"], "8px");
    }

    /// Vini: with a bigger radius the window buttons stayed put and crowded
    /// the curve: Sonata's own windows' dots move in with it.
    #[test]
    fn test_window_buttons_crowded_a_rounder_corner() {
        let pal = user_palette(false, &cfg(serde_json::json!({"radius": {"window": 24}})));
        let extra = (24.0 - 10.0) / 2.0;
        assert_eq!(pal["tl_margin_left"], px_g(13.0 + extra - 6.0 - 4.0));
        assert_eq!(pal["tl_margin_top"], px_g(14.0 + extra - 6.0));
    }

    #[test]
    fn test_button_size_keeps_the_room_from_the_edges() {
        let pal = user_palette(false, &cfg(serde_json::json!({"button_size": 16})));
        assert_eq!(pal["tl_dot"], "16px");
        // the same room from the edge (a bigger dot grows inwards), minus half the wider gap
        assert_eq!((pal["tl_margin_left"].as_str(), pal["tl_half_gap"].as_str()), ("1.5px", "5.5px"));
    }

    #[test]
    fn test_picked_per_mode() {
        let c = cfg(serde_json::json!({"background": {"light": "#e4e9f0", "dark": "#121826"}}));
        let (light, dark) = (user_palette(false, &c), user_palette(true, &c));
        assert_eq!((light["window_bg"].as_str(), dark["window_bg"].as_str()), ("#e4e9f0", "#121826"));
        // the surfaces around it keep their distance: content still lighter (light), sidebar lighter (dark)
        assert!(lum_of(&light["content_bg"]) > lum_of(&light["window_bg"]));
        assert!(lum_of(&dark["sidebar_bg"]) > lum_of(&dark["window_bg"]));
        // the opaque title bar: its (moved) glass over the theme's window
        assert_eq!(light["titlebar_bg"], over(&light["titlebar_glass"], &palette(false)["window_bg"]));
        assert!(light["menu_bg"].starts_with("rgba(") && light["menu_bg"].ends_with("0.97)"));
    }

    /// Vini: the colour changed only where it wasn't see-through -- the
    /// Dock, the menu bar, menus and panels and the sidebars' glass follow,
    /// moved as seen (over what is behind) like the window.
    #[test]
    fn test_glass_follows_too() {
        let c = cfg(serde_json::json!({"background": {"dark": "#121826"}}));
        let (base, got) = (palette(true), user_palette(true, &c));
        let (n, b) = (parse_rgb(&got["window_bg"]).0, parse_rgb(&base["window_bg"]).0);
        let delta = [n[0] - b[0], n[1] - b[1], n[2] - b[2]];
        for k in BACKGROUND_GLASS {
            let ((g0, a), (g1, a1)) = (parse_rgb(&base[k]), parse_rgb(&got[k]));
            assert_eq!(a, a1, "{k}"); // as see-through as before
            for i in 0..3 {
                let seen = ((g1[i] - g0[i]) as f64 * a.unwrap_or(1.0)).round_ties_even() as i64;
                if 0 < g1[i] && g1[i] < 255 {
                    assert!((seen - delta[i]).abs() <= 1, "{k}");
                }
            }
        }
    }

    #[test]
    fn test_text_stays_legible() {
        let c = cfg(serde_json::json!({"background": {"light": "#202020", "dark": "#ffffff"}}));
        assert!(lum_of(&user_palette(false, &c)["window_bg"]) >= LIGHT_BG_MIN);
        assert!(lum_of(&user_palette(true, &c)["window_bg"]) <= DARK_BG_MAX);
    }

    #[test]
    fn test_every_preset_already_legible() {
        for (dark, opts) in [(false, BACKGROUNDS_LIGHT), (true, BACKGROUNDS_DARK)] {
            for (name, c) in opts {
                assert_eq!(legible_background(c, dark), c, "{name}");
            }
        }
    }
}
