//! The puff of smoke where an item dragged out of the Dock disappears
//! (shell/poof.py, macOS): a small cloud that swells and fades in ~0.35 s,
//! at the pointer.
//!
//! The pointer's place comes from Wayfire (window-rules/get_cursor_position,
//! layout coordinates) and the display under it; the cloud is a tiny
//! layer-shell surface there (overlay, no input). Without Wayfire it shows
//! where the Dock last saw the icon.

use gtk::prelude::*;
use gtk::{gdk, glib};
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::json;
use std::cell::Cell;
use std::rc::Rc;

/// the cloud's box (px)
pub const SIZE: i32 = 96;
pub const POOF_MS: f64 = 360.0;
/// the puffs: (offset x, offset y, radius) as fractions of SIZE, from the centre
pub const PUFFS: [(f64, f64, f64); 8] = [(0.0, 0.0, 0.20), (-0.17, 0.05, 0.14), (0.17, 0.04, 0.15), (-0.08, -0.15, 0.13), (0.10, -0.14, 0.12), (0.0, 0.17, 0.13), (-0.2, -0.08, 0.09), (0.21, -0.06, 0.10)];

/// (x, y) of the pointer in Wayfire's layout, or None.
pub fn cursor() -> Option<(f64, f64)> {
    for m in ["window-rules/get_cursor_position", "window-rules/get-cursor-position"] {
        if let Some(pos) = sonata_backend::wfipc::call(m, json!({})).and_then(|r| r.get("pos").cloned()) {
            if let (Some(x), Some(y)) = (pos.get("x").and_then(|v| v.as_f64()), pos.get("y").and_then(|v| v.as_f64())) {
                return Some((x, y));
            }
        }
    }
    None
}

/// The puffs at t (0..1): [(cx, cy, radius, alpha)] in SIZE units. They
/// swell outwards, then fade (macOS: five frames of a little cloud).
pub fn frame(t: f64) -> Vec<(f64, f64, f64, f64)> {
    let grow = 1.0 - (1.0 - t).powi(3); // ease-out
    let alpha = if t < 0.35 { 1.0 } else { (1.0 - (t - 0.35) / 0.65).max(0.0) };
    PUFFS
        .iter()
        .map(|(dx, dy, r)| {
            let spread = 0.55 + 0.75 * grow;
            (0.5 + dx * spread, 0.5 + dy * spread, r * (0.55 + 0.6 * grow), alpha)
        })
        .collect()
}

fn draw(cr: &gtk::cairo::Context, t: f64) {
    let puffs: Vec<_> = frame(t).into_iter().filter(|p| p.3 > 0.0).collect();
    let Some(a) = puffs.first().map(|p| p.3) else { return };
    let s = SIZE as f64;
    // all the puffs as one shape: no darker overlaps
    let cloud = |dy: f64, grow: f64| {
        cr.new_path();
        for (cx, cy, r, _) in &puffs {
            cr.new_sub_path();
            cr.arc(cx * s, cy * s + dy, r * s + grow, 0.0, std::f64::consts::TAU);
        }
    };
    cr.set_fill_rule(gtk::cairo::FillRule::Winding);
    cloud(1.5, 1.0); // a soft edge below
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.22 * a);
    let _ = cr.fill();
    cloud(0.0, 0.0);
    cr.set_source_rgba(240.0 / 255.0, 240.0 / 255.0, 244.0 / 255.0, 0.95 * a);
    let _ = cr.fill();
}

/// The cloud at (x, y) of `monitor` (display coordinates).
pub fn show(app: &gtk::Application, monitor: Option<&gdk::Monitor>, x: f64, y: f64) -> gtk::Window {
    // a see-through window: only the cloud shows (the theme painted it as a square)
    let win = gtk::Window::builder().application(app).decorated(false).css_classes(["sonata-poof"]).default_width(SIZE).default_height(SIZE).build();
    let t = Rc::new(Cell::new(0.0));
    let area = gtk::DrawingArea::builder().content_width(SIZE).content_height(SIZE).can_target(false).build();
    let t2 = t.clone();
    area.set_draw_func(move |_, cr, _, _| draw(cr, t2.get()));
    win.set_child(Some(&area));
    if sonata_shell::layer::available() {
        win.init_layer_shell();
        win.set_namespace(Some("sonata-poof")); // (no "sonata2": not blurred)
        if let Some(m) = monitor {
            win.set_monitor(Some(m));
        }
        win.set_layer(Layer::Overlay);
        win.set_anchor(Edge::Top, true);
        win.set_anchor(Edge::Left, true);
        win.set_margin(Edge::Left, (x - SIZE as f64 / 2.0).max(0.0) as i32);
        win.set_margin(Edge::Top, (y - SIZE as f64 / 2.0).max(0.0) as i32);
        win.set_exclusive_zone(-1);
        win.set_keyboard_mode(KeyboardMode::None);
    }
    win.connect_realize(|w| {
        let Some(surface) = w.surface() else { return };
        let w = w.clone();
        surface.connect_layout(move |_, _, _| sonata_shell::layer::set_input_region(&w, &[]));
    });
    let t0: Cell<Option<i64>> = Cell::new(None);
    win.add_tick_callback(move |w, clock| {
        let now = clock.frame_time();
        let start = *t0.get().get_or_insert(now);
        t0.set(Some(start));
        let v = ((now - start) as f64 / 1000.0 / POOF_MS).min(1.0);
        t.set(v);
        area.queue_draw();
        if v >= 1.0 {
            let w = w.clone();
            glib::idle_add_local_once(move || w.destroy());
            return glib::ControlFlow::Break;
        }
        glib::ControlFlow::Continue
    });
    win.present();
    win
}

/// Show the puff where the pointer is (fallback: (monitor, x, y)). False:
/// nowhere to show it.
pub fn at_pointer(app: &gtk::Application, fallback: Option<(Option<gdk::Monitor>, f64, f64)>) -> bool {
    let mut spot = fallback.clone();
    if let Some((px, py)) = cursor() {
        for m in sonata_shell::layer::monitors() {
            let g = m.geometry();
            let (gx, gy, gw, gh) = (g.x() as f64, g.y() as f64, g.width() as f64, g.height() as f64);
            if gx <= px && px < gx + gw && gy <= py && py < gy + gh {
                spot = Some((Some(m), px - gx, py - gy));
                break;
            }
        }
    }
    match spot {
        Some((m, x, y)) if m.is_some() || fallback.is_some() => {
            show(app, m.as_ref(), x, y);
            true
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// test_poof_swells_then_fades.
    #[test]
    fn test_poof_swells_then_fades() {
        let start = frame(0.0);
        let mid = frame(0.3);
        let end = frame(1.0);
        assert_eq!(start.len(), PUFFS.len());
        assert!(mid[0].2 > start[0].2); // it swells
        assert_eq!(mid[0].3, 1.0); // still solid
        assert!(end[0].3 <= 0.0); // gone by the end
        assert!(frame(0.7)[0].3 < 1.0 && frame(0.7)[0].3 > 0.0);
    }
}
