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
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// (no "sonata2": Wayfire's blur rule must skip it)
pub const NAMESPACE: &str = "sonata-screen-corners";

// The setting and the drawing are sonata-shell's: the lock and login
// screens draw the very same corners, and they don't link this crate.
pub use sonata_shell::screencorners::{corner_path, corners_overlay, enabled, paint_corners, BLEED, CORNERS, RADIUS};

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
    use serde_json::json;

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
