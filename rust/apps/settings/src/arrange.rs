//! Settings > Displays > Arrange (macOS): the displays drawn where they
//! sit; drag one to move it (it snaps edge to edge with the others, gliding
//! into place), drag the white menu bar to another display to make that
//! one the main display (Dock, desktop icons, notifications).
//!
//! ```ignore
//! snap(&rects, name, x, y) -> (x, y)   // pure: where a dragged display lands
//! normalize(&rects) -> rects           // the layout's top-left at 0,0
//! Arrangement::new(&displays, main, on_move, on_main, wallpaper)
//! ```

use gtk::prelude::*;
use gtk::{gdk, glib};
use sonata_backend::system::Display;
use sonata_i18n::tr;
use std::cell::RefCell;
use std::rc::Rc;
use std::time::{Duration, Instant};

/// the drawing's height in Settings
pub const HEIGHT: i32 = 220;
pub const PAD: f64 = 24.0;
/// displays side by side share at least this much edge (layout px)
pub const MIN_OVERLAP: i64 = 64;
/// the menu bar's share of a display's height in the drawing
pub const BAR: f64 = 0.07;
const GLIDE_MS: u64 = 220;

/// (x, y, w, h) in the layout's pixels.
pub type Rect = (i64, i64, i64, i64);
/// The displays in drawing order (later ones on top).
pub type Rects = Vec<(String, Rect)>;

fn get(rects: &Rects, name: &str) -> Option<Rect> {
    rects.iter().find(|(n, _)| n == name).map(|(_, r)| *r)
}

/// The desktop picture, small (read in a worker thread), or None.
pub fn wallpaper_thumb(width: i32) -> Option<gtk::gdk_pixbuf::Pixbuf> {
    let uri = sonata_tweaks::prefs_write::wallpaper_still_uri(sonata_ui::is_dark()); // (a video wallpaper: its poster)
    let path = gtk::gio::File::for_uri(&uri).path()?;
    gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(path, width, -1, true).ok()
}

/// "Built-in Display", or the monitor's name without its codes:
/// 'LG Electronics LG ULTRAGEAR 0x00066A07 (HDMI-A-1)' -> 'LG Electronics LG ULTRAGEAR'.
pub fn display_title(d: &Display) -> String {
    if sonata_backend::system::is_builtin(Some(&d.name)) {
        return tr!("Built-in Display");
    }
    let words: Vec<&str> = d.description.split('(').next().unwrap_or("").split_whitespace().filter(|w| !w.to_lowercase().starts_with("0x")).collect();
    if words.is_empty() {
        d.name.clone()
    } else {
        words.join(" ")
    }
}

#[cfg(test)]
fn overlap(a0: i64, a1: i64, b0: i64, b1: i64) -> i64 {
    a1.min(b1) - a0.max(b0)
}

#[cfg(test)]
fn collides(r: Rect, rects: &Rects, skip: &str) -> bool {
    let (x, y, w, h) = r;
    rects.iter().any(|(n, o)| n != skip && overlap(x, x + w, o.0, o.0 + o.2) > 0 && overlap(y, y + h, o.1, o.1 + o.3) > 0)
}

/// Where display `name` lands when dropped at (x, y): touching another
/// display's edge, sharing at least MIN_OVERLAP of it, never overlapping
/// any -- the closest such place.
pub fn snap(rects: &Rects, name: &str, x: f64, y: f64) -> (i64, i64) {
    let Some((x0, y0, w, h)) = get(rects, name) else { return (0, 0) };
    let others: Vec<Rect> = rects.iter().filter(|(n, _)| n != name).map(|(_, r)| *r).collect();
    if others.is_empty() {
        return (0, 0);
    }
    let mut best: Option<((i64, i64), f64)> = None;
    for (ox, oy, ow, oh) in others {
        let need_v = MIN_OVERLAP.min(h).min(oh) as f64;
        let need_h = MIN_OVERLAP.min(w).min(ow) as f64;
        let (oxf, oyf, owf, ohf, wf, hf) = (ox as f64, oy as f64, ow as f64, oh as f64, w as f64, h as f64);
        let cy = (oyf - hf + need_v).max(y.min(oyf + ohf - need_v)); // beside: slide along the edge
        let cx = (oxf - wf + need_h).max(x.min(oxf + owf - need_h)); // above / below
        for (px, py) in [(oxf + owf, cy), (oxf - wf, cy), (cx, oyf + ohf), (cx, oyf - hf)] {
            let r = (px.round_ties_even() as i64, py.round_ties_even() as i64, w, h);
            if collides_f(px, py, w, h, rects, name) {
                continue;
            }
            let d = (px - x).powi(2) + (py - y).powi(2);
            if best.is_none_or(|(_, bd)| d < bd) {
                best = Some(((r.0, r.1), d));
            }
        }
    }
    best.map(|b| b.0).unwrap_or((x0, y0))
}

/// collides() on a place that may not be on whole pixels yet (as Python
/// checks the unrounded one).
fn collides_f(x: f64, y: f64, w: i64, h: i64, rects: &Rects, skip: &str) -> bool {
    let ov = |a0: f64, a1: f64, b0: f64, b1: f64| a1.min(b1) - a0.max(b0);
    rects.iter().any(|(n, o)| {
        n != skip && ov(x, x + w as f64, o.0 as f64, (o.0 + o.2) as f64) > 0.0 && ov(y, y + h as f64, o.1 as f64, (o.1 + o.3) as f64) > 0.0
    })
}

/// The same layout moved so its top-left corner is 0,0 (Wayfire's origin).
pub fn normalize(rects: &Rects) -> Rects {
    let mx = rects.iter().map(|(_, r)| r.0).min().unwrap_or(0);
    let my = rects.iter().map(|(_, r)| r.1).min().unwrap_or(0);
    rects.iter().map(|(n, r)| (n.clone(), (r.0 - mx, r.1 - my, r.2, r.3))).collect()
}

#[derive(Clone, Debug, PartialEq)]
pub enum What {
    Display,
    Bar,
}

#[derive(Clone, Debug)]
pub struct Drag {
    pub what: What,
    pub name: String,
    pub x: f64,
    pub y: f64,
    x0: f64,
    y0: f64,
    pub fit: (f64, f64, f64),
    px: f64,
    py: f64,
    bar_at: Option<(f64, f64)>,
}

/// The drawing's state, apart from the widget (the tests drive it).
pub struct Model {
    pub rects: Rects,
    pub titles: Vec<(String, String)>,
    pub main: String,
    pub drag: Option<Drag>,
    /// name -> drawn (x, y): gliding positions
    pub shown: Vec<(String, (f64, f64))>,
    pub size: (f64, f64),
}

impl Model {
    pub fn new(displays: &[Display], main: &str) -> Self {
        let rects = normalize(&displays.iter().filter(|d| d.enabled && d.size().0 != 0).map(|d| (d.name.clone(), (d.x, d.y, d.size().0, d.size().1))).collect());
        let main = if get(&rects, main).is_some() { main.to_string() } else { rects.first().map(|r| r.0.clone()).unwrap_or_default() };
        Model { titles: displays.iter().map(|d| (d.name.clone(), display_title(d))).collect(), rects, main, drag: None, shown: Vec::new(), size: (1.0, 1.0) }
    }

    /// (scale, ox, oy): layout px -> widget px, the layout centred.
    pub fn fit(&self) -> (f64, f64, f64) {
        if self.rects.is_empty() {
            return (1.0, 0.0, 0.0);
        }
        let it = || self.rects.iter().map(|(_, r)| r);
        let mx = it().map(|r| r.0).min().unwrap_or(0) as f64;
        let my = it().map(|r| r.1).min().unwrap_or(0) as f64;
        let bw = it().map(|r| r.0 + r.2).max().unwrap_or(0) as f64 - mx;
        let bh = it().map(|r| r.1 + r.3).max().unwrap_or(0) as f64 - my;
        let (ww, hh) = (self.size.0.max(1.0), self.size.1.max(1.0));
        let s = ((ww - 2.0 * PAD) / bw.max(1.0)).min((hh - 2.0 * PAD) / bh.max(1.0));
        (s, (ww - bw * s) / 2.0 - mx * s, (hh - bh * s) / 2.0 - my * s)
    }

    fn drawn(&self, name: &str) -> (f64, f64, f64, f64) {
        let (x, y, w, h) = get(&self.rects, name).unwrap_or_default();
        let (mut x, mut y) = self.shown.iter().find(|(n, _)| n == name).map(|(_, p)| *p).unwrap_or((x as f64, y as f64));
        if let Some(d) = self.drag.as_ref().filter(|d| d.what == What::Display && d.name == name) {
            (x, y) = (d.x, d.y);
        }
        (x, y, w as f64, h as f64)
    }

    pub fn widget_rect(&self, name: &str) -> (f64, f64, f64, f64) {
        let (s, ox, oy) = self.fit();
        let (x, y, w, h) = self.drawn(name);
        (ox + x * s, oy + y * s, w * s, h * s)
    }

    /// (Bar | Display, name) under the pointer, or None.
    pub fn hit(&self, px: f64, py: f64) -> Option<(What, String)> {
        for (name, _) in self.rects.iter().rev() {
            let (x, y, w, h) = self.widget_rect(name);
            if x <= px && px <= x + w && y <= py && py <= y + h {
                if *name == self.main && py <= y + (h * BAR).max(6.0) + 2.0 {
                    return Some((What::Bar, name.clone()));
                }
                return Some((What::Display, name.clone()));
            }
        }
        None
    }

    pub fn begin(&mut self, px: f64, py: f64) {
        self.drag = self.hit(px, py).map(|(what, name)| {
            let (x, y, _, _) = get(&self.rects, &name).unwrap_or_default();
            Drag { what, name, x: x as f64, y: y as f64, x0: x as f64, y0: y as f64, fit: self.fit(), px, py, bar_at: None }
        });
    }

    pub fn update(&mut self, dx: f64, dy: f64) {
        let Some(d) = self.drag.as_mut() else { return };
        if d.what == What::Display {
            (d.x, d.y) = (d.x0 + dx / d.fit.0, d.y0 + dy / d.fit.0);
        } else {
            d.bar_at = Some((d.px + dx, d.py + dy));
        }
    }

    /// The drag is over. Returns (moved: the new positions, new main
    /// display, the glide's starting places).
    pub fn end(&mut self) -> (Option<Vec<(String, (i64, i64))>>, Option<String>, Option<Vec<(String, (f64, f64))>>) {
        let Some(d) = self.drag.take() else { return (None, None, None) };
        if d.what == What::Bar {
            let (bx, by) = d.bar_at.unwrap_or((d.px, d.py));
            if let Some((_, target)) = self.hit(bx, by).filter(|(_, n)| *n != self.main) {
                self.main = target.clone();
                return (None, Some(target), None);
            }
            return (None, None, None);
        }
        let name = d.name.clone();
        let (tx, ty) = snap(&self.rects, &name, d.x, d.y);
        let old = self.rects.clone();
        for (n, r) in self.rects.iter_mut() {
            if *n == name {
                *r = (tx, ty, r.2, r.3);
            }
        }
        let new = normalize(&self.rects);
        let moved = new != normalize(&old);
        // glide from where it was dropped (the others keep their drawn place:
        // the whole layout may shift when normalized, so they glide too)
        let nr = get(&new, &name).unwrap_or_default();
        let shift = ((nr.0 - tx) as f64, (nr.1 - ty) as f64);
        let start: Vec<(String, (f64, f64))> = old.iter().map(|(n, r)| {
            let (sx, sy) = if *n == name { (d.x, d.y) } else { (r.0 as f64, r.1 as f64) };
            (n.clone(), (sx + shift.0, sy + shift.1))
        }).collect();
        self.rects = new;
        self.shown = start.clone(); // drawn where it was dropped until the glide runs
        let positions = moved.then(|| self.rects.iter().map(|(n, r)| (n.clone(), (r.0, r.1))).collect());
        (positions, None, Some(start))
    }

    /// The glide at t (0..1): every display between its start and its place.
    pub fn glide_to(&mut self, start: &[(String, (f64, f64))], t: f64) {
        self.shown = if t >= 1.0 {
            Vec::new()
        } else {
            start.iter().filter_map(|(n, (sx, sy))| get(&self.rects, n).map(|r| (n.clone(), (sx + (r.0 as f64 - sx) * t, sy + (r.1 as f64 - sy) * t)))).collect()
        };
    }
}

/// The drawing. on_move(positions); on_main(name).
pub struct Arrangement {
    pub widget: gtk::DrawingArea,
    pub model: Rc<RefCell<Model>>,
}

impl Arrangement {
    pub fn new(displays: &[Display], main: &str, on_move: impl Fn(Vec<(String, (i64, i64))>) + 'static, on_main: impl Fn(&str) + 'static, wallpaper: Option<gtk::gdk_pixbuf::Pixbuf>) -> Self {
        let area = gtk::DrawingArea::builder().height_request(HEIGHT).hexpand(true).css_classes(["display-arrange"]).build();
        area.set_cursor_from_name(Some("default"));
        let model = Rc::new(RefCell::new(Model::new(displays, main)));
        let m = model.clone();
        area.set_draw_func(move |a, cr, w, h| {
            m.borrow_mut().size = (w as f64, h as f64);
            draw(a, cr, &m.borrow(), wallpaper.as_ref());
        });
        let g = gtk::GestureDrag::new();
        let (m, a) = (model.clone(), area.downgrade());
        g.connect_drag_begin(move |_, x, y| {
            m.borrow_mut().begin(x, y);
            if let Some(a) = a.upgrade() {
                a.queue_draw();
            }
        });
        let (m, a) = (model.clone(), area.downgrade());
        g.connect_drag_update(move |_, dx, dy| {
            m.borrow_mut().update(dx, dy);
            if let Some(a) = a.upgrade() {
                a.queue_draw();
            }
        });
        let (m, a) = (model.clone(), area.downgrade());
        g.connect_drag_end(move |_, _, _| {
            let (moved, main, start) = m.borrow_mut().end();
            if let Some(main) = main {
                on_main(&main);
            }
            if let (Some(start), Some(area)) = (start, a.upgrade()) {
                glide(&area, &m, start);
            }
            if let Some(moved) = moved {
                on_move(moved);
            }
            if let Some(a) = a.upgrade() {
                a.queue_draw();
            }
        });
        area.add_controller(g);
        let a = area.downgrade();
        sonata_ui::on_change(move || {
            if let Some(a) = a.upgrade() {
                a.queue_draw();
            }
        });
        Arrangement { widget: area, model }
    }
}

fn glide(area: &gtk::DrawingArea, model: &Rc<RefCell<Model>>, start: Vec<(String, (f64, f64))>) {
    let t0 = Instant::now();
    let total = sonata_ui::tokens::ms(GLIDE_MS as f64).max(1) as f64;
    let (m, a) = (model.clone(), area.downgrade());
    glib::timeout_add_local(Duration::from_millis(16), move || {
        let p = (t0.elapsed().as_millis() as f64 / total).min(1.0);
        let t = 1.0 - (1.0 - p).powi(3); // ease out
        m.borrow_mut().glide_to(&start, t);
        let Some(a) = a.upgrade() else { return glib::ControlFlow::Break };
        a.queue_draw();
        if p >= 1.0 {
            glib::ControlFlow::Break
        } else {
            glib::ControlFlow::Continue
        }
    });
}

fn rounded(cr: &gtk::cairo::Context, x: f64, y: f64, w: f64, h: f64, r: f64) {
    use std::f64::consts::PI;
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -PI / 2.0, 0.0);
    cr.arc(x + w - r, y + h - r, r, 0.0, PI / 2.0);
    cr.arc(x + r, y + h - r, r, PI / 2.0, PI);
    cr.arc(x + r, y + r, r, PI, 1.5 * PI);
    cr.close_path();
}

fn rgba(cr: &gtk::cairo::Context, c: &gdk::RGBA) {
    cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
}

fn draw(area: &gtk::DrawingArea, cr: &gtk::cairo::Context, m: &Model, wallpaper: Option<&gtk::gdk_pixbuf::Pixbuf>) {
    use gtk::gdk::prelude::GdkCairoContextExt;
    let accent = sonata_ui::rgba("accent");
    for (name, _) in &m.rects {
        let (x, y, w, h) = m.widget_rect(name);
        let _ = cr.save();
        rounded(cr, x, y, w, h, 4.0);
        cr.clip();
        if let Some(pb) = wallpaper {
            // cover-fit, like the desktop
            let (tw, th) = (pb.width().max(1) as f64, pb.height().max(1) as f64);
            let k = (w / tw).max(h / th);
            let _ = cr.save();
            cr.translate(x + (w - tw * k) / 2.0, y + (h - th * k) / 2.0);
            cr.scale(k, k);
            cr.set_source_pixbuf(pb, 0.0, 0.0);
            let _ = cr.paint();
            let _ = cr.restore();
        } else {
            rgba(cr, &sonata_ui::rgba("module_bg"));
            let _ = cr.paint();
        }
        if *name == m.main {
            // its menu bar
            let bar = m.drag.as_ref().is_some_and(|d| d.what == What::Bar);
            cr.set_source_rgba(1.0, 1.0, 1.0, if bar { 0.45 } else { 0.9 });
            cr.rectangle(x, y, w, (h * BAR).max(6.0));
            let _ = cr.fill();
        }
        let _ = cr.restore();
        let dragging = m.drag.as_ref().is_some_and(|d| d.name == *name && d.what == What::Display);
        rgba(cr, &if dragging { accent } else { sonata_ui::rgba("hairline") });
        let lw = if dragging { 2.0 } else { 1.0 };
        cr.set_line_width(lw);
        rounded(cr, x + lw / 2.0, y + lw / 2.0, w - lw, h - lw, 4.0);
        let _ = cr.stroke();
        let title = m.titles.iter().find(|(n, _)| n == name).map(|(_, t)| t.as_str()).unwrap_or(name);
        label(area, cr, title, x, y, w, h);
    }
    if let Some(d) = m.drag.as_ref().filter(|d| d.what == What::Bar) {
        if let Some((bx, by)) = d.bar_at {
            // the bar follows the pointer
            let (_, _, w, h) = m.widget_rect(&d.name);
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95);
            cr.rectangle(bx - w / 2.0, by - 3.0, w, (h * BAR).max(6.0));
            let _ = cr.fill();
        }
    }
}

fn label(area: &gtk::DrawingArea, cr: &gtk::cairo::Context, text: &str, x: f64, y: f64, w: f64, h: f64) {
    let layout = area.create_pango_layout(Some(text));
    layout.set_width(((w - 12.0).max(10.0) * gtk::pango::SCALE as f64) as i32);
    layout.set_ellipsize(gtk::pango::EllipsizeMode::End);
    layout.set_alignment(gtk::pango::Alignment::Center);
    let (_, logical) = layout.pixel_extents();
    let ty = y + (h - logical.height() as f64) / 2.0;
    // a soft dark line under the name: readable on any wallpaper
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.55);
    cr.move_to(x + 6.0, ty + 1.0);
    pangocairo_show(cr, &layout);
    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0);
    cr.move_to(x + 6.0, ty);
    pangocairo_show(cr, &layout);
}

fn pangocairo_show(cr: &gtk::cairo::Context, layout: &gtk::pango::Layout) {
    pangocairo::functions::show_layout(cr, layout);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(list: &[(&str, Rect)]) -> Rects {
        list.iter().map(|(n, r)| (n.to_string(), *r)).collect()
    }

    fn two() -> Rects {
        r(&[("a", (0, 0, 1920, 1080)), ("b", (1920, 0, 1920, 1080))])
    }

    #[test]
    fn test_dropped_on_the_left_lands_edge_to_edge() {
        assert_eq!(snap(&two(), "b", -1700.0, 40.0), (-1920, 40));
    }

    #[test]
    fn test_dropped_overlapping_moves_out_to_the_nearest_edge() {
        assert_eq!(snap(&two(), "b", 1000.0, 900.0), (1000, 1080)); // under it, not over it
    }

    #[test]
    fn test_keeps_a_shared_edge() {
        assert_eq!(snap(&two(), "b", 1990.0, 600.0), (1920, 600)); // slid along the edge
        let rr = r(&[("a", (0, 0, 1920, 1080)), ("b", (2500, 0, 1920, 1080))]);
        assert_eq!(snap(&rr, "b", 1950.0, 1060.0), (1920, 1080 - MIN_OVERLAP)); // touching by MIN_OVERLAP
    }

    #[test]
    fn test_never_overlaps_a_third() {
        let mut rr = two();
        rr.push(("c".into(), (0, 1080, 1920, 1080)));
        let (x, y) = snap(&rr, "b", 10.0, 1000.0);
        for n in ["a", "c"] {
            let (ox, oy, ow, oh) = get(&rr, n).unwrap();
            assert!(!(overlap(x, x + 1920, ox, ox + ow) > 0 && overlap(y, y + 1080, oy, oy + oh) > 0));
        }
        assert!(!collides((x, y, 1920, 1080), &rr, "b"));
    }

    #[test]
    fn test_normalize() {
        assert_eq!(normalize(&r(&[("a", (0, 0, 10, 10)), ("b", (-10, 5, 10, 10))])), r(&[("a", (10, 0, 10, 10)), ("b", (0, 5, 10, 10))]));
    }

    fn displays() -> Vec<Display> {
        let d = |name: &str, desc: &str, cur: &str, x: i64, scale: f64| Display { name: name.into(), description: desc.into(), modes: vec![cur.into()], current: cur.into(), scale, x, y: 0, enabled: true, transform: "normal".into() };
        vec![d("eDP-1", "Built-in", "1920x1080@60.000", 0, 1.25), d("HDMI-A-1", "LG Electronics LG ULTRAGEAR 0x00066A07 (HDMI-A-1)", "1920x1080@60.000", 1536, 1.0)]
    }

    fn model() -> Model {
        let mut m = Model::new(&displays(), "eDP-1");
        m.size = (500.0, HEIGHT as f64);
        m
    }

    fn centre(m: &Model, name: &str) -> (f64, f64) {
        let (x, y, w, h) = m.widget_rect(name);
        (x + w / 2.0, y + h / 2.0)
    }

    #[test]
    fn test_drag_a_display_to_the_other_side() {
        let mut m = model();
        let (cx, cy) = centre(&m, "HDMI-A-1");
        m.begin(cx, cy);
        let s = m.drag.as_ref().unwrap().fit.0;
        m.update(-(1536.0 + 1920.0) * s, 0.0); // to the left of the laptop
        let (moved, _, start) = m.end();
        let pos = moved.expect("moved");
        let at = |n: &str| pos.iter().find(|p| p.0 == n).unwrap().1;
        assert_eq!(at("HDMI-A-1").0, 0); // the layout starts at 0,0 again
        assert_eq!(at("eDP-1").0, 1920); // the laptop now right of it
        assert!(!m.shown.is_empty()); // gliding into place
        m.glide_to(&start.unwrap(), 1.0);
        assert!(m.shown.is_empty());
    }

    #[test]
    fn test_drag_the_menu_bar_to_choose_the_main_display() {
        let mut m = model();
        let (x, y, w, _) = m.widget_rect("eDP-1");
        assert_eq!(m.hit(x + w / 2.0, y + 2.0), Some((What::Bar, "eDP-1".into())));
        m.begin(x + w / 2.0, y + 2.0);
        let (cx, cy) = centre(&m, "HDMI-A-1");
        m.update(cx - (x + w / 2.0), cy - (y + 2.0));
        let (moved, main, _) = m.end();
        assert_eq!(main.as_deref(), Some("HDMI-A-1"));
        assert!(moved.is_none());
        assert_eq!(m.main, "HDMI-A-1");
    }

    #[test]
    fn test_a_click_moves_nothing() {
        let mut m = model();
        let (cx, cy) = centre(&m, "HDMI-A-1");
        m.begin(cx, cy);
        let (moved, main, _) = m.end();
        assert!(moved.is_none() && main.is_none());
    }

    #[test]
    fn titles() {
        let ds = displays();
        assert_eq!(display_title(&ds[1]), "LG Electronics LG ULTRAGEAR");
    }
}
