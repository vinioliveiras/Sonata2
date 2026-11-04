//! The Performance widget's graphs when no menu bar lends Control Center's
//! own (statsui.module lives in sonata-topbar, which hosts the Game Bar: it
//! can't be a dependency of it). The same figures, from
//! sonata_backend::stats, read once a second only while one is on screen;
//! a plainer look: the figure's text and a small graph.

use gtk::prelude::*;
use sonata_backend::stats::{self as S, Card, Reading};
use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::path::Path;
use std::rc::Rc;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

struct Machine {
    cards: Vec<Card>,
    kinds: Vec<String>,
}

fn machine() -> &'static Machine {
    static M: OnceLock<Machine> = OnceLock::new();
    M.get_or_init(|| {
        let sys = Path::new("/sys");
        let cards = S::gpu_cards(sys);
        let kinds = S::kinds(&cards, &S::cpu_temp_path(sys));
        Machine { cards, kinds }
    })
}

/// What this machine can show (statsui.kinds()).
pub fn kinds() -> &'static [String] {
    &machine().kinds
}

type Listener = Rc<dyn Fn(&Reading, &BTreeMap<String, Vec<f64>>)>;

struct Work {
    sampler: S::Sampler,
    t: Option<Instant>,
}

/// One reader for every module on screen.
#[derive(Default)]
struct Reader {
    listeners: RefCell<Vec<(u64, Listener)>>,
    next: Cell<u64>,
    history: RefCell<BTreeMap<String, Vec<f64>>>,
    timer: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    work: RefCell<Option<Arc<Mutex<Work>>>>,
}

thread_local! {
    static READER: Rc<Reader> = Rc::default();
}

impl Reader {
    fn subscribe(self: &Rc<Self>, cb: Listener) -> u64 {
        let id = self.next.get() + 1;
        self.next.set(id);
        self.listeners.borrow_mut().push((id, cb));
        if self.timer.borrow().is_none() {
            self.tick();
            let me = Rc::downgrade(self);
            let t = glib::timeout_add_local(Duration::from_millis(S::INTERVAL_MS), move || match me.upgrade() {
                Some(me) => {
                    me.tick();
                    glib::ControlFlow::Continue
                }
                None => glib::ControlFlow::Break,
            });
            self.timer.replace(Some(t));
        }
        id
    }

    fn unsubscribe(&self, id: u64) {
        self.listeners.borrow_mut().retain(|l| l.0 != id);
        if self.listeners.borrow().is_empty() {
            if let Some(t) = self.timer.take() {
                t.remove();
            }
        }
    }

    fn tick(self: &Rc<Self>) {
        if self.busy.replace(true) {
            return;
        }
        let work = self.work.borrow_mut().get_or_insert_with(|| Arc::new(Mutex::new(Work { sampler: S::Sampler::new(Path::new("/proc"), Path::new("/sys")), t: None }))).clone();
        let me = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            let r = gio::spawn_blocking(move || {
                let mut w = work.lock().unwrap_or_else(|e| e.into_inner());
                let now = Instant::now();
                let dt = w.t.map_or(0.0, |t| (now - t).as_secs_f64());
                w.t = Some(now);
                S::read_once(&mut w.sampler, None, None, dt)
            })
            .await;
            let Some(me) = me.upgrade() else { return };
            me.busy.set(false);
            let Ok(r) = r else { return };
            S::remember(&mut me.history.borrow_mut(), &machine().cards, &r);
            let h = me.history.borrow().clone();
            let ls: Vec<Listener> = me.listeners.borrow().iter().map(|l| l.1.clone()).collect();
            for l in ls {
                l(&r, &h);
            }
        });
    }
}

fn icon(kind: &str) -> &'static str {
    match kind {
        "cpu" => "cpu-symbolic",
        "ram" | "vram" => "media-memory-symbolic",
        "net" => "network-transmit-receive-symbolic",
        "temp_cpu" => "temperature-symbolic",
        k if k.starts_with("vram") => "media-memory-symbolic",
        k if k.starts_with("temp_") => "freon-gpu-temperature-symbolic",
        _ => "gpu-symbolic",
    }
}

/// A module for `kind`: its text ("CPU 12%") and a graph of the last half minute.
pub fn module(kind: &str) -> gtk::Box {
    let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["panel-module", "cc-stat"]).build();
    let head = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    head.append(&gtk::Image::builder().icon_name(icon(kind)).pixel_size(16).build());
    let text = gtk::Label::builder().label("–").xalign(0.0).hexpand(true).css_classes(["panel-module-title"]).ellipsize(gtk::pango::EllipsizeMode::End).width_chars(1).build();
    head.append(&text);
    b.append(&head);
    let data: Rc<RefCell<Vec<Vec<f64>>>> = Rc::default();
    let area = gtk::DrawingArea::builder().hexpand(true).vexpand(true).content_height(26).build();
    let series: Vec<String> = if kind == "net" { vec!["down".into(), "up".into()] } else { vec![kind.into()] };
    let percent = !matches!(kind, "net" | "fps");
    let d = data.clone();
    area.set_draw_func(move |_, cr, w, h| {
        let (w, h) = (w as f64, h as f64);
        let data = d.borrow();
        let peak = if percent { 100.0 } else { data.iter().flatten().copied().fold(1.0, f64::max) };
        let step = w / (S::HISTORY.max(2) - 1) as f64;
        for (i, vals) in data.iter().enumerate().filter(|(_, v)| !v.is_empty()) {
            let (token, alpha) = if i % 2 == 0 { ("label", 0.55) } else { ("accent", 0.8) };
            let c = sonata_ui::rgba(token);
            let x0 = w - step * (vals.len() - 1) as f64;
            cr.move_to(x0, h);
            for (j, v) in vals.iter().enumerate() {
                cr.line_to(x0 + j as f64 * step, h - (v / peak).clamp(0.0, 1.0) * (h - 1.0));
            }
            cr.line_to(w, h);
            cr.close_path();
            cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, alpha);
            let _ = cr.fill();
        }
    });
    b.append(&area);
    let k = kind.to_string();
    let update: Listener = Rc::new(move |r, history| {
        text.set_label(&S::text(&machine().cards, &k, r));
        data.replace(series.iter().map(|s| history.get(s).cloned().unwrap_or_default()).collect());
        area.queue_draw();
    });
    // read only while shown
    let sub: Rc<Cell<Option<u64>>> = Rc::default();
    let s = sub.clone();
    b.connect_map(move |_| {
        let reader = READER.with(Rc::clone);
        if let Some(old) = s.take() {
            reader.unsubscribe(old);
        }
        s.set(Some(reader.subscribe(update.clone())));
    });
    b.connect_unmap(move |_| {
        if let Some(id) = sub.take() {
            READER.with(|r| r.unsubscribe(id));
        }
    });
    b
}
