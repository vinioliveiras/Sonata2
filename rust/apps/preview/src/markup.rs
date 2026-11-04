//! Preview's Markup editor (sonata2/preview/markup.py, its second half):
//! the marks over the canvas, every one still editable (select it to move
//! it, resize it, recolour it or delete it) until Done bakes them into the
//! picture as one edit (⌘Z takes it back); and its toolbar.

use crate::marks::{self, Mark, COLORS, EMOJI, EMOJI_SIZE, SHAPES, STEP_SIZE, TEXT_SIZE, WIDTHS};
use gtk::{cairo, gdk, glib, prelude::*};
use sonata_i18n::{gettext, n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::f64::consts::PI;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// a pen click waits this long: half of a double click leaves no dot
const DOUBLE_CLICK_MS: u64 = 300;

type Rect = (f64, f64, f64, f64);

/// A point (picture fractions) on the picture.
pub fn inside(f: [f64; 2]) -> bool {
    (0.0..=1.0).contains(&f[0]) && (0.0..=1.0).contains(&f[1])
}

pub fn clamp(f: [f64; 2]) -> [f64; 2] {
    [f[0].clamp(0.0, 1.0), f[1].clamp(0.0, 1.0)]
}

/// A Cairo surface of a texture (pixelate samples it on screen).
pub fn texture_surface(tex: &gdk::Texture) -> Option<cairo::ImageSurface> {
    let mut d = gdk::TextureDownloader::new(tex);
    d.set_format(gdk::MemoryFormat::B8g8r8a8Premultiplied);
    let (bytes, stride) = d.download_bytes();
    cairo::ImageSurface::create_for_data(bytes.to_vec(), cairo::Format::ARgb32, tex.width(), tex.height(), stride as i32).ok()
}

#[derive(Clone)]
struct Drag {
    x: f64,
    y: f64,
    /// "move" / "resize" of the selected mark
    mode: Option<&'static str>,
    orig: Option<Mark>,
    moved: bool,
}

/// Over the canvas while Markup is on. rect(): where the picture is
/// (x, y, w, h); texture(): the picture shown (for pixelate).
pub struct MarkupLayer {
    pub area: gtk::DrawingArea,
    rect: Box<dyn Fn() -> Option<Rect>>,
    texture: Box<dyn Fn() -> Option<gdk::Texture>>,
    pub items: RefCell<Vec<Mark>>,
    pub tool: RefCell<String>,
    pub shape: RefCell<String>,
    pub emoji: RefCell<String>,
    pub color: RefCell<String>,
    pub width: Cell<f64>,
    /// the selected mark's index
    pub sel: Cell<Option<usize>>,
    /// the mark being drawn
    cur: RefCell<Option<Mark>>,
    undo_stack: RefCell<Vec<Vec<Mark>>>,
    redo_stack: RefCell<Vec<Vec<Mark>>>,
    src: RefCell<Option<(gdk::Texture, cairo::ImageSurface)>>,
    drag: RefCell<Option<Drag>>,
    dots: RefCell<Vec<(glib::SourceId, u64)>>,
    dot_serial: Cell<u64>,
    text_pop: RefCell<Option<gtk::Popover>>,
    pub on_change: RefCell<Option<Box<dyn Fn()>>>,
    me: Weak<MarkupLayer>,
}

impl MarkupLayer {
    pub fn new(rect: impl Fn() -> Option<Rect> + 'static, texture: impl Fn() -> Option<gdk::Texture> + 'static) -> Rc<Self> {
        sonata_ui::css::register_module("preview/markup.py");
        let area = gtk::DrawingArea::builder().hexpand(true).vexpand(true).can_focus(true).focusable(true).build();
        let layer = Rc::new_cyclic(|me| MarkupLayer {
            area,
            rect: Box::new(rect),
            texture: Box::new(texture),
            items: RefCell::default(),
            tool: RefCell::new("pen".into()),
            shape: RefCell::new("rect".into()),
            emoji: RefCell::new(EMOJI[0].into()),
            color: RefCell::new(COLORS[0].into()),
            width: Cell::new(WIDTHS[1]),
            sel: Cell::new(None),
            cur: RefCell::new(None),
            undo_stack: RefCell::default(),
            redo_stack: RefCell::default(),
            src: RefCell::new(None),
            drag: RefCell::new(None),
            dots: RefCell::default(),
            dot_serial: Cell::new(0),
            text_pop: RefCell::new(None),
            on_change: RefCell::new(None),
            me: me.clone(),
        });
        let w = Rc::downgrade(&layer);
        layer.area.set_draw_func(move |_, cr, _, _| {
            if let Some(l) = w.upgrade() {
                l.draw(cr);
            }
        });
        let drag = gtk::GestureDrag::new();
        let w = Rc::downgrade(&layer);
        drag.connect_drag_begin(move |_, x, y| {
            if let Some(l) = w.upgrade() {
                l.begin(x, y);
            }
        });
        let w = Rc::downgrade(&layer);
        drag.connect_drag_update(move |_, dx, dy| {
            if let Some(l) = w.upgrade() {
                l.update(dx, dy);
            }
        });
        let w = Rc::downgrade(&layer);
        drag.connect_drag_end(move |_, _, _| {
            if let Some(l) = w.upgrade() {
                l.end();
            }
        });
        layer.area.add_controller(drag);
        let dbl = gtk::GestureClick::new();
        let w = Rc::downgrade(&layer);
        dbl.connect_pressed(move |_, n, x, y| {
            if let (2, Some(l)) = (n, w.upgrade()) {
                l.double_click(x, y);
            }
        });
        layer.area.add_controller(dbl);
        layer.cursor();
        layer
    }

    // -- coordinates ------------------------------------------------------------------------------
    fn pic(&self) -> Option<Rect> {
        (self.rect)().filter(|r| r.2 > 0.0 && r.3 > 0.0)
    }

    fn to_frac(&self, x: f64, y: f64) -> [f64; 2] {
        let (px, py, pw, ph) = self.pic().unwrap_or((0.0, 0.0, 1.0, 1.0));
        [(x - px) / pw, (y - py) / ph]
    }

    // -- history ----------------------------------------------------------------------------------
    fn snapshot(&self) {
        self.undo_stack.borrow_mut().push(self.items.borrow().clone());
        self.redo_stack.borrow_mut().clear();
    }

    pub fn undo(&self) -> bool {
        let Some(prev) = self.undo_stack.borrow_mut().pop() else { return false };
        self.redo_stack.borrow_mut().push(self.items.replace(prev));
        self.sel.set(None);
        self.changed();
        true
    }

    pub fn redo(&self) -> bool {
        let Some(next) = self.redo_stack.borrow_mut().pop() else { return false };
        self.undo_stack.borrow_mut().push(self.items.replace(next));
        self.sel.set(None);
        self.changed();
        true
    }

    fn changed(&self) {
        self.area.queue_draw();
        if let Some(f) = self.on_change.borrow().as_ref() {
            f();
        }
    }

    // -- tools ------------------------------------------------------------------------------------
    pub fn set_tool(&self, tool: &str) {
        *self.tool.borrow_mut() = tool.to_string();
        if tool != "select" {
            self.sel.set(None);
        }
        self.cursor();
        self.area.queue_draw();
    }

    fn cursor(&self) {
        let name = match self.tool.borrow().as_str() {
            "select" => "default",
            "text" => "text",
            _ => "crosshair",
        };
        self.area.set_cursor_from_name(Some(name));
    }

    pub fn set_color(&self, c: &str) {
        *self.color.borrow_mut() = c.to_string();
        if let Some(i) = self.sel.get() {
            self.snapshot();
            self.items.borrow_mut()[i].c = c.to_string();
            self.changed();
        }
    }

    pub fn set_width(&self, w: f64) {
        self.width.set(w);
        if let Some(i) = self.sel.get() {
            let stroked = !self.items.borrow()[i].has_size();
            if stroked {
                self.snapshot();
                self.items.borrow_mut()[i].w = w;
                self.changed();
            }
        }
    }

    pub fn delete_selected(&self) {
        if let Some(i) = self.sel.get() {
            self.snapshot();
            self.items.borrow_mut().remove(i);
            self.sel.set(None);
            self.changed();
        }
    }

    /// Every mark goes (the trash button); one Undo brings them back.
    pub fn clear(&self) {
        if !self.items.borrow().is_empty() {
            self.snapshot();
            self.items.borrow_mut().clear();
            self.sel.set(None);
            self.changed();
        }
    }

    pub fn deselect(&self) {
        self.sel.set(None);
        self.area.queue_draw();
    }

    fn next_step(&self) -> u32 {
        1 + self.items.borrow().iter().filter(|m| m.t == "step").map(|m| m.n).max().unwrap_or(0)
    }

    /// The topmost mark under (x, y), or None.
    fn hit(&self, x: f64, y: f64) -> Option<usize> {
        let (px, py, pw, ph) = self.pic()?;
        let items = self.items.borrow();
        (0..items.len()).rev().find(|&i| {
            let (x0, y0, x1, y1) = marks::bounds(&items[i], pw, ph);
            px + x0 - 6.0 <= x && x <= px + x1 + 6.0 && py + y0 - 6.0 <= y && y <= py + y1 + 6.0
        })
    }

    /// The selected mark's resize handle (bottom right), in widget pixels.
    fn handle(&self) -> Option<(f64, f64)> {
        let (px, py, pw, ph) = self.pic()?;
        let i = self.sel.get()?;
        let (_, _, x1, y1) = marks::bounds(self.items.borrow().get(i)?, pw, ph);
        Some((px + x1, py + y1))
    }

    // -- input ------------------------------------------------------------------------------------
    fn begin(&self, x: f64, y: f64) {
        self.area.grab_focus();
        if self.pic().is_none() {
            return;
        }
        let f = self.to_frac(x, y);
        let t = self.tool.borrow().clone();
        *self.drag.borrow_mut() = Some(Drag { x, y, mode: None, orig: None, moved: false });
        if t != "select" && !inside(f) {
            *self.drag.borrow_mut() = None; // marks only on the picture, not around it
            return;
        }
        if t == "select" {
            // the undo step is taken on the first real move: a click that only selects is no edit
            if let Some((hx, hy)) = self.handle() {
                if (x - hx).abs() < 10.0 && (y - hy).abs() < 10.0 {
                    let orig = self.sel.get().map(|i| self.items.borrow()[i].clone());
                    *self.drag.borrow_mut() = Some(Drag { x, y, mode: Some("resize"), orig, moved: false });
                    return;
                }
            }
            self.sel.set(self.hit(x, y));
            if let Some(i) = self.sel.get() {
                let orig = Some(self.items.borrow()[i].clone());
                *self.drag.borrow_mut() = Some(Drag { x, y, mode: Some("move"), orig, moved: false });
            }
            self.area.queue_draw();
            return;
        }
        if t == "text" {
            if self.text_pop.borrow().is_some() {
                return; // its field is open (a double click's second press)
            }
            match self.hit(x, y) {
                Some(i) if self.items.borrow()[i].t == "text" => self.edit_text_at(x, y), // a click on a text edits it
                _ => self.ask_text(x, y, None),
            }
            return;
        }
        if t == "emoji" || t == "step" {
            self.snapshot();
            let mut m = Mark::new(&t, &self.color.borrow(), vec![f]);
            if t == "emoji" {
                m.text = self.emoji.borrow().clone();
                m.s = EMOJI_SIZE;
                if let Some((_, _, pw, ph)) = self.pic() {
                    let (w, h) = marks::text_size(&m, pw, ph);
                    m.p[0] = [f[0] - w / 2.0 / pw, f[1] - h / 2.0 / ph]; // centred where clicked
                }
            } else {
                m.n = self.next_step();
                m.s = STEP_SIZE;
            }
            self.items.borrow_mut().push(m);
            self.changed();
            return;
        }
        let kind = if t == "shape" { self.shape.borrow().clone() } else { t };
        let mut m = Mark::new(&kind, &self.color.borrow(), if marks::is_boxed(&kind) { vec![f, f] } else { vec![f] });
        m.w = self.width.get();
        *self.cur.borrow_mut() = Some(m);
    }

    fn update(&self, dx: f64, dy: f64) {
        let Some(pic) = self.pic() else { return };
        let Some(mut d) = self.drag.borrow().clone() else { return };
        let (x, y) = (d.x + dx, d.y + dy);
        if d.mode.is_some() && !d.moved {
            if dx.abs() + dy.abs() < 1.0 {
                return;
            }
            self.snapshot(); // before the first change
            d.moved = true;
            *self.drag.borrow_mut() = Some(d.clone());
        }
        if let (Some(mode), Some(i), Some(orig)) = (d.mode, self.sel.get(), d.orig.as_ref()) {
            if mode == "move" {
                let (fx, fy) = (dx / pic.2, dy / pic.3);
                self.items.borrow_mut()[i].p = orig.p.iter().map(|p| [p[0] + fx, p[1] + fy]).collect();
            } else {
                self.resize(i, orig, dx, dy, pic);
            }
            self.area.queue_draw();
            return;
        }
        let mut cur = self.cur.borrow_mut();
        let Some(m) = cur.as_mut() else { return };
        let f = clamp(self.to_frac(x, y)); // a stroke stops at the picture's edge
        if m.boxed() {
            let last = m.p.len() - 1;
            m.p[last] = f;
        } else {
            let last = m.p[m.p.len() - 1];
            if ((f[0] - last[0]) * pic.2).abs() + ((f[1] - last[1]) * pic.3).abs() >= 2.0 {
                m.p.push(f);
            }
        }
        drop(cur);
        self.area.queue_draw();
    }

    fn resize(&self, i: usize, orig: &Mark, dx: f64, dy: f64, pic: Rect) {
        let mut items = self.items.borrow_mut();
        let it = &mut items[i];
        if it.boxed() {
            let mut p = orig.p.clone();
            let last = p.len() - 1;
            // the right/bottom corner follows
            let ix = if p[0][0] > p[last][0] { 0 } else { last };
            p[ix][0] += dx / pic.2;
            let iy = if p[0][1] > p[last][1] { 0 } else { last };
            p[iy][1] += dy / pic.3;
            it.p = p;
        } else if orig.has_size() {
            // text, emoji, steps: their size
            let (x0, _, x1, _) = marks::bounds(orig, pic.2, pic.3);
            let k = ((x1 - x0 + dx) / (x1 - x0).max(1.0)).max(0.2);
            it.s = (orig.s * k).max(0.008);
        } else {
            // a stroke: scaled from its top left
            let (x0, y0, x1, y1) = marks::bounds(orig, pic.2, pic.3);
            let kx = ((x1 - x0 + dx) / (x1 - x0).max(1.0)).max(0.05);
            let ky = ((y1 - y0 + dy) / (y1 - y0).max(1.0)).max(0.05);
            let (ox, oy) = (x0 / pic.2, y0 / pic.3);
            it.p = orig.p.iter().map(|p| [ox + (p[0] - ox) * kx, oy + (p[1] - oy) * ky]).collect();
        }
    }

    fn end(&self) {
        let d = self.drag.borrow_mut().take();
        if let Some(d) = &d {
            if d.mode.is_some() {
                if d.moved {
                    self.changed();
                }
                return;
            }
        }
        let Some(cur) = self.cur.borrow_mut().take() else { return };
        let Some(pic) = self.pic() else {
            self.area.queue_draw();
            return;
        };
        if !cur.boxed() && cur.p.len() == 1 {
            // a click with the pen: a dot -- unless it's half of a double
            // click on a text (to edit it), which must leave no dots behind
            let serial = self.dot_serial.get() + 1;
            self.dot_serial.set(serial);
            let w = self.me.clone();
            let src = glib::timeout_add_local_once(Duration::from_millis(DOUBLE_CLICK_MS), move || {
                if let Some(l) = w.upgrade() {
                    l.dots.borrow_mut().retain(|d| d.1 != serial);
                    l.snapshot();
                    l.items.borrow_mut().push(cur.clone());
                    l.changed();
                }
            });
            self.dots.borrow_mut().push((src, serial));
            return;
        }
        if cur.boxed() {
            let (x0, y0, x1, y1) = marks::bounds(&cur, pic.2, pic.3);
            if x1 - x0 < 3.0 && y1 - y0 < 3.0 {
                self.area.queue_draw();
                return; // a click, not a shape
            }
        }
        self.snapshot();
        self.items.borrow_mut().push(cur);
        self.changed();
    }

    fn double_click(&self, x: f64, y: f64) {
        if let Some(i) = self.hit(x, y) {
            if self.items.borrow()[i].t == "text" {
                // the clicks were to edit it, not to draw
                for (src, _) in self.dots.borrow_mut().drain(..) {
                    src.remove();
                }
                if self.text_pop.borrow().is_none() {
                    self.edit_text_at(x, y);
                }
            }
        }
    }

    // -- text -------------------------------------------------------------------------------------
    fn ask_text(&self, x: f64, y: f64, index: Option<usize>) {
        let start = index.map(|i| self.items.borrow()[i].text.clone()).unwrap_or_default();
        let entry = sonata_ui::controls::text_field(&start, &tr!("Text"));
        entry.set_size_request(220, -1);
        let pop = gtk::Popover::builder().child(&entry).has_arrow(false).position(gtk::PositionType::Bottom).build();
        pop.set_parent(&self.area);
        pop.set_pointing_to(Some(&gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
        let f = self.to_frac(x, y);
        *self.text_pop.borrow_mut() = Some(pop.clone());
        let done_flag = Rc::new(Cell::new(false));
        let done = {
            let (w, entry, pop, flag) = (self.me.clone(), entry.clone(), pop.clone(), done_flag.clone());
            Rc::new(move || {
                // Return, or the field closed by a click elsewhere (macOS keeps the text then too)
                if flag.replace(true) {
                    return;
                }
                let text = entry.text().trim().to_string();
                pop.popdown();
                let Some(l) = w.upgrade() else { return };
                if let Some(i) = index {
                    let alive = l.items.borrow().get(i).is_some_and(|m| m.t == "text");
                    if !alive {
                        return; // (the mark went meanwhile)
                    }
                    l.snapshot();
                    if text.is_empty() {
                        l.items.borrow_mut().remove(i);
                        l.sel.set(None);
                    } else {
                        l.items.borrow_mut()[i].text = text;
                    }
                    l.changed();
                } else if !text.is_empty() {
                    l.snapshot();
                    let mut m = Mark::new("text", &l.color.borrow(), vec![f]);
                    m.text = text;
                    m.s = TEXT_SIZE;
                    l.items.borrow_mut().push(m);
                    l.changed();
                }
            })
        };
        let d = done.clone();
        entry.connect_activate(move |_| d());
        let (w, d) = (self.me.clone(), done);
        pop.connect_closed(move |p| {
            d();
            if let Some(l) = w.upgrade() {
                if l.text_pop.borrow().as_ref() == Some(p) {
                    *l.text_pop.borrow_mut() = None;
                }
            }
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        pop.popup();
        entry.grab_focus();
    }

    fn edit_text_at(&self, x: f64, y: f64) {
        let Some(i) = self.hit(x, y) else { return };
        let Some((px, py, pw, ph)) = self.pic() else { return };
        let p = self.items.borrow()[i].p[0];
        if self.items.borrow()[i].t == "text" {
            self.ask_text(px + p[0] * pw, py + p[1] * ph, Some(i));
        }
    }

    // -- drawing ----------------------------------------------------------------------------------
    fn source(&self) -> Option<cairo::ImageSurface> {
        let tex = (self.texture)()?;
        let fresh = self.src.borrow().as_ref().is_some_and(|s| s.0 == tex);
        if !fresh {
            let surf = texture_surface(&tex)?;
            *self.src.borrow_mut() = Some((tex, surf));
        }
        self.src.borrow().as_ref().map(|s| s.1.clone())
    }

    fn draw(&self, cr: &cairo::Context) {
        let Some((px, py, pw, ph)) = self.pic() else { return };
        let mut all = self.items.borrow().clone();
        if let Some(c) = self.cur.borrow().as_ref() {
            all.push(c.clone());
        }
        let _ = cr.save();
        cr.translate(px, py);
        let mut src = None;
        if all.iter().any(|m| m.t == "pixelate") {
            if let Some(surf) = self.source() {
                if let Ok(scaled) = cairo::ImageSurface::create(cairo::Format::ARgb32, (pw as i32).max(1), (ph as i32).max(1)) {
                    if let Ok(sc) = cairo::Context::new(&scaled) {
                        sc.scale(pw / surf.width() as f64, ph / surf.height() as f64);
                        let _ = sc.set_source_surface(&surf, 0.0, 0.0);
                        let _ = sc.paint();
                    }
                    src = Some(scaled);
                }
            }
        }
        marks::draw(cr, &all, pw, ph, src.as_ref());
        let _ = cr.restore();
        if let Some(m) = self.sel.get().and_then(|i| self.items.borrow().get(i).cloned()) {
            let (x0, y0, x1, y1) = marks::bounds(&m, pw, ph);
            cr.set_source_rgba(0.0, 0.48, 1.0, 0.9);
            cr.set_line_width(1.0);
            cr.set_dash(&[4.0, 3.0], 0.0);
            cr.rectangle(px + x0 - 3.5, py + y0 - 3.5, x1 - x0 + 7.0, y1 - y0 + 7.0);
            let _ = cr.stroke();
            cr.set_dash(&[], 0.0);
            let (hx, hy) = (px + x1, py + y1); // the resize handle
            cr.arc(hx, hy, 5.0, 0.0, 2.0 * PI);
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0);
            let _ = cr.fill_preserve();
            cr.set_source_rgba(0.0, 0.48, 1.0, 1.0);
            cr.set_line_width(1.5);
            let _ = cr.stroke();
        }
    }
}

// -- the Markup toolbar -----------------------------------------------------------------------------
fn glyph_paint(name: &str, cr: &cairo::Context) {
    let line = |pts: &[(f64, f64)], close: bool| {
        cr.move_to(pts[0].0, pts[0].1);
        for p in &pts[1..] {
            cr.line_to(p.0, p.1);
        }
        if close {
            cr.close_path();
        }
        let _ = cr.stroke();
    };
    match name {
        "select" => line(&[(4.0, 2.0), (12.5, 8.3), (8.4, 9.0), (6.6, 13.2)], true),
        "pen" => line(&[(3.0, 13.0), (4.0, 10.0), (11.0, 3.0), (13.0, 5.0), (6.0, 12.0)], true),
        "hl" => {
            line(&[(5.0, 11.0), (11.0, 3.0), (14.0, 6.0), (8.0, 12.0)], true);
            line(&[(5.0, 11.0), (3.0, 14.0), (7.0, 14.0), (8.0, 12.0)], false);
        }
        "shape" => {
            cr.rectangle(2.0, 2.0, 7.0, 7.0);
            let _ = cr.stroke();
            cr.arc(10.5, 10.5, 3.6, 0.0, 2.0 * PI);
            let _ = cr.stroke();
        }
        "text" => {
            cr.move_to(3.0, 3.0);
            cr.line_to(13.0, 3.0);
            cr.move_to(8.0, 3.0);
            cr.line_to(8.0, 13.0);
            let _ = cr.stroke();
        }
        "step" => {
            cr.arc(8.0, 8.0, 6.3, 0.0, 2.0 * PI);
            let _ = cr.fill();
            let lay = marks::layout(cr, "1", 8.5, true);
            let (_i, log) = lay.pixel_extents();
            cr.set_operator(cairo::Operator::Clear); // the digit cut out
            cr.move_to(8.0 - log.width() as f64 / 2.0, 8.0 - log.height() as f64 / 2.0);
            pangocairo::functions::show_layout(cr, &lay);
        }
        "pixelate" => {
            for (x, y, a) in [(2.0, 2.0, 1.0), (10.0, 2.0, 0.4), (6.0, 6.0, 0.75), (2.0, 10.0, 0.4), (10.0, 10.0, 1.0), (6.0, 2.0, 0.2), (2.0, 6.0, 0.6), (10.0, 6.0, 0.3), (6.0, 10.0, 0.55)] {
                let _ = cr.save();
                cr.rectangle(x, y, 4.0, 4.0);
                cr.clip();
                let _ = cr.paint_with_alpha(a);
                let _ = cr.restore();
            }
        }
        "undo" | "redo" => {
            if name == "redo" {
                cr.translate(16.0, 0.0);
                cr.scale(-1.0, 1.0);
            }
            line(&[(5.0, 3.5), (2.5, 6.5), (5.0, 9.5)], false);
            cr.move_to(2.5, 6.5);
            cr.line_to(9.5, 6.5);
            cr.arc(9.5, 10.0, 3.5, -PI / 2.0, PI / 2.0);
            cr.line_to(7.0, 13.5);
            let _ = cr.stroke();
        }
        "width" => {
            for (y, w) in [(4.0, 1.0), (8.0, 2.0), (12.5, 3.4)] {
                cr.set_line_width(w);
                cr.move_to(2.0, y);
                cr.line_to(14.0, y);
                let _ = cr.stroke();
            }
        }
        "trash" => {
            cr.move_to(3.0, 4.0);
            cr.line_to(13.0, 4.0);
            cr.move_to(6.0, 4.0);
            cr.line_to(6.0, 2.5);
            cr.line_to(10.0, 2.5);
            cr.line_to(10.0, 4.0);
            cr.move_to(4.5, 4.0);
            cr.line_to(5.2, 13.5);
            cr.line_to(10.8, 13.5);
            cr.line_to(11.5, 4.0);
            let _ = cr.stroke();
        }
        n if n.starts_with("shape-") => {
            let kind = &n[6..];
            // drawn with the glyph's colour: the mark's colour comes from the source
            let (r, g, b) = cairo::SolidPattern::try_from(cr.source()).ok().and_then(|p| p.rgba().ok()).map(|(r, g, b, _)| (r, g, b)).unwrap_or((0.0, 0.0, 0.0));
            let hex = format!("#{:02x}{:02x}{:02x}", (r * 255.0) as u8, (g * 255.0) as u8, (b * 255.0) as u8);
            let pts = if kind == "line" || kind == "arrow" { vec![[0.15, 0.85], [0.85, 0.15]] } else { vec![[0.15, 0.15], [0.85, 0.85]] };
            let mut m = Mark::new(kind, &hex, pts);
            m.w = 1.5 / 16.0;
            marks::draw(cr, &[m], 16.0, 16.0, None);
        }
        _ => {}
    }
}

/// A tool's icon, drawn in the label colour (crisp at any scale, light and dark).
fn glyph(name: &str, size: i32) -> gtk::DrawingArea {
    let area = gtk::DrawingArea::builder().content_width(size).content_height(size).can_target(false).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
    let name = name.to_string();
    area.set_draw_func(move |a, cr, w, h| {
        let c = a.color();
        cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
        cr.scale(w as f64 / 16.0, h as f64 / 16.0);
        cr.set_line_width(1.5);
        cr.set_line_cap(cairo::LineCap::Round);
        cr.set_line_join(cairo::LineJoin::Round);
        glyph_paint(&name, cr);
    });
    area
}

pub const TOOLS: [(&str, &str); 8] = [
    ("select", n_!("Select (move, resize, recolour; Delete removes)")),
    ("pen", n_!("Pen")),
    ("hl", n_!("Highlighter")),
    ("shape", n_!("Shapes")),
    ("text", n_!("Text")),
    ("emoji", n_!("Emoji")),
    ("step", n_!("Numbered steps")),
    ("pixelate", n_!("Pixelate (hide something)")),
];

/// Tools | colours | width | undo redo clear | Copy Cancel Done.
pub struct MarkupBar {
    pub root: gtk::Box,
    layer: Rc<MarkupLayer>,
    tools: HashMap<&'static str, gtk::Button>,
    swatches: Vec<gtk::DrawingArea>,
    copy_btn: Option<gtk::Button>,
}

impl MarkupBar {
    pub fn new(
        layer: Rc<MarkupLayer>,
        on_done: Box<dyn Fn()>,
        on_cancel: Box<dyn Fn()>,
        on_copy: Option<Box<dyn Fn(Box<dyn Fn(bool)>)>>,
    ) -> Rc<Self> {
        let root = gtk::Box::builder().spacing(2).halign(gtk::Align::Fill).build();
        root.add_css_class("pv-markup");
        let row = gtk::Box::builder().spacing(2).hexpand(true).halign(gtk::Align::Center).build();
        let mut tools = HashMap::new();
        for (name, tip) in TOOLS {
            let b = gtk::Button::builder().tooltip_text(gettext(tip)).can_focus(false).build();
            b.add_css_class("mk");
            if name == "emoji" {
                b.set_child(Some(&gtk::Label::new(Some(&layer.emoji.borrow()))));
            } else {
                b.set_child(Some(&glyph(name, 16)));
            }
            row.append(&b);
            tools.insert(name, b);
        }
        let sep = || {
            let s = gtk::Box::new(gtk::Orientation::Horizontal, 0);
            s.add_css_class("mk-sep");
            s
        };
        row.append(&sep());
        let mut swatches = Vec::new();
        let mut swatch_buttons = Vec::new();
        for c in COLORS {
            let b = gtk::Button::builder().can_focus(false).tooltip_text(c).build();
            b.add_css_class("mk-color");
            let sw = gtk::DrawingArea::builder().content_width(16).content_height(16).can_target(false).build();
            let l = Rc::downgrade(&layer);
            sw.set_draw_func(move |_, cr, w, h| {
                let on = l.upgrade().is_some_and(|l| *l.color.borrow() == c);
                let (r, g, b) = marks::rgb(c);
                let (w, h) = (w as f64, h as f64);
                cr.arc(w / 2.0, h / 2.0, w / 2.0 - if on { 2.5 } else { 1.0 }, 0.0, 2.0 * PI);
                cr.set_source_rgba(r, g, b, 1.0);
                let _ = cr.fill_preserve();
                cr.set_source_rgba(0.0, 0.0, 0.0, 0.25);
                cr.set_line_width(0.75);
                let _ = cr.stroke();
                if on {
                    // the chosen one: a ring in the accent
                    cr.arc(w / 2.0, h / 2.0, w / 2.0 - 0.75, 0.0, 2.0 * PI);
                    cr.set_source_rgba(0.0, 0.48, 1.0, 1.0);
                    cr.set_line_width(1.5);
                    let _ = cr.stroke();
                }
            });
            b.set_child(Some(&sw));
            row.append(&b);
            swatches.push(sw);
            swatch_buttons.push((c, b));
        }
        row.append(&sep());
        let wb = gtk::Button::builder().tooltip_text(tr!("Line width")).can_focus(false).build();
        wb.add_css_class("mk");
        wb.set_child(Some(&glyph("width", 16)));
        row.append(&wb);
        let actions: [(&str, &str, fn(&MarkupLayer)); 3] = [
            ("undo", n_!("Undo"), |l| {
                l.undo();
            }),
            ("redo", n_!("Redo"), |l| {
                l.redo();
            }),
            ("trash", n_!("Delete all marks"), |l| l.clear()),
        ];
        for (name, tip, f) in actions {
            let b = gtk::Button::builder().tooltip_text(gettext(tip)).can_focus(false).build();
            b.add_css_class("mk");
            b.set_child(Some(&glyph(name, 16)));
            let l = Rc::downgrade(&layer);
            b.connect_clicked(move |_| {
                if let Some(l) = l.upgrade() {
                    f(&l);
                }
            });
            row.append(&b);
        }
        root.append(&row);
        let copy_btn = on_copy.as_ref().map(|_| {
            let b = gtk::Button::with_label(&tr!("Copy"));
            b.add_css_class("sonata-button");
            b.add_css_class("mk-done");
            b.set_valign(gtk::Align::Center);
            b.set_tooltip_text(Some(&tr!("Copy the picture with its marks")));
            root.append(&b);
            b
        });
        let cancel = sonata_ui::controls::push_button(&tr!("Cancel"), on_cancel, "");
        cancel.set_valign(gtk::Align::Center);
        cancel.add_css_class("mk-done");
        let done = sonata_ui::controls::push_button(&tr!("Done"), on_done, "default");
        done.set_valign(gtk::Align::Center);
        done.add_css_class("mk-done");
        root.append(&cancel);
        root.append(&done);
        let bar = Rc::new(MarkupBar { root, layer: layer.clone(), tools, swatches, copy_btn });
        for (name, b) in &bar.tools {
            let (w, name) = (Rc::downgrade(&bar), *name);
            b.connect_clicked(move |_| {
                if let Some(bar) = w.upgrade() {
                    bar.tool(name);
                }
            });
        }
        for (c, b) in swatch_buttons {
            let w = Rc::downgrade(&bar);
            b.connect_clicked(move |_| {
                if let Some(bar) = w.upgrade() {
                    bar.layer.set_color(c);
                    for sw in &bar.swatches {
                        sw.queue_draw();
                    }
                }
            });
        }
        let w = Rc::downgrade(&bar);
        wb.connect_clicked(move |b| {
            if let Some(bar) = w.upgrade() {
                bar.widths(b);
            }
        });
        if let (Some(cb), Some(f)) = (bar.copy_btn.clone(), on_copy) {
            let f = Rc::new(f);
            cb.connect_clicked(move |b| {
                let b = b.clone();
                f(Box::new(move |ok| {
                    // the Copy button says so for a moment
                    b.set_label(&if ok { tr!("Copied") } else { tr!("Couldn't Copy") });
                    let b = b.clone();
                    glib::timeout_add_local_once(Duration::from_millis(1400), move || b.set_label(&tr!("Copy")));
                }));
            });
        }
        let t = layer.tool.borrow().clone();
        bar.select_tool(&t);
        bar
    }

    fn select_tool(&self, name: &str) {
        self.layer.set_tool(name);
        for (n, b) in &self.tools {
            if *n == name {
                b.add_css_class("on");
            } else {
                b.remove_css_class("on");
            }
        }
    }

    fn tool(self: &Rc<Self>, name: &str) {
        if name == "shape" {
            let choices: Vec<(String, String)> = SHAPES.iter().map(|s| (format!("shape-{s}"), s.to_string())).collect();
            self.pick("shape", choices);
        } else if name == "emoji" {
            let choices: Vec<(String, String)> = EMOJI.iter().map(|e| (e.to_string(), e.to_string())).collect();
            self.pick("emoji", choices);
        }
        self.select_tool(name);
    }

    /// Shapes / emoji: a grid under the tool's button.
    fn pick(self: &Rc<Self>, what: &'static str, choices: Vec<(String, String)>) {
        let anchor = self.tools[what].clone();
        let grid = gtk::FlowBox::builder()
            .max_children_per_line(if what == "shape" { 5 } else { 8 })
            .selection_mode(gtk::SelectionMode::None)
            .row_spacing(2)
            .column_spacing(2)
            .build();
        grid.add_css_class("mk-pop");
        let pop = gtk::Popover::builder().child(&grid).has_arrow(false).build();
        pop.set_parent(&anchor);
        let current = if what == "shape" { self.layer.shape.borrow().clone() } else { self.layer.emoji.borrow().clone() };
        for (key, value) in choices {
            let b = gtk::Button::builder().can_focus(false).build();
            if what == "shape" {
                b.set_child(Some(&glyph(&key, 18)));
            } else {
                b.set_child(Some(&gtk::Label::new(Some(&key))));
            }
            if value == current {
                b.add_css_class("on");
            }
            let (w, anchor, pop) = (Rc::downgrade(self), anchor.clone(), pop.clone());
            b.connect_clicked(move |_| {
                let Some(bar) = w.upgrade() else { return };
                if what == "shape" {
                    *bar.layer.shape.borrow_mut() = value.clone();
                    anchor.set_child(Some(&glyph(&format!("shape-{value}"), 16)));
                } else {
                    *bar.layer.emoji.borrow_mut() = value.clone();
                    anchor.set_child(Some(&gtk::Label::new(Some(&value))));
                }
                pop.popdown();
            });
            grid.insert(&b, -1);
        }
        pop.connect_closed(|p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        pop.popup();
    }

    fn widths(&self, anchor: &gtk::Button) {
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 2);
        row.add_css_class("mk-pop");
        let pop = gtk::Popover::builder().child(&row).has_arrow(false).build();
        pop.set_parent(anchor);
        let tips = [tr!("Thin"), tr!("Medium"), tr!("Thick")];
        for (i, w) in WIDTHS.iter().enumerate() {
            let b = gtk::Button::builder().can_focus(false).tooltip_text(tips[i].as_str()).build();
            let area = gtk::DrawingArea::builder().content_width(22).content_height(18).can_target(false).build();
            area.set_draw_func(move |a, cr, ww, hh| {
                let c = a.color();
                cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, 1.0);
                cr.set_line_width([1.2, 2.4, 4.2][i]);
                cr.set_line_cap(cairo::LineCap::Round);
                cr.move_to(3.0, hh as f64 / 2.0);
                cr.line_to(ww as f64 - 3.0, hh as f64 / 2.0);
                let _ = cr.stroke();
            });
            b.set_child(Some(&area));
            if (w - self.layer.width.get()).abs() < 1e-6 {
                b.add_css_class("on");
            }
            let (l, p, w) = (Rc::downgrade(&self.layer), pop.clone(), *w);
            b.connect_clicked(move |_| {
                if let Some(l) = l.upgrade() {
                    l.set_width(w);
                }
                p.popdown();
            });
            row.append(&b);
        }
        pop.connect_closed(|p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        pop.popup();
    }
}
