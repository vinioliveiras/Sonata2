//! Preview's edits (sonata2/preview/edit.py; macOS Preview: Rotate, Flip,
//! Crop, Adjust Color, Adjust Size, Markup): a list of operations over the
//! picture, applied to a small copy while editing (instant) and to the full
//! picture when saving. Nothing touches the file until Save.
//!
//! The pixels come from GdkPixbuf (every format it reads, upright) and are
//! written by GdkPixbuf; the colour adjustments follow Pillow's ImageEnhance
//! (what the Python Preview used); the camera's EXIF and the colour profile
//! go along to JPEG and PNG (the colour profile to TIFF and WebP too).

use crate::imageload::{self, is_raw};
use crate::marks::{self, Mark};
use base64::Engine;
use gtk::gdk_pixbuf::{Colorspace, Pixbuf, PixbufAnimation};
use gtk::prelude::*;
use image::{imageops, RgbaImage};
use sonata_i18n::n_;
use std::io::Read;
use std::path::Path;

/// the working copy's longest side
pub const PROXY: u32 = 1800;
pub const ADJUSTMENTS: [(&str, &str); 5] = [
    ("brightness", n_!("Brightness")),
    ("contrast", n_!("Contrast")),
    ("saturation", n_!("Saturation")),
    ("warmth", n_!("Temperature")),
    ("sharpness", n_!("Sharpness")),
];
/// formats Preview writes back in place; others (RAW, SVG, animated GIF...) need Save As
pub const WRITABLE: [(&str, &str); 7] =
    [(".png", "png"), (".jpg", "jpeg"), (".jpeg", "jpeg"), (".webp", "webp"), (".bmp", "bmp"), (".tif", "tiff"), (".tiff", "tiff")];
/// Export As: (menu name, format, extension, has a quality setting)
pub const EXPORT_FORMATS: [(&str, &str, &str, bool); 4] =
    [("PNG", "png", ".png", false), ("JPEG", "jpeg", ".jpg", true), ("WebP", "webp", ".webp", true), ("TIFF", "tiff", ".tiff", false)];

#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    /// degrees clockwise
    Rotate(i32),
    /// 'h' or 'v'
    Flip(char),
    /// fractions (x0, y0, x1, y1)
    Crop([f64; 4]),
    /// factors of the size at that point
    Resize(f64, f64),
    Markup(Vec<Mark>),
}

pub fn ext_of(path: &str) -> String {
    Path::new(path).extension().map(|e| format!(".{}", e.to_string_lossy().to_lowercase())).unwrap_or_default()
}

pub fn format_for(path: &str) -> Option<&'static str> {
    let e = ext_of(path);
    WRITABLE.iter().find(|w| w.0 == e).map(|w| w.1)
}

pub fn writable(path: &str) -> bool {
    format_for(path).is_some() && !is_raw(path)
}

/// Python's round(): halves to even.
fn pyround(v: f64) -> i64 {
    v.round_ties_even() as i64
}

pub struct Edits {
    /// the picture as opened (upright)
    pub full: RgbaImage,
    pub proxy: RgbaImage,
    /// the source had an alpha channel (saved without one otherwise)
    pub alpha: bool,
    /// kept on save: the camera's EXIF (TIFF data, its orientation reset)
    pub exif: Option<Vec<u8>>,
    /// and the colour profile
    pub icc: Option<Vec<u8>>,
    pub ops: Vec<Op>,
    /// brightness, contrast, saturation, warmth, sharpness: -1 .. 1, 0 = unchanged
    pub adjust: [f64; 5],
}

fn proxy_of(im: &RgbaImage) -> RgbaImage {
    let (w, h) = im.dimensions();
    if w.max(h) <= PROXY {
        return im.clone();
    }
    // Pillow's thumbnail(): the longer side to PROXY, aspect kept
    let s = PROXY as f64 / w.max(h) as f64;
    imageops::resize(im, ((w as f64 * s).round() as u32).max(1), ((h as f64 * s).round() as u32).max(1), imageops::FilterType::Lanczos3)
}

impl Edits {
    pub fn new(full: RgbaImage, alpha: bool, exif: Option<Vec<u8>>, icc: Option<Vec<u8>>) -> Self {
        let proxy = proxy_of(&full);
        Edits { full, proxy, alpha, exif, icc, ops: Vec::new(), adjust: [0.0; 5] }
    }

    /// None when the picture can't be edited (unreadable, an animation).
    pub fn load(path: &str) -> Option<Self> {
        let source = if is_raw(path) {
            let data = imageload::raw_preview(path);
            if data.is_empty() {
                return None;
            }
            data
        } else {
            if let Ok(anim) = PixbufAnimation::from_file(path) {
                if !anim.is_static_image() {
                    return None; // an animation: not edited here
                }
            }
            std::fs::read(path).ok()?
        };
        let pb = if is_raw(path) { imageload::pixbuf_from_bytes(&source, 0)? } else { imageload::pixbuf(path)? };
        let (data, w, h, alpha) = imageload::pixbuf_rgba(&pb);
        let full = RgbaImage::from_raw(w, h, data)?;
        let (exif, icc) = metadata(&source);
        Some(Edits::new(full, alpha, exif.map(|e| reset_orientation(e)), icc))
    }

    pub fn edited(&self) -> bool {
        !self.ops.is_empty() || self.adjust.iter().any(|v| v.abs() > 1e-3)
    }

    pub fn push(&mut self, op: Op) {
        self.ops.push(op);
    }

    pub fn undo(&mut self) -> bool {
        self.ops.pop().is_some()
    }

    pub fn revert(&mut self) {
        self.ops.clear();
        self.adjust = [0.0; 5];
    }

    pub fn adjust_index(key: &str) -> usize {
        ADJUSTMENTS.iter().position(|a| a.0 == key).unwrap_or(0)
    }

    /// The full-size result's pixels (w, h), without rendering it.
    pub fn size(&self) -> (u32, u32) {
        let (mut w, mut h) = (self.full.width() as i64, self.full.height() as i64);
        for op in &self.ops {
            match op {
                Op::Rotate(a) if a.rem_euclid(180) != 0 => std::mem::swap(&mut w, &mut h),
                Op::Crop([x0, y0, x1, y1]) => {
                    let (fw, fh) = (w as f64, h as f64);
                    w = (pyround(x1 * fw) - pyround(x0 * fw)).max(1);
                    h = (pyround(y1 * fh) - pyround(y0 * fh)).max(1);
                }
                Op::Resize(fx, fy) => {
                    w = pyround(w as f64 * fx).max(1);
                    h = pyround(h as f64 * fy).max(1);
                }
                _ => {}
            }
        }
        (w as u32, h as u32)
    }

    /// Adjust Size: the result becomes w x h pixels.
    pub fn resize_to(&mut self, w: u32, h: u32) {
        let (cw, ch) = self.size();
        if (w, h) != (cw, ch) && w > 0 && h > 0 {
            self.push(Op::Resize(w as f64 / cw as f64, h as f64 / ch as f64));
        }
    }

    pub fn render(&self, image: &RgbaImage) -> RgbaImage {
        let mut im = image.clone();
        for op in &self.ops {
            im = match op {
                Op::Rotate(a) => match a.rem_euclid(360) {
                    90 => imageops::rotate90(&im),
                    180 => imageops::rotate180(&im),
                    270 => imageops::rotate270(&im),
                    _ => im,
                },
                Op::Flip('h') => imageops::flip_horizontal(&im),
                Op::Flip(_) => imageops::flip_vertical(&im),
                Op::Crop([x0, y0, x1, y1]) => {
                    let (w, h) = (im.width() as f64, im.height() as f64);
                    let (bx0, by0) = (pyround(x0 * w).max(0), pyround(y0 * h).max(0));
                    let bx1 = (bx0 + 1).max(pyround(x1 * w)).min(im.width() as i64);
                    let by1 = (by0 + 1).max(pyround(y1 * h)).min(im.height() as i64);
                    imageops::crop_imm(&im, bx0 as u32, by0 as u32, (bx1 - bx0).max(1) as u32, (by1 - by0).max(1) as u32).to_image()
                }
                Op::Markup(items) => {
                    // drawn marks, at this size
                    let (w, h) = im.dimensions();
                    let mut data = im.into_raw();
                    marks::apply(&mut data, w, h, items);
                    RgbaImage::from_raw(w, h, data).expect("same size")
                }
                Op::Resize(fx, fy) => {
                    let w = pyround(im.width() as f64 * fx).max(1) as u32;
                    let h = pyround(im.height() as f64 * fy).max(1) as u32;
                    imageops::resize(&im, w, h, imageops::FilterType::Lanczos3)
                }
            };
        }
        adjust(&mut im, &self.adjust);
        im
    }

    /// The edited picture (the small copy), for the screen.
    pub fn texture(&self) -> gtk::gdk::Texture {
        let im = self.render(&self.proxy);
        imageload::texture_from_rgba(im.as_raw(), im.width(), im.height())
    }

    /// The full-size result into path (fmt: "png", "jpeg"...; default: by
    /// the extension), written next to it then moved over it. The edits stay.
    /// A symlink is written through (the link stays), the file keeps its
    /// permissions; EXIF and the colour profile go along.
    pub fn write(&self, path: &str, fmt: Option<&str>, quality: u32) -> Result<RgbaImage, String> {
        let fmt = fmt.or_else(|| format_for(path)).unwrap_or("png");
        let result = self.render(&self.full);
        let data = encode(&result, self.alpha, fmt, quality, self.exif.as_deref(), self.icc.as_deref())?;
        let real = std::fs::canonicalize(path).unwrap_or_else(|_| Path::new(path).to_path_buf());
        let st = std::fs::metadata(&real).ok();
        let dir = real.parent().unwrap_or(Path::new("."));
        let name = real.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let tmp = dir.join(format!(".{name}.{}.sonata-tmp", std::process::id()));
        let res = (|| -> std::io::Result<()> {
            use std::io::Write;
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            let mut f = std::fs::File::create(&tmp)?;
            f.write_all(&data)?;
            if let Some(st) = &st {
                // the old file's mode/owner, not the umask's
                std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(st.mode() & 0o7777))?;
                let _ = std::os::unix::fs::chown(&tmp, Some(st.uid()), Some(st.gid()));
            }
            f.sync_all()?;
            std::fs::rename(&tmp, &real)
        })();
        if res.is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
        res.map_err(|e| e.to_string())?;
        Ok(result)
    }

    /// The full-size result over path; it becomes the new start.
    pub fn save(&mut self, path: &str) -> Result<(), String> {
        let result = self.write(path, None, 92)?;
        self.proxy = proxy_of(&result);
        self.full = result;
        self.revert();
        Ok(())
    }
}

// -- colour (Pillow's ImageEnhance) ---------------------------------------------------------------
fn luma(p: &[u8]) -> f64 {
    // Pillow's "L": (r*19595 + g*38470 + b*7471 + 0x8000) >> 16
    ((p[0] as u32 * 19595 + p[1] as u32 * 38470 + p[2] as u32 * 7471 + 0x8000) >> 16) as f64
}

fn blend(deg: f64, v: u8, f: f64) -> u8 {
    // ImagingBlend: in1 + alpha * (in2 - in1), cut toward zero, clipped
    (deg + f * (v as f64 - deg)).trunc().clamp(0.0, 255.0) as u8
}

pub fn adjust(im: &mut RgbaImage, a: &[f64; 5]) {
    let [brightness, contrast, saturation, warmth, sharpness] = *a;
    if warmth.abs() > 1e-3 {
        // warmer: more red, less blue (and back)
        let t = warmth * 0.25;
        for p in im.pixels_mut() {
            p[0] = (p[0] as f64 * (1.0 + t)).trunc().clamp(0.0, 255.0) as u8;
            p[2] = (p[2] as f64 * (1.0 - t)).trunc().clamp(0.0, 255.0) as u8;
        }
    }
    if brightness.abs() > 1e-3 {
        for p in im.pixels_mut() {
            for c in 0..3 {
                p[c] = blend(0.0, p[c], 1.0 + brightness);
            }
        }
    }
    if contrast.abs() > 1e-3 {
        let n = (im.width() as f64 * im.height() as f64).max(1.0);
        let mean = (im.pixels().map(|p| luma(&p.0)).sum::<f64>() / n + 0.5).trunc();
        for p in im.pixels_mut() {
            for c in 0..3 {
                p[c] = blend(mean, p[c], 1.0 + contrast);
            }
        }
    }
    if saturation.abs() > 1e-3 {
        for p in im.pixels_mut() {
            let g = luma(&p.0);
            for c in 0..3 {
                p[c] = blend(g, p[c], 1.0 + saturation);
            }
        }
    }
    if sharpness.abs() > 1e-3 {
        // degenerate: ImageFilter.SMOOTH (3x3, centre 5, /13); the border stays
        let src = im.clone();
        let (w, h) = im.dimensions();
        let f = 1.0 + sharpness * 2.0;
        for y in 1..h.saturating_sub(1) {
            for x in 1..w.saturating_sub(1) {
                for c in 0..3 {
                    let mut sum = 0.0;
                    for dy in 0..3 {
                        for dx in 0..3 {
                            let k = if dx == 1 && dy == 1 { 5.0 } else { 1.0 };
                            sum += k * src.get_pixel(x + dx - 1, y + dy - 1)[c] as f64;
                        }
                    }
                    let smooth = (sum / 13.0).round().clamp(0.0, 255.0);
                    let v = src.get_pixel(x, y)[c];
                    im.get_pixel_mut(x, y)[c] = blend(smooth, v, f);
                }
            }
        }
    }
}

// -- metadata: EXIF and the colour profile ------------------------------------------------------------
/// (EXIF as TIFF data, ICC profile) found in a JPEG, PNG or WebP file.
pub fn metadata(data: &[u8]) -> (Option<Vec<u8>>, Option<Vec<u8>>) {
    if data.starts_with(b"\xff\xd8") {
        return jpeg_metadata(data);
    }
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png_metadata(data);
    }
    if data.len() > 12 && &data[..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        let (mut exif, mut icc) = (None, None);
        let mut i = 12;
        while i + 8 <= data.len() {
            let id = &data[i..i + 4];
            let n = u32::from_le_bytes([data[i + 4], data[i + 5], data[i + 6], data[i + 7]]) as usize;
            let body = data.get(i + 8..i + 8 + n);
            match (id, body) {
                (b"EXIF", Some(b)) => exif = Some(b.strip_prefix(b"Exif\0\0").unwrap_or(b).to_vec()),
                (b"ICCP", Some(b)) => icc = Some(b.to_vec()),
                _ => {}
            }
            i += 8 + n + (n & 1);
        }
        return (exif, icc);
    }
    (None, None)
}

fn jpeg_metadata(data: &[u8]) -> (Option<Vec<u8>>, Option<Vec<u8>>) {
    let (mut exif, mut icc_parts): (Option<Vec<u8>>, Vec<(u8, Vec<u8>)>) = (None, Vec::new());
    let mut i = 2;
    while i + 4 <= data.len() && data[i] == 0xFF {
        let marker = data[i + 1];
        if marker == 0xDA || marker == 0xD9 {
            break;
        }
        let n = u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
        let Some(body) = data.get(i + 4..i + 2 + n) else { break };
        if marker == 0xE1 && body.starts_with(b"Exif\0\0") && exif.is_none() {
            exif = Some(body[6..].to_vec());
        } else if marker == 0xE2 && body.starts_with(b"ICC_PROFILE\0") && body.len() > 14 {
            icc_parts.push((body[12], body[14..].to_vec()));
        }
        i += 2 + n;
    }
    icc_parts.sort_by_key(|p| p.0);
    let icc = (!icc_parts.is_empty()).then(|| icc_parts.into_iter().flat_map(|p| p.1).collect());
    (exif, icc)
}

fn png_metadata(data: &[u8]) -> (Option<Vec<u8>>, Option<Vec<u8>>) {
    let (mut exif, mut icc) = (None, None);
    let mut i = 8;
    while i + 8 <= data.len() {
        let n = u32::from_be_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]) as usize;
        let kind = &data[i + 4..i + 8];
        let Some(body) = data.get(i + 8..i + 8 + n) else { break };
        match kind {
            b"eXIf" => exif = Some(body.to_vec()),
            b"iCCP" => {
                // name, NUL, compression method, zlib data
                if let Some(z) = body.iter().position(|b| *b == 0) {
                    let mut out = Vec::new();
                    if flate2::read::ZlibDecoder::new(&body[z + 2..]).read_to_end(&mut out).is_ok() {
                        icc = Some(out);
                    }
                }
            }
            b"IDAT" | b"IEND" => break,
            _ => {}
        }
        i += 12 + n;
    }
    (exif, icc)
}

/// The EXIF's orientation set to 1 (normal): the pixels are upright now.
pub fn reset_orientation(mut tiff: Vec<u8>) -> Vec<u8> {
    if tiff.len() < 8 {
        return tiff;
    }
    let le = &tiff[..2] == b"II";
    let u16_at = |d: &[u8], i: usize| if le { u16::from_le_bytes([d[i], d[i + 1]]) } else { u16::from_be_bytes([d[i], d[i + 1]]) };
    let u32_at = |d: &[u8], i: usize| {
        if le { u32::from_le_bytes([d[i], d[i + 1], d[i + 2], d[i + 3]]) } else { u32::from_be_bytes([d[i], d[i + 1], d[i + 2], d[i + 3]]) }
    };
    let ifd = u32_at(&tiff, 4) as usize;
    if ifd + 2 > tiff.len() {
        return tiff;
    }
    let count = u16_at(&tiff, ifd) as usize;
    for k in 0..count {
        let e = ifd + 2 + k * 12;
        if e + 12 > tiff.len() {
            break;
        }
        if u16_at(&tiff, e) == 0x0112 {
            let one = if le { 1u16.to_le_bytes() } else { 1u16.to_be_bytes() };
            tiff[e + 8..e + 10].copy_from_slice(&one);
        }
    }
    tiff
}

fn pixbuf_of(im: &RgbaImage, alpha: bool) -> Pixbuf {
    let (w, h) = im.dimensions();
    if alpha {
        return Pixbuf::from_bytes(&gtk::glib::Bytes::from(im.as_raw()), Colorspace::Rgb, true, 8, w as i32, h as i32, w as i32 * 4);
    }
    let rgb: Vec<u8> = im.pixels().flat_map(|p| [p[0], p[1], p[2]]).collect();
    Pixbuf::from_bytes(&gtk::glib::Bytes::from(&rgb), Colorspace::Rgb, false, 8, w as i32, h as i32, w as i32 * 3)
}

/// The picture encoded as `fmt` (GdkPixbuf's savers), EXIF and colour
/// profile put back.
pub fn encode(im: &RgbaImage, alpha: bool, fmt: &str, quality: u32, exif: Option<&[u8]>, icc: Option<&[u8]>) -> Result<Vec<u8>, String> {
    let keep_alpha = alpha && !matches!(fmt, "jpeg" | "bmp");
    let pb = pixbuf_of(im, keep_alpha);
    let q = quality.clamp(1, 100).to_string();
    let icc64 = icc.map(|i| base64::engine::general_purpose::STANDARD.encode(i));
    let mut opts: Vec<(&str, &str)> = Vec::new();
    if matches!(fmt, "jpeg" | "webp") {
        opts.push(("quality", &q));
    }
    if fmt == "tiff" {
        opts.push(("compression", "5")); // LZW
    }
    if let (Some(i), true) = (&icc64, matches!(fmt, "png" | "jpeg" | "tiff" | "webp")) {
        opts.push(("icc-profile", i));
    }
    let mut data = pb.save_to_bufferv(fmt, &opts).or_else(|e| {
        // a saver without that option (WebP's ICC): once more without the profile
        opts.retain(|o| o.0 != "icc-profile");
        pb.save_to_bufferv(fmt, &opts).map_err(|_| e.to_string())
    })?;
    if let Some(ex) = exif {
        data = match fmt {
            "jpeg" => jpeg_with_exif(&data, ex),
            "png" => png_with_exif(&data, ex),
            _ => data,
        };
    }
    Ok(data)
}

fn jpeg_with_exif(data: &[u8], exif: &[u8]) -> Vec<u8> {
    let n = exif.len() + 8;
    if n > 0xFFFF || !data.starts_with(b"\xff\xd8") {
        return data.to_vec();
    }
    // after SOI and the JFIF APP0 when there is one
    let mut at = 2;
    if data.len() > 6 && data[2] == 0xFF && data[3] == 0xE0 {
        at = 4 + u16::from_be_bytes([data[4], data[5]]) as usize;
    }
    let mut out = data[..at].to_vec();
    out.extend_from_slice(&[0xFF, 0xE1]);
    out.extend_from_slice(&(n as u16).to_be_bytes());
    out.extend_from_slice(b"Exif\0\0");
    out.extend_from_slice(exif);
    out.extend_from_slice(&data[at..]);
    out
}

fn crc32(bytes: &[u8]) -> u32 {
    let mut c = !0u32;
    for b in bytes {
        c ^= *b as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 { 0xEDB88320 ^ (c >> 1) } else { c >> 1 };
        }
    }
    !c
}

fn png_with_exif(data: &[u8], exif: &[u8]) -> Vec<u8> {
    if data.len() < 33 || !data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return data.to_vec();
    }
    let at = 8 + 25; // after IHDR (13 bytes of data + length, type, crc)
    let mut chunk = Vec::with_capacity(exif.len() + 12);
    chunk.extend_from_slice(&(exif.len() as u32).to_be_bytes());
    let mut body = b"eXIf".to_vec();
    body.extend_from_slice(exif);
    chunk.extend_from_slice(&body);
    chunk.extend_from_slice(&crc32(&body).to_be_bytes());
    let mut out = data[..at].to_vec();
    out.extend_from_slice(&chunk);
    out.extend_from_slice(&data[at..]);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn picture(w: u32, h: u32) -> RgbaImage {
        RgbaImage::from_fn(w, h, |x, y| image::Rgba([(x * 10) as u8, (y * 10) as u8, 100, 255]))
    }

    #[test]
    fn size_follows_the_operations() {
        let mut ed = Edits::new(picture(40, 20), false, None, None);
        ed.push(Op::Rotate(90));
        assert_eq!(ed.size(), (20, 40));
        ed.push(Op::Crop([0.0, 0.0, 0.5, 0.5]));
        assert_eq!(ed.size(), (10, 20));
        ed.resize_to(5, 10);
        assert_eq!(ed.size(), (5, 10));
        let out = ed.render(&ed.full);
        assert_eq!(out.dimensions(), (5, 10));
        assert!(ed.edited());
        assert!(ed.undo());
        ed.revert();
        assert!(!ed.edited());
    }

    #[test]
    fn rotate_and_flip_move_the_pixels() {
        let mut ed = Edits::new(picture(3, 2), false, None, None);
        ed.push(Op::Rotate(90)); // clockwise: the bottom-left corner goes to the top left
        let out = ed.render(&ed.full);
        assert_eq!(out.get_pixel(0, 0), ed.full.get_pixel(0, 1));
        ed.revert();
        ed.push(Op::Flip('h'));
        let out = ed.render(&ed.full);
        assert_eq!(out.get_pixel(0, 0), ed.full.get_pixel(2, 0));
    }

    #[test]
    fn adjustments_follow_pillow() {
        let mut im = RgbaImage::from_pixel(4, 4, image::Rgba([100, 150, 200, 255]));
        adjust(&mut im, &[0.2, 0.0, 0.0, 0.0, 0.0]); // Brightness(1.2)
        assert_eq!(im.get_pixel(0, 0).0, [120, 180, 240, 255]);
        let mut im = RgbaImage::from_pixel(2, 2, image::Rgba([100, 150, 200, 255]));
        adjust(&mut im, &[0.0, 0.0, -1.0, 0.0, 0.0]); // Color(0): grey
        let p = im.get_pixel(0, 0).0;
        assert!(p[0] == p[1] && p[1] == p[2]);
        let mut im = RgbaImage::from_pixel(2, 2, image::Rgba([100, 150, 200, 255]));
        adjust(&mut im, &[0.0, 0.0, 0.0, 1.0, 0.0]); // warmer
        assert_eq!(im.get_pixel(0, 0).0, [125, 150, 150, 255]);
    }

    #[test]
    fn orientation_is_reset_in_the_exif() {
        // II*\0, IFD at 8: one entry, 0x0112 SHORT 1 value 6
        let tiff = vec![b'I', b'I', 42, 0, 8, 0, 0, 0, 1, 0, 0x12, 0x01, 3, 0, 1, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0];
        let out = reset_orientation(tiff);
        assert_eq!(&out[18..20], &[1, 0]);
    }

    #[test]
    fn exif_goes_into_jpeg_and_png() {
        let j = jpeg_with_exif(b"\xff\xd8\xff\xe0\x00\x04ab\xff\xd9", b"TIFFDATA");
        assert_eq!(jpeg_metadata(&j).0.as_deref(), Some(&b"TIFFDATA"[..]));
        let mut png = b"\x89PNG\r\n\x1a\n".to_vec();
        png.extend_from_slice(&[0, 0, 0, 13]);
        png.extend_from_slice(b"IHDR");
        png.extend_from_slice(&[0; 13 + 4]);
        png.extend_from_slice(&[0, 0, 0, 0]);
        png.extend_from_slice(b"IEND");
        png.extend_from_slice(&[0; 4]);
        let p = png_with_exif(&png, b"TIFFDATA");
        assert_eq!(png_metadata(&p).0.as_deref(), Some(&b"TIFFDATA"[..]));
        assert_eq!(crc32(b"IEND"), 0xAE426082);
    }

    #[test]
    fn writable_formats() {
        assert!(writable("/a/b.JPG") && writable("/a/b.png") && !writable("/a/b.svg") && !writable("/a/b.dng"));
    }
}
