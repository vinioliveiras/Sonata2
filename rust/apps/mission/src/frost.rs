//! The "fake" glass, at no cost per frame (shell/frost.py): a small picture
//! of a display (grim), blurred once, drawn under a surface's panels in
//! place of the compositor's blur -- which blurs a surface's whole changed
//! area again on every frame it changes. The Game Bar's (over a running
//! game) and Notification Center's (Vini: laggy with many notifications --
//! the column filled the display's height, and each frame of scrolling or
//! sliding was a blur pass over all of it). Both are in glass.NEVER_BLUR.
//!
//! ```no_run
//! # use gtk::prelude::*;
//! # let win = gtk::Window::new();
//! let w = win.clone();
//! sonata_mission::frost::capture(Some("DP-1"), |_tex| { /* blurred, or None */ }, move || w.renderer());
//! ```

use gtk::prelude::*;
use gtk::{gdk, graphene, gsk};

/// the display's picture at this scale (blurred: no detail needed)
pub const FROST_SCALE: f64 = 0.25;
/// blurred this much (px at full size)
pub const FROST_BLUR: f64 = 28.0;
/// Notification Center's, closer to macOS (Vini): blurred more, and the
/// colours lifted -- (saturation, brightness) -- so its glass looks vivid
/// rather than washed out. Baked into the picture once, never per frame.
pub const NC_BLUR: f64 = 40.0;
pub const NC_VIBRANCY: (f64, f64) = (1.35, 1.06);
/// each channel's share of brightness (Rec. 709)
pub const LUMA: [f64; 3] = [0.2126, 0.7152, 0.0722];

pub fn available() -> bool {
    glib::find_program_in_path("grim").is_some()
}

/// grim: the display's picture, uncompressed (fast). At full size: grim
/// shrinking it itself (-s) took ~160 ms on a 1080p display, ~25 ms
/// without -- blur_texture shrinks it on the GPU instead (Vini: the Game
/// Bar opened solid, then turned to glass). PPM: its pixels as they are,
/// made a texture without decoding (texture_from; a PNG of a 4K display
/// took ~16 ms to decode on the main loop).
pub fn frost_command(output: Option<&str>) -> Vec<String> {
    let mut cmd: Vec<String> = ["grim", "-t", "ppm"].iter().map(|s| s.to_string()).collect();
    if let Some(o) = output.filter(|o| !o.is_empty()) {
        cmd.extend(["-o".to_string(), o.to_string()]);
    }
    cmd.push("-".into());
    cmd
}

/// A binary PPM's (width, height, where its pixels start), when it is one
/// GDK can take as it is (8-bit, all its pixels there). Comments allowed.
pub fn ppm_header(raw: &[u8]) -> Option<(usize, usize, usize)> {
    if !raw.starts_with(b"P6") {
        return None;
    }
    let (mut fields, mut i) = (Vec::new(), 2);
    while fields.len() < 3 && i < raw.len() {
        // width, height, maxval (and comments)
        while i < raw.len() && raw[i].is_ascii_whitespace() {
            i += 1;
        }
        if raw.get(i) == Some(&b'#') {
            while i < raw.len() && raw[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        let j = (i..raw.len()).find(|&j| raw[j].is_ascii_whitespace()).unwrap_or(raw.len());
        fields.push(std::str::from_utf8(&raw[i..j]).ok()?.parse::<usize>().ok()?);
        i = j;
    }
    let [w, h, maxval] = fields[..] else { return None };
    i += 1; // the one whitespace before the pixels
    if maxval != 255 || w == 0 || h == 0 || raw.len().saturating_sub(i) < w * h * 3 {
        return None;
    }
    Some((w, h, i))
}

/// grim's picture (a binary PPM, else anything GDK reads) as a texture;
/// None when it isn't one. The PPM's pixels are used in place.
pub fn texture_from(data: Option<&glib::Bytes>) -> Option<gdk::Texture> {
    let data = data.filter(|d| !d.is_empty())?;
    if data.starts_with(b"P6") {
        let (w, h, at) = ppm_header(data)?;
        let pixels = glib::Bytes::from_bytes(data, at..at + w * h * 3);
        return Some(gdk::MemoryTexture::new(w as i32, h as i32, gdk::MemoryFormat::R8g8b8, &pixels, w * 3).upcast());
    }
    gdk::Texture::from_bytes(data).ok()
}

/// GSK's colour matrix (16 floats, row by row) that saturates a colour
/// around its own grey and brightens it. GSK multiplies the colour as a row
/// vector (colour x matrix): row i is what input channel i gives to each
/// output channel.
pub fn vibrancy_matrix(saturation: f64, brightness: f64) -> [f32; 16] {
    let (s, b) = (saturation, brightness);
    let mut m = [0.0f32; 16];
    for i in 0..3 {
        for j in 0..3 {
            m[i * 4 + j] = ((LUMA[i] * (1.0 - s) + if i == j { s } else { 0.0 }) * b) as f32;
        }
    }
    m[15] = 1.0; // alpha as it is
    m
}

/// What [`vibrancy_matrix`] does to one colour (0..1 channels), clamped.
pub fn vibrant(rgb: [f64; 3], saturation: f64, brightness: f64) -> [f64; 3] {
    let m = vibrancy_matrix(saturation, brightness);
    let mut out = [0.0; 3];
    for (j, o) in out.iter_mut().enumerate() {
        *o = (0..3).map(|i| rgb[i] * f64::from(m[i * 4 + j])).sum::<f64>().clamp(0.0, 1.0);
    }
    out
}

/// The picture shrunk to FROST_SCALE and blurred once (GSK, on the GPU).
pub fn blur_texture(renderer: Option<&gsk::Renderer>, texture: &gdk::Texture) -> gdk::Texture {
    blur_texture_with(renderer, texture, FROST_BLUR, None)
}

/// [`blur_texture`] blurred `blur` px; `vibrancy` (saturation,
/// brightness): its colours lifted too, in the same pass.
pub fn blur_texture_with(renderer: Option<&gsk::Renderer>, texture: &gdk::Texture, blur: f64, vibrancy: Option<(f64, f64)>) -> gdk::Texture {
    let Some(renderer) = renderer else { return texture.clone() };
    let w = ((texture.width() as f64 * FROST_SCALE).round() as i32).max(1) as f32;
    let h = ((texture.height() as f64 * FROST_SCALE).round() as i32).max(1) as f32;
    let snap = gtk::Snapshot::new();
    let r = graphene::Rect::new(0.0, 0.0, w, h);
    snap.push_clip(&r);
    if let Some((s, b)) = vibrancy {
        snap.push_color_matrix(&graphene::Matrix::from_float(vibrancy_matrix(s, b)), &graphene::Vec4::new(0.0, 0.0, 0.0, 0.0));
    }
    snap.push_blur(blur * FROST_SCALE);
    // (mipmapped: no shimmer; a little past the edges: no dark rim)
    snap.append_scaled_texture(texture, gsk::ScalingFilter::Trilinear, &graphene::Rect::new(-8.0, -8.0, w + 16.0, h + 16.0));
    snap.pop();
    if vibrancy.is_some() {
        snap.pop();
    }
    snap.pop();
    match snap.to_node() {
        Some(node) => renderer.render_texture(node, Some(&r)),
        None => texture.clone(),
    }
}

/// The display `output` (a connector, None: grim's choice) pictured and
/// blurred: done(texture) on the main loop, done(None) if it couldn't be.
/// renderer_of(): the renderer to blur with, asked when the picture comes
/// (the window may only be realized by then). False: no grim, nothing asked.
pub fn capture(output: Option<&str>, done: impl FnOnce(Option<gdk::Texture>) + 'static, renderer_of: impl FnOnce() -> Option<gsk::Renderer> + 'static) -> bool {
    capture_with(output, FROST_BLUR, None, done, renderer_of)
}

/// [`capture`], blurred and lifted as [`blur_texture_with`]: Notification
/// Center's (`NC_BLUR`, `NC_VIBRANCY`).
pub fn capture_with(output: Option<&str>, blur: f64, vibrancy: Option<(f64, f64)>, done: impl FnOnce(Option<gdk::Texture>) + 'static, renderer_of: impl FnOnce() -> Option<gsk::Renderer> + 'static) -> bool {
    if !available() {
        return false;
    }
    let argv: Vec<std::ffi::OsString> = frost_command(output).into_iter().map(Into::into).collect();
    let argv: Vec<&std::ffi::OsStr> = argv.iter().map(|s| s.as_os_str()).collect();
    let Ok(proc) = gio::Subprocess::newv(&argv, gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE) else { return false };
    proc.communicate_async(None, gio::Cancellable::NONE, move |res| {
        let tex = res.ok().and_then(|(out, _err)| texture_from(out.as_ref()));
        // never the picture unblurred: without a renderer yet, no glass this
        // time (the panels solid) -- not a sharp screenshot behind them
        let renderer = renderer_of();
        done(match (tex, renderer) {
            (Some(t), Some(r)) => Some(blur_texture_with(Some(&r), &t, blur, vibrancy)),
            _ => None,
        });
    });
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command() {
        assert_eq!(frost_command(Some("DP-1")), ["grim", "-t", "ppm", "-o", "DP-1", "-"]);
        assert_eq!(frost_command(None), ["grim", "-t", "ppm", "-"]);
    }

    /// tests/test_gamebar_glass.py: test_picture_decoded_on_the_main_loop --
    /// grim's PPM is used as it is; anything short or odd is no picture.
    #[test]
    fn ppm_read_in_place() {
        let px = [255u8, 0, 0].repeat(6);
        let mut ok = b"P6\n# grim\n3 2\n255\n".to_vec();
        ok.extend(&px);
        assert_eq!(ppm_header(&ok), Some((3, 2, 18)));
        let mut short = b"P6\n3 2\n255\n".to_vec();
        short.extend(&px[..5]);
        for bad in [&b""[..], &short, b"not a picture", b"P6\n3 2\n65535\n"] {
            assert_eq!(ppm_header(bad), None);
        }
        assert!(texture_from(None).is_none());
        assert!(texture_from(Some(&glib::Bytes::from_static(b""))).is_none());
    }

    /// tests/test_nc_glass.py: test_glass_washed_out -- Vini wanted
    /// Notification Center's glass closer to macOS: its colours lifted. A
    /// colour gets more saturated and a little brighter; a grey stays grey
    /// (only brighter); saturation 0 is its own grey.
    #[test]
    fn test_glass_washed_out() {
        let (s, b) = NC_VIBRANCY;
        assert!(s > 1.0 && b > 1.0 && NC_BLUR > FROST_BLUR);
        let rgb = [200.0 / 255.0, 100.0 / 255.0, 60.0 / 255.0];
        let out = vibrant(rgb, s, b);
        assert!(out[0] - out[2] > rgb[0] - rgb[2]); // further apart: more saturated
        assert!(out[0] > rgb[0]);
        let grey = vibrant([0.5; 3], s, b);
        assert!(grey.iter().all(|c| (c - 0.5 * b).abs() < 1e-6));
        let flat = vibrant(rgb, 0.0, 1.0);
        let y: f64 = (0..3).map(|i| rgb[i] * LUMA[i]).sum();
        assert!(flat.iter().all(|c| (c - y).abs() < 1e-6));
        assert_eq!(vibrancy_matrix(1.0, 1.0), [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]);
    }
}
