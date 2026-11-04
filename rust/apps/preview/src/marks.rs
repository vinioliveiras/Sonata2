//! Preview's Markup marks and how they are drawn (sonata2/preview/markup.py,
//! its first half): pen, highlighter, shapes, arrows, text, emoji, numbered
//! steps and pixelate. Positions are fractions of the picture (0..1 across,
//! 0..1 down), sizes fractions of its longer side -- the same marks at any
//! zoom and on the full-size picture. One renderer (Cairo + Pango) draws them
//! on screen and into the saved file, so what is saved is what was seen.

use gtk::{cairo, pango};
use std::f64::consts::PI;

pub const COLORS: [&str; 8] = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#af52de", "#1c1c1e", "#ffffff"];
/// thin / medium / thick, of the picture's longer side
pub const WIDTHS: [f64; 3] = [0.0025, 0.005, 0.009];
pub const SHAPES: [&str; 10] = ["rect", "round", "ellipse", "line", "arrow", "star", "bubble", "check", "cross", "question"];
pub const EMOJI: [&str; 32] = [
    "😀", "😂", "😍", "🤔", "😮", "😢", "😡", "👍", "👎", "👏", "🙏", "💪", "👀", "🔥", "✨", "🎉", "❤️", "💯", "✅", "❌", "⚠️",
    "❓", "❗", "💡", "📌", "⭐", "🚀", "🐛", "👉", "👆", "👇", "👈",
];
pub const TEXT_SIZE: f64 = 0.03;
pub const EMOJI_SIZE: f64 = 0.06;
pub const STEP_SIZE: f64 = 0.035;
/// pixelate: a block, of the picture's longer side
const PIXEL: f64 = 0.012;

/// One mark. `t`: its kind ("pen", "hl", a shape, "pixelate", "text",
/// "emoji", "step"); `p`: its points (two corners for a boxed one).
#[derive(Clone, Debug, PartialEq)]
pub struct Mark {
    pub t: String,
    pub c: String,
    pub w: f64,
    pub p: Vec<[f64; 2]>,
    pub text: String,
    pub s: f64,
    pub n: u32,
    /// opacity (live drawing's strokes that fade away)
    pub o: f64,
}

impl Mark {
    pub fn new(t: &str, c: &str, p: Vec<[f64; 2]>) -> Self {
        Mark { t: t.into(), c: c.into(), w: WIDTHS[1], p, text: String::new(), s: 0.0, n: 0, o: 1.0 }
    }

    /// Drawn from a drag: two corners.
    pub fn boxed(&self) -> bool {
        is_boxed(&self.t)
    }

    pub fn has_size(&self) -> bool {
        matches!(self.t.as_str(), "text" | "emoji" | "step")
    }
}

pub fn is_boxed(t: &str) -> bool {
    SHAPES.contains(&t) || t == "pixelate"
}

/// A "#rrggbb" colour as Cairo's 0..1 channels.
pub fn rgb(hex: &str) -> (f64, f64, f64) {
    let h = hex.trim_start_matches('#');
    let h: String = if h.len() == 3 { h.chars().flat_map(|c| [c, c]).collect() } else { h.to_string() };
    let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0) as f64 / 255.0;
    (p(0), p(2), p(4))
}

fn mbox(m: &Mark, w: f64, h: f64) -> (f64, f64, f64, f64) {
    let (a, b) = (m.p[0], *m.p.last().unwrap_or(&m.p[0]));
    let (x0, x1) = if a[0] * w <= b[0] * w { (a[0] * w, b[0] * w) } else { (b[0] * w, a[0] * w) };
    let (y0, y1) = if a[1] * h <= b[1] * h { (a[1] * h, b[1] * h) } else { (b[1] * h, a[1] * h) };
    (x0, y0, x1, y1)
}

fn round_rect(cr: &cairo::Context, x0: f64, y0: f64, x1: f64, y1: f64, r: f64) {
    let r = r.min((x1 - x0) / 2.0).min((y1 - y0) / 2.0).max(0.0);
    cr.new_sub_path();
    cr.arc(x1 - r, y0 + r, r, -PI / 2.0, 0.0);
    cr.arc(x1 - r, y1 - r, r, 0.0, PI / 2.0);
    cr.arc(x0 + r, y1 - r, r, PI / 2.0, PI);
    cr.arc(x0 + r, y0 + r, r, PI, 3.0 * PI / 2.0);
    cr.close_path();
}

/// A freehand stroke through the points, smoothed (midpoint quadratic curves).
fn smooth(cr: &cairo::Context, pts: &[(f64, f64)]) {
    cr.move_to(pts[0].0, pts[0].1);
    if pts.len() == 1 {
        cr.line_to(pts[0].0 + 0.01, pts[0].1);
        return;
    }
    for i in 1..pts.len() - 1 {
        let ((x0, y0), (x1, y1)) = (pts[i], pts[i + 1]);
        let (mx, my) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0);
        let (cx, cy) = cr.current_point().unwrap_or((x0, y0));
        // quadratic (control x0, y0) as a cubic
        cr.curve_to(cx + 2.0 / 3.0 * (x0 - cx), cy + 2.0 / 3.0 * (y0 - cy), mx + 2.0 / 3.0 * (x0 - mx), my + 2.0 / 3.0 * (y0 - my), mx, my);
    }
    let last = pts[pts.len() - 1];
    cr.line_to(last.0, last.1);
}

pub fn layout(cr: &cairo::Context, text: &str, px: f64, bold: bool) -> pango::Layout {
    let lay = pangocairo::functions::create_layout(cr);
    let mut fd = pango::FontDescription::from_string("Inter Variable, Inter, sans-serif");
    fd.set_absolute_size(px.max(1.0) * pango::SCALE as f64);
    if bold {
        fd.set_weight(pango::Weight::Semibold);
    }
    lay.set_font_description(Some(&fd));
    lay.set_text(text);
    lay
}

/// (w, h) of a text / emoji mark in picture units.
pub fn text_size(m: &Mark, w: f64, h: f64) -> (f64, f64) {
    let surf = cairo::ImageSurface::create(cairo::Format::ARgb32, 1, 1).expect("a 1x1 surface");
    let cr = cairo::Context::new(&surf).expect("a context");
    let s = w.max(h);
    let text = if m.text.is_empty() { " " } else { &m.text };
    let size = if m.s > 0.0 { m.s } else { TEXT_SIZE };
    let lay = layout(&cr, text, size * s, m.t == "text");
    let (_ink, log) = lay.pixel_extents();
    (log.width() as f64, log.height() as f64)
}

/// The mark's box in picture units (x0, y0, x1, y1), for selecting it.
pub fn bounds(m: &Mark, w: f64, h: f64) -> (f64, f64, f64, f64) {
    let s = w.max(h);
    if m.boxed() {
        return mbox(m, w, h);
    }
    if m.t == "pen" || m.t == "hl" {
        let xs = m.p.iter().map(|p| p[0] * w);
        let ys = m.p.iter().map(|p| p[1] * h);
        let pad = m.w * s * if m.t == "hl" { 3.0 } else { 1.0 } / 2.0;
        let (x0, x1) = xs.fold((f64::MAX, f64::MIN), |(a, b), v| (a.min(v), b.max(v)));
        let (y0, y1) = ys.fold((f64::MAX, f64::MIN), |(a, b), v| (a.min(v), b.max(v)));
        return (x0 - pad, y0 - pad, x1 + pad, y1 + pad);
    }
    let (x, y) = (m.p[0][0] * w, m.p[0][1] * h);
    if m.t == "step" {
        let r = (if m.s > 0.0 { m.s } else { STEP_SIZE }) * s / 2.0;
        return (x - r, y - r, x + r, y + r);
    }
    let (tw, th) = text_size(m, w, h);
    (x, y, x + tw, y + th)
}

fn light(c: &str) -> bool {
    matches!(c.to_lowercase().as_str(), "#ffffff" | "#ffcc00")
}

fn draw_item(cr: &cairo::Context, m: &Mark, w: f64, h: f64, source: Option<&cairo::ImageSurface>) {
    let s = w.max(h);
    let lw = m.w * s;
    let (r, g, b) = rgb(&m.c);
    let _ = cr.save();
    cr.new_path(); // nothing joins the last mark's end (a text's pen position)
    cr.set_line_join(cairo::LineJoin::Round);
    cr.set_line_cap(cairo::LineCap::Round);
    match m.t.as_str() {
        "pixelate" => {
            let (x0, y0, x1, y1) = mbox(m, w, h);
            match source.filter(|_| x1 - x0 > 1.0 && y1 - y0 > 1.0) {
                Some(src) => {
                    let block = (PIXEL * s).max(2.0);
                    let sw = (((x1 - x0) / block) as i32).max(1);
                    let sh = (((y1 - y0) / block) as i32).max(1);
                    if let Ok(small) = cairo::ImageSurface::create(cairo::Format::ARgb32, sw, sh) {
                        if let Ok(sc) = cairo::Context::new(&small) {
                            sc.scale(sw as f64 / (x1 - x0), sh as f64 / (y1 - y0));
                            sc.translate(-x0, -y0);
                            let _ = sc.set_source_surface(src, 0.0, 0.0);
                            sc.source().set_filter(cairo::Filter::Good);
                            let _ = sc.paint();
                        }
                        cr.rectangle(x0, y0, x1 - x0, y1 - y0);
                        cr.clip();
                        cr.translate(x0, y0);
                        cr.scale((x1 - x0) / sw as f64, (y1 - y0) / sh as f64);
                        let _ = cr.set_source_surface(&small, 0.0, 0.0);
                        cr.source().set_filter(cairo::Filter::Nearest);
                        let _ = cr.paint();
                    }
                }
                None => {
                    // (no picture to sample: a grey stand-in)
                    cr.set_source_rgba(0.5, 0.5, 0.5, 0.85);
                    cr.rectangle(x0, y0, x1 - x0, y1 - y0);
                    let _ = cr.fill();
                }
            }
        }
        "pen" | "hl" => {
            let pts: Vec<(f64, f64)> = m.p.iter().map(|p| (p[0] * w, p[1] * h)).collect();
            if m.t == "hl" {
                cr.set_source_rgba(r, g, b, 0.38);
                cr.set_line_width(lw * 3.0);
                cr.set_line_cap(cairo::LineCap::Square); // a marker's flat tip
            } else {
                cr.set_source_rgba(r, g, b, 1.0);
                cr.set_line_width(lw);
            }
            smooth(cr, &pts);
            let _ = cr.stroke();
        }
        "text" | "emoji" => {
            let (x, y) = (m.p[0][0] * w, m.p[0][1] * h);
            let size = if m.s > 0.0 { m.s } else if m.t == "text" { TEXT_SIZE } else { EMOJI_SIZE };
            let lay = layout(cr, &m.text, size * s, m.t == "text");
            if m.t == "text" && light(&m.c) {
                // light text: a soft dark edge to read on light pictures
                cr.move_to(x, y);
                pangocairo::functions::layout_path(cr, &lay);
                cr.set_source_rgba(0.0, 0.0, 0.0, 0.35);
                cr.set_line_width((size * s * 0.08).max(1.0));
                let _ = cr.stroke();
            }
            cr.move_to(x, y);
            cr.set_source_rgba(r, g, b, 1.0);
            pangocairo::functions::show_layout(cr, &lay);
        }
        "step" => {
            let (x, y) = (m.p[0][0] * w, m.p[0][1] * h);
            let rad = (if m.s > 0.0 { m.s } else { STEP_SIZE }) * s / 2.0;
            cr.arc(x, y, rad, 0.0, 2.0 * PI);
            cr.set_source_rgba(r, g, b, 1.0);
            let _ = cr.fill_preserve();
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.9);
            cr.set_line_width((rad * 0.12).max(1.0));
            let _ = cr.stroke();
            let lay = layout(cr, &m.n.max(1).to_string(), rad * 1.15, true);
            let (_i, log) = lay.pixel_extents();
            cr.move_to(x - log.width() as f64 / 2.0, y - log.height() as f64 / 2.0);
            if light(&m.c) {
                cr.set_source_rgba(0.1, 0.1, 0.1, 1.0);
            } else {
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0);
            }
            pangocairo::functions::show_layout(cr, &lay);
        }
        t if SHAPES.contains(&t) => {
            let (x0, y0, x1, y1) = mbox(m, w, h);
            let last = *m.p.last().unwrap_or(&m.p[0]);
            let (ax, ay, bx, by) = (m.p[0][0] * w, m.p[0][1] * h, last[0] * w, last[1] * h);
            cr.set_source_rgba(r, g, b, 1.0);
            cr.set_line_width(lw);
            match t {
                "rect" => cr.rectangle(x0, y0, x1 - x0, y1 - y0),
                "round" => round_rect(cr, x0, y0, x1, y1, (x1 - x0).min(y1 - y0) * 0.18),
                "ellipse" => {
                    let _ = cr.save();
                    cr.translate((x0 + x1) / 2.0, (y0 + y1) / 2.0);
                    cr.scale(((x1 - x0) / 2.0).max(0.5), ((y1 - y0) / 2.0).max(0.5));
                    cr.arc(0.0, 0.0, 1.0, 0.0, 2.0 * PI);
                    let _ = cr.restore();
                }
                "line" | "arrow" => {
                    let ang = (by - ay).atan2(bx - ax);
                    let head = if t == "arrow" { (lw * 3.2).max(8.0) } else { 0.0 };
                    let (ex, ey) = (bx - ang.cos() * head * 0.6, by - ang.sin() * head * 0.6);
                    cr.move_to(ax, ay);
                    cr.line_to(ex, ey);
                    let _ = cr.stroke();
                    if t == "arrow" {
                        cr.move_to(bx, by);
                        for side in [1.0, -1.0] {
                            let a2 = ang + PI - side * 0.45;
                            cr.line_to(bx + a2.cos() * head, by + a2.sin() * head);
                        }
                        cr.close_path();
                        let _ = cr.fill();
                    }
                    let _ = cr.restore();
                    return;
                }
                "star" => {
                    let (cx, cy, rx, ry) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0);
                    for i in 0..10 {
                        let k = if i % 2 == 0 { 1.0 } else { 0.42 };
                        let a = -PI / 2.0 + i as f64 * PI / 5.0;
                        let (px, py) = (cx + a.cos() * rx * k, cy + a.sin() * ry * k);
                        if i == 0 {
                            cr.move_to(px, py);
                        } else {
                            cr.line_to(px, py);
                        }
                    }
                    cr.close_path();
                }
                "bubble" => {
                    let hh = y1 - y0;
                    let body = y0 + hh * 0.78;
                    round_rect(cr, x0, y0, x1, body, (x1 - x0).min(body - y0) * 0.25);
                    cr.new_sub_path();
                    let tx = x0 + (x1 - x0) * 0.22;
                    cr.move_to(tx, body - lw / 2.0);
                    cr.line_to(tx - (x1 - x0) * 0.05, y1);
                    cr.line_to(tx + (x1 - x0) * 0.12, body - lw / 2.0);
                }
                "check" => {
                    cr.move_to(x0, y0 + (y1 - y0) * 0.55);
                    cr.line_to(x0 + (x1 - x0) * 0.38, y1);
                    cr.line_to(x1, y0);
                }
                "cross" => {
                    cr.move_to(x0, y0);
                    cr.line_to(x1, y1);
                    cr.move_to(x1, y0);
                    cr.line_to(x0, y1);
                }
                _ => {
                    // "question"
                    let lay = layout(cr, "?", (y1 - y0) * 1.15, true);
                    let (_i, log) = lay.pixel_extents();
                    cr.move_to((x0 + x1) / 2.0 - log.width() as f64 / 2.0, (y0 + y1) / 2.0 - log.height() as f64 / 2.0);
                    pangocairo::functions::show_layout(cr, &lay);
                    let _ = cr.restore();
                    return;
                }
            }
            let _ = cr.stroke();
        }
        _ => {}
    }
    let _ = cr.restore();
}

/// Every mark, in order, on a context where the picture spans 0..w x 0..h.
/// source: the picture at that size (pixelate samples it).
pub fn draw(cr: &cairo::Context, marks: &[Mark], w: f64, h: f64, source: Option<&cairo::ImageSurface>) {
    for m in marks {
        if m.o >= 0.999 {
            draw_item(cr, m, w, h, source);
        } else if m.o > 0.001 {
            cr.push_group();
            draw_item(cr, m, w, h, source);
            let _ = cr.pop_group_to_source();
            let _ = cr.paint_with_alpha(m.o);
        }
    }
}

/// Straight RGBA pixels as a Cairo surface (premultiplied BGRA in memory).
pub fn rgba_to_surface(data: &[u8], w: u32, h: u32) -> cairo::ImageSurface {
    let mut surf = cairo::ImageSurface::create(cairo::Format::ARgb32, w as i32, h as i32).expect("a picture surface");
    let stride = surf.stride() as usize;
    {
        let mut d = surf.data().expect("the surface's pixels");
        for y in 0..h as usize {
            for x in 0..w as usize {
                let i = (y * w as usize + x) * 4;
                let (r, g, b, a) = (data[i] as u32, data[i + 1] as u32, data[i + 2] as u32, data[i + 3] as u32);
                let pm = |c: u32| ((c * a + 127) / 255) as u8;
                let o = y * stride + x * 4;
                let px = u32::from(pm(b)) | (u32::from(pm(g)) << 8) | (u32::from(pm(r)) << 16) | (a << 24);
                d[o..o + 4].copy_from_slice(&px.to_ne_bytes());
            }
        }
    }
    surf
}

/// A Cairo surface back to straight RGBA pixels.
pub fn surface_to_rgba(surf: &mut cairo::ImageSurface) -> Vec<u8> {
    surf.flush();
    let (w, h, stride) = (surf.width() as usize, surf.height() as usize, surf.stride() as usize);
    let d = surf.data().expect("the surface's pixels");
    let mut out = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        for x in 0..w {
            let o = y * stride + x * 4;
            let px = u32::from_ne_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]]);
            let a = px >> 24;
            let un = |c: u32| if a == 0 { 0 } else { ((c * 255 + a / 2) / a).min(255) as u8 };
            out.extend_from_slice(&[un((px >> 16) & 0xff), un((px >> 8) & 0xff), un(px & 0xff), a as u8]);
        }
    }
    out
}

/// The marks drawn into full-size straight RGBA pixels (in place). Pixelate
/// samples the picture as it was (as on screen), not the marks drawn into it.
pub fn apply(data: &mut Vec<u8>, w: u32, h: u32, marks: &[Mark]) {
    if marks.is_empty() {
        return;
    }
    let mut surf = rgba_to_surface(data, w, h);
    let clean = marks.iter().any(|m| m.t == "pixelate").then(|| rgba_to_surface(data, w, h));
    {
        let cr = cairo::Context::new(&surf).expect("a context");
        draw(&cr, marks, w as f64, h as f64, Some(clean.as_ref().unwrap_or(&surf)));
    }
    *data = surface_to_rgba(&mut surf);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marks_draw_into_the_picture() {
        let (w, h) = (40u32, 30u32);
        let mut px = vec![255u8; (w * h * 4) as usize]; // white
        let mut rect = Mark::new("rect", "#ff0000", vec![[0.25, 0.25], [0.75, 0.75]]);
        rect.w = 0.05;
        apply(&mut px, w, h, &[rect]);
        let at = |x: u32, y: u32| px[((y * w + x) * 4) as usize..((y * w + x) * 4 + 4) as usize].to_vec();
        assert_eq!(at(10, 15)[..3], [255, 0, 0]); // on the rectangle's left edge
        assert_eq!(at(20, 15), vec![255, 255, 255, 255]); // inside: untouched
    }

    #[test]
    fn bounds_of_boxed_marks_and_strokes() {
        let r = Mark::new("rect", "#000", vec![[0.8, 0.6], [0.2, 0.1]]);
        assert_eq!(bounds(&r, 100.0, 50.0), (20.0, 5.0, 80.0, 30.0));
        let mut pen = Mark::new("pen", "#000", vec![[0.1, 0.1], [0.5, 0.2]]);
        pen.w = 0.01;
        assert_eq!(bounds(&pen, 100.0, 100.0), (9.5, 9.5, 50.5, 20.5));
    }

    #[test]
    fn premultiplied_round_trip() {
        let px = vec![200, 100, 50, 128, 0, 0, 0, 0, 10, 20, 30, 255];
        let mut s = rgba_to_surface(&px, 3, 1);
        let back = surface_to_rgba(&mut s);
        for (a, b) in px.iter().zip(&back) {
            assert!((*a as i32 - *b as i32).abs() <= 1, "{px:?} {back:?}");
        }
    }
}
