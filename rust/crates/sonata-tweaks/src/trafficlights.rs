//! The window buttons' colours (sonata2/trafficlights.py; Vini: Settings >
//! Appearance > Button Colours): Colourful (macOS, the default), Graphite
//! (all grey), Black & White (black on light, white on dark) or Custom (a
//! colour per button).
//!
//! The pictures are drawn here in both forms the buttons need: SVG (GTK,
//! Steam: sharp at any scale) and PNG (pixdecor draws them 1:1), into
//! ~/.cache/sonata2/tl/<look>/<light|dark>/ -- the same folder names as the
//! Python Sonata's, so both share them. The default look keeps the bundled
//! ones (data/icons, data/decor). A glyph on hover is dark on a light dot
//! and light on a dark one, so every look stays readable.

use crate::frame::{self, TL_COLORS};
use serde_json::{Map, Value};
use std::f64::consts::PI;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const STYLES: [&str; 4] = ["color", "graphite", "mono", "custom"];
pub const NAMES: [&str; 4] = ["close", "minimize", "maximize", "restore"];
pub const GRAPHITE: &str = "#8e8e93";
/// black on light, white on dark
pub fn mono(dark: bool) -> &'static str {
    if dark { "#f5f5f7" } else { "#1d1d1f" }
}
const RIM: (f64, f64, f64, f64) = (0.0, 0.0, 0.0, 0.18);
/// PNGs: drawn 8x larger, then averaged down
const SS: usize = 8;
/// bump when dot() draws differently: pictures made again
pub const DRAW_VERSION: i64 = 1;
/// one apply at a time (quick picks in Settings)
static APPLY: Mutex<()> = Mutex::new(());

pub type Colors = Vec<(String, String)>;

pub fn style_in(cfg: &Map<String, Value>) -> String {
    match cfg.get("buttons_style").and_then(Value::as_str) {
        Some(s) if STYLES.contains(&s) => s.to_string(),
        _ => "color".into(),
    }
}

pub fn style() -> String {
    style_in(&frame::appearance())
}

pub fn custom_in(cfg: &Map<String, Value>) -> Colors {
    let c = cfg.get("buttons_colors").and_then(Value::as_object).cloned().unwrap_or_default();
    let pick = |n: &str| c.get(n).and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or(frame::tl_color(n)).to_string();
    let zoom = pick("maximize");
    vec![("close".into(), pick("close")), ("minimize".into(), pick("minimize")), ("maximize".into(), zoom.clone()), ("restore".into(), zoom)]
}

pub fn custom() -> Colors {
    custom_in(&frame::appearance())
}

/// The colour of each button for a look.
pub fn colors(dark: bool, look: &str, chosen: Option<&Colors>) -> Colors {
    match look {
        "graphite" => NAMES.iter().map(|n| (n.to_string(), GRAPHITE.to_string())).collect(),
        "mono" => NAMES.iter().map(|n| (n.to_string(), mono(dark).to_string())).collect(),
        "custom" => chosen.cloned().unwrap_or_else(custom),
        _ => TL_COLORS.iter().map(|(n, c)| (n.to_string(), c.to_string())).collect(),
    }
}

fn color_of<'a>(cols: &'a Colors, name: &str) -> &'a str {
    cols.iter().find(|(n, _)| n == name).map(|(_, c)| c.as_str()).unwrap_or("#28c840")
}

fn rgb(hex: &str) -> (f64, f64, f64) {
    let h = hex.trim_start_matches('#');
    let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0) as f64 / 255.0;
    (p(0), p(2), p(4))
}

/// The hover glyph: dark on a light dot, light on a dark one.
pub fn glyph_rgba(hex: &str) -> (f64, f64, f64, f64) {
    let (r, g, b) = rgb(hex);
    if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 { (0.0, 0.0, 0.0, 0.55) } else { (1.0, 1.0, 1.0, 0.85) }
}

/// One button on a Cairo context (size x size): the dot, its hairline
/// rim, and the glyph on hover (macOS': x, -, two small triangles).
pub fn dot(cr: &gtk::cairo::Context, size: f64, color: &str, glyph: Option<&str>) {
    let c = size / 2.0;
    let (r, g, b) = rgb(color);
    cr.arc(c, c, c - 0.2, 0.0, 2.0 * PI);
    cr.set_source_rgb(r, g, b);
    let _ = cr.fill();
    cr.arc(c, c, c - 0.45, 0.0, 2.0 * PI);
    cr.set_source_rgba(RIM.0, RIM.1, RIM.2, RIM.3);
    cr.set_line_width(0.5);
    let _ = cr.stroke();
    let Some(glyph) = glyph else { return };
    let (gr, gg, gb, ga) = glyph_rgba(color);
    cr.set_source_rgba(gr, gg, gb, ga);
    cr.set_line_width(1.1);
    cr.set_line_cap(gtk::cairo::LineCap::Round);
    for (closed, pts) in glyph_paths(size, glyph) {
        cr.move_to(pts[0].0, pts[0].1);
        for p in &pts[1..] {
            cr.line_to(p.0, p.1);
        }
        if closed {
            cr.close_path();
            let _ = cr.fill();
        }
    }
    if glyph == "close" || glyph == "minimize" {
        let _ = cr.stroke();
    }
}

/// The glyph's paths: (filled, points) -- strokes for x and -, two filled
/// triangles for maximize / restore (macOS zoom).
fn glyph_paths(size: f64, glyph: &str) -> Vec<(bool, Vec<(f64, f64)>)> {
    let c = size / 2.0;
    let a = size * 0.2;
    match glyph {
        "close" => vec![(false, vec![(c - a, c - a), (c + a, c + a)]), (false, vec![(c - a, c + a), (c + a, c - a)])],
        "minimize" => vec![(false, vec![(c - a * 1.25, c), (c + a * 1.25, c)])],
        _ => {
            let b = size * 0.2;
            [1.0, -1.0]
                .iter()
                .map(|sx| (true, vec![(c - sx * (b + 0.3), c - sx * (b + 0.3)), (c + sx * b * 0.55, c - sx * (b + 0.3)), (c - sx * (b + 0.3), c + sx * b * 0.55)]))
                .collect()
        }
    }
}

/// The PNG: drawn SS times larger, then a box filter (premultiplied), then
/// written straight (PNG's alpha isn't premultiplied).
pub fn write_png(path: &Path, size: usize, color: &str, glyph: Option<&str>) -> Result<(), String> {
    use gtk::cairo::{Context, Format, ImageSurface};
    let big = ImageSurface::create(Format::ARgb32, (size * SS) as i32, (size * SS) as i32).map_err(|e| e.to_string())?;
    {
        let cr = Context::new(&big).map_err(|e| e.to_string())?;
        cr.scale(SS as f64, SS as f64);
        dot(&cr, size as f64, color, glyph);
    }
    big.flush();
    let stride = big.stride() as usize;
    let data = big.take_data().map_err(|e| e.to_string())?;
    let n = SS * SS;
    let mut out = image::RgbaImage::new(size as u32, size as u32);
    for y in 0..size {
        for x in 0..size {
            let mut acc = [0usize; 4];
            for yy in y * SS..y * SS + SS {
                for xx in x * SS..x * SS + SS {
                    let o = yy * stride + xx * 4;
                    for (k, a) in acc.iter_mut().enumerate() {
                        *a += data[o + k] as usize;
                    }
                }
            }
            // cairo's ARGB32 is native-endian: B, G, R, A in memory here
            let px: Vec<usize> = acc.iter().map(|a| (a + n / 2) / n).collect();
            let (b, g, r, a) = (px[0], px[1], px[2], px[3]);
            let un = |v: usize| if a == 0 { 0 } else { ((v * 255 + a / 2) / a).min(255) as u8 };
            out.put_pixel(x as u32, y as u32, image::Rgba([un(r), un(g), un(b), a as u8]));
        }
    }
    out.save_with_format(path, image::ImageFormat::Png).map_err(|e| e.to_string())
}

fn css_rgba((r, g, b, a): (f64, f64, f64, f64)) -> (String, f64) {
    (format!("rgb({},{},{})", (r * 255.0) as u8, (g * 255.0) as u8, (b * 255.0) as u8), a)
}

/// The SVG: the same shapes as dot(), written out.
pub fn svg_text(size: f64, color: &str, glyph: Option<&str>) -> String {
    let c = size / 2.0;
    let mut s = format!("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{size}pt\" height=\"{size}pt\" viewBox=\"0 0 {size} {size}\">\n");
    s += &format!("<circle cx=\"{c}\" cy=\"{c}\" r=\"{}\" fill=\"{color}\"/>\n", c - 0.2);
    s += &format!("<circle cx=\"{c}\" cy=\"{c}\" r=\"{}\" fill=\"none\" stroke=\"rgb(0,0,0)\" stroke-opacity=\"{}\" stroke-width=\"0.5\"/>\n", c - 0.45, RIM.3);
    if let Some(g) = glyph {
        let (col, op) = css_rgba(glyph_rgba(color));
        for (filled, pts) in glyph_paths(size, g) {
            let d: Vec<String> = pts.iter().enumerate().map(|(i, (x, y))| format!("{}{x:.3} {y:.3}", if i == 0 { "M" } else { "L" })).collect();
            let d = d.join(" ");
            if filled {
                s += &format!("<path d=\"{d} Z\" fill=\"{col}\" fill-opacity=\"{op}\"/>\n");
            } else {
                s += &format!("<path d=\"{d}\" fill=\"none\" stroke=\"{col}\" stroke-opacity=\"{op}\" stroke-width=\"1.1\" stroke-linecap=\"round\"/>\n");
            }
        }
    }
    s + "</svg>\n"
}

/// (svg folder, png folder) of the default look (shipped).
pub fn bundled() -> (PathBuf, PathBuf) {
    (sonata_config::data_path("icons/Sonata/apps/scalable"), sonata_config::data_path("decor"))
}

/// The pictures' folder name: the look, its colours, the size and how
/// they're drawn (Python's json.dumps(sort_keys=True), hashed the same).
pub fn key(look: &str, cols: &Colors, size: i64) -> String {
    let mut sorted = cols.clone();
    sorted.sort();
    let c: Vec<String> = sorted.iter().map(|(n, v)| format!("\"{n}\": \"{v}\"")).collect();
    let what = format!("{{\"c\": {{{}}}, \"size\": {size}, \"v\": {DRAW_VERSION}}}", c.join(", "));
    let sum = glib::compute_checksum_for_data(glib::ChecksumType::Sha1, what.as_bytes()).map(|s| s.to_string()).unwrap_or_default();
    format!("{look}-{}", &sum[..10.min(sum.len())])
}

/// The bundled pictures do: the default look at the default size.
pub fn bundled_ok_in(cfg: &Map<String, Value>) -> bool {
    style_in(cfg) == "color" && frame::button_size(cfg) == frame::DOT
}

/// Where this look's pictures are (made if needed): SVGs named
/// sonata-tl-<name>[-hover].svg, PNGs <name>[-hover].png.
pub fn folder_in(dark: bool, cfg: &Map<String, Value>, cache: &Path) -> PathBuf {
    if bundled_ok_in(cfg) {
        return bundled().0;
    }
    let look = style_in(cfg);
    let cols = if look == "custom" { colors(dark, &look, Some(&custom_in(cfg))) } else { colors(dark, &look, None) };
    let size = frame::button_size(cfg);
    let d = cache.join("tl").join(key(&look, &cols, size)).join(if dark { "dark" } else { "light" });
    if !d.join("restore-hover.png").is_file() {
        make(&d, &cols, size);
    }
    d
}

fn cache() -> PathBuf {
    crate::util::cache_home().join("sonata2")
}

pub fn folder(dark: bool) -> PathBuf {
    folder_in(dark, &frame::appearance(), &cache())
}

pub fn png_folder_in(dark: bool, cfg: &Map<String, Value>, cache: &Path) -> PathBuf {
    if bundled_ok_in(cfg) { bundled().1 } else { folder_in(dark, cfg, cache) }
}

pub fn png_folder(dark: bool) -> PathBuf {
    png_folder_in(dark, &frame::appearance(), &cache())
}

pub fn make(d: &Path, cols: &Colors, px: i64) {
    let _ = std::fs::create_dir_all(d);
    for n in NAMES {
        for (suffix, g) in [("", None), ("-hover", Some(n))] {
            let col = color_of(cols, n);
            let _ = std::fs::write(d.join(format!("sonata-tl-{n}{suffix}.svg")), svg_text(px as f64, col, g));
            let _ = write_png(&d.join(format!("{n}{suffix}.png")), px as usize, col, g);
        }
    }
}

/// Sonata's own windows (.traffic buttons) in this look.
pub fn css_rules_in(dark: bool, cfg: &Map<String, Value>, cache: &Path) -> String {
    if bundled_ok_in(cfg) {
        return String::new();
    }
    let d = folder_in(dark, cfg, cache);
    let url = |n: &str| format!("url(\"file://{}\")", d.join(format!("sonata-tl-{n}.svg")).display());
    [
        format!(".traffic button.tl-close {{ background-image: {}; }}", url("close")),
        format!(".traffic button.tl-close:hover {{ background-image: {}; }}", url("close-hover")),
        format!(".traffic button.tl-min {{ background-image: {}; }}", url("minimize")),
        format!(".traffic button.tl-min:hover {{ background-image: {}; }}", url("minimize-hover")),
        // (a greyed-out zoom stays grey: Settings' showed green in another look or size)
        format!(".traffic button.tl-zoom:not(.tl-disabled) {{ background-image: {}; }}", url("maximize")),
        format!(".traffic button.tl-zoom:not(.tl-disabled):hover {{ background-image: {}; }}", url("maximize-hover")),
    ]
    .join("\n")
}

pub fn css_rules(dark: bool) -> String {
    css_rules_in(dark, &frame::appearance(), &cache())
}

/// pixdecor's button pictures (Wayfire's title bars) for this look.
pub fn wayfire_options_in(dark: bool, cfg: &Map<String, Value>, cache: &Path) -> Vec<(&'static str, String, String)> {
    let d = png_folder_in(dark, cfg, cache);
    let mut out = Vec::new();
    for n in NAMES {
        out.push(("pixdecor", format!("button_{n}_image"), d.join(format!("{n}.png")).display().to_string()));
        out.push(("pixdecor", format!("button_{n}_hover_image"), d.join(format!("{n}-hover.png")).display().to_string()));
    }
    out
}

pub fn apply_wayfire(dark: bool) {
    for (section, key, value) in wayfire_options_in(dark, &frame::appearance(), &cache()) {
        sonata_backend::wfconfig::set(section, &key, &value);
    }
}

/// After a change in Settings (in a thread: `dark` taken on the main loop
/// and passed in -- GTK isn't asked from here): the pictures, then each
/// place. One at a time: quick picks wrote the same files together.
/// (GTK 4 apps' style, adwstyle.py, isn't ported yet: the Python session
/// writes it.)
pub fn apply(dark: bool) {
    let _one = APPLY.lock().unwrap_or_else(|e| e.into_inner());
    for d in [false, true] {
        folder(d);
    }
    apply_wayfire(dark);
    if crate::steamtheme::enabled() {
        crate::steamtheme::apply(); // Steam, next time it opens
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn cfg(v: Value) -> Map<String, Value> {
        v.as_object().cloned().unwrap()
    }

    #[test]
    fn four_looks() {
        assert_eq!(colors(false, "color", None)[0], ("close".into(), "#ff5f57".into()));
        assert!(colors(true, "graphite", None).iter().all(|(_, c)| c == GRAPHITE));
        assert!(colors(false, "mono", None).iter().all(|(_, c)| c == "#1d1d1f")); // black on light
        assert!(colors(true, "mono", None).iter().all(|(_, c)| c == "#f5f5f7")); // white on dark
        // custom fills gaps and restore follows zoom
        let c = custom_in(&cfg(json!({"buttons_style": "custom", "buttons_colors": {"maximize": "#336699"}})));
        assert_eq!(color_of(&c, "close"), "#ff5f57");
        assert_eq!(color_of(&c, "restore"), "#336699");
        assert_eq!(style_in(&cfg(json!({"buttons_style": "neon"}))), "color"); // unknown: colourful
        assert_eq!(glyph_rgba("#f5f5f7").0, 0.0); // white dot: dark glyph
        assert_eq!(glyph_rgba("#1d1d1f").0, 1.0); // black dot: light glyph
    }

    #[test]
    fn colourful_keeps_the_bundled_pictures() {
        let t = tempfile::tempdir().unwrap();
        let c = cfg(json!({"buttons_style": "color"}));
        assert!(folder_in(false, &c, t.path()).ends_with("apps/scalable"));
        assert!(png_folder_in(false, &c, t.path()).ends_with("decor"));
        assert!(bundled().0.join("sonata-tl-close.svg").is_file());
        assert_eq!(css_rules_in(false, &c, t.path()), "");
    }

    #[test]
    fn mono_light_and_dark_are_drawn() {
        let t = tempfile::tempdir().unwrap();
        let c = cfg(json!({"buttons_style": "mono"}));
        let (light, dark) = (folder_in(false, &c, t.path()), folder_in(true, &c, t.path()));
        assert_ne!(light, dark);
        for (d, want) in [(&light, 0x1d), (&dark, 0xf5)] {
            for n in NAMES {
                for suf in ["", "-hover"] {
                    assert!(d.join(format!("sonata-tl-{n}{suf}.svg")).is_file());
                    assert!(d.join(format!("{n}{suf}.png")).is_file());
                }
            }
            let im = image::open(d.join("close.png")).unwrap().to_rgba8();
            let p = im.get_pixel(im.width() / 2, im.height() / 2);
            assert_eq!(p[3], 255);
            assert!((p[0] as i32 - want).abs() <= 3, "{p:?}");
        }
        let hover = image::open(light.join("close-hover.png")).unwrap().to_rgba8();
        let plain = image::open(light.join("close.png")).unwrap().to_rgba8();
        assert_ne!(hover.as_raw(), plain.as_raw()); // the glyph shows
        assert!(std::fs::read_to_string(light.join("sonata-tl-close-hover.svg")).unwrap().contains("stroke-linecap=\"round\""));
    }

    /// Review: the folder only hashed the colours -- an update drawing the
    /// buttons differently (or at another size) kept the old pictures.
    #[test]
    fn a_new_colour_or_size_gets_new_pictures() {
        let t = tempfile::tempdir().unwrap();
        let a = folder_in(false, &cfg(json!({"buttons_style": "custom", "buttons_colors": {"close": "#112233"}})), t.path());
        let b = folder_in(false, &cfg(json!({"buttons_style": "custom", "buttons_colors": {"close": "#445566"}})), t.path());
        assert_ne!(a, b);
        let g = colors(false, "graphite", None);
        assert_ne!(key("graphite", &g, 12), key("graphite", &g, 14));
        // the colourful look at another size is drawn too (pixdecor draws its PNGs 1:1)
        assert!(!bundled_ok_in(&cfg(json!({"button_size": 16}))));
    }

    #[test]
    fn css_and_wayfire_point_at_the_look() {
        let t = tempfile::tempdir().unwrap();
        let c = cfg(json!({"buttons_style": "graphite"}));
        let d = folder_in(false, &c, t.path());
        let css = css_rules_in(false, &c, t.path());
        assert!(css.contains(&d.join("sonata-tl-close-hover.svg").display().to_string()));
        assert!(css.contains(".traffic button.tl-zoom"));
        let opts = wayfire_options_in(false, &c, t.path());
        assert_eq!(opts.len(), 2 * NAMES.len());
        assert_eq!(opts[0], ("pixdecor", "button_close_image".to_string(), d.join("close.png").display().to_string()));
    }

    #[test]
    fn the_folder_name_is_pythons() {
        // python3 -c 'import json,hashlib; print(hashlib.sha1(json.dumps({"c": {n: "#8e8e93" for n in
        //   ("close","minimize","maximize","restore")}, "size": 12, "v": 1}, sort_keys=True).encode()).hexdigest()[:10])'
        assert_eq!(key("graphite", &colors(false, "graphite", None), 12), "graphite-325e4118e4");
    }
}
