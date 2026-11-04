//! Clock's Stopwatch and Timers pages (sonata2/clock/pages.py). The state
//! lives in timers.json: both keep going with Clock closed, and the menu
//! bar rings the timer. Nothing ticks while a page isn't on screen or
//! nothing runs: the figures follow the display's frames only then.

use crate::timers::{self as T, Stopwatch, Timer, TimerState};
use gtk::{cairo, glib, prelude::*};
use sonata_i18n::{fmt, tr, trp};
use sonata_ui::controls;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

/// The round buttons' distance from the window's sides.
const SIDE: i32 = 48;

/// timers.json, shared by both pages; reread when it changes on disk (the
/// menu bar sets a timer that rang back to idle).
pub struct State {
    pub sw: RefCell<Stopwatch>,
    pub tm: RefCell<Timer>,
    listeners: RefCell<Vec<Rc<dyn Fn()>>>,
    monitor: RefCell<Option<gtk::gio::FileMonitor>>,
}

impl State {
    pub fn new() -> Rc<Self> {
        let (sw, tm) = T::load();
        let s = Rc::new(State { sw: RefCell::new(sw), tm: RefCell::new(tm), listeners: RefCell::default(), monitor: RefCell::default() });
        let weak = Rc::downgrade(&s);
        *s.monitor.borrow_mut() = sonata_config::watch_path(&T::path(), move || {
            if let Some(s) = weak.upgrade() {
                s.reload();
            }
        });
        s
    }

    fn listen<F: Fn() + 'static>(&self, f: F) {
        self.listeners.borrow_mut().push(Rc::new(f));
    }

    fn notify(&self) {
        let ls = self.listeners.borrow().clone();
        for f in ls {
            f();
        }
    }

    pub fn reload(&self) {
        let (sw, tm) = T::load();
        if sw != *self.sw.borrow() || tm != *self.tm.borrow() {
            *self.sw.borrow_mut() = sw;
            *self.tm.borrow_mut() = tm;
            self.notify();
        }
    }

    pub fn save(&self) {
        if let Err(e) = T::save(&self.sw.borrow(), &self.tm.borrow()) {
            eprintln!("sonata2-rs clock: timers.json: {e}");
        }
        self.notify();
    }
}

fn buttons(left: &gtk::Button, right: &gtk::Button) -> gtk::Box {
    let row = gtk::Box::builder().margin_start(SIDE).margin_end(SIDE).build();
    row.append(left);
    row.append(&gtk::Box::builder().hexpand(true).build());
    row.append(right);
    row
}

/// Two-digit spin button (the alarm's time, the timer's hours / minutes / seconds).
pub fn two_digit_spin(value: f64, upper: f64) -> gtk::SpinButton {
    let s = gtk::SpinButton::with_range(0.0, upper, 1.0);
    s.set_wrap(true);
    s.set_numeric(true);
    s.set_value(value);
    s.connect_output(|sp| {
        sp.set_text(&format!("{:02}", sp.value() as i64));
        glib::Propagation::Stop
    });
    s.set_orientation(gtk::Orientation::Vertical);
    s.set_width_chars(2); // two digits, never wider (they wrapped)
    s.set_max_width_chars(2);
    s
}

/// Follows the display's frames while running and on screen.
struct Ticking {
    id: RefCell<Option<gtk::TickCallbackId>>,
}

impl Ticking {
    fn new() -> Self {
        Ticking { id: RefCell::new(None) }
    }

    fn set(&self, root: &gtk::Widget, on: bool, update: Rc<dyn Fn()>) {
        let mut id = self.id.borrow_mut();
        if on && id.is_none() {
            *id = Some(root.add_tick_callback(move |_, _| {
                update();
                glib::ControlFlow::Continue
            }));
        } else if !on {
            if let Some(i) = id.take() {
                i.remove();
            }
        }
    }
}

// -- Stopwatch -----------------------------------------------------------------------------
pub struct StopwatchPage {
    pub state: Rc<State>,
    pub root: gtk::Box,
    big: gtk::Label,
    pub lap_btn: gtk::Button,
    pub go_btn: gtk::Button,
    laps: gtk::ListBox,
    laps_shown: RefCell<Option<(Vec<u64>, bool)>>,
    current: RefCell<Option<gtk::Label>>,
    ticking: Ticking,
    me: Weak<StopwatchPage>,
}

impl StopwatchPage {
    pub fn new(state: Rc<State>) -> Rc<Self> {
        let page = Rc::new_cyclic(|me: &Weak<StopwatchPage>| {
            let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).vexpand(true).build();
            root.add_css_class("ck-main");
            let big = gtk::Label::builder().margin_top(48).margin_bottom(20).build();
            big.add_css_class("ck-big");
            root.append(&big);
            let w = me.clone();
            let lap_btn = controls::circle_button(&tr!("Lap"), move || {
                if let Some(p) = w.upgrade() {
                    p.lap_or_reset();
                }
            }, "grey");
            let w = me.clone();
            let go_btn = controls::circle_button(&tr!("Start"), move || {
                if let Some(p) = w.upgrade() {
                    p.start_or_stop();
                }
            }, "green");
            root.append(&buttons(&lap_btn, &go_btn));
            let laps = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::None).build();
            laps.add_css_class("ck-laps");
            root.append(&gtk::ScrolledWindow::builder().child(&laps).vexpand(true).margin_top(16).hscrollbar_policy(gtk::PolicyType::Never).build());
            StopwatchPage {
                state: state.clone(),
                root,
                big,
                lap_btn,
                go_btn,
                laps,
                laps_shown: RefCell::new(None),
                current: RefCell::new(None),
                ticking: Ticking::new(),
                me: me.clone(),
            }
        });
        for sig in [true, false] {
            let w = Rc::downgrade(&page);
            let f = move |_: &gtk::Box| {
                if let Some(p) = w.upgrade() {
                    p.frames(if sig { None } else { Some(false) });
                }
            };
            if sig {
                page.root.connect_map(f);
            } else {
                page.root.connect_unmap(f);
            }
        }
        let w = Rc::downgrade(&page);
        state.listen(move || {
            if let Some(p) = w.upgrade() {
                p.refresh();
            }
        });
        page.refresh();
        page
    }

    fn running(&self) -> bool {
        self.state.sw.borrow().running
    }

    fn frames(&self, on: Option<bool>) {
        let on = on.unwrap_or_else(|| self.running() && self.root.is_mapped());
        let w = self.me.clone();
        self.ticking.set(self.root.upcast_ref(), on, Rc::new(move || {
            if let Some(p) = w.upgrade() {
                p.update();
            }
        }));
    }

    pub fn start_or_stop(&self) {
        {
            let mut sw = self.state.sw.borrow_mut();
            if sw.running {
                T::sw_stop(&mut sw, T::now());
            } else {
                T::sw_start(&mut sw, T::now());
            }
        }
        self.state.save();
    }

    pub fn lap_or_reset(&self) {
        {
            let mut sw = self.state.sw.borrow_mut();
            if sw.running {
                T::sw_lap(&mut sw, T::now());
            } else {
                T::sw_reset(&mut sw);
            }
        }
        self.state.save();
    }

    fn refresh(&self) {
        let (running, started) = {
            let sw = self.state.sw.borrow();
            (sw.running, sw.running || sw.before > 0.0)
        };
        self.go_btn.set_label(&if running { tr!("Stop") } else { tr!("Start") });
        controls::set_tone(&self.go_btn, if running { "red" } else { "green" });
        self.lap_btn.set_label(&if running || !started { tr!("Lap") } else { trp!("stopwatch", "Reset") });
        self.lap_btn.set_sensitive(started);
        self.fill_laps();
        self.update();
        self.frames(None);
    }

    fn fill_laps(&self) {
        let (laps, started) = {
            let sw = self.state.sw.borrow();
            (sw.laps.clone(), sw.running || sw.before > 0.0)
        };
        let key = (laps.iter().map(|l| l.to_bits()).collect::<Vec<_>>(), started);
        if self.laps_shown.borrow().as_ref() == Some(&key) {
            return;
        }
        *self.laps_shown.borrow_mut() = Some(key);
        while let Some(row) = self.laps.first_child() {
            self.laps.remove(&row);
        }
        *self.current.borrow_mut() = None;
        if !started {
            return;
        }
        let (best, worst) = T::best_worst(&laps);
        let (row, time) = lap_row(laps.len() + 1, 0.0); // the lap under way, at the top
        self.laps.append(&row);
        *self.current.borrow_mut() = Some(time);
        for i in (0..laps.len()).rev() {
            let (row, _) = lap_row(i + 1, laps[i]);
            if Some(i) == best {
                row.add_css_class("best");
            } else if Some(i) == worst {
                row.add_css_class("worst");
            }
            self.laps.append(&row);
        }
    }

    fn update(&self) {
        let now = T::now();
        let sw = self.state.sw.borrow();
        self.big.set_label(&T::stopwatch_text(T::elapsed(&sw, now)));
        if let Some(t) = self.current.borrow().as_ref() {
            t.set_label(&T::stopwatch_text(T::current_lap(&sw, now)));
        }
    }
}

fn lap_row(n: usize, seconds: f64) -> (gtk::ListBoxRow, gtk::Label) {
    let row_box = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    let name = fmt(&tr!("Lap {n}"), &[("n", &n.to_string())]);
    row_box.append(&gtk::Label::builder().label(name).xalign(0.0).hexpand(true).build());
    let time = gtk::Label::builder().label(T::stopwatch_text(seconds)).xalign(1.0).build();
    row_box.append(&time);
    (gtk::ListBoxRow::builder().child(&row_box).activatable(false).build(), time)
}

// -- Timer ----------------------------------------------------------------------------------
/// What's left of the timer, as an arc from the top (orange on a faint track).
struct Ring {
    area: gtk::DrawingArea,
    fraction: Rc<Cell<f64>>,
}

impl Ring {
    fn new() -> Self {
        let area = gtk::DrawingArea::builder().content_width(240).content_height(240).halign(gtk::Align::Center).build();
        let fraction = Rc::new(Cell::new(1.0));
        let f = fraction.clone();
        area.set_draw_func(move |_, cr, w, h| draw_ring(cr, w as f64, h as f64, f.get()));
        Ring { area, fraction }
    }
}

fn draw_ring(cr: &cairo::Context, w: f64, h: f64, fraction: f64) {
    let line = 7.0;
    let r = w.min(h) / 2.0 - line;
    let (cx, cy) = (w / 2.0, h / 2.0);
    cr.set_line_width(line);
    cr.set_line_cap(cairo::LineCap::Round);
    let track = sonata_ui::rgba("label");
    cr.set_source_rgba(track.red() as f64, track.green() as f64, track.blue() as f64, 0.10);
    cr.arc(cx, cy, r, 0.0, 2.0 * std::f64::consts::PI);
    let _ = cr.stroke();
    if fraction > 0.0 {
        let c = sonata_ui::rgba("sys_orange");
        cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, 1.0);
        let top = -std::f64::consts::FRAC_PI_2;
        cr.arc(cx, cy, r, top, top + 2.0 * std::f64::consts::PI * fraction);
        let _ = cr.stroke();
    }
}

pub struct TimerPage {
    pub state: Rc<State>,
    pub root: gtk::Stack,
    pub spins: Vec<gtk::SpinButton>,
    pub name: gtk::Entry,
    ring: Ring,
    left_lbl: gtk::Label,
    pub sub: gtk::Label,
    pub cancel_btn: gtk::Button,
    pub pause_btn: gtk::Button,
    ticking: Ticking,
    /// the menu bar's 24-hour choice, read on refresh (not on every frame)
    h24: Cell<bool>,
    me: Weak<TimerPage>,
}

impl TimerPage {
    pub fn new(state: Rc<State>) -> Rc<Self> {
        let page = Rc::new_cyclic(|me: &Weak<TimerPage>| {
            let call = |f: fn(&TimerPage)| {
                let w = me.clone();
                move || {
                    if let Some(p) = w.upgrade() {
                        f(&p);
                    }
                }
            };
            let root = gtk::Stack::builder().vexpand(true).build();
            root.add_css_class("ck-main");
            // -- setting a timer up: hours, minutes, seconds; quick choices; a name
            let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(18).margin_top(40).valign(gtk::Align::Start).build();
            let pick = gtk::Box::builder().spacing(10).halign(gtk::Align::Center).build();
            pick.add_css_class("ck-pick");
            let mut spins = Vec::new();
            for (unit, upper) in [(tr!("hours"), 23.0), (tr!("min"), 59.0), (tr!("sec"), 59.0)] {
                let s = two_digit_spin(0.0, upper);
                let cell = gtk::Box::new(gtk::Orientation::Vertical, 2);
                cell.append(&s);
                let u = gtk::Label::new(Some(&unit));
                u.add_css_class("ck-unit");
                cell.append(&u);
                pick.append(&cell);
                spins.push(s);
            }
            col.append(&pick);
            let chips = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).build();
            for secs in T::PRESETS {
                let b = gtk::Button::builder().label(T::duration_text(secs as f64)).can_focus(false).build();
                b.add_css_class("ck-chip");
                let w = me.clone();
                b.connect_clicked(move |_| {
                    if let Some(p) = w.upgrade() {
                        p.start(Some(secs as f64));
                    }
                });
                chips.append(&b);
            }
            col.append(&chips);
            let name = controls::text_field("", &tr!("Timer"));
            let w = me.clone();
            name.connect_activate(move |_| {
                if let Some(p) = w.upgrade() {
                    p.start(None);
                }
            });
            name.set_halign(gtk::Align::Center);
            name.set_size_request(220, -1);
            col.append(&name);
            let w = me.clone();
            let start_btn = controls::circle_button(&tr!("Start"), move || {
                if let Some(p) = w.upgrade() {
                    p.start(None);
                }
            }, "green");
            col.append(&start_btn);
            root.add_named(&col, Some("setup"));
            // -- a timer running or paused
            let run = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(22).margin_top(28).build();
            let over = gtk::Overlay::builder().halign(gtk::Align::Center).build();
            let ring = Ring::new();
            over.set_child(Some(&ring.area));
            let mid = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
            let left_lbl = gtk::Label::new(None);
            left_lbl.add_css_class("ck-ring-time");
            let sub = gtk::Label::new(None);
            sub.add_css_class("ck-ring-sub");
            mid.append(&left_lbl);
            mid.append(&sub);
            over.add_overlay(&mid);
            run.append(&over);
            let cancel_btn = controls::circle_button(&tr!("Cancel"), call(TimerPage::cancel), "grey");
            let pause_btn = controls::circle_button(&tr!("Pause"), call(TimerPage::pause_or_resume), "orange");
            run.append(&buttons(&cancel_btn, &pause_btn));
            root.add_named(&run, Some("run"));
            TimerPage { state: state.clone(), root, spins, name, ring, left_lbl, sub, cancel_btn, pause_btn, ticking: Ticking::new(), h24: Cell::new(true), me: me.clone() }
        });
        let w = Rc::downgrade(&page);
        page.root.connect_map(move |_| {
            if let Some(p) = w.upgrade() {
                p.frames(None);
            }
        });
        let w = Rc::downgrade(&page);
        page.root.connect_unmap(move |_| {
            if let Some(p) = w.upgrade() {
                p.frames(Some(false));
            }
        });
        let w = Rc::downgrade(&page);
        state.listen(move || {
            if let Some(p) = w.upgrade() {
                p.refresh();
            }
        });
        page.refresh();
        page
    }

    fn running(&self) -> bool {
        self.state.tm.borrow().state == TimerState::Running
    }

    fn frames(&self, on: Option<bool>) {
        let on = on.unwrap_or_else(|| self.running() && self.root.is_mapped());
        let w = self.me.clone();
        self.ticking.set(self.root.upcast_ref(), on, Rc::new(move || {
            if let Some(p) = w.upgrade() {
                p.update();
            }
        }));
    }

    fn duration(&self) -> f64 {
        let v: Vec<i64> = self.spins.iter().map(|s| s.value() as i64).collect();
        (v[0] * 3600 + v[1] * 60 + v[2]) as f64
    }

    pub fn start(&self, seconds: Option<f64>) {
        let seconds = seconds.filter(|s| *s > 0.0).unwrap_or_else(|| self.duration());
        if seconds <= 0.0 {
            return;
        }
        let label = self.name.text().trim().to_string();
        T::tm_start(&mut self.state.tm.borrow_mut(), Some(seconds), Some(&label), T::now());
        self.state.save();
    }

    pub fn pause_or_resume(&self) {
        {
            let mut tm = self.state.tm.borrow_mut();
            if tm.state == TimerState::Running {
                T::tm_pause(&mut tm, T::now());
            } else {
                T::tm_resume(&mut tm, T::now());
            }
        }
        self.state.save();
    }

    pub fn cancel(&self) {
        T::tm_cancel(&mut self.state.tm.borrow_mut());
        self.state.save();
    }

    fn refresh(&self) {
        self.h24.set(crate::alarms::h24());
        let tm = self.state.tm.borrow().clone();
        let idle = tm.state == TimerState::Idle;
        self.root.set_visible_child_name(if idle { "setup" } else { "run" });
        if idle {
            // ready again with the last one
            let d = tm.duration as i64;
            for (spin, v) in self.spins.iter().zip([d / 3600, d % 3600 / 60, d % 60]) {
                spin.set_value(v as f64);
            }
            self.name.set_text(&tm.label);
        } else {
            let running = tm.state == TimerState::Running;
            self.pause_btn.set_label(&if running { tr!("Pause") } else { tr!("Resume") });
            controls::set_tone(&self.pause_btn, if running { "orange" } else { "green" });
        }
        self.update();
        self.frames(None);
    }

    fn update(&self) {
        let tm = self.state.tm.borrow();
        if tm.state == TimerState::Idle {
            return;
        }
        let now = T::now();
        self.left_lbl.set_label(&T::timer_text(T::left(&tm, now)));
        self.ring.fraction.set(T::progress(&tm, now));
        self.ring.area.queue_draw();
        let name = if tm.label.is_empty() { T::duration_text(tm.duration) } else { tm.label.clone() };
        if tm.state == TimerState::Running {
            let pattern = if self.h24.get() { "%H:%M" } else { "%-I:%M %p" };
            let end = glib::DateTime::from_unix_local(tm.ends as i64).ok().and_then(|d| d.format(pattern).ok()).map(|s| s.to_string()).unwrap_or_default();
            self.sub.set_label(&format!("{end} · {name}"));
        } else {
            self.sub.set_label(&fmt(&tr!("Paused · {name}"), &[("name", &name)]));
        }
    }
}
