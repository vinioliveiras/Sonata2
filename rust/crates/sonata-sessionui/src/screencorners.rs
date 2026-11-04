//! Rounded screen corners (macOS; shell/screencorners.py): the four
//! corners of every display are masked in black, as if the glass of the
//! screen had round corners. Settings > Displays > "Rounded screen corners"
//! (appearance.json "screen_corners", on by default).
//!
//! The desktop: four tiny layer-shell surfaces per display (OVERLAY layer,
//! RADIUS px each, no input -- clicks go through), run by the menu bar
//! process; small on purpose: a full-screen overlay would be blended over
//! every frame. The lock screen and the login screen draw the same corners
//! in their own windows ([`corners_overlay`]): ext-session-lock hides every
//! other surface.
//!
//! Drawn with cairo: GSK paths (what the Python code fills) are GTK 4.14.

use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::json;
use std::cell::{Cell, RefCell};
use std::f64::consts::PI;
use std::rc::Rc;

/// px; macOS: about the menu bar's height / 2.4
pub const RADIUS: i32 = 10;
pub const CORNERS: [&str; 4] = ["tl", "tr", "bl", "br"];
/// (no "sonata2": Wayfire's blur rule must skip it)
pub const NAMESPACE: &str = "sonata-screen-corners";
/// the wedge reaches past the screen's edge: no half-lit outer pixel
pub const BLEED: f64 = 1.0;

pub fn enabled() -> bool {
    sonata_config::load("appearance", &json!({"screen_corners": true})).get("screen_corners").is_none_or(crate::truthy)
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

/// All four corners over a full-screen window (lock / login screens):
/// an overlay that takes no input.
pub fn corners_overlay(radius: i32) -> gtk::DrawingArea {
    let area = gtk::DrawingArea::builder().can_target(false).hexpand(true).vexpand(true).build();
    area.set_draw_func(move |_, cr, w, h| paint_corners(cr, w as f64, h as f64, radius as f64));
    area
}

/// One corner of one display: a layer surface that takes no input.
pub struct CornerWindow {
    pub win: gtk::Window,
    pub connector: String,
}

impl CornerWindow {
    pub fn new(app: &gtk::Application, monitor: &gtk::gdk::Monitor, corner: &'static str, radius: i32) -> Self {
        let win = gtk::Window::builder().application(app).decorated(false).css_classes(["sonata-screen-corner"]).default_width(radius).default_height(radius).build();
        let area = gtk::DrawingArea::builder().can_target(false).width_request(radius).height_request(radius).build();
        area.set_draw_func(move |_, cr, _, _| {
            cr.set_source_rgb(0.0, 0.0, 0.0);
            corner_path(cr, corner, 0.0, 0.0, radius as f64);
            let _ = cr.fill();
        });
        win.set_child(Some(&area));
        if sonata_shell::layer::available() {
            win.init_layer_shell();
            win.set_namespace(Some(NAMESPACE));
            win.set_monitor(Some(monitor));
            win.set_layer(Layer::Overlay);
            win.set_anchor(if corner.starts_with('t') { Edge::Top } else { Edge::Bottom }, true);
            win.set_anchor(if corner.ends_with('l') { Edge::Left } else { Edge::Right }, true);
            win.set_exclusive_zone(-1); // the very corner, past the menu bar's zone
            win.set_keyboard_mode(KeyboardMode::None);
        }
        win.connect_realize(|w| {
            if let Some(s) = w.surface() {
                let w = w.clone();
                s.connect_layout(move |_, _, _| sonata_shell::layer::set_input_region(&w, &[]));
            }
        });
        CornerWindow { win, connector: sonata_shell::layer::connector(monitor) }
    }
}

type Wins = Rc<RefCell<Vec<Rc<CornerWindow>>>>;

/// The desktop's corners, on every display, following the setting live.
pub struct ScreenCorners {
    wins: Wins,
    /// a full-screen app's display: no corners there
    full_on: Rc<RefCell<Option<String>>>,
    on: Cell<bool>,
    surfaces: RefCell<Option<Rc<crate::monitors::Surfaces<Vec<Rc<CornerWindow>>>>>>,
    _mon: RefCell<Vec<gio::FileMonitor>>,
}

impl ScreenCorners {
    /// None on trial: the Python menu bar draws them already.
    pub fn start(app: &gtk::Application) -> Option<Rc<Self>> {
        if sonata_config::side_by_side() {
            return None;
        }
        let me = Rc::new(ScreenCorners { wins: Rc::default(), full_on: Rc::default(), on: Cell::new(enabled()), surfaces: RefCell::default(), _mon: RefCell::default() });
        let (wins, full, wins2) = (me.wins.clone(), me.full_on.clone(), me.wins.clone());
        let app = app.clone();
        let surfaces = crate::monitors::each(
            move |m| {
                if !enabled() {
                    return Vec::new();
                }
                let made: Vec<Rc<CornerWindow>> = CORNERS.iter().map(|c| Rc::new(CornerWindow::new(&app, m, c, RADIUS))).collect();
                for w in &made {
                    if Some(&w.connector) != full.borrow().as_ref() {
                        w.win.present();
                    }
                }
                wins.borrow_mut().extend(made.iter().cloned());
                made
            },
            move |made: Vec<Rc<CornerWindow>>| {
                for w in made {
                    wins2.borrow_mut().retain(|x| !Rc::ptr_eq(x, &w));
                    w.win.destroy();
                }
            },
        );
        *me.surfaces.borrow_mut() = Some(surfaces);
        let w = Rc::downgrade(&me);
        *me._mon.borrow_mut() = sonata_config::watch("appearance", move || {
            if let Some(me) = w.upgrade() {
                me.apply();
            }
        });
        Some(me)
    }

    /// A full-screen app (a game) on `output` (a connector; None: none): no
    /// corners over it. Vini: frame drops and a laggy mouse in a game -- a
    /// surface over a full-screen window keeps Wayfire composing every
    /// frame instead of handing the game's own picture to the display.
    pub fn fullscreen_on(&self, output: Option<&str>) {
        *self.full_on.borrow_mut() = output.map(String::from);
        for w in self.wins.borrow().iter() {
            w.win.set_visible(Some(w.connector.as_str()) != output);
        }
    }

    /// The setting changed: the corners made again (or none).
    pub fn apply(&self) {
        let on = enabled();
        if on != self.on.replace(on) {
            if let Some(s) = self.surfaces.borrow().as_ref() {
                s.rebuild();
            }
        }
    }
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

    #[test]
    fn on_by_default() {
        let _e = crate::testenv::env();
        sonata_config::save("appearance", json!({"theme": "mac"}).as_object().unwrap()).unwrap();
        assert!(enabled());
        sonata_config::save("appearance", json!({"screen_corners": false}).as_object().unwrap()).unwrap();
        assert!(!enabled());
        assert!(!NAMESPACE.contains("sonata2")); // Wayfire's blur rule must not blur behind them
    }
}
