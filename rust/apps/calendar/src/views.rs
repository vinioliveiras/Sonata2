//! Calendar's views (calendar/views.py), painted with cairo (one widget
//! per view, not one per event): Month (6 x 7 grid), Day / Week (hourly
//! timeline with an all-day strip), Year (12 small months) and the
//! sidebar's mini month. Colours come from tokens and the calendar palette
//! (model::PALETTE); they repaint on appearance changes.
//!
//! Every view talks to its window through CalendarWindow's methods: date,
//! first_weekday, occurrences(a, b), selected_key, select, edit, create,
//! move_occ, go, event_menu, empty_menu, fmt_time, fmt_hour, calendar_color.

use crate::ics::{midnight, Occurrence, DT};
use crate::model;
use crate::window::CalendarWindow;
use chrono::{Datelike, Duration, NaiveDate, Timelike};
use adw::prelude::AnimationExt;
use gtk::prelude::*;
use gtk::{cairo, gdk, pango};
use sonata_i18n::{fmt, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::f64::consts::PI;
use std::rc::{Rc, Weak};

/// timeline: pixels per hour
pub const HOUR_H: f64 = 48.0;
/// timeline: hour labels column
pub const GUTTER: f64 = 56.0;
/// all-day strip / month bars: one line
pub const LANE: f64 = 20.0;
/// week header: "Wed 30"
pub const DAYS_H: f64 = 30.0;
/// minutes: moves and new events snap to it
pub const SNAP: i64 = 15;
/// px at an event's bottom that resize it
const EDGE: f64 = 6.0;
/// month view: weekday names
const MONTH_HEAD: f64 = 26.0;

fn one_day() -> Duration {
    Duration::days(1)
}

// -- painting helpers -------------------------------------------------------------------------
/// The tokens of one paint (read once: a paint asks for many colours).
pub struct Ink {
    vals: sonata_ui::tokens::Values,
}

impl Ink {
    pub fn new() -> Self {
        Self { vals: sonata_ui::theme::values() }
    }

    pub fn rgba(&self, token: &str) -> gdk::RGBA {
        self.vals.get(token).and_then(|c| gdk::RGBA::parse(c.as_str()).ok()).unwrap_or(gdk::RGBA::BLACK)
    }

    pub fn px(&self, token: &str) -> f64 {
        self.vals.get(token).and_then(|v| v.trim_end_matches("px").trim().parse().ok()).unwrap_or(13.0)
    }

    fn family(&self, display: bool) -> String {
        self.vals.get(if display { "font_display" } else { "font" }).cloned().unwrap_or_default().replace('"', "")
    }
}

/// A text to paint; `width` ellipsizes it.
#[allow(clippy::too_many_arguments)]
pub fn layout(widget: &impl IsA<gtk::Widget>, ink: &Ink, text: &str, token: &str, weight: i32, width: Option<f64>, align: pango::Alignment, scale: f64) -> pango::Layout {
    let lay = widget.create_pango_layout(Some(text));
    let mut d = pango::FontDescription::new();
    d.set_family(&ink.family(false));
    d.set_absolute_size(ink.px(token) * scale * pango::SCALE as f64);
    d.set_weight(match weight {
        w if w >= 700 => pango::Weight::Bold,
        w if w >= 600 => pango::Weight::Semibold,
        w if w >= 500 => pango::Weight::Medium,
        _ => pango::Weight::Normal,
    });
    lay.set_font_description(Some(&d));
    lay.set_alignment(align);
    if let Some(w) = width {
        lay.set_width(((w * pango::SCALE as f64) as i32).max(1));
        lay.set_ellipsize(pango::EllipsizeMode::End);
    }
    lay
}

fn small(widget: &impl IsA<gtk::Widget>, ink: &Ink, text: &str, weight: i32, width: Option<f64>) -> pango::Layout {
    layout(widget, ink, text, "text_small", weight, width, pango::Alignment::Left, 1.0)
}

pub fn source(cr: &cairo::Context, c: &gdk::RGBA, alpha: f64) {
    cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64 * alpha);
}

pub fn show(cr: &cairo::Context, lay: &pango::Layout, x: f64, y: f64, c: &gdk::RGBA, alpha: f64) {
    source(cr, c, alpha);
    cr.move_to(x, y);
    pangocairo::functions::show_layout(cr, lay);
}

pub fn text_h(lay: &pango::Layout) -> f64 {
    lay.pixel_extents().1.height() as f64
}

fn text_w(lay: &pango::Layout) -> f64 {
    lay.pixel_extents().1.width() as f64
}

pub fn rrect(cr: &cairo::Context, x: f64, y: f64, w: f64, h: f64, r: f64) {
    let r = r.min(w / 2.0).min(h / 2.0).max(0.0);
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -PI / 2.0, 0.0);
    cr.arc(x + w - r, y + h - r, r, 0.0, PI / 2.0);
    cr.arc(x + r, y + h - r, r, PI / 2.0, PI);
    cr.arc(x + r, y + r, r, PI, 3.0 * PI / 2.0);
    cr.close_path();
}

fn fill_rect(cr: &cairo::Context, c: &gdk::RGBA, alpha: f64, x: f64, y: f64, w: f64, h: f64) {
    source(cr, c, alpha);
    cr.rectangle(x, y, w, h);
    let _ = cr.fill();
}

pub fn palette_rgba(ink: &Ink, color_id: &str) -> gdk::RGBA {
    let r = model::PALETTE.iter().find(|p| p.0 == color_id).map(|p| p.2).unwrap_or("sys_blue");
    if r.starts_with('#') {
        gdk::RGBA::parse(r).unwrap_or(gdk::RGBA::BLACK)
    } else {
        ink.rgba(r)
    }
}

/// (solid, fill, text) for events of a calendar colour, per appearance: a
/// tinted fill with text in a deeper (light) / lighter (dark) shade of the
/// colour, like macOS Calendar.
pub fn event_colors(ink: &Ink, color_id: &str) -> (gdk::RGBA, gdk::RGBA, gdk::RGBA) {
    let solid = palette_rgba(ink, color_id);
    let lab = ink.rgba("label");
    let dark = sonata_ui::theme::is_dark();
    let mut fill = solid;
    fill.set_alpha(if dark { 0.30 } else { 0.18 });
    let t = if dark { 0.30 } else { 0.42 };
    let mix = |a: f32, b: f32| a * (1.0 - t) + b * t;
    let text = gdk::RGBA::new(mix(solid.red(), lab.red()), mix(solid.green(), lab.green()), mix(solid.blue(), lab.blue()), 1.0);
    (solid, fill, text)
}

/// A date's name in the session's language (strftime through GLib).
pub fn strftime(d: NaiveDate, pattern: &str) -> String {
    glib::DateTime::from_local(d.year(), d.month() as i32, d.day() as i32, 12, 0, 0.0).ok().and_then(|g| g.format(pattern).ok()).map(|s| s.to_string()).unwrap_or_default()
}

/// The first `n` characters (a weekday's initial).
fn initial(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

pub fn today() -> NaiveDate {
    crate::alerts::now().date()
}

fn today_circle(cr: &cairo::Context, ink: &Ink, cx: f64, cy: f64, r: f64) {
    source(cr, &ink.rgba("destructive"), 1.0);
    cr.arc(cx, cy, r, 0.0, 2.0 * PI);
    let _ = cr.fill();
}

type Rect = (f64, f64, f64, f64);

/// A painted view: repaints on appearance changes, keeps hit rects.
pub struct Canvas {
    pub area: gtk::DrawingArea,
    /// [(x, y, w, h, occurrence)], topmost last
    pub hits: RefCell<Vec<(Rect, Occurrence)>>,
    pub drag: RefCell<Option<Drag>>,
    pub win: Weak<CalendarWindow>,
}

impl Canvas {
    fn new(win: Weak<CalendarWindow>) -> Rc<Self> {
        let area = gtk::DrawingArea::new();
        let c = Rc::new(Self { area, hits: RefCell::default(), drag: RefCell::default(), win });
        let a = c.area.downgrade();
        sonata_ui::theme::on_change(move || {
            if let Some(a) = a.upgrade() {
                a.queue_draw();
            }
        });
        c
    }

    pub fn hit(&self, x: f64, y: f64) -> Option<(Occurrence, Rect)> {
        self.hits.borrow().iter().rev().find(|((hx, hy, hw, hh), _)| *hx <= x && x < hx + hw && *hy <= y && y < hy + hh).map(|(r, o)| (o.clone(), *r))
    }

    fn add_hit(&self, r: Rect, o: &Occurrence) {
        self.hits.borrow_mut().push((r, o.clone()));
    }

    pub fn rect(x: f64, y: f64, w: f64, h: f64) -> gdk::Rectangle {
        gdk::Rectangle::new(x as i32, y as i32, (w as i32).max(1), (h as i32).max(1))
    }

    /// (start, end) of an occurrence, as moved by the drag in progress.
    fn dragged(&self, occ: &Occurrence) -> (DT, DT) {
        match self.drag.borrow().as_ref() {
            Some(d) if d.active && d.occ == *occ => (occ.start + d.ds, occ.end + d.de),
            _ => (occ.start, occ.end),
        }
    }

    fn active_drag(&self) -> Option<Drag> {
        self.drag.borrow().clone().filter(|d| d.active)
    }
}

/// A drag in progress: move by days (and minutes on the grid) or resize.
#[derive(Clone)]
pub struct Drag {
    pub occ: Occurrence,
    pub resize: bool,
    pub x0: f64,
    pub y0: f64,
    pub active: bool,
    pub ds: Duration,
    pub de: Duration,
}

/// The shared drag logic. delta(x0, y0, dx, dy, resizing) -> (days,
/// minutes); seg_end(occ, rect): the end the hit piece shows (resizing
/// only from the real end); changed(): repaint.
fn drag_setup(c: &Rc<Canvas>, resizable: bool, delta: impl Fn(f64, f64, f64, f64, bool) -> (i64, i64) + 'static, seg_end: impl Fn(&Occurrence, Rect) -> Option<DT> + 'static, changed: impl Fn() + 'static) {
    let g = gtk::GestureDrag::builder().button(1).build();
    let w = Rc::downgrade(c);
    g.connect_drag_begin(move |_, x, y| {
        let Some(c) = w.upgrade() else { return };
        let found = c.hit(x, y);
        c.drag.replace(found.map(|(occ, r)| {
            let resize = resizable && y > r.1 + r.3 - EDGE && Some(occ.end) == seg_end(&occ, r);
            Drag { occ, resize, x0: x, y0: y, active: false, ds: Duration::zero(), de: Duration::zero() }
        }));
    });
    let w = Rc::downgrade(c);
    let changed = Rc::new(changed);
    let ch = changed.clone();
    g.connect_drag_update(move |_, dx, dy| {
        let Some(c) = w.upgrade() else { return };
        let mut slot = c.drag.borrow_mut();
        let Some(d) = slot.as_mut() else { return };
        if !d.active && dx.abs() + dy.abs() < 4.0 {
            return;
        }
        d.active = true;
        let (days, minutes) = delta(d.x0, d.y0, dx, dy, d.resize);
        if d.resize {
            let o = &d.occ;
            let mut de = Duration::minutes(minutes);
            if o.end + de < o.start + Duration::minutes(SNAP) {
                de = o.start + Duration::minutes(SNAP) - o.end;
            }
            d.ds = Duration::zero();
            d.de = de;
        } else {
            d.ds = Duration::days(days) + Duration::minutes(minutes);
            d.de = d.ds;
        }
        drop(slot);
        ch();
    });
    let w = Rc::downgrade(c);
    g.connect_drag_end(move |_, _, _| {
        let Some(c) = w.upgrade() else { return };
        let d = c.drag.take();
        match d {
            Some(d) if d.active && (!d.ds.is_zero() || !d.de.is_zero()) => {
                if let Some(win) = c.win.upgrade() {
                    win.move_occ(&d.occ, d.ds, d.de);
                }
            }
            _ => changed(),
        }
    });
    c.area.add_controller(g);
}

/// Presses of any button: pressed(button, n, x, y).
fn clicks(area: &gtk::DrawingArea, pressed: impl Fn(u32, i32, f64, f64) + 'static) {
    let g = gtk::GestureClick::builder().button(0).build();
    g.connect_pressed(move |gest, n, x, y| pressed(gest.current_button(), n, x, y));
    area.add_controller(g);
}

/// A timed event on the timeline: tinted block, solid left bar, title (+
/// time and location when there is room).
#[allow(clippy::too_many_arguments)]
fn draw_block(area: &gtk::DrawingArea, cr: &cairo::Context, ink: &Ink, color: &str, x: f64, y: f64, w: f64, h: f64, occ: &Occurrence, selected: bool, time_text: Option<&str>) {
    let (solid, fill, text) = event_colors(ink, color);
    let r = ink.px("r_menu_row");
    let _ = cr.save();
    rrect(cr, x, y, w, h, r);
    cr.clip();
    source(cr, if selected { &solid } else { &fill }, 1.0);
    let _ = cr.paint();
    if !selected {
        fill_rect(cr, &solid, 1.0, x, y, 3.0, h);
    }
    let col = if selected { ink.rgba("label_on_accent") } else { text };
    let (tx, tw) = (x + 7.0, w - 10.0);
    let title = if occ.event.summary.is_empty() { tr!("New Event") } else { occ.event.summary.clone() };
    if h >= 30.0 && w > 40.0 {
        let lay = small(area, ink, &title, 600, Some(tw));
        show(cr, &lay, tx, y + 2.0, &col, 1.0);
        let mut cy = y + 2.0 + text_h(&lay);
        for extra in [time_text.unwrap_or(""), occ.event.location.as_str()] {
            if extra.is_empty() {
                continue;
            }
            let sub = small(area, ink, extra, 400, Some(tw));
            if cy + text_h(&sub) > y + h {
                break;
            }
            show(cr, &sub, tx, cy, &col, 0.85);
            cy += text_h(&sub);
        }
    } else {
        // short: one line, title then time
        let mut line = title;
        if let Some(t) = time_text.filter(|_| w > 90.0) {
            line.push_str("  ");
            line.push_str(t);
        }
        let lay = small(area, ink, &line, 600, Some(tw));
        show(cr, &lay, tx, y + ((h.min(18.0) - text_h(&lay)) / 2.0).max(0.0), &col, 1.0);
    }
    let _ = cr.restore();
}

/// An all-day / multi-day event: a filled bar with its title.
#[allow(clippy::too_many_arguments)]
fn draw_bar(area: &gtk::DrawingArea, cr: &cairo::Context, ink: &Ink, color: &str, x: f64, y: f64, w: f64, h: f64, occ: &Occurrence, selected: bool, label: bool, round_l: bool, round_r: bool) {
    let (solid, fill, text) = event_colors(ink, color);
    let r = ink.px("r_menu_row");
    let _ = cr.save();
    cr.rectangle(x, y, w, h);
    cr.clip();
    let (ext_l, ext_r) = (if round_l { 0.0 } else { r + 1.0 }, if round_r { 0.0 } else { r + 1.0 });
    rrect(cr, x - ext_l, y, w + ext_l + ext_r, h, r);
    cr.clip();
    source(cr, if selected { &solid } else { &fill }, 1.0);
    let _ = cr.paint();
    if label {
        let title = if occ.event.summary.is_empty() { tr!("New Event") } else { occ.event.summary.clone() };
        let lay = small(area, ink, &title, 600, Some((w - 10.0).max(1.0)));
        show(cr, &lay, x + 6.0, y + (h - text_h(&lay)) / 2.0, &if selected { ink.rgba("label_on_accent") } else { text }, 1.0);
    }
    let _ = cr.restore();
}

// -- timeline: Day and Week ---------------------------------------------------------------------
type Segment = (Occurrence, DT, DT, usize, usize);

/// Week (7 days) or Day (1 day): day names, an all-day strip, and a
/// scrolled 24-hour grid with events as blocks side by side when they
/// overlap, and the red current-time line.
pub struct Timeline {
    pub root: gtk::Box,
    pub ndays: i64,
    pub days_h: f64,
    pub head: Rc<Canvas>,
    pub grid: Rc<Canvas>,
    pub scroll: gtk::ScrolledWindow,
    /// day index -> [(occurrence, seg_start, seg_end, col, ncols)]
    segments: RefCell<HashMap<i64, Vec<Segment>>>,
    /// [(occurrence, first col, last col, lane)]
    bars: RefCell<Vec<(Occurrence, i64, i64, usize)>>,
    scrolled: Cell<bool>,
    tick: RefCell<Option<glib::SourceId>>,
    anim: RefCell<Option<adw::TimedAnimation>>,
    win: Weak<CalendarWindow>,
}

impl Timeline {
    pub fn new(win: Weak<CalendarWindow>, ndays: i64) -> Rc<Self> {
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["cal-timeline"]).build();
        let days_h = if ndays > 1 { DAYS_H } else { 0.0 }; // Day view: the title names the day
        let head = Canvas::new(win.clone());
        head.area.set_content_height((days_h + LANE + 8.0) as i32);
        head.area.set_hexpand(true);
        head.area.add_css_class("cal-head");
        let grid = Canvas::new(win.clone());
        grid.area.set_content_height((24.0 * HOUR_H) as i32);
        grid.area.set_vexpand(true);
        grid.area.set_hexpand(true);
        let scroll = gtk::ScrolledWindow::builder().child(&grid.area).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build();
        root.append(&head.area);
        root.append(&gtk::Separator::builder().css_classes(["cal-sep"]).build());
        root.append(&scroll);
        let tl = Rc::new(Self { root, ndays, days_h, head, grid, scroll, segments: RefCell::default(), bars: RefCell::default(), scrolled: Cell::new(false), tick: RefCell::default(), anim: RefCell::default(), win });
        Self::wire(&tl);
        tl
    }

    fn wire(tl: &Rc<Self>) {
        let w = Rc::downgrade(tl);
        tl.root.connect_map(move |_| {
            if let Some(t) = w.upgrade() {
                t.mapped();
            }
        });
        let w = Rc::downgrade(tl);
        tl.root.connect_unmap(move |_| {
            if let Some(t) = w.upgrade() {
                if let Some(id) = t.tick.take() {
                    id.remove();
                }
            }
        });
        // the grid
        let w = Rc::downgrade(tl);
        tl.grid.area.set_draw_func(move |a, cr, width, height| {
            if let Some(t) = w.upgrade() {
                t.grid.hits.borrow_mut().clear();
                t.paint_grid(a, cr, width as f64, height as f64);
            }
        });
        let (w1, w2, w3) = (Rc::downgrade(tl), Rc::downgrade(tl), Rc::downgrade(tl));
        drag_setup(
            &tl.grid,
            true,
            move |x0, _y0, dx, dy, resizing| {
                let Some(t) = w1.upgrade() else { return (0, 0) };
                let cw = (t.grid.area.width() as f64 - GUTTER) / t.ndays as f64;
                let i0 = ((x0 - GUTTER) / cw).floor() as i64;
                let i1 = (((x0 + dx - GUTTER) / cw).floor() as i64).clamp(0, t.ndays - 1);
                let minutes = ((dy / HOUR_H * 60.0 / SNAP as f64).round_ties_even() as i64) * SNAP;
                (if resizing { 0 } else { i1 - i0 }, minutes)
            },
            move |occ, r| {
                let t = w2.upgrade()?;
                let day_end = midnight(t.slot(r.0 + 1.0, r.1).date()) + one_day();
                (occ.end <= day_end).then_some(occ.end)
            },
            move || {
                if let Some(t) = w3.upgrade() {
                    t.grid.area.queue_draw();
                }
            },
        );
        let w = Rc::downgrade(tl);
        clicks(&tl.grid.area, move |b, n, x, y| {
            if let Some(t) = w.upgrade() {
                t.grid_pressed(b, n, x, y);
            }
        });
        let motion = gtk::EventControllerMotion::new();
        let w = Rc::downgrade(tl);
        motion.connect_motion(move |_, x, y| {
            if let Some(t) = w.upgrade() {
                let edge = t.grid.hit(x, y).is_some_and(|(_, r)| y > r.1 + r.3 - EDGE);
                t.grid.area.set_cursor_from_name(if edge { Some("ns-resize") } else { None });
            }
        });
        tl.grid.area.add_controller(motion);
        // the head
        let w = Rc::downgrade(tl);
        tl.head.area.set_draw_func(move |a, cr, width, height| {
            if let Some(t) = w.upgrade() {
                t.head.hits.borrow_mut().clear();
                t.paint_head(a, cr, width as f64, height as f64);
            }
        });
        let (w1, w3) = (Rc::downgrade(tl), Rc::downgrade(tl));
        drag_setup(
            &tl.head,
            false,
            move |x0, _y0, dx, _dy, _| {
                let Some(t) = w1.upgrade() else { return (0, 0) };
                let cw = (t.head.area.width() as f64 - GUTTER) / t.ndays as f64;
                ((((x0 + dx - GUTTER) / cw).floor() as i64).clamp(0, t.ndays - 1) - ((x0 - GUTTER) / cw).floor() as i64, 0)
            },
            |occ, _| Some(occ.end),
            move || {
                if let Some(t) = w3.upgrade() {
                    t.head.area.queue_draw();
                }
            },
        );
        let w = Rc::downgrade(tl);
        clicks(&tl.head.area, move |b, n, x, y| {
            if let Some(t) = w.upgrade() {
                t.head_pressed(b, n, x, y);
            }
        });
    }

    pub fn days(&self) -> Vec<NaiveDate> {
        let Some(win) = self.win.upgrade() else { return Vec::new() };
        let d = win.date();
        let first = if self.ndays == 1 { d } else { model::week_start(d, win.first_weekday()) };
        (0..self.ndays).map(|i| first + Duration::days(i)).collect()
    }

    pub fn range(&self) -> (DT, DT) {
        let d = self.days();
        (midnight(d[0]), midnight(d[d.len() - 1]) + one_day())
    }

    pub fn refresh(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let (a, b) = self.range();
        let occs = win.occurrences(a, b);
        let days = self.days();
        let multi: Vec<Occurrence> = occs.iter().filter(|o| model::is_multiday(o)).cloned().collect();
        let bars = model::lanes(&multi, days[0], self.ndays);
        let mut segments = HashMap::new();
        for (i, d) in days.iter().enumerate() {
            let (da, db) = (midnight(*d), midnight(*d) + one_day());
            let mut segs = Vec::new();
            let mut orig = HashMap::new();
            for o in occs.iter() {
                if model::is_multiday(o) || !(o.start < db && (o.end > da || (o.start == o.end && o.end >= da))) {
                    continue;
                }
                let s = Occurrence { event: o.event.clone(), start: o.start.max(da), end: o.end.min(db) };
                orig.insert(s.key(), o.clone());
                segs.push(s);
            }
            let cols = model::columns(&segs);
            segments.insert(i as i64, segs.iter().map(|s| {
                let (c, n) = cols[&s.key()];
                (orig[&s.key()].clone(), s.start, s.end, c, n)
            }).collect());
        }
        let nl = bars.iter().map(|b| b.3 + 1).max().unwrap_or(1).max(1);
        self.bars.replace(bars);
        self.segments.replace(segments);
        self.head.area.set_content_height((self.days_h + nl.min(6) as f64 * LANE + 8.0) as i32);
        self.head.area.queue_draw();
        self.grid.area.queue_draw();
    }

    pub fn col_x(&self, width: f64, i: i64) -> (f64, f64) {
        let cw = (width - GUTTER) / self.ndays as f64;
        (GUTTER + i as f64 * cw, cw)
    }

    /// Scroll so `hour` is at the top (smoothly, unless animate is false).
    pub fn scroll_to_hour(&self, hour: f64, animate: bool) {
        let adj = self.scroll.vadjustment();
        let target = (hour * HOUR_H - 8.0).max(0.0).min((adj.upper() - adj.page_size()).max(0.0));
        if !animate {
            adj.set_value(target);
            return;
        }
        let start = adj.value();
        let standard: f64 = sonata_ui::theme::values().get("t_standard").and_then(|v| v.trim_end_matches("ms").parse().ok()).unwrap_or(250.0);
        let a = adj.clone();
        let target_cb = adw::CallbackAnimationTarget::new(move |v| a.set_value(start + (target - start) * v));
        let anim = adw::TimedAnimation::new(&self.scroll, 0.0, 1.0, sonata_ui::tokens::ms(2.0 * standard), target_cb);
        anim.set_easing(adw::Easing::EaseOutCubic);
        anim.play();
        self.anim.replace(Some(anim));
    }

    /// Smooth scroll to 8 AM the first time the timeline shows.
    fn mapped(self: &Rc<Self>) {
        if !self.scrolled.replace(true) {
            let w = Rc::downgrade(self);
            glib::timeout_add_local(std::time::Duration::from_millis(60), move || {
                let Some(t) = w.upgrade() else { return glib::ControlFlow::Break };
                if t.scroll.vadjustment().upper() <= 1.0 {
                    return glib::ControlFlow::Continue; // not allocated yet
                }
                t.scroll_to_hour(8.0, true);
                glib::ControlFlow::Break
            });
        }
        self.schedule_tick();
    }

    /// Repaint at the next minute (the current-time line), while shown.
    fn schedule_tick(self: &Rc<Self>) {
        if let Some(id) = self.tick.take() {
            id.remove();
        }
        let wait = (60 - crate::alerts::now().second()).max(1);
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_seconds_local_once(wait, move || {
            let Some(t) = w.upgrade() else { return };
            t.tick.replace(None);
            let suspended = t.root.root().and_downcast::<gtk::Window>().is_some_and(|w| w.is_suspended()); // minimized: no repaint
            if !suspended {
                t.grid.area.queue_draw();
            }
            if t.root.is_mapped() {
                t.schedule_tick();
            }
        });
        self.tick.replace(Some(id));
    }

    fn slot(&self, x: f64, y: f64) -> DT {
        let w = self.grid.area.width() as f64;
        let i = ((x - GUTTER) / ((w - GUTTER) / self.ndays as f64)).floor().clamp(0.0, (self.ndays - 1) as f64) as usize;
        let minutes = ((y / HOUR_H * 60.0).clamp(0.0, (24 * 60 - SNAP) as f64) as i64) / SNAP * SNAP;
        midnight(self.days()[i]) + Duration::minutes(minutes)
    }

    fn grid_pressed(&self, button: u32, n: i32, x: f64, y: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let found = self.grid.hit(x, y);
        if button == 3 {
            match found {
                Some((occ, _)) => {
                    win.select(Some(occ.clone()));
                    win.event_menu(&occ, self.grid.area.upcast_ref(), x, y);
                }
                None => win.empty_menu(self.grid.area.upcast_ref(), x, y, self.slot(x, y), false),
            }
            return;
        }
        if button != 1 {
            return;
        }
        if n == 1 {
            win.select(found.map(|f| f.0));
        } else if n == 2 {
            match found {
                Some((occ, r)) => win.edit(&occ, self.grid.area.upcast_ref(), Some(Canvas::rect(r.0, r.1, r.2, r.3)), false),
                None => {
                    let s = self.slot(x, y);
                    let s = s.with_minute(if s.minute() < 30 { 0 } else { 30 }).unwrap();
                    let i = self.days().iter().position(|d| *d == s.date()).unwrap_or(0) as i64;
                    let (x0, cw) = self.col_x(self.grid.area.width() as f64, i);
                    let top = (s.hour() as f64 + s.minute() as f64 / 60.0) * HOUR_H;
                    win.create(s, s + Duration::hours(1), false, Some(self.grid.area.upcast_ref()), Some(Canvas::rect(x0, top, cw, HOUR_H)));
                }
            }
        }
    }

    fn paint_grid(&self, area: &gtk::DrawingArea, cr: &cairo::Context, w: f64, h: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let ink = Ink::new();
        let days = self.days();
        source(cr, &ink.rgba("content_bg"), 1.0);
        let _ = cr.paint();
        let cw = (w - GUTTER) / self.ndays as f64;
        // weekends a shade darker
        for (i, d) in days.iter().enumerate() {
            if model::weekday(*d) >= 5 && self.ndays > 1 {
                fill_rect(cr, &ink.rgba("row_alt"), 1.0, GUTTER + i as f64 * cw, 0.0, cw, h);
            }
        }
        // hour lines and labels
        let (sep, lab2) = (ink.rgba("separator"), ink.rgba("label_secondary"));
        for hr in 0..24 {
            let y = hr as f64 * HOUR_H + 0.5;
            fill_rect(cr, &sep, 1.0, GUTTER - 6.0, y, w - GUTTER + 6.0, 1.0);
            if hr > 0 {
                let lay = layout(area, &ink, &win.fmt_hour(hr), "text_small", 400, Some(GUTTER - 12.0), pango::Alignment::Right, 1.0);
                show(cr, &lay, 2.0, y - text_h(&lay) / 2.0 - 1.0, &lab2, 1.0);
            }
        }
        for i in 0..=self.ndays {
            fill_rect(cr, &sep, 1.0, (GUTTER + i as f64 * cw).floor(), 0.0, 1.0, h);
        }
        // events
        let sel = win.selected_key();
        let drag = self.grid.active_drag();
        for i in 0..self.ndays {
            let x0 = GUTTER + i as f64 * cw;
            for (occ, s, e, col, ncols) in self.segments.borrow().get(&i).into_iter().flatten() {
                if drag.as_ref().is_some_and(|d| d.occ == *occ) {
                    continue;
                }
                self.block(area, cr, &ink, &win, occ, *s, *e, x0, cw, *col, *ncols, sel.as_ref() == Some(&occ.key()), None);
            }
        }
        if let Some(d) = drag {
            // the dragged event, where it would go
            let occ = d.occ.clone();
            let (s, e) = self.grid.dragged(&occ);
            for (i, day) in days.iter().enumerate() {
                let da = midnight(*day);
                let (ss, ee) = (s.max(da), e.min(da + one_day()));
                if ss < ee || (ss == ee && ee == s && da <= s && s < da + one_day()) {
                    self.block(area, cr, &ink, &win, &occ, ss, ee, GUTTER + i as f64 * cw, cw, 0, 1, true, Some(ss == s));
                }
            }
        }
        // the current time: a red line over today, its time in the gutter
        let now = crate::alerts::now();
        if let Some(i) = days.iter().position(|d| *d == now.date()) {
            let y = (now.hour() as f64 + now.minute() as f64 / 60.0) * HOUR_H;
            let red = ink.rgba("destructive");
            let x0 = GUTTER + i as f64 * cw;
            fill_rect(cr, &red, 1.0, x0, y - 0.75, cw, 1.5);
            if self.ndays > 1 {
                fill_rect(cr, &red, 0.35, GUTTER, y - 0.5, w - GUTTER, 1.0);
            }
            cr.arc(x0, y, 4.0, 0.0, 2.0 * PI);
            source(cr, &red, 1.0);
            let _ = cr.fill();
            let lay = layout(area, &ink, &win.fmt_time(now.time()), "text_small", 600, Some(GUTTER - 12.0), pango::Alignment::Right, 1.0);
            let th = text_h(&lay);
            fill_rect(cr, &ink.rgba("content_bg"), 1.0, 0.0, y - th / 2.0 - 1.0, GUTTER - 4.0, th + 2.0);
            show(cr, &lay, 2.0, y - th / 2.0 - 1.0, &red, 1.0);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn block(&self, area: &gtk::DrawingArea, cr: &cairo::Context, ink: &Ink, win: &CalendarWindow, occ: &Occurrence, s: DT, e: DT, x0: f64, cw: f64, col: usize, ncols: usize, selected: bool, first: Option<bool>) {
        let y = (s.hour() as f64 + s.minute() as f64 / 60.0) * HOUR_H;
        let mins = (e - s).num_seconds() as f64 / 60.0;
        let hgt = (mins / 60.0 * HOUR_H).max(18.0);
        let colw = (cw - 6.0) / ncols as f64;
        let x = x0 + 1.0 + col as f64 * colw;
        let bw = if ncols > 1 { colw - 1.0 } else { cw - 6.0 };
        let first = first.unwrap_or(s == occ.start);
        let tt = first.then(|| win.fmt_time(occ.start.time()));
        draw_block(area, cr, ink, &win.calendar_color(occ), x, y + 1.0, bw, hgt - 2.0, occ, selected, tt.as_deref());
        self.grid.add_hit((x, y, bw, hgt), occ);
    }

    fn day_at(&self, x: f64) -> (NaiveDate, i64) {
        let cw = (self.head.area.width() as f64 - GUTTER) / self.ndays as f64;
        let i = (((x - GUTTER) / cw).floor() as i64).clamp(0, self.ndays - 1);
        (self.days()[i as usize], i)
    }

    fn head_pressed(&self, button: u32, n: i32, x: f64, y: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let found = self.head.hit(x, y);
        let (d, i) = self.day_at(x);
        if y < self.days_h {
            if button == 1 && self.ndays > 1 && x > GUTTER {
                win.go(d, Some("day"));
            }
            return;
        }
        if button == 3 {
            match found {
                Some((occ, _)) => {
                    win.select(Some(occ.clone()));
                    win.event_menu(&occ, self.head.area.upcast_ref(), x, y);
                }
                None => win.empty_menu(self.head.area.upcast_ref(), x, y, midnight(d), true),
            }
            return;
        }
        if button != 1 {
            return;
        }
        if n == 1 {
            win.select(found.map(|f| f.0));
        } else if n == 2 {
            match found {
                Some((occ, r)) => win.edit(&occ, self.head.area.upcast_ref(), Some(Canvas::rect(r.0, r.1, r.2, r.3)), false),
                None if x > GUTTER => {
                    let (x0, cw) = self.col_x(self.head.area.width() as f64, i);
                    let s = midnight(d);
                    win.create(s, s + one_day(), true, Some(self.head.area.upcast_ref()), Some(Canvas::rect(x0, self.days_h, cw, LANE)));
                }
                None => {}
            }
        }
    }

    /// Day names (today's date in a red circle) and the all-day strip.
    fn paint_head(&self, area: &gtk::DrawingArea, cr: &cairo::Context, w: f64, h: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let ink = Ink::new();
        let days = self.days();
        source(cr, &ink.rgba("content_bg"), 1.0);
        let _ = cr.paint();
        let cw = (w - GUTTER) / self.ndays as f64;
        let today = today();
        let (lab, lab2) = (ink.rgba("label"), ink.rgba("label_secondary"));
        for (i, d) in days.iter().enumerate() {
            let x0 = GUTTER + i as f64 * cw;
            let weekend = model::weekday(*d) >= 5;
            if weekend && self.ndays > 1 {
                fill_rect(cr, &ink.rgba("row_alt"), 1.0, x0, 0.0, cw, h);
            }
            if self.days_h == 0.0 {
                continue;
            }
            let name = layout(area, &ink, &strftime(*d, "%a"), "text_body", 400, None, pango::Alignment::Left, 1.0);
            let num = layout(area, &ink, &d.day().to_string(), "text_body", if *d == today { 600 } else { 400 }, None, pango::Alignment::Left, 1.0);
            let (nw, nh) = (text_w(&num), text_h(&num));
            let ty = (self.days_h - nh) / 2.0;
            // right-aligned like macOS: "Wed (30)"
            let right = x0 + cw - 10.0;
            let col = if self.ndays == 1 || !weekend { &lab } else { &lab2 };
            if *d == today {
                today_circle(cr, &ink, right - nw / 2.0, self.days_h / 2.0, nw.max(nh) / 2.0 + 4.0);
                show(cr, &num, right - nw, ty, &ink.rgba("label_on_accent"), 1.0);
            } else {
                show(cr, &num, right - nw, ty, col, 1.0);
            }
            show(cr, &name, right - nw - if *d == today { 10.0 } else { 5.0 } - text_w(&name), ty, col, 1.0);
        }
        let sep = ink.rgba("separator");
        for i in 0..=self.ndays {
            fill_rect(cr, &sep, 1.0, (GUTTER + i as f64 * cw).floor(), self.days_h, 1.0, h - self.days_h);
        }
        fill_rect(cr, &sep, 1.0, GUTTER, self.days_h, w - GUTTER, 1.0);
        let lay = layout(area, &ink, &tr!("all-day"), "text_small", 400, Some(GUTTER - 12.0), pango::Alignment::Right, 1.0);
        show(cr, &lay, 2.0, self.days_h + 4.0 + (LANE - text_h(&lay)) / 2.0, &lab2, 1.0);
        // all-day and multi-day bars
        let sel = win.selected_key();
        let drag = self.head.active_drag();
        for (occ, s, e, lane) in self.bars.borrow().iter() {
            if *lane >= 6 {
                continue;
            }
            let (mut s, mut e) = (*s, *e);
            if let Some(d) = drag.as_ref().filter(|d| d.occ == *occ) {
                let ds = d.ds.num_seconds().div_euclid(86400);
                s = (s + ds).max(0);
                e = (e + ds).min(self.ndays - 1);
                if e < s {
                    continue;
                }
            }
            let x = GUTTER + s as f64 * cw + 2.0;
            let y = self.days_h + 4.0 + *lane as f64 * LANE;
            let bw = (e - s + 1) as f64 * cw - 4.0;
            draw_bar(area, cr, &ink, &win.calendar_color(occ), x, y + 1.0, bw, LANE - 2.0, occ, sel.as_ref() == Some(&occ.key()), true, true, true);
            self.head.add_hit((x, y, bw, LANE), occ);
        }
    }
}

// -- month ------------------------------------------------------------------------------------
enum Item {
    Bar(usize),
    Pill(Occurrence),
}

/// 6 x 7 days; all-day / multi-day events as filled bars, timed ones as a
/// coloured dot, title and time; "N more" when a day overflows.
pub struct MonthView {
    pub canvas: Rc<Canvas>,
    dates: RefCell<Vec<NaiveDate>>,
    occs: RefCell<Rc<Vec<Occurrence>>>,
    /// [(x, y, w, h, date)]
    more: RefCell<Vec<(Rect, NaiveDate)>>,
    target: Cell<Option<i64>>,
    win: Weak<CalendarWindow>,
}

fn step_on_scroll(area: &gtk::DrawingArea, win: Weak<CalendarWindow>) {
    let sc = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::VERTICAL | gtk::EventControllerScrollFlags::DISCRETE);
    sc.connect_scroll(move |_, _, dy| {
        if let Some(w) = win.upgrade() {
            w.step(if dy > 0.0 { 1 } else { -1 });
        }
        glib::Propagation::Stop
    });
    area.add_controller(sc);
}

impl MonthView {
    pub fn new(win: Weak<CalendarWindow>) -> Rc<Self> {
        let canvas = Canvas::new(win.clone());
        canvas.area.set_vexpand(true);
        canvas.area.set_hexpand(true);
        canvas.area.add_css_class("cal-month");
        let m = Rc::new(Self { canvas, dates: RefCell::default(), occs: RefCell::default(), more: RefCell::default(), target: Cell::new(None), win: win.clone() });
        let w = Rc::downgrade(&m);
        m.canvas.area.set_draw_func(move |a, cr, width, height| {
            if let Some(m) = w.upgrade() {
                m.canvas.hits.borrow_mut().clear();
                m.paint(a, cr, width as f64, height as f64);
            }
        });
        let (w1, w2) = (Rc::downgrade(&m), Rc::downgrade(&m));
        drag_setup(
            &m.canvas,
            false,
            move |x0, y0, dx, dy, _| match w1.upgrade() {
                Some(m) => (m.cell(x0 + dx, y0 + dy) as i64 - m.cell(x0, y0) as i64, 0),
                None => (0, 0),
            },
            |occ, _| Some(occ.end),
            move || {
                if let Some(m) = w2.upgrade() {
                    let d = m.canvas.drag.borrow().clone();
                    m.target.set(d.filter(|d| d.active).map(|d| m.cell(d.x0, d.y0) as i64 + d.ds.num_seconds().div_euclid(86400)));
                    m.canvas.area.queue_draw();
                }
            },
        );
        let w = Rc::downgrade(&m);
        clicks(&m.canvas.area, move |b, n, x, y| {
            if let Some(m) = w.upgrade() {
                m.pressed(b, n, x, y);
            }
        });
        step_on_scroll(&m.canvas.area, win);
        m
    }

    pub fn range(&self) -> (DT, DT) {
        let Some(win) = self.win.upgrade() else { return Default::default() };
        let d = win.date();
        let dates = model::month_grid(d.year(), d.month(), win.first_weekday());
        let r = (midnight(dates[0]), midnight(dates[41]) + one_day());
        self.dates.replace(dates);
        r
    }

    pub fn refresh(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let (a, b) = self.range();
        self.occs.replace(win.occurrences(a, b));
        self.target.set(None);
        self.canvas.area.queue_draw();
    }

    fn cell(&self, x: f64, y: f64) -> usize {
        let (w, h) = (self.canvas.area.width() as f64, self.canvas.area.height() as f64);
        let (cw, ch) = (w / 7.0, (h - MONTH_HEAD) / 6.0);
        let c = (x / cw).floor().clamp(0.0, 6.0) as usize;
        let r = ((y - MONTH_HEAD) / ch).floor().clamp(0.0, 5.0) as usize;
        r * 7 + c
    }

    fn pressed(&self, button: u32, n: i32, x: f64, y: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let more = self.more.borrow().iter().find(|((mx, my, mw, mh), _)| *mx <= x && x < mx + mw && *my <= y && y < my + mh).map(|m| m.1);
        if let Some(d) = more.filter(|_| button == 1) {
            win.go(d, Some("day"));
            return;
        }
        let found = self.canvas.hit(x, y);
        if y < MONTH_HEAD {
            return;
        }
        let d = self.dates.borrow().get(self.cell(x, y)).copied().unwrap_or_else(|| win.date());
        if button == 3 {
            match found {
                Some((occ, _)) => {
                    win.select(Some(occ.clone()));
                    win.event_menu(&occ, self.canvas.area.upcast_ref(), x, y);
                }
                None => win.empty_menu(self.canvas.area.upcast_ref(), x, y, midnight(d), true),
            }
            return;
        }
        if button != 1 {
            return;
        }
        if n == 1 {
            win.select(found.map(|f| f.0));
        } else if n == 2 {
            match found {
                Some((occ, r)) => win.edit(&occ, self.canvas.area.upcast_ref(), Some(Canvas::rect(r.0, r.1, r.2, r.3)), false),
                None => {
                    let s = midnight(d);
                    let i = self.cell(x, y);
                    let (cw, ch) = (self.canvas.area.width() as f64 / 7.0, (self.canvas.area.height() as f64 - MONTH_HEAD) / 6.0);
                    win.create(s, s + one_day(), true, Some(self.canvas.area.upcast_ref()), Some(Canvas::rect((i % 7) as f64 * cw, MONTH_HEAD + (i / 7) as f64 * ch, cw, ch)));
                }
            }
        }
    }

    fn limit(items: &BTreeMap<usize, Item>, avail: usize) -> usize {
        match items.keys().max() {
            Some(m) if *m >= avail => avail - 1,
            _ => avail,
        }
    }

    fn paint(&self, area: &gtk::DrawingArea, cr: &cairo::Context, w: f64, h: f64) {
        let Some(win) = self.win.upgrade() else { return };
        self.more.borrow_mut().clear();
        if self.dates.borrow().is_empty() {
            self.range();
        }
        let dates = self.dates.borrow().clone();
        let ink = Ink::new();
        source(cr, &ink.rgba("content_bg"), 1.0);
        let _ = cr.paint();
        let (cw, ch) = (w / 7.0, (h - MONTH_HEAD) / 6.0);
        let (lab, lab2, lab3, sep) = (ink.rgba("label"), ink.rgba("label_secondary"), ink.rgba("label_tertiary"), ink.rgba("separator"));
        let today = today();
        let month = win.date().month();
        for (c, d) in dates.iter().take(7).enumerate() {
            if model::weekday(*d) >= 5 {
                fill_rect(cr, &ink.rgba("row_alt"), 1.0, c as f64 * cw, 0.0, cw, h);
            }
            let lay = layout(area, &ink, &strftime(*d, "%a"), "text_body", 400, Some(cw - 10.0), pango::Alignment::Right, 1.0);
            show(cr, &lay, c as f64 * cw, (MONTH_HEAD - text_h(&lay)) / 2.0 + 2.0, &lab, 1.0);
        }
        if let Some(t) = self.target.get().filter(|t| (0..42).contains(t)) {
            // the drop target of a drag
            fill_rect(cr, &ink.rgba("accent"), 0.12, (t % 7) as f64 * cw, MONTH_HEAD + (t / 7) as f64 * ch, cw, ch);
        }
        for r in 0..6 {
            fill_rect(cr, &sep, 1.0, 0.0, (MONTH_HEAD + r as f64 * ch).floor(), w, 1.0);
        }
        for c in 1..7 {
            fill_rect(cr, &sep, 1.0, (c as f64 * cw).floor(), MONTH_HEAD, 1.0, h - MONTH_HEAD);
        }
        let num_h = 26.0;
        let sel = win.selected_key();
        let drag = self.canvas.active_drag();
        let occs_all = self.occs.borrow().clone();
        let line = LANE - 2.0;
        for r in 0..6 {
            let row = &dates[r * 7..r * 7 + 7];
            let y0 = MONTH_HEAD + r as f64 * ch;
            // day numbers
            for (c, d) in row.iter().enumerate() {
                let text = if d.day() == 1 { format!("{} {}", strftime(*d, "%b"), d.day()) } else { d.day().to_string() };
                let lay = layout(area, &ink, &text, "text_body", if *d == today { 600 } else { 400 }, None, pango::Alignment::Left, 1.0);
                let (tw, th) = (text_w(&lay), text_h(&lay));
                let xr = (c + 1) as f64 * cw - 8.0;
                if *d == today {
                    let rr = (th / 2.0 + 3.0).max((tw + 10.0) / 2.0);
                    source(cr, &ink.rgba("destructive"), 1.0);
                    rrect(cr, xr - tw - (2.0 * rr - tw) / 2.0 - 1.0, y0 + 4.0, 2.0 * rr, 2.0 * (th / 2.0 + 3.0), rr);
                    let _ = cr.fill();
                    show(cr, &lay, xr - tw - 1.0, y0 + 7.0, &ink.rgba("label_on_accent"), 1.0);
                } else {
                    show(cr, &lay, xr - tw, y0 + 7.0, if d.month() == month { &lab } else { &lab3 }, 1.0);
                }
            }
            // events of this week
            let (ra, rb) = (midnight(row[0]), midnight(row[6]) + one_day());
            let occs: Vec<Occurrence> = occs_all.iter().filter(|o| o.start < rb && (o.end > ra || (o.start == o.end && o.end >= ra))).filter(|o| drag.as_ref().is_none_or(|d| d.occ != **o)).cloned().collect();
            let multi: Vec<Occurrence> = occs.iter().filter(|o| model::is_multiday(o)).cloned().collect();
            let bars = model::lanes(&multi, row[0], 7);
            let mut slots: Vec<BTreeMap<usize, Item>> = (0..7).map(|_| BTreeMap::new()).collect();
            for (bi, (_, s, e, lane)) in bars.iter().enumerate() {
                for c in *s..=*e {
                    slots[c as usize].insert(*lane, Item::Bar(bi));
                }
            }
            for o in occs.iter().filter(|o| !model::is_multiday(o)) {
                let c = (model::day_start(o.start) - ra).num_seconds().div_euclid(86400);
                if (0..7).contains(&c) {
                    let mut k = 0;
                    while slots[c as usize].contains_key(&k) {
                        k += 1;
                    }
                    slots[c as usize].insert(k, Item::Pill(o.clone()));
                }
            }
            let avail = (((ch - num_h - 2.0) / (LANE - 2.0)).floor() as usize).max(1);
            let mut labels = Vec::new();
            for c in 0..7 {
                let limit = Self::limit(&slots[c], avail);
                let x = c as f64 * cw;
                for (k, item) in slots[c].iter() {
                    if *k >= limit {
                        continue;
                    }
                    let y = y0 + num_h + *k as f64 * line;
                    match item {
                        Item::Pill(o) => self.pill(area, cr, &ink, &win, x + 3.0, y, cw - 6.0, line - 1.0, o, sel.as_ref() == Some(&o.key())),
                        Item::Bar(bi) => {
                            let (o, s, e, lane) = &bars[*bi];
                            let ci = c as i64;
                            let prev_vis = ci > *s && *lane < Self::limit(&slots[c - 1], avail);
                            let next_vis = ci < *e && *lane < Self::limit(&slots[c + 1], avail);
                            let bx = x + if prev_vis { 0.0 } else { 3.0 };
                            let bw = cw - if prev_vis { 0.0 } else { 3.0 } - if next_vis { 0.0 } else { 3.0 };
                            draw_bar(area, cr, &ink, &win.calendar_color(o), bx, y + 1.0, bw, line - 2.0, o, sel.as_ref() == Some(&o.key()), false, !prev_vis, !next_vis);
                            self.canvas.add_hit((bx, y, bw, line), o);
                            if !prev_vis {
                                // the title runs over the following visible pieces
                                let mut span = 1;
                                while ci + span <= *e && *lane < Self::limit(&slots[c + span as usize], avail) {
                                    span += 1;
                                }
                                labels.push((bx, y, span as f64 * cw - 6.0, line, o.clone()));
                            }
                        }
                    }
                }
            }
            for (bx, y, lw, lh, o) in labels {
                let col = if sel.as_ref() == Some(&o.key()) { ink.rgba("label_on_accent") } else { event_colors(&ink, &win.calendar_color(&o)).2 };
                let title = if o.event.summary.is_empty() { tr!("New Event") } else { o.event.summary.clone() };
                let lay = small(area, &ink, &title, 600, Some(lw - 10.0));
                show(cr, &lay, bx + 6.0, y + 1.0 + (lh - 2.0 - text_h(&lay)) / 2.0, &col, 1.0);
            }
            for c in 0..7 {
                let limit = Self::limit(&slots[c], avail);
                let hidden = slots[c].keys().filter(|k| **k >= limit).count();
                if hidden > 0 {
                    let (x, y) = (c as f64 * cw, y0 + num_h + limit as f64 * line);
                    let lay = small(area, &ink, &fmt(&trn!("{n} more", "{n} more", hidden), &[("n", &hidden.to_string())]), 600, Some(cw - 14.0));
                    show(cr, &lay, x + 8.0, y + (line - text_h(&lay)) / 2.0, &lab2, 1.0);
                    self.more.borrow_mut().push(((x, y, cw, line), row[c]));
                }
            }
        }
        if let (Some(d), Some(c)) = (drag, self.target.get().filter(|c| (0..42).contains(c))) {
            let o = d.occ;
            let (x, y) = ((c % 7) as f64 * cw, MONTH_HEAD + (c / 7) as f64 * ch + num_h);
            if model::is_multiday(&o) {
                draw_bar(area, cr, &ink, &win.calendar_color(&o), x + 3.0, y + 1.0, cw - 6.0, line - 2.0, &o, true, true, true, true);
            } else {
                self.pill(area, cr, &ink, &win, x + 3.0, y, cw - 6.0, line - 1.0, &o, true);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn pill(&self, area: &gtk::DrawingArea, cr: &cairo::Context, ink: &Ink, win: &CalendarWindow, x: f64, y: f64, w: f64, h: f64, occ: &Occurrence, selected: bool) {
        let (solid, _, _) = event_colors(ink, &win.calendar_color(occ));
        if selected {
            source(cr, &solid, 1.0);
            rrect(cr, x, y, w, h, ink.px("r_menu_row"));
            let _ = cr.fill();
        }
        let dot = if selected { ink.rgba("label_on_accent") } else { solid };
        source(cr, &dot, 1.0);
        cr.arc(x + 7.0, y + h / 2.0, 3.5, 0.0, 2.0 * PI);
        let _ = cr.fill();
        let t = small(area, ink, &win.fmt_time(occ.start.time()), 400, None);
        let tw = text_w(&t);
        let show_time = w > tw + 60.0;
        let title = if occ.event.summary.is_empty() { tr!("New Event") } else { occ.event.summary.clone() };
        let name = small(area, ink, &title, 400, Some(w - 18.0 - if show_time { tw + 6.0 } else { 0.0 }));
        let fg = if selected { ink.rgba("label_on_accent") } else { ink.rgba("label") };
        show(cr, &name, x + 15.0, y + (h - text_h(&name)) / 2.0, &fg, 1.0);
        if show_time {
            show(cr, &t, x + w - tw - 4.0, y + (h - text_h(&t)) / 2.0, &if selected { ink.rgba("label_on_accent") } else { ink.rgba("label_secondary") }, 1.0);
        }
        self.canvas.add_hit((x, y, w, h), occ);
    }
}

// -- year -------------------------------------------------------------------------------------
/// Twelve small months; a click on a day opens it in Day view, on a
/// month's name in Month view. Days with events get a soft disc.
pub struct YearView {
    pub canvas: Rc<Canvas>,
    busy: RefCell<HashSet<NaiveDate>>,
    /// [(rect, date or month-date, is a day)]
    cells: RefCell<Vec<(Rect, NaiveDate, bool)>>,
    win: Weak<CalendarWindow>,
}

impl YearView {
    pub fn new(win: Weak<CalendarWindow>) -> Rc<Self> {
        let canvas = Canvas::new(win.clone());
        canvas.area.set_vexpand(true);
        canvas.area.set_hexpand(true);
        canvas.area.add_css_class("cal-year");
        let y = Rc::new(Self { canvas, busy: RefCell::default(), cells: RefCell::default(), win: win.clone() });
        let w = Rc::downgrade(&y);
        y.canvas.area.set_draw_func(move |a, cr, width, height| {
            if let Some(y) = w.upgrade() {
                y.paint(a, cr, width as f64, height as f64);
            }
        });
        let w = Rc::downgrade(&y);
        clicks(&y.canvas.area, move |b, _, px, py| {
            let Some(y) = w.upgrade() else { return };
            if b != 1 {
                return;
            }
            let found = y.cells.borrow().iter().find(|((cx, cy, cw, chh), _, _)| *cx <= px && px < cx + cw && *cy <= py && py < cy + chh).map(|c| (c.1, c.2));
            if let (Some((d, day)), Some(win)) = (found, y.win.upgrade()) {
                win.go(d, Some(if day { "day" } else { "month" }));
            }
        });
        step_on_scroll(&y.canvas.area, win);
        y
    }

    pub fn range(&self) -> (DT, DT) {
        let y = self.win.upgrade().map(|w| w.date().year()).unwrap_or(2000);
        (midnight(crate::ics::date(y, 1, 1)), midnight(crate::ics::date(y + 1, 1, 1)))
    }

    pub fn refresh(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let (a, b) = self.range();
        let mut busy = HashSet::new();
        for o in win.occurrences(a, b).iter() {
            let mut d = o.start.date();
            let last = if o.end > o.start { (o.end - Duration::microseconds(1)).date() } else { d };
            while d <= last && d.year() == a.year() {
                busy.insert(d);
                d += one_day();
            }
        }
        self.busy.replace(busy);
        self.canvas.area.queue_draw();
    }

    fn paint(&self, area: &gtk::DrawingArea, cr: &cairo::Context, w: f64, h: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let mut cells = Vec::new();
        let ink = Ink::new();
        source(cr, &ink.rgba("content_bg"), 1.0);
        let _ = cr.paint();
        let ncols = if w >= h * 1.1 { 4 } else { 3 };
        let nrows = 12 / ncols;
        let pad = 18.0;
        let (mw, mh) = ((w - pad * (ncols + 1) as f64) / ncols as f64, (h - pad * (nrows + 1) as f64) / nrows as f64);
        let today = today();
        let year = win.date().year();
        let (lab, lab2, red) = (ink.rgba("label"), ink.rgba("label_secondary"), ink.rgba("destructive"));
        let busy = self.busy.borrow();
        for m in 0..12u32 {
            let mx = pad + (m % ncols) as f64 * (mw + pad);
            let my = pad + (m / ncols) as f64 * (mh + pad);
            let first = crate::ics::date(year, m + 1, 1);
            let title = layout(area, &ink, &strftime(first, "%B"), "text_title", 700, None, pango::Alignment::Left, 1.0);
            show(cr, &title, mx + 4.0, my, if (year, m + 1) == (today.year(), today.month()) { &red } else { &lab }, 1.0);
            let th = text_h(&title) + 6.0;
            cells.push(((mx, my, mw, th), first, false));
            let dates = model::month_grid(year, m + 1, win.first_weekday());
            let (cellw, cellh) = (mw / 7.0, (mh - th) / 7.0);
            for (c, d) in dates.iter().take(7).enumerate() {
                let lay = layout(area, &ink, &initial(&strftime(*d, "%a"), 1), "text_small", 600, Some(cellw), pango::Alignment::Center, 1.0);
                show(cr, &lay, mx + c as f64 * cellw, my + th + (cellh - text_h(&lay)) / 2.0, &lab2, 1.0);
            }
            for (i, d) in dates.iter().enumerate() {
                if d.month() != m + 1 {
                    continue;
                }
                let (cx, cy) = (mx + (i % 7) as f64 * cellw, my + th + (i / 7 + 1) as f64 * cellh);
                let r = cellw.min(cellh) / 2.0 - 1.0;
                if *d == today {
                    today_circle(cr, &ink, cx + cellw / 2.0, cy + cellh / 2.0, r);
                } else if busy.contains(d) {
                    source(cr, &ink.rgba("item_selected_bg"), 1.0);
                    cr.arc(cx + cellw / 2.0, cy + cellh / 2.0, r, 0.0, 2.0 * PI);
                    let _ = cr.fill();
                }
                let lay = layout(area, &ink, &d.day().to_string(), "text_small", if *d == today { 600 } else { 400 }, Some(cellw), pango::Alignment::Center, 1.0);
                let col = if *d == today { ink.rgba("label_on_accent") } else if model::weekday(*d) < 5 { lab } else { lab2 };
                show(cr, &lay, cx, cy + (cellh - text_h(&lay)) / 2.0, &col, 1.0);
                cells.push(((cx, cy, cellw, cellh), *d, true));
            }
        }
        self.cells.replace(cells);
    }
}

// -- sidebar mini month -----------------------------------------------------------------------
/// The sidebar's small month: ‹ › change the month shown, a click on a day
/// shows it in the main view; the shown day / week is highlighted.
pub struct MiniMonth {
    pub root: gtk::Box,
    month: Cell<NaiveDate>,
    title: gtk::Label,
    canvas: Rc<Canvas>,
    win: Weak<CalendarWindow>,
}

impl MiniMonth {
    pub fn new(win: Weak<CalendarWindow>, date: NaiveDate) -> Rc<Self> {
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["cal-mini"]).build();
        let head = gtk::Box::builder().css_classes(["cal-mini-head"]).build();
        let title = gtk::Label::builder().xalign(0.0).hexpand(true).css_classes(["cal-mini-title"]).build();
        head.append(&title);
        root.append(&head);
        let canvas = Canvas::new(win.clone());
        canvas.area.set_content_height(7 * 22);
        canvas.area.set_hexpand(true);
        root.append(&canvas.area);
        let m = Rc::new(Self { root, month: Cell::new(date.with_day(1).unwrap()), title, canvas, win });
        for (icon, step) in [("go-previous-symbolic", -1), ("go-next-symbolic", 1)] {
            let b = gtk::Button::builder().icon_name(icon).css_classes(["cal-mini-nav"]).can_focus(false).valign(gtk::Align::Center).build();
            let w = Rc::downgrade(&m);
            b.connect_clicked(move |_| {
                if let Some(m) = w.upgrade() {
                    m.shift(step);
                }
            });
            head.append(&b);
        }
        let w = Rc::downgrade(&m);
        m.canvas.area.set_draw_func(move |a, cr, width, height| {
            if let Some(m) = w.upgrade() {
                m.paint(a, cr, width as f64, height as f64);
            }
        });
        let w = Rc::downgrade(&m);
        clicks(&m.canvas.area, move |b, _, x, y| {
            if let Some(m) = w.upgrade() {
                m.pressed(b, x, y);
            }
        });
        m.sync_title();
        m
    }

    pub fn shift(&self, step: i32) {
        let m = self.month.get();
        let total = m.month0() as i32 + step;
        self.month.set(crate::ics::date(m.year() + total.div_euclid(12), total.rem_euclid(12) as u32 + 1, 1));
        self.sync_title();
        self.canvas.area.queue_draw();
    }

    /// Show the month of the main view's date.
    pub fn follow(&self) {
        if let Some(w) = self.win.upgrade() {
            self.month.set(w.date().with_day(1).unwrap());
        }
        self.sync_title();
        self.canvas.area.queue_draw();
    }

    fn sync_title(&self) {
        self.title.set_label(&strftime(self.month.get(), "%B %Y"));
    }

    fn pressed(&self, button: u32, x: f64, y: f64) {
        let Some(win) = self.win.upgrade() else { return };
        if button != 1 {
            return;
        }
        let (w, h) = (self.canvas.area.width() as f64, self.canvas.area.height() as f64);
        let (cw, chh) = (w / 7.0, h / 7.0);
        let (r, c) = ((y / chh).floor() as i64 - 1, (x / cw).floor().min(6.0) as i64);
        if r < 0 {
            return;
        }
        let m = self.month.get();
        let dates = model::month_grid(m.year(), m.month(), win.first_weekday());
        win.go(dates[((r * 7 + c) as usize).min(41)], None);
    }

    fn paint(&self, area: &gtk::DrawingArea, cr: &cairo::Context, w: f64, h: f64) {
        let Some(win) = self.win.upgrade() else { return };
        let ink = Ink::new();
        let m = self.month.get();
        let dates = model::month_grid(m.year(), m.month(), win.first_weekday());
        let (cw, chh) = (w / 7.0, h / 7.0);
        let (lab, lab2, lab3) = (ink.rgba("label"), ink.rgba("label_secondary"), ink.rgba("label_tertiary"));
        let today = today();
        for (c, d) in dates.iter().take(7).enumerate() {
            let lay = layout(area, &ink, &initial(&strftime(*d, "%a"), 2), "text_small", 600, Some(cw), pango::Alignment::Center, 0.9);
            show(cr, &lay, c as f64 * cw, (chh - text_h(&lay)) / 2.0, &lab2, 1.0);
        }
        // the shown period: a soft band (week) or disc (day)
        let (view, focus) = (win.view(), win.date());
        let fw = win.first_weekday();
        for (i, d) in dates.iter().enumerate() {
            let (cx, cy) = ((i % 7) as f64 * cw, (i / 7 + 1) as f64 * chh);
            let shown = (view == "day" && *d == focus) || (view == "week" && model::week_start(*d, fw) == model::week_start(focus, fw));
            if shown {
                source(cr, &ink.rgba("sidebar_selected"), 1.0);
                if view == "week" {
                    if i % 7 == 0 {
                        rrect(cr, cx + 1.0, cy + 1.0, cw * 7.0 - 2.0, chh - 2.0, (chh - 2.0) / 2.0);
                        let _ = cr.fill();
                    }
                } else {
                    rrect(cr, cx + (cw - chh) / 2.0 + 1.0, cy + 1.0, chh - 2.0, chh - 2.0, (chh - 2.0) / 2.0);
                    let _ = cr.fill();
                }
            }
        }
        for (i, d) in dates.iter().enumerate() {
            let (cx, cy) = ((i % 7) as f64 * cw, (i / 7 + 1) as f64 * chh);
            if *d == today {
                today_circle(cr, &ink, cx + cw / 2.0, cy + chh / 2.0, chh / 2.0 - 1.0);
            }
            let lay = layout(area, &ink, &d.day().to_string(), "text_small", if *d == today { 600 } else { 400 }, Some(cw), pango::Alignment::Center, 0.95);
            let col = if *d == today { ink.rgba("label_on_accent") } else if d.month() == m.month() { lab } else { lab3 };
            show(cr, &lay, cx, cy + (chh - text_h(&lay)) / 2.0, &col, 1.0);
        }
    }
}
