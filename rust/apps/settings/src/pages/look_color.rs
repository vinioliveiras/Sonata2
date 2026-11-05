//! A colour picker in Sonata's own style (ui/colorpicker.py; no GTK /
//! libadwaita colour dialog: its buttons and header came out in another
//! theme). A panel popover: a colour wheel (hue around, saturation
//! outwards; drag the knob), a brightness slider, a hex field with a
//! preview, Cancel / Select. Here because sonata-ui has no picker yet:
//! Appearance is its only user so far.
//!

use adw::prelude::*;
use gtk::{cairo, glib};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::f64::consts::PI;
use std::rc::Rc;

pub const DEFAULT: &str = "#0a84ff";
/// the wheel's diameter
const WHEEL: i32 = 200;
/// the knob's radius (11 while held)
const KNOB: f64 = 9.0;

const CSS: &str = "
.cp-panel { padding: 2px 2px 4px 2px; }
.cp-wheel { margin: 6px 14px 4px 14px; }
.cp-bright { margin: 10px 14px 2px 14px; }
.cp-row { margin: 10px 10px 2px 10px; }
entry.cp-hex { min-height: %(control_h)s; font-family: monospace; }
.cp-buttons { margin: 10px 10px 4px 10px; }
button.cp-dot { min-width: 16px; min-height: 16px; padding: 2px; margin: 0 3px; border-radius: 99px; border: none;
  background: conic-gradient(#ff3b30, #ffcc00, #34c759, #0a84ff, #af52de, #ff3b30);
  box-shadow: none; transition: box-shadow %(t_fast)s; }
button.cp-dot.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 3.5px alpha(%(label)s, 0.45); }
";

fn register_css() {
    thread_local!(static DONE: Cell<bool> = const { Cell::new(false) });
    if !DONE.with(|d| d.replace(true)) {
        sonata_ui::register("colorpicker", CSS);
    }
}

// -- colours (colorsys) ----------------------------------------------------------------------------
pub fn hsv_to_rgb(h: f64, s: f64, v: f64) -> (f64, f64, f64) {
    if s == 0.0 {
        return (v, v, v);
    }
    let i = (h * 6.0).floor();
    let f = h * 6.0 - i;
    let (p, q, t) = (v * (1.0 - s), v * (1.0 - s * f), v * (1.0 - s * (1.0 - f)));
    match (i as i64).rem_euclid(6) {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    }
}

pub fn to_hex(h: f64, s: f64, v: f64) -> String {
    let (r, g, b) = hsv_to_rgb(h.rem_euclid(1.0), s, v);
    let c = |x: f64| (x * 255.0).round_ties_even() as u8;
    format!("#{:02x}{:02x}{:02x}", c(r), c(g), c(b))
}

fn rgb_of(hex: &str) -> (f64, f64, f64) {
    let h = hex.trim_start_matches('#');
    let p = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0) as f64 / 255.0;
    (p(0), p(2), p(4))
}

pub fn to_hsv(hex: &str) -> (f64, f64, f64) {
    let (r, g, b) = rgb_of(hex);
    let (maxc, minc) = (r.max(g).max(b), r.min(g).min(b));
    if maxc == minc {
        return (0.0, 0.0, maxc);
    }
    let s = (maxc - minc) / maxc;
    let (rc, gc, bc) = ((maxc - r) / (maxc - minc), (maxc - g) / (maxc - minc), (maxc - b) / (maxc - minc));
    let h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2.0 + rc - bc
    } else {
        4.0 + gc - rc
    };
    ((h / 6.0).rem_euclid(1.0), s, maxc)
}

/// '#abc', 'abc', '#aabbcc' -> '#aabbcc'; None when not a colour.
pub fn parse_hex(text: &str) -> Option<String> {
    let t = text.trim().trim_start_matches('#');
    let t: String = if t.chars().count() == 3 { t.chars().flat_map(|c| [c, c]).collect() } else { t.to_string() };
    (t.len() == 6 && t.chars().all(|c| c.is_ascii_hexdigit())).then(|| format!("#{}", t.to_lowercase()))
}

fn round_swatch(color: Rc<RefCell<String>>, size: i32) -> gtk::DrawingArea {
    let a = gtk::DrawingArea::builder().content_width(size).content_height(size).can_target(false).valign(gtk::Align::Center).build();
    a.set_draw_func(move |_, cr, w, h| {
        let (r, g, b) = rgb_of(&color.borrow());
        cr.arc(w as f64 / 2.0, h as f64 / 2.0, w.min(h) as f64 / 2.0, 0.0, 2.0 * PI);
        cr.set_source_rgb(r, g, b);
        let _ = cr.fill();
    });
    a
}

// -- the dot that opens it --------------------------------------------------------------------------
/// The "other colour" dot: a rainbow ring around the colour picked.
#[derive(Clone)]
pub struct ColorDot {
    pub button: gtk::Button,
    color: Rc<RefCell<String>>,
    swatch: gtk::DrawingArea,
}

impl ColorDot {
    pub fn new(color: &str, tooltip: Option<&str>) -> Self {
        register_css();
        let color = Rc::new(RefCell::new(color.to_string()));
        let swatch = round_swatch(color.clone(), 12);
        let button = gtk::Button::builder().child(&swatch).css_classes(["cp-dot"]).valign(gtk::Align::Center).build();
        button.set_tooltip_text(Some(&tooltip.map(str::to_string).unwrap_or_else(|| tr!("Other colour…"))));
        ColorDot { button, color, swatch }
    }

    pub fn set_color(&self, color: &str) {
        *self.color.borrow_mut() = color.to_string();
        self.swatch.queue_draw();
    }

    pub fn color(&self) -> String {
        self.color.borrow().clone()
    }

    /// The picker under the dot; on_pick("#rrggbb") when Select is pressed.
    pub fn connect_pick(&self, title: Option<String>, on_pick: impl Fn(&str) + 'static) {
        let (me, on_pick) = (self.clone(), Rc::new(on_pick));
        self.button.connect_clicked(move |b| {
            let on_pick = on_pick.clone();
            popup(b, &me.color(), move |c| on_pick(c), title.as_deref());
        });
    }
}

// -- the wheel --------------------------------------------------------------------------------------
fn centre(w: f64, h: f64) -> (f64, f64, f64) {
    (w / 2.0, h / 2.0, w.min(h) / 2.0)
}

/// Hue and saturation under (x, y); outside the circle, its edge. Hue
/// counter-clockwise from the right (red), as macOS.
pub fn hs_at(x: f64, y: f64, w: f64, h: f64) -> (f64, f64) {
    let (cx, cy, r) = centre(w, h);
    let (dx, dy) = (x - cx, cy - y);
    ((dy.atan2(dx) / (2.0 * PI)).rem_euclid(1.0), (dx.hypot(dy) / r.max(1.0)).min(1.0))
}

fn draw_wheel(cr: &cairo::Context, w: f64, h: f64, (hue, sat, val): (f64, f64, f64), knob: f64) {
    let (cx, cy, r) = centre(w, h);
    // the hues, a thin wedge each (cairo has no conic gradient)
    for i in 0..360 {
        let (a0, a1) = (i as f64 * PI / 180.0, (i as f64 + 1.5) * PI / 180.0);
        let (cr_, cg, cb) = hsv_to_rgb(i as f64 / 360.0, 1.0, 1.0);
        cr.move_to(cx, cy);
        cr.arc_negative(cx, cy, r, -a0, -a1);
        cr.close_path();
        cr.set_source_rgb(cr_, cg, cb);
        let _ = cr.fill();
    }
    let white = cairo::RadialGradient::new(cx, cy, 0.0, cx, cy, r);
    white.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 1.0);
    white.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0);
    cr.arc(cx, cy, r, 0.0, 2.0 * PI);
    let _ = cr.set_source(&white);
    let _ = cr.fill();
    if val < 1.0 {
        cr.arc(cx, cy, r, 0.0, 2.0 * PI);
        cr.set_source_rgba(0.0, 0.0, 0.0, 1.0 - val);
        let _ = cr.fill();
    }
    cr.arc(cx, cy, r - 0.25, 0.0, 2.0 * PI);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.12);
    cr.set_line_width(0.5);
    let _ = cr.stroke();
    // the knob: the colour, in a white ring
    let a = hue * 2.0 * PI;
    let (x, y) = (cx + r * sat * a.cos(), cy - r * sat * a.sin());
    cr.arc(x, y + 0.5, knob + 1.0, 0.0, 2.0 * PI);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.25);
    let _ = cr.fill();
    cr.arc(x, y, knob, 0.0, 2.0 * PI);
    cr.set_source_rgb(1.0, 1.0, 1.0);
    let _ = cr.fill();
    let (kr, kg, kb) = hsv_to_rgb(hue, sat, val);
    cr.arc(x, y, knob - 3.0, 0.0, 2.0 * PI);
    cr.set_source_rgb(kr, kg, kb);
    let _ = cr.fill();
}

// -- motion: the knob glides and grows (transition.tween) ----------------------------------------
/// The wheel's running tweens: one glide, one knob size at a time.
#[derive(Clone, Default)]
struct Tweens {
    glide: Rc<RefCell<Option<adw::TimedAnimation>>>,
    knob: Rc<RefCell<Option<adw::TimedAnimation>>>,
}

/// The point at `t` of the straight line from (h0, s0) to (h1, s1), as
/// hue and saturation (the knob crosses the wheel, it doesn't go round);
/// exact at the end (the centre has no hue).
pub fn glide_at((h0, s0): (f64, f64), (h1, s1): (f64, f64), t: f64) -> (f64, f64) {
    if t >= 1.0 {
        return (h1, s1);
    }
    let (a0, a1) = (h0 * 2.0 * PI, h1 * 2.0 * PI);
    let (x0, y0, x1, y1) = (s0 * a0.cos(), s0 * a0.sin(), s1 * a1.cos(), s1 * a1.sin());
    let (x, y) = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
    ((y.atan2(x) / (2.0 * PI)).rem_euclid(1.0), x.hypot(y).min(1.0))
}

fn tween(slot: &RefCell<Option<adw::TimedAnimation>>, widget: &gtk::DrawingArea, from: f64, to: f64, ms: f64, step: impl Fn(f64) + 'static) {
    if let Some(old) = slot.borrow_mut().take() {
        old.pause(); // a new one with the same key replaces it
    }
    let target = adw::CallbackAnimationTarget::new(step);
    let anim = adw::TimedAnimation::new(widget, from, to, sonata_ui::tokens::ms(ms), target);
    anim.set_easing(adw::Easing::EaseInOutCubic);
    *slot.borrow_mut() = Some(anim.clone());
    anim.play();
}

impl Tweens {
    fn stop_glide(&self) {
        if let Some(a) = self.glide.borrow_mut().take() {
            a.pause();
        }
    }

    /// The knob a little bigger while held, back when let go.
    fn grow(&self, wheel: &gtk::DrawingArea, knob: &Rc<Cell<f64>>, held: bool) {
        let (k, w) = (knob.clone(), wheel.downgrade());
        tween(&self.knob, wheel, knob.get(), KNOB + if held { 2.0 } else { 0.0 }, 150.0, move |v| {
            k.set(v);
            if let Some(w) = w.upgrade() {
                w.queue_draw();
            }
        });
    }

    /// The knob slides to (h, s) on a straight line, the colour (and the
    /// brightness) with it; `changed` each step, none when quiet.
    fn glide(&self, wheel: &gtk::DrawingArea, hsv: &Rc<Cell<(f64, f64, f64)>>, (h, s, v): (f64, f64, f64), changed: Option<Rc<dyn Fn()>>) {
        let (h0, s0, v0) = hsv.get();
        let (hsv, w) = (hsv.clone(), wheel.downgrade());
        tween(&self.glide, wheel, 0.0, 1.0, 180.0, move |t| {
            let (nh, ns) = glide_at((h0, s0), (h, s), t);
            hsv.set((nh, ns, v0 + (v - v0) * t));
            if let Some(w) = w.upgrade() {
                w.queue_draw();
            }
            if let Some(c) = &changed {
                c();
            }
        });
    }
}

// -- the brightness: controls.ModuleSlider with a gradient ------------------------------------------
/// A Control Center capsule slider whose track is black to the colour at
/// full brightness (the knob and the ramp line up; a Gtk.Scale's didn't).
#[derive(Clone)]
struct BrightSlider {
    area: gtk::DrawingArea,
    value: Rc<Cell<f64>>,
    end: Rc<RefCell<String>>,
    changed: Rc<RefCell<Vec<Rc<dyn Fn(f64)>>>>,
}

const BRIGHT_H: f64 = 22.0;

impl BrightSlider {
    fn new(value: f64, end: &str) -> Self {
        let area = gtk::DrawingArea::builder().hexpand(true).valign(gtk::Align::Center).height_request(BRIGHT_H as i32).width_request(120).build();
        area.set_cursor_from_name(Some("default"));
        let me = BrightSlider { area: area.clone(), value: Rc::new(Cell::new(value.clamp(0.0, 100.0))), end: Rc::new(RefCell::new(end.to_string())), changed: Rc::default() };
        let (v, e) = (me.value.clone(), me.end.clone());
        area.set_draw_func(move |_, cr, w, h| draw_bright(cr, w as f64, h as f64, v.get(), &e.borrow()));
        let drag = gtk::GestureDrag::new();
        let m = me.clone();
        drag.connect_drag_begin(move |_, x, _| m.to(x));
        let m = me.clone();
        drag.connect_drag_update(move |g, dx, _| {
            if let Some((x, _)) = g.start_point() {
                m.to(x + dx);
            }
        });
        area.add_controller(drag);
        me
    }

    /// The knob's centre follows the pointer.
    fn to(&self, x: f64) {
        let span = (self.area.width() as f64 - BRIGHT_H).max(1.0);
        self.set_value(((x - BRIGHT_H / 2.0) / span).clamp(0.0, 1.0) * 100.0);
    }

    fn value(&self) -> f64 {
        self.value.get()
    }

    fn set_value(&self, v: f64) {
        let v = v.clamp(0.0, 100.0);
        if v != self.value.get() {
            self.value.set(v);
            self.area.queue_draw();
            let fs: Vec<Rc<dyn Fn(f64)>> = self.changed.borrow().clone();
            for f in fs {
                f(v);
            }
        }
    }

    fn set_gradient_end(&self, hex: &str) {
        *self.end.borrow_mut() = hex.to_string();
        self.area.queue_draw();
    }

    fn connect_changed(&self, f: impl Fn(f64) + 'static) {
        self.changed.borrow_mut().push(Rc::new(f));
    }
}

fn capsule(cr: &cairo::Context, x: f64, y: f64, w: f64, h: f64) {
    let r = h / 2.0;
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -PI / 2.0, PI / 2.0);
    cr.arc(x + r, y + r, r, PI / 2.0, 3.0 * PI / 2.0);
    cr.close_path();
}

fn draw_bright(cr: &cairo::Context, w: f64, height: f64, value: f64, end: &str) {
    let h = BRIGHT_H;
    let y = (height - h) / 2.0;
    let r = h / 2.0;
    let kx = (w - h) * value / 100.0; // the knob's left edge
    let ramp = cairo::LinearGradient::new(r, 0.0, w - r, 0.0);
    let (er, eg, eb) = rgb_of(end);
    ramp.add_color_stop_rgb(0.0, 0.0, 0.0, 0.0);
    ramp.add_color_stop_rgb(1.0, er, eg, eb);
    capsule(cr, 0.0, y, w, h);
    let _ = cr.set_source(&ramp);
    let _ = cr.fill();
    // the knob: white, a soft shadow and a hairline edge
    capsule(cr, kx - 0.5, y + 0.5, h + 1.0, h + 1.0);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.18);
    let _ = cr.fill();
    capsule(cr, kx, y, h, h);
    cr.set_source_rgb(1.0, 1.0, 1.0);
    let _ = cr.fill_preserve();
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.12);
    cr.set_line_width(0.5);
    let _ = cr.stroke();
}

/// The picker under `anchor`; on_pick("#rrggbb") when Select is pressed.
pub fn popup(anchor: &impl IsA<gtk::Widget>, current: &str, on_pick: impl Fn(&str) + 'static, title: Option<&str>) -> gtk::Popover {
    register_css();
    let title = title.map(str::to_string).unwrap_or_else(|| tr!("Accent Colour"));
    let color = Rc::new(RefCell::new(parse_hex(current).unwrap_or_else(|| DEFAULT.to_string())));
    let hsv = Rc::new(Cell::new(to_hsv(&color.borrow())));
    let knob = Rc::new(Cell::new(KNOB));
    let wheel = gtk::DrawingArea::builder().content_width(WHEEL).content_height(WHEEL).halign(gtk::Align::Center).css_classes(["cp-wheel"]).build();
    wheel.set_cursor_from_name(Some("crosshair"));
    {
        let (hsv, knob) = (hsv.clone(), knob.clone());
        wheel.set_draw_func(move |_, cr, w, h| draw_wheel(cr, w as f64, h as f64, hsv.get(), knob.get()));
    }
    let (h0, s0, v0) = hsv.get();
    let bright = BrightSlider::new(v0 * 100.0, &to_hex(h0, s0, 1.0));
    bright.area.set_tooltip_text(Some(&tr!("Brightness")));
    bright.area.add_css_class("cp-bright");
    let preview = round_swatch(color.clone(), 22);
    let hexe = gtk::Entry::builder().text(color.borrow().as_str()).css_classes(["cp-hex"]).max_length(7).width_chars(8).hexpand(true).build();
    // (a change made here: not echoed back)
    let busy = Rc::new(Cell::new(false));

    let show: Rc<dyn Fn(&str, bool)> = {
        let (color, preview, hexe, busy, bright, hsv) = (color.clone(), preview.clone(), hexe.clone(), busy.clone(), bright.clone(), hsv.clone());
        Rc::new(move |c: &str, from_entry: bool| {
            *color.borrow_mut() = c.to_string();
            preview.queue_draw();
            let (h, s, _) = hsv.get();
            bright.set_gradient_end(&to_hex(h, s, 1.0));
            if !from_entry && hexe.text() != c {
                busy.set(true);
                hexe.set_text(c);
                busy.set(false);
            }
        })
    };
    // the wheel moved (ColorWheel's "changed"): the colour, the brightness with it
    let from_wheel: Rc<dyn Fn()> = {
        let (hsv, show, bright, busy) = (hsv.clone(), show.clone(), bright.clone(), busy.clone());
        Rc::new(move || {
            let (h, s, v) = hsv.get();
            show(&to_hex(h, s, v), false);
            if (bright.value() - v * 100.0).abs() > 0.5 {
                busy.set(true);
                bright.set_value(v * 100.0);
                busy.set(false);
            }
        })
    };
    let tweens = Tweens::default();
    let drag = gtk::GestureDrag::new();
    {
        let (hsv_outer, fw_outer) = (hsv.clone(), from_wheel.clone());
        let (hsv, wheel2, from_wheel, tw) = (hsv.clone(), wheel.clone(), from_wheel.clone(), tweens.clone());
        // a drag: the knob follows (a glide still running stops)
        let at = Rc::new(move |x: f64, y: f64| {
            tw.stop_glide();
            let (_, _, v) = hsv.get();
            let (h, s) = hs_at(x, y, wheel2.width() as f64, wheel2.height() as f64);
            hsv.set((h, s, v));
            wheel2.queue_draw();
            from_wheel();
        });
        let (hsv1, knob1, wheel1, tw1, fw1) = (hsv_outer.clone(), knob.clone(), wheel.clone(), tweens.clone(), fw_outer.clone());
        // a press: the knob grows and glides to the spot
        drag.connect_drag_begin(move |_, x, y| {
            tw1.grow(&wheel1, &knob1, true);
            let (h, s) = hs_at(x, y, wheel1.width() as f64, wheel1.height() as f64);
            let v = hsv1.get().2;
            let fw = fw1.clone();
            tw1.glide(&wheel1, &hsv1, (h, s, v), Some(Rc::new(move || fw())));
        });
        drag.connect_drag_update(move |g, dx, dy| {
            if let Some((x, y)) = g.start_point() {
                at(x + dx, y + dy);
            }
        });
        let (w3, tw3, knob3) = (wheel.clone(), tweens.clone(), knob.clone());
        drag.connect_drag_end(move |_, _, _| tw3.grow(&w3, &knob3, false));
    }
    wheel.add_controller(drag);
    {
        let (hsv, wheel, show, busy, tw) = (hsv.clone(), wheel.clone(), show.clone(), busy.clone(), tweens.clone());
        bright.connect_changed(move |value| {
            if busy.get() {
                return;
            }
            tw.stop_glide(); // (set_hsv: quietly, what the wheel shows now)
            let (h, s, _) = hsv.get();
            let v = value / 100.0;
            hsv.set((h, s, v));
            wheel.queue_draw();
            show(&to_hex(h, s, v), false);
        });
    }
    {
        let (hsv, wheel, show, busy, bright, tw) = (hsv.clone(), wheel.clone(), show.clone(), busy.clone(), bright.clone(), tweens.clone());
        hexe.connect_changed(move |e| {
            let Some(c) = parse_hex(&e.text()) else { return }; // ignored until it's a colour
            if busy.get() {
                return;
            }
            let (mut nh, ns, nv) = to_hsv(&c);
            if ns == 0.0 {
                nh = hsv.get().0; // a grey: keep the hue (no swing round)
            }
            show(&c, true);
            tw.glide(&wheel, &hsv, (nh, ns, nv), None); // quietly: the colour typed stays
            busy.set(true);
            bright.set_value(nv * 100.0);
            busy.set(false);
            bright.set_gradient_end(&to_hex(nh, ns, 1.0));
        });
    }
    let row = gtk::Box::builder().spacing(8).css_classes(["cp-row"]).build();
    row.append(&preview);
    row.append(&hexe);
    let pop_slot: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
    let select = {
        let (color, pop_slot) = (color.clone(), pop_slot.clone());
        Rc::new(move || {
            on_pick(&color.borrow().clone());
            if let Some(p) = pop_slot.borrow().as_ref() {
                p.popdown();
            }
        })
    };
    let btns = gtk::Box::builder().spacing(8).css_classes(["cp-buttons"]).homogeneous(true).build();
    let ps = pop_slot.clone();
    btns.append(&sonata_ui::controls::push_button(&tr!("Cancel"), move || {
        if let Some(p) = ps.borrow().as_ref() {
            p.popdown();
        }
    }, ""));
    let s2 = select.clone();
    btns.append(&sonata_ui::controls::push_button(&tr!("Select"), move || s2(), "default"));
    hexe.connect_activate(move |_| select());
    let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
    col.add_css_class("cp-panel");
    col.append(&sonata_ui::panel::header(&title, None));
    col.append(&wheel);
    col.append(&bright.area);
    col.append(&row);
    col.append(&btns);
    let pop = sonata_ui::panel::popup(anchor, &col, gtk::PositionType::Bottom);
    *pop_slot.borrow_mut() = Some(pop.clone());
    // (the popover holds itself through pop_slot until it closes)
    let ps = pop_slot.clone();
    pop.connect_closed(move |_| {
        ps.borrow_mut().take();
    });
    glib::idle_add_local_once(move || {
        hexe.grab_focus();
    });
    pop
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_round_trips() {
        for c in ["#ff6a00", "#0a84ff", "#808080", "#000000", "#ffffff"] {
            let (h, s, v) = to_hsv(c);
            assert_eq!(to_hex(h, s, v), c);
        }
        assert_eq!(to_hex(0.0, 1.0, 1.0), "#ff0000");
    }

    #[test]
    fn parses_hex() {
        assert_eq!(parse_hex("#ABC").as_deref(), Some("#aabbcc"));
        assert_eq!(parse_hex(" aabbcc ").as_deref(), Some("#aabbcc"));
        assert_eq!(parse_hex("#abcd"), None);
        assert_eq!(parse_hex("#gggggg"), None);
    }

    /// The knob jumped to the spot pressed: colorpicker.py glides it there
    /// on a straight line (crossing the wheel, not going round it).
    #[test]
    fn test_knob_jumped_instead_of_gliding() {
        // red to cyan across the centre: halfway is the centre itself
        let (_, s) = glide_at((0.0, 1.0), (0.5, 1.0), 0.5);
        assert!(s < 1e-9);
        // the end is exact (the centre has no hue)
        assert_eq!(glide_at((0.3, 0.0), (0.7, 0.4), 1.0), (0.7, 0.4));
        assert_eq!(glide_at((0.25, 0.5), (0.25, 1.0), 0.5), (0.25, 0.75));
    }

    #[test]
    fn wheel_geometry() {
        // red on the right, green-ish up (counter-clockwise), the centre white
        let (h, s) = hs_at(200.0, 100.0, 200.0, 200.0);
        assert!((h - 0.0).abs() < 1e-9 && (s - 1.0).abs() < 1e-9);
        let (h, _) = hs_at(100.0, 0.0, 200.0, 200.0);
        assert!((h - 0.25).abs() < 1e-9);
        assert_eq!(hs_at(100.0, 100.0, 200.0, 200.0).1, 0.0);
        assert_eq!(hs_at(500.0, 100.0, 200.0, 200.0).1, 1.0);
    }
}
