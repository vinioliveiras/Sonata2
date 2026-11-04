//! A colour picker in Sonata's own style (ui/colorpicker.py; no GTK /
//! libadwaita colour dialog: its buttons and header came out in another
//! theme). A panel popover: a colour wheel (hue around, saturation
//! outwards; drag the knob), a brightness slider, a hex field with a
//! preview, Cancel / Select. Here because sonata-ui has no picker yet:
//! Appearance is its only user so far.
//!
//! Not ported: the knob's glide to a new spot and its grow tween (it jumps).

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
    let bright = gtk::Scale::with_range(gtk::Orientation::Horizontal, 0.0, 100.0, 1.0);
    bright.set_draw_value(false);
    bright.set_value(hsv.get().2 * 100.0);
    bright.set_tooltip_text(Some(&tr!("Brightness")));
    bright.add_css_class("cp-bright");
    let preview = round_swatch(color.clone(), 22);
    let hexe = gtk::Entry::builder().text(color.borrow().as_str()).css_classes(["cp-hex"]).max_length(7).width_chars(8).hexpand(true).build();
    // (a change made here: not echoed back)
    let busy = Rc::new(Cell::new(false));

    let show: Rc<dyn Fn(&str, bool)> = {
        let (color, preview, hexe, busy) = (color.clone(), preview.clone(), hexe.clone(), busy.clone());
        Rc::new(move |c: &str, from_entry: bool| {
            *color.borrow_mut() = c.to_string();
            preview.queue_draw();
            if !from_entry && hexe.text() != c {
                busy.set(true);
                hexe.set_text(c);
                busy.set(false);
            }
        })
    };
    let drag = gtk::GestureDrag::new();
    {
        let (hsv, knob, wheel2, show, bright, busy) = (hsv.clone(), knob.clone(), wheel.clone(), show.clone(), bright.clone(), busy.clone());
        let at = Rc::new(move |x: f64, y: f64| {
            let (_, _, v) = hsv.get();
            let (h, s) = hs_at(x, y, wheel2.width() as f64, wheel2.height() as f64);
            hsv.set((h, s, v));
            wheel2.queue_draw();
            show(&to_hex(h, s, v), false);
            if (bright.value() - v * 100.0).abs() > 0.5 {
                busy.set(true);
                bright.set_value(v * 100.0);
                busy.set(false);
            }
        });
        let (at1, knob1) = (at.clone(), knob.clone());
        drag.connect_drag_begin(move |_, x, y| {
            knob1.set(KNOB + 2.0);
            at1(x, y);
        });
        drag.connect_drag_update(move |g, dx, dy| {
            if let Some((x, y)) = g.start_point() {
                at(x + dx, y + dy);
            }
        });
        let w3 = wheel.clone();
        drag.connect_drag_end(move |_, _, _| {
            knob.set(KNOB);
            w3.queue_draw();
        });
    }
    wheel.add_controller(drag);
    {
        let (hsv, wheel, show, busy) = (hsv.clone(), wheel.clone(), show.clone(), busy.clone());
        bright.connect_value_changed(move |sl| {
            if busy.get() {
                return;
            }
            let (h, s, _) = hsv.get();
            let v = sl.value() / 100.0;
            hsv.set((h, s, v));
            wheel.queue_draw();
            show(&to_hex(h, s, v), false);
        });
    }
    {
        let (hsv, wheel, show, busy, bright) = (hsv.clone(), wheel.clone(), show.clone(), busy.clone(), bright.clone());
        hexe.connect_changed(move |e| {
            let Some(c) = parse_hex(&e.text()) else { return }; // ignored until it's a colour
            if busy.get() {
                return;
            }
            let (mut nh, ns, nv) = to_hsv(&c);
            if ns == 0.0 {
                nh = hsv.get().0; // a grey: keep the hue (no swing round)
            }
            hsv.set((nh, ns, nv));
            wheel.queue_draw();
            busy.set(true);
            bright.set_value(nv * 100.0);
            busy.set(false);
            show(&c, true);
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
    col.append(&bright);
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
