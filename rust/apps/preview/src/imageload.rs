//! Pictures for Preview (sonata2/imageload.py): every format the system can
//! read (GTK, then GdkPixbuf's loaders: WebP, AVIF/HEIF, SVG...), plus camera
//! RAW files, shown through the full-size JPEG their camera put inside --
//! like macOS Quick Look: no RAW decoder needed, and instant.

use gtk::gdk_pixbuf::{Pixbuf, PixbufLoader};
use gtk::{gdk, prelude::*};
use std::path::Path;

pub const RAW_EXTS: &[&str] = &[
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw", ".3fr",
    ".erf", ".kdc", ".mrw", ".x3f", ".iiq", ".rwl", ".mos",
];
pub const RAW_TYPES: &[&str] = &[
    "image/x-canon-cr2", "image/x-canon-cr3", "image/x-canon-crw", "image/x-nikon-nef", "image/x-nikon-nrw", "image/x-sony-arw",
    "image/x-sony-srf", "image/x-sony-sr2", "image/x-adobe-dng", "image/x-olympus-orf", "image/x-panasonic-rw2",
    "image/x-panasonic-raw", "image/x-fuji-raf", "image/x-pentax-pef", "image/x-samsung-srw", "image/x-sigma-x3f", "image/x-dcraw",
];
const MAX_RAW: u64 = 200 * 1000 * 1000;

pub fn is_raw(path: &str) -> bool {
    let low = path.to_lowercase();
    RAW_EXTS.iter().any(|e| low.ends_with(e))
}

/// End of the JPEG at `start` (after its EOI), walking its segments so
/// thumbnails nested in its EXIF are skipped; None if it isn't one.
pub fn jpeg_end(data: &[u8], start: usize) -> Option<usize> {
    let (mut i, n) = (start + 2, data.len());
    while i + 4 <= n {
        if data[i] != 0xFF {
            return None;
        }
        let marker = data[i + 1];
        if marker == 0xD9 {
            return Some(i + 2);
        }
        if marker == 0xDA {
            // scan data: up to the EOI
            return find(data, b"\xff\xd9", i + 2).map(|e| e + 2);
        }
        if (0xD0..=0xD8).contains(&marker) || marker == 0x01 {
            i += 2;
            continue;
        }
        i += 2 + u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
    }
    None
}

fn find(data: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from >= data.len() {
        return None;
    }
    data[from..].windows(needle.len()).position(|w| w == needle).map(|p| p + from)
}

/// The largest JPEG inside a RAW file (the camera's full preview). Mapped,
/// not read: only the pages walked are loaded (a RAW can be 200 MB).
pub fn raw_preview(path: &str) -> Vec<u8> {
    let Ok(f) = std::fs::File::open(path) else { return Vec::new() };
    if f.metadata().map(|m| m.len() == 0 || m.len() > MAX_RAW).unwrap_or(true) {
        return Vec::new();
    }
    let Ok(data) = (unsafe { memmap2::Mmap::map(&f) }) else { return Vec::new() };
    let mut best = (0usize, 0usize);
    let mut pos = find(&data, b"\xff\xd8\xff", 0);
    while let Some(p) = pos {
        let end = jpeg_end(&data, p);
        if let Some(e) = end.filter(|e| *e > p && e - p > best.1 - best.0) {
            best = (p, e);
        }
        let next = match end {
            Some(e) if e > p => e.max(p + 3),
            _ => p + 3,
        };
        pos = find(&data, b"\xff\xd8\xff", next);
    }
    data[best.0..best.1].to_vec()
}

/// A pixbuf from encoded bytes (at most `size` on its longer side), upright.
pub fn pixbuf_from_bytes(data: &[u8], size: i32) -> Option<Pixbuf> {
    let loader = PixbufLoader::new();
    if size > 0 {
        loader.connect_size_prepared(move |ld, w, h| {
            let s = (size as f64 / w.max(1) as f64).min(size as f64 / h.max(1) as f64).min(1.0);
            ld.set_size(((w as f64 * s).round() as i32).max(1), ((h as f64 * s).round() as i32).max(1));
        });
    }
    loader.write(data).ok()?;
    loader.close().ok()?;
    let pb = loader.pixbuf()?;
    Some(pb.apply_embedded_orientation().unwrap_or(pb))
}

/// The picture upright, as a pixbuf (RAW: its camera preview).
pub fn pixbuf(path: &str) -> Option<Pixbuf> {
    if is_raw(path) {
        let data = raw_preview(path);
        return if data.is_empty() { None } else { pixbuf_from_bytes(&data, 0) };
    }
    let pb = Pixbuf::from_file(path).ok()?;
    Some(pb.apply_embedded_orientation().unwrap_or(pb))
}

/// Texture of any picture (RAW: its camera preview), or None.
pub fn texture(path: &str) -> Option<gdk::Texture> {
    if is_raw(path) {
        return pixbuf(path).map(|pb| gdk::Texture::for_pixbuf(&pb));
    }
    if let Ok(t) = gdk::Texture::from_filename(Path::new(path)) {
        return Some(t);
    }
    pixbuf(path).map(|pb| gdk::Texture::for_pixbuf(&pb))
}

/// A thumbnail at most size x size (orientation applied), or None.
pub fn pixbuf_at_scale(path: &str, size: i32) -> Option<Pixbuf> {
    if is_raw(path) {
        let data = raw_preview(path);
        return if data.is_empty() { None } else { pixbuf_from_bytes(&data, size) };
    }
    let pb = Pixbuf::from_file_at_scale(path, size, size, true).ok()?;
    Some(pb.apply_embedded_orientation().unwrap_or(pb))
}

/// The pixels of a pixbuf (thread-safe plain data): (rgba bytes, width,
/// height, has alpha) -- 4 bytes a pixel, rows packed.
pub fn pixbuf_rgba(pb: &Pixbuf) -> (Vec<u8>, u32, u32, bool) {
    let (w, h) = (pb.width() as usize, pb.height() as usize);
    let (n, stride) = (pb.n_channels() as usize, pb.rowstride() as usize);
    let bytes = pb.read_pixel_bytes();
    let mut out = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        let row = &bytes[y * stride..];
        for x in 0..w {
            let p = &row[x * n..x * n + n];
            out.extend_from_slice(&[p[0], p[1], p[2], if n == 4 { p[3] } else { 255 }]);
        }
    }
    (out, w as u32, h as u32, pb.has_alpha())
}

/// A texture from straight RGBA pixels.
pub fn texture_from_rgba(data: &[u8], w: u32, h: u32) -> gdk::Texture {
    gdk::MemoryTexture::new(w as i32, h as i32, gdk::MemoryFormat::R8g8b8a8, &gtk::glib::Bytes::from(data), w as usize * 4).upcast()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_largest_jpeg_inside_a_raw_is_found() {
        // a small JPEG (APP1 holding nothing), then a bigger one
        let small: &[u8] = b"\xff\xd8\xff\xe1\x00\x04ab\xff\xd9";
        let big: &[u8] = b"\xff\xd8\xff\xe0\x00\x06JFIF\xff\xda\x00\x02\x01\x02\x03\x04\x05\x06\xff\xd9";
        let mut raw = b"RAWHEADER".to_vec();
        raw.extend_from_slice(small);
        raw.extend_from_slice(b"junk");
        raw.extend_from_slice(big);
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("x.nef");
        std::fs::write(&p, &raw).unwrap();
        assert_eq!(raw_preview(p.to_str().unwrap()), big);
        assert!(is_raw("/a/B.CR2") && !is_raw("/a/b.jpg"));
    }
}
