//! Rounded screen corners on the lock and login screens
//! (shell/screencorners.py's CornersOverlay): the four corners of every
//! display masked in black, as if the glass of the screen had round
//! corners. ext-session-lock hides every other surface -- the desktop's own
//! corner surfaces too -- so these screens draw the same corners in their
//! own windows. Settings > Displays > "Rounded screen corners"
//! (appearance.json "screen_corners", on by default).
//!
//! Drawn with cairo: GSK paths (what the Python code fills) are GTK 4.14.
//! The desktop's corner surfaces (sonata-sessionui) draw the same wedge.

use gtk::prelude::*;
use serde_json::{json, Value};
use std::f64::consts::PI;

/// px; macOS: about the menu bar's height / 2.4
pub const RADIUS: i32 = 10;
pub const CORNERS: [&str; 4] = ["tl", "tr", "bl", "br"];
/// the wedge reaches past the screen's edge: no half-lit outer pixel
pub const BLEED: f64 = 1.0;

/// The setting, as Python reads it (a value that isn't a clear "off" is on).
pub fn enabled() -> bool {
    match sonata_config::load("appearance", &json!({"screen_corners": true})).get("screen_corners") {
        None => true,
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) => false,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// The black wedge outside a quarter circle in an r x r square at (x, y)
/// (and BLEED px past the screen's two edges), as a cairo path.
pub fn corner_path(cr: &gtk::cairo::Context, corner: &str, x: f64, y: f64, r: f64) {
    let (left, top) = (corner == "tl" || corner == "bl", corner == "tl" || corner == "tr");
    let (cx, cy) = (if left { x } else { x + r }, if top { y } else { y + r }); // the screen's corner
    let (ox, oy) = (if left { x + r } else { x }, if top { y + r } else { y }); // the arc's centre
    let (ex, ey) = (cx + if left { -BLEED } else { BLEED }, cy + if top { -BLEED } else { BLEED }); // outside the screen
    cr.move_to(ex, ey);
    cr.line_to(ox, ey);
    cr.line_to(ox, cy);
    // quarter circle from (ox, cy) to (cx, oy), bulging towards the corner
    let a1 = if top { -PI / 2.0 } else { PI / 2.0 };
    let a2 = if left { PI } else { 0.0 };
    if left == top {
        cr.arc_negative(ox, oy, r, a1, if left { -PI } else { a2 });
    } else {
        cr.arc(ox, oy, r, a1, a2);
    }
    cr.line_to(ex, oy);
    cr.close_path();
}

/// All four corners over a w x h area, in black.
pub fn paint_corners(cr: &gtk::cairo::Context, w: f64, h: f64, r: f64) {
    cr.set_source_rgb(0.0, 0.0, 0.0);
    for c in CORNERS {
        let x = if c == "tl" || c == "bl" { 0.0 } else { w - r };
        let y = if c == "tl" || c == "tr" { 0.0 } else { h - r };
        corner_path(cr, c, x, y, r);
        let _ = cr.fill();
    }
}

/// All four corners over a full-screen window (lock / login screens): an
/// overlay that takes no input.
pub fn corners_overlay(radius: i32) -> gtk::DrawingArea {
    let area = gtk::DrawingArea::builder().can_target(false).hexpand(true).vexpand(true).build();
    area.set_draw_func(move |_, cr, w, h| paint_corners(cr, w as f64, h as f64, radius as f64));
    area
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pixel(s: &mut gtk::cairo::ImageSurface, x: i32, y: i32) -> (u8, u8, u8) {
        let stride = s.stride() as usize;
        let d = s.data().unwrap();
        let i = y as usize * stride + x as usize * 4;
        (d[i + 2], d[i + 1], d[i]) // ARGB32, little-endian: B G R A
    }

    #[test]
    fn corners_black_outside_the_arc() {
        let (w, h) = (200, 120);
        let mut s = gtk::cairo::ImageSurface::create(gtk::cairo::Format::ARgb32, w, h).unwrap();
        {
            let cr = gtk::cairo::Context::new(&s).unwrap();
            cr.set_source_rgb(1.0, 1.0, 1.0);
            cr.paint().unwrap();
            paint_corners(&cr, w as f64, h as f64, 20.0);
        }
        s.flush();
        for (x, y) in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (2, 3), (w - 3, h - 2)] {
            let p = pixel(&mut s, x, y);
            assert!(p.0 as u32 + p.1 as u32 + p.2 as u32 <= 30, "({x}, {y}): {p:?}"); // the corners: black
        }
        for (x, y) in [(25, 25), (w - 25, 25), (w / 2, 0), (0, h / 2), (14, 14), (w - 15, h - 15)] {
            assert_eq!(pixel(&mut s, x, y), (255, 255, 255), "({x}, {y})"); // inside the arc: untouched
        }
    }
}
