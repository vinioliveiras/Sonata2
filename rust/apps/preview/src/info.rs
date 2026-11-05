//! Preview's Info panel (sonata2/preview/info.py; macOS Preview: Tools >
//! Show Inspector, ⌘I): General (size in pixels, file size, kind, colour
//! space, dates) and, when the picture carries them, the camera's EXIF
//! details (make/model, lens, exposure, ISO, focal length, date taken, GPS).
//!
//! ```text
//! let data = read(path, size);   // off the main loop: plain data
//! panel.show(path, &data);
//! ```

use crate::edit;
use crate::imageload::{self, is_raw};
use gtk::{gdk_pixbuf::Pixbuf, gio, glib, pango, prelude::*};
use sonata_i18n::{fmt, gettext, n_, tr};
use std::cell::RefCell;

const INFO_W: i32 = 250;

/// Everything the panel shows, as plain data (safe in a thread). Labels
/// are English (translated when shown); values are ready.
#[derive(Clone, Debug, Default)]
pub struct Info {
    pub name: String,
    pub general: Vec<(&'static str, String)>,
    pub camera: Vec<(&'static str, String)>,
}

/// Python's "{:g}": no trailing zeros.
fn g(x: f64) -> String {
    let s = format!("{x:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    s.to_string()
}

pub fn exposure_text(x: f64) -> String {
    if x <= 0.0 {
        return String::new();
    }
    if x < 1.0 {
        return format!("1/{} s", (1.0 / x).round_ties_even());
    }
    format!("{} s", g(x))
}

pub fn fnumber_text(x: f64) -> String {
    if x == 0.0 { String::new() } else { format!("ƒ/{x:.1}").replace(".0", "") }
}

pub fn focal_text(x: f64) -> String {
    if x == 0.0 { String::new() } else { format!("{} mm", g(x)) }
}

pub fn date_text(d: &glib::DateTime) -> String {
    fmt(
        &tr!("{month} {day}, {year} at {time}"),
        &[
            ("month", &d.format("%b").map(|s| s.to_string()).unwrap_or_default()),
            ("day", &d.day_of_month().to_string()),
            ("year", &d.year().to_string()),
            ("time", &d.format("%H:%M").map(|s| s.to_string()).unwrap_or_default()),
        ],
    )
}

/// "2024:05:01 12:34:56" -> "May 1, 2024 at 12:34".
pub fn exif_date_text(s: &str) -> String {
    let s = s.trim_matches(|c| c == '\0' || c == ' ');
    let n: Vec<i32> = s.get(..19).unwrap_or("").split([':', ' ']).filter_map(|x| x.parse().ok()).collect();
    if n.len() == 6 {
        if let Ok(d) = glib::DateTime::new(&glib::TimeZone::local(), n[0], n[1], n[2], n[3], n[4], n[5] as f64) {
            return date_text(&d);
        }
    }
    s.to_string()
}

/// The name a colour profile gives itself (ICC 'desc' tag), or a stand-in.
pub fn profile_name(icc: &[u8]) -> String {
    let be32 = |i: usize| icc.get(i..i + 4).map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as usize);
    let found = (|| {
        let count = be32(128)?;
        for k in 0..count.min(100) {
            let e = 132 + k * 12;
            if icc.get(e..e + 4)? != b"desc" {
                continue;
            }
            let (off, size) = (be32(e + 4)?, be32(e + 8)?);
            let tag = icc.get(off..off + size)?;
            if tag.starts_with(b"desc") {
                // ICC v2: a count, then ASCII
                let n = u32::from_be_bytes([tag[8], tag[9], tag[10], tag[11]]) as usize;
                let text = tag.get(12..12 + n)?;
                return Some(String::from_utf8_lossy(text).trim_matches(char::from(0)).trim().to_string());
            }
            if tag.starts_with(b"mluc") {
                // ICC v4: the first record, UTF-16BE
                let (len, at) = (u32::from_be_bytes([tag[20], tag[21], tag[22], tag[23]]) as usize, u32::from_be_bytes([tag[24], tag[25], tag[26], tag[27]]) as usize);
                let raw = tag.get(at..at + len)?;
                let units: Vec<u16> = raw.chunks(2).filter(|c| c.len() == 2).map(|c| u16::from_be_bytes([c[0], c[1]])).collect();
                return Some(String::from_utf16_lossy(&units).trim_matches(char::from(0)).trim().to_string());
            }
        }
        None
    })();
    found.filter(|s| !s.is_empty()).unwrap_or_else(|| tr!("Embedded profile"))
}

/// Dots per inch from a JPEG's JFIF header or a PNG's pHYs.
fn dpi(data: &[u8]) -> Option<f64> {
    if data.starts_with(b"\xff\xd8\xff\xe0") && data.get(6..11) == Some(b"JFIF\0") && data.len() > 18 {
        let units = data[13];
        let x = u16::from_be_bytes([data[14], data[15]]) as f64;
        return match units {
            1 => Some(x),
            2 => Some(x * 2.54),
            _ => None,
        };
    }
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        let mut i = 8;
        while i + 8 <= data.len() {
            let n = u32::from_be_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]) as usize;
            if &data[i + 4..i + 8] == b"pHYs" && n >= 9 {
                let b = data.get(i + 8..i + 17)?;
                let x = u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as f64;
                return (b[8] == 1).then_some(x * 0.0254);
            }
            if &data[i + 4..i + 8] == b"IDAT" {
                break;
            }
            i += 12 + n;
        }
    }
    None
}

/// The colour model as Pillow names it ("L", "P", "CMYK"...), read from the
/// file's header: PNG, JPEG, GIF and BMP (None: another format, shown as
/// RGB). Python read it from Pillow's `im.mode`.
pub fn color_mode(data: &[u8]) -> Option<&'static str> {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") && data.get(12..16) == Some(b"IHDR") {
        let (depth, kind) = (*data.get(24)?, *data.get(25)?);
        return Some(match (kind, depth) {
            (0, 1) => "1",
            (0, 16) => "I;16",
            (0, _) => "L",
            (2, _) => "RGB",
            (3, _) => "P",
            (4, _) => "LA",
            _ => "RGBA",
        });
    }
    if data.starts_with(b"\xff\xd8") {
        // the frame header (SOFn): its component count
        let mut i = 2;
        while i + 9 < data.len() && data[i] == 0xFF {
            let m = data[i + 1];
            if (0xC0..=0xCF).contains(&m) && !matches!(m, 0xC4 | 0xC8 | 0xCC) {
                return Some(match data[i + 9] {
                    1 => "L",
                    4 => "CMYK",
                    _ => "RGB",
                });
            }
            if m == 0xD8 || (0xD0..=0xD7).contains(&m) || m == 0x01 {
                i += 2;
                continue;
            }
            i += 2 + u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
        }
        return None;
    }
    if data.starts_with(b"GIF8") {
        return Some("P");
    }
    if data.starts_with(b"BM") {
        let bits = u16::from_le_bytes([*data.get(28)?, *data.get(29)?]);
        return Some(if bits == 1 { "1" } else if bits <= 8 { "P" } else { "RGB" });
    }
    None
}

/// What the Info panel calls a colour model (info.py's MODES), translated.
fn mode_label(mode: &str) -> String {
    let label = match mode {
        "1" => n_!("Black & White"),
        "L" | "LA" => n_!("Gray"),
        "P" => n_!("Indexed"),
        "I" => n_!("Gray (32-bit)"),
        "F" => n_!("Gray (float)"),
        "I;16" => n_!("Gray (16-bit)"),
        "RGB" | "RGBA" => "RGB",
        other => other,
    };
    gettext(label)
}

fn rational(f: &exif::Field) -> Option<f64> {
    match &f.value {
        exif::Value::Rational(v) => v.first().map(|r| r.to_f64()),
        exif::Value::SRational(v) => v.first().map(|r| r.to_f64()),
        exif::Value::Short(v) => v.first().map(|x| *x as f64),
        exif::Value::Long(v) => v.first().map(|x| *x as f64),
        _ => None,
    }
}

fn ascii(f: &exif::Field) -> String {
    match &f.value {
        exif::Value::Ascii(v) => v.first().map(|s| String::from_utf8_lossy(s).trim_matches(|c| c == '\0' || c == ' ').to_string()).unwrap_or_default(),
        _ => f.display_value().to_string(),
    }
}

/// (label, value) rows from the camera's EXIF.
pub fn camera_rows(ex: &exif::Exif) -> Vec<(&'static str, String)> {
    use exif::{In, Tag};
    let get = |t: Tag| ex.get_field(t, In::PRIMARY);
    let mut rows = Vec::new();
    let make = get(Tag::Make).map(ascii).unwrap_or_default();
    let mut model = get(Tag::Model).map(ascii).unwrap_or_default();
    if !model.is_empty() && !make.is_empty() {
        let first = make.split_whitespace().next().unwrap_or("").to_lowercase();
        if !model.to_lowercase().starts_with(&first) {
            model = format!("{make} {model}");
        }
    }
    if !model.is_empty() || !make.is_empty() {
        rows.push((n_!("Camera"), if model.is_empty() { make } else { model }));
    }
    if let Some(lens) = get(Tag::LensModel).map(ascii).filter(|l| !l.is_empty()) {
        rows.push((n_!("Lens"), lens));
    }
    for (label, tag, f) in [
        (n_!("Exposure"), Tag::ExposureTime, exposure_text as fn(f64) -> String),
        (n_!("Aperture"), Tag::FNumber, fnumber_text),
        (n_!("Focal Length"), Tag::FocalLength, focal_text),
    ] {
        if let Some(v) = get(tag).and_then(rational).map(f).filter(|v| !v.is_empty()) {
            rows.push((label, v));
        }
    }
    if let Some(iso) = get(Tag::PhotographicSensitivity).and_then(rational).filter(|i| *i > 0.0) {
        rows.push(("ISO", g(iso)));
    }
    if let Some(taken) = get(Tag::DateTimeOriginal).or_else(|| get(Tag::DateTime)).map(ascii).filter(|t| !t.is_empty()) {
        rows.push((n_!("Date Taken"), exif_date_text(&taken)));
    }
    if ex.fields().any(|f| f.tag.context() == exif::Context::Gps) {
        rows.push((n_!("Location"), tr!("GPS coordinates included")));
    }
    rows
}

fn orientation_turns(ex: &exif::Exif) -> bool {
    ex.get_field(exif::Tag::Orientation, exif::In::PRIMARY).and_then(|f| f.value.get_uint(0)).is_some_and(|o| (5..=8).contains(&o))
}

/// Everything the panel shows (blocking: run it off the main loop).
pub fn read(path: &str, texture_size: Option<(i32, i32)>) -> Info {
    let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut info = Info { name, ..Default::default() };
    let st = std::fs::metadata(path).ok();
    let (ct, _) = gio::content_type_guess(Some(path), &[]);
    let kind = gio::content_type_get_description(&ct).to_string();
    // the bytes the details come from: the file (a RAW: its camera preview)
    let raw = is_raw(path);
    let data = if raw { imageload::raw_preview(path) } else { std::fs::read(path).unwrap_or_default() };
    let ex = exif::Reader::new().read_from_container(&mut std::io::Cursor::new(&data)).ok();
    let file_info = if raw { None } else { Pixbuf::file_info(path) };
    let format = if raw {
        Some(format!("{} (RAW)", edit::ext_of(path).trim_start_matches('.').to_uppercase()))
    } else {
        file_info.as_ref().and_then(|(f, _, _)| f.name()).map(|n| n.to_uppercase())
    };
    let mut size = file_info.as_ref().map(|(_, w, h)| (*w, *h)).filter(|_| !raw);
    if let (Some((w, h)), Some(ex)) = (size, &ex) {
        if orientation_turns(ex) {
            size = Some((h, w)); // as shown: EXIF says it's turned a quarter
        }
    }
    let g_ = &mut info.general;
    if let Some((w, h)) = size.or(texture_size).filter(|(w, h)| *w > 0 && *h > 0) {
        g_.push((n_!("Dimensions"), fmt(&tr!("{w} × {h} pixels"), &[("w", &w.to_string()), ("h", &h.to_string())])));
        let mp = w as f64 * h as f64 / 1e6;
        if mp >= 1.0 {
            g_.push((n_!("Megapixels"), format!("{mp:.1}")));
        }
    }
    if let Some(st) = &st {
        g_.push((n_!("File Size"), sonata_ui::fmt::size(st.len())));
    }
    let kind_text = match &format {
        Some(f) if !kind.is_empty() && !kind.to_lowercase().contains(&f.to_lowercase()) => format!("{kind} ({f})"),
        _ if !kind.is_empty() => kind.clone(),
        Some(f) => f.clone(),
        None => tr!("Picture"),
    };
    g_.push((n_!("Kind"), kind_text));
    let (_exif, icc) = edit::metadata(&data);
    if let Some(icc) = icc {
        g_.push((n_!("Color Space"), profile_name(&icc)));
    }
    // the colour model and alpha: a tiny decode tells (scaled down, cheap)
    let small = if raw { imageload::pixbuf_from_bytes(&data, 8) } else { Pixbuf::from_file_at_scale(path, 8, 8, true).ok() };
    if let Some(pb) = small {
        g_.push((n_!("Color Model"), mode_label(color_mode(&data).unwrap_or("RGB"))));
        g_.push((n_!("Alpha Channel"), if pb.has_alpha() { tr!("Yes") } else { tr!("No") }));
    }
    if let Some(d) = dpi(&data).filter(|d| *d > 1.0) {
        g_.push((n_!("Resolution"), format!("{} ppi", d.round_ties_even())));
    }
    if let Some(m) = st.as_ref().and_then(|s| s.modified().ok()) {
        let secs = m.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
        if let Ok(d) = glib::DateTime::from_unix_local(secs) {
            g_.push((n_!("Modified"), date_text(&d)));
        }
    }
    if let Some(ex) = &ex {
        info.camera = camera_rows(ex);
    }
    info
}

pub struct InfoPanel {
    pub root: gtk::ScrolledWindow,
    body: gtk::Box,
    pub path: RefCell<Option<String>>,
}

impl InfoPanel {
    pub fn new() -> Self {
        sonata_ui::css::register_module("preview/info.py");
        let root = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).build();
        root.add_css_class("pv-info");
        root.set_size_request(INFO_W, -1);
        root.set_hexpand(false); // its wrapping labels mustn't widen it
        let body = gtk::Box::new(gtk::Orientation::Vertical, 6);
        body.add_css_class("pv-info-body");
        root.set_child(Some(&body));
        InfoPanel { root, body, path: RefCell::new(None) }
    }

    pub fn show(&self, path: &str, data: &Info) {
        *self.path.borrow_mut() = Some(path.to_string());
        while let Some(c) = self.body.first_child() {
            self.body.remove(&c);
        }
        let title = gtk::Label::builder().label(data.name.as_str()).xalign(0.0).wrap(true).wrap_mode(pango::WrapMode::WordChar).build();
        title.add_css_class("pv-info-title");
        self.body.append(&title);
        let grid = gtk::Grid::builder().column_spacing(8).row_spacing(4).build(); // one grid: both sections' columns line up
        let mut top = 0;
        section(&grid, &mut top, &tr!("General Info"), &data.general);
        if !data.camera.is_empty() {
            section(&grid, &mut top, &tr!("Camera"), &data.camera);
        }
        self.body.append(&grid);
    }
}

impl Default for InfoPanel {
    fn default() -> Self {
        Self::new()
    }
}

fn section(grid: &gtk::Grid, top: &mut i32, name: &str, rows: &[(&'static str, String)]) {
    let head = gtk::Label::builder().label(name).xalign(0.0).build();
    head.add_css_class("pv-info-section");
    grid.attach(&head, 0, *top, 2, 1);
    *top += 1;
    for (k, v) in rows {
        let key = gtk::Label::builder().label(gettext(k)).xalign(1.0).yalign(0.0).build();
        key.add_css_class("pv-info-key");
        grid.attach(&key, 0, *top, 1, 1);
        let val = gtk::Label::builder().label(v.as_str()).xalign(0.0).wrap(true).hexpand(true).wrap_mode(pango::WrapMode::WordChar).build();
        val.add_css_class("pv-info-value");
        grid.attach(&val, 1, *top, 1, 1);
        *top += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn camera_values_read_like_python() {
        assert_eq!(exposure_text(0.004), "1/250 s");
        assert_eq!(exposure_text(2.0), "2 s");
        assert_eq!(exposure_text(1.5), "1.5 s");
        assert_eq!(fnumber_text(2.8), "ƒ/2.8");
        assert_eq!(fnumber_text(4.0), "ƒ/4");
        assert_eq!(focal_text(35.0), "35 mm");
        assert_eq!(focal_text(4.25), "4.25 mm");
    }

    #[test]
    fn test_color_model_was_always_rgb() {
        // Info said "RGB" for every picture -- a grey scan, a GIF, a CMYK
        // JPEG -- where Python's said Gray / Indexed / CMYK.
        let png = |kind: u8, depth: u8| {
            let mut d = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR\0\0\0\x01\0\0\0\x01".to_vec();
            d.extend_from_slice(&[depth, kind, 0, 0, 0]);
            d
        };
        assert_eq!(color_mode(&png(0, 8)), Some("L"));
        assert_eq!(color_mode(&png(0, 16)), Some("I;16"));
        assert_eq!(color_mode(&png(3, 8)), Some("P"));
        assert_eq!(color_mode(&png(6, 8)), Some("RGBA"));
        assert_eq!(color_mode(&png(2, 8)), Some("RGB"));
        // a JPEG: APP0, then the frame header with 1, 3 or 4 components
        let jpeg = |n: u8| {
            let mut d = vec![0xFF, 0xD8, 0xFF, 0xE0, 0, 4, 0, 0];
            d.extend_from_slice(&[0xFF, 0xC0, 0, 8 + 3 * n, 8, 0, 1, 0, 1, n]);
            d.extend(std::iter::repeat_n(0u8, 3 * n as usize));
            d
        };
        assert_eq!(color_mode(&jpeg(1)), Some("L"));
        assert_eq!(color_mode(&jpeg(3)), Some("RGB"));
        assert_eq!(color_mode(&jpeg(4)), Some("CMYK"));
        assert_eq!(color_mode(b"GIF89a\x01\0\x01\0"), Some("P"));
        assert_eq!(color_mode(b"RIFF....WEBP"), None);
        assert_eq!(mode_label("L"), "Gray");
        assert_eq!(mode_label("P"), "Indexed");
        assert_eq!(mode_label("RGBA"), "RGB");
        assert_eq!(mode_label("CMYK"), "CMYK");
    }

    #[test]
    fn icc_v2_description() {
        let mut icc = vec![0u8; 128];
        icc.extend_from_slice(&1u32.to_be_bytes());
        icc.extend_from_slice(b"desc");
        icc.extend_from_slice(&144u32.to_be_bytes());
        icc.extend_from_slice(&17u32.to_be_bytes());
        icc.extend_from_slice(b"desc\0\0\0\0");
        icc.extend_from_slice(&5u32.to_be_bytes());
        icc.extend_from_slice(b"sRGB\0");
        assert_eq!(profile_name(&icc), "sRGB");
    }
}
