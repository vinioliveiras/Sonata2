//! CPU, GPU, memory, network and FPS for the menu bar and Control Center
//! (shell/statsui.py; Vini; off by default). The figures come from
//! sonata_backend::stats, read only while one of these is on screen.
//!
//! ```text
//! kinds()                  cpu gpu vram temp ram net fps (this machine's)
//! menu_item(kind, style)   menu bar: "text" or "graph"
//! module(kind)             Control Center module (2x1)
//! ```
//!
//! [`Stats`] is backend/stats.py's Stats (one per process, reads while
//! anyone subscribes): it needs the main loop, so it lives here, by its
//! only users, rather than in the GTK-free backend.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{glib, graphene, pango};
use sonata_backend::stats::{self as S, Card, Reading};
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashSet};
use std::rc::{Rc, Weak};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

// -- this machine's figures ---------------------------------------------------------------------
struct Machine {
    cards: Vec<Card>,
    kinds: Vec<String>,
}

fn machine() -> &'static Machine {
    static M: OnceLock<Machine> = OnceLock::new();
    M.get_or_init(|| {
        let sys = std::path::Path::new("/sys");
        let cards = S::gpu_cards(sys);
        let kinds = S::kinds(&cards, &S::cpu_temp_path(sys));
        Machine { cards, kinds }
    })
}

/// The graphics cards (read once per process, like stats.py's CARDS).
pub fn cards() -> &'static [Card] {
    &machine().cards
}

/// What can be shown here: cpu, gpu or gpu_<card> for each card, vram...,
/// temp_cpu, temp_gpu..., ram, net, fps.
pub fn kinds() -> &'static [String] {
    &machine().kinds
}

/// a card's maker beside a menu bar graph
fn short(maker: &str) -> String {
    match maker {
        "NVIDIA" => "NV".into(),
        "Intel" => "INT".into(),
        m => m.into(),
    }
}

fn maker_of(cards: &[Card], key: &str) -> String {
    cards.iter().find(|c| c.key == key).map(|c| c.maker.clone()).unwrap_or_default()
}

/// The card's maker for a per-card figure ("gpu_nvidia" -> "NVIDIA"), "" otherwise.
pub fn card_maker_in(cards: &[Card], kind: &str) -> String {
    if let Some(key) = kind.strip_prefix("gpu_") {
        if cards.len() >= 2 {
            return maker_of(cards, key);
        }
    }
    let (vk, tk) = (S::vram_kinds(cards), S::temp_kinds(cards));
    S::key_of(&vk, kind).or_else(|| S::key_of(&tk, kind)).map(|k| maker_of(cards, k)).unwrap_or_default()
}

pub fn card_maker(kind: &str) -> String {
    card_maker_in(cards(), kind)
}

/// The figure's name, translated: "CPU", "GPU (NVIDIA)", "Video Memory"...
pub fn title_in(cards: &[Card], kind: &str) -> String {
    let maker = card_maker_in(cards, kind);
    match kind {
        "cpu" => tr!("CPU"),
        "gpu" => tr!("GPU"),
        "ram" => tr!("Memory"),
        "net" => tr!("Network"),
        "fps" => tr!("FPS"),
        "temp_cpu" => tr!("CPU Temperature"),
        "temp_gpu" => tr!("GPU Temperature"),
        "vram" => tr!("Video Memory"),
        k if k.starts_with("gpu_") => fmt(&tr!("GPU ({maker})"), &[("maker", &maker)]),
        k if k.starts_with("temp_") => fmt(&tr!("GPU Temperature ({maker})"), &[("maker", &maker)]),
        k if k.starts_with("vram_") => fmt(&tr!("Video Memory ({maker})"), &[("maker", &maker)]),
        _ => kind.to_string(),
    }
}

pub fn title(kind: &str) -> String {
    title_in(cards(), kind)
}

/// Beside a menu bar graph: "CPU", "NV", "GPU°", "VRAM AMD".
pub fn caption_in(cards: &[Card], kind: &str) -> String {
    let maker = short(&card_maker_in(cards, kind));
    match kind {
        "cpu" => "CPU".into(),
        "gpu" => "GPU".into(),
        "ram" => "RAM".into(),
        "net" => "NET".into(),
        "fps" => "FPS".into(),
        "temp_cpu" => "CPU°".into(),
        "temp_gpu" => "GPU°".into(),
        "vram" => "VRAM".into(),
        k if k.starts_with("gpu_") => maker,
        k if k.starts_with("temp_") => format!("{maker}°"),
        k if k.starts_with("vram_") => format!("VRAM {maker}"),
        _ => kind.to_uppercase(),
    }
}

pub fn caption(kind: &str) -> String {
    caption_in(cards(), kind)
}

pub fn icon(kind: &str) -> &'static str {
    match kind {
        "cpu" => "cpu-symbolic",
        "ram" => "media-memory-symbolic",
        "net" => "network-transmit-receive-symbolic",
        "fps" => "speedometer-symbolic",
        "temp_cpu" => "temperature-symbolic",
        k if k.starts_with("temp_") => "freon-gpu-temperature-symbolic",
        k if k.starts_with("vram") => "media-memory-symbolic",
        _ => "gpu-symbolic",
    }
}

/// The history series a graph draws: two for the network (down, up).
pub fn series(kind: &str) -> Vec<String> {
    if kind == "net" { vec!["down".into(), "up".into()] } else { vec![kind.to_string()] }
}

/// On a 0..100 scale (°C too); the others scale to their own peak.
pub fn percent(kind: &str) -> bool {
    !matches!(kind, "net" | "fps")
}

/// Modules with the busiest programs in them.
pub fn top_kind(kind: &str) -> bool {
    kind == "cpu" || kind == "gpu" || kind.starts_with("gpu_")
}

/// Under the FPS figure: why there is none.
pub fn fps_hint(state: &str) -> String {
    match state {
        "no-plugin" => tr!("Rebuild Sonata's Wayfire plugin (install.sh) to read FPS"),
        "no-app" => tr!("No app in front"),
        "starting" => tr!("Counting…"),
        _ => String::new(),
    }
}

/// A Control Center module's figure from the menu bar's text: "12%"
/// for "CPU 12%", "3.2 / 8.0 GB" for video memory, both speeds for the
/// network, the bare FPS.
pub fn module_value(cards: &[Card], kind: &str, r: &Reading) -> String {
    if let Some(key) = S::key_of(&S::vram_kinds(cards), kind) {
        return S::vram_text(r, key);
    }
    let t = S::text(cards, kind, r);
    match kind {
        "net" => t.replace("  ", " "),
        "fps" => r.fps.map_or("–".into(), |f| f.to_string()),
        _ => t.split_once(' ').map(|(_, v)| v.to_string()).unwrap_or(t),
    }
}

/// "12%  firefox": a busiest-program row.
pub fn top_row(name: &str, pct: f64) -> String {
    format!("{pct:.0}%  {name}")
}

fn css() {
    sonata_ui::css::register_module("ui/panel.py");
    sonata_ui::css::register_module("shell/statsui.py");
}

// -- the shared reader ----------------------------------------------------------------------------
/// The kinds some subscriber shows, None when one wants all.
pub fn wanted<'a>(each: impl IntoIterator<Item = &'a Option<HashSet<String>>>) -> Option<HashSet<String>> {
    let mut out = HashSet::new();
    for k in each {
        out.extend(k.as_ref()?.iter().cloned());
    }
    Some(out)
}

type Listener = Rc<dyn Fn(&Reading)>;

struct Work {
    sampler: S::Sampler,
    top: Option<S::TopProcs>,
    t: Option<Instant>,
}

/// One per process: reads while anyone subscribes.
pub struct Stats {
    listeners: RefCell<Vec<(u64, Option<HashSet<String>>, Listener)>>,
    next_id: Cell<u64>,
    history: RefCell<BTreeMap<String, Vec<f64>>>,
    last: RefCell<Option<Rc<Reading>>>,
    /// modules showing the busiest programs (TopProcs read only then)
    top_watchers: Cell<u32>,
    timer: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    work: Arc<Mutex<Work>>,
}

thread_local! {
    static SHARED: Rc<Stats> = Rc::new(Stats::new());
}

/// Nobody sees the figures behind the lock screen (idlelock.is_locked:
/// its marker, with a lock screen still alive behind the pid).
fn locked() -> bool {
    crate::quiet::locked()
}

impl Stats {
    fn new() -> Self {
        let work = Work { sampler: S::Sampler::new(std::path::Path::new("/proc"), std::path::Path::new("/sys")), top: None, t: None };
        Self { listeners: RefCell::default(), next_id: Cell::new(1), history: RefCell::default(), last: RefCell::default(), top_watchers: Cell::new(0), timer: RefCell::default(), busy: Cell::new(false), work: Arc::new(Mutex::new(work)) }
    }

    pub fn shared() -> Rc<Stats> {
        SHARED.with(Rc::clone)
    }

    /// The kinds some subscriber shows, None when one wants all.
    pub fn wanted(&self) -> Option<HashSet<String>> {
        wanted(self.listeners.borrow().iter().map(|l| &l.1))
    }

    /// `kinds`: the figures `cb` shows (None: all of them). Only what some
    /// subscriber shows is read (nvidia-smi, sensors...). Returns the id
    /// to unsubscribe with.
    pub fn subscribe(self: &Rc<Self>, cb: Listener, kinds: Option<Vec<String>>) -> u64 {
        let id = self.next_id.get();
        self.next_id.set(id + 1);
        self.listeners.borrow_mut().push((id, kinds.map(|k| k.into_iter().collect()), cb.clone()));
        let last = self.last.borrow().clone();
        if let Some(r) = last {
            cb(&r);
        }
        if self.timer.borrow().is_none() {
            self.tick();
            let weak = Rc::downgrade(self);
            let id = glib::timeout_add_local(Duration::from_millis(S::INTERVAL_MS), move || match weak.upgrade() {
                Some(me) if me.tick() => glib::ControlFlow::Continue,
                Some(me) => {
                    me.timer.take();
                    glib::ControlFlow::Break
                }
                None => glib::ControlFlow::Break,
            });
            self.timer.replace(Some(id));
        }
        id
    }

    pub fn unsubscribe(&self, id: u64) {
        self.listeners.borrow_mut().retain(|l| l.0 != id);
        if self.listeners.borrow().is_empty() {
            if let Some(t) = self.timer.take() {
                sonata_ui::cancel(t);
            }
        }
    }

    /// One more (or one less) module showing the busiest programs.
    pub fn watch_top(&self, on: bool) {
        let n = self.top_watchers.get();
        self.top_watchers.set(if on { n + 1 } else { n.saturating_sub(1) });
    }

    fn tick(self: &Rc<Self>) -> bool {
        if self.listeners.borrow().is_empty() {
            return false;
        }
        if !self.busy.get() && !locked() {
            self.busy.set(true);
            let (work, want, top) = (self.work.clone(), self.wanted(), self.top_watchers.get() > 0);
            let weak: Weak<Self> = Rc::downgrade(self);
            glib::spawn_future_local(async move {
                let r = gio::spawn_blocking(move || {
                    let mut w = work.lock().unwrap_or_else(|e| e.into_inner());
                    let now = Instant::now();
                    let dt = w.t.map_or(0.0, |t| (now - t).as_secs_f64());
                    w.t = Some(now);
                    let Work { sampler, top: procs, .. } = &mut *w;
                    let procs = if top { Some(procs.get_or_insert_with(S::TopProcs::new)) } else { None };
                    S::read_once(sampler, procs, want.as_ref(), dt)
                })
                .await;
                if let Some(me) = weak.upgrade() {
                    me.busy.set(false);
                    if let Ok(r) = r {
                        me.deliver(r);
                    }
                }
            });
        }
        true
    }

    fn deliver(&self, mut r: Reading) {
        let mut h = self.history.borrow_mut();
        S::remember(&mut h, cards(), &r);
        r.history = h.clone();
        drop(h);
        let r = Rc::new(r);
        self.last.replace(Some(r.clone()));
        let listeners: Vec<Listener> = self.listeners.borrow().iter().map(|l| l.2.clone()).collect();
        for cb in listeners {
            cb(&r);
        }
    }
}

/// Subscribe `update` while `widget` is on screen.
fn live(widget: &impl IsA<gtk::Widget>, update: Listener, top: bool, kinds: Option<Vec<String>>) {
    let sub: Rc<Cell<Option<u64>>> = Rc::default();
    let s = sub.clone();
    widget.connect_map(move |_| {
        let st = Stats::shared();
        if top {
            st.watch_top(true); // the per-program reading runs only while one is shown
        }
        if let Some(old) = s.take() {
            st.unsubscribe(old);
        }
        s.set(Some(st.subscribe(update.clone(), kinds.clone())));
    });
    widget.connect_unmap(move |_| {
        let st = Stats::shared();
        if top {
            st.watch_top(false);
        }
        if let Some(id) = sub.take() {
            st.unsubscribe(id);
        }
    });
}

// -- the graph --------------------------------------------------------------------------------
mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Graph {
        pub series: RefCell<Vec<String>>,
        pub percent: Cell<bool>,
        pub data: RefCell<Vec<Vec<f64>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Graph {
        const NAME: &'static str = "SonataRsStatsGraph";
        type Type = super::Graph;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Graph {}

    impl WidgetImpl for Graph {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width() as f64, obj.height() as f64);
            if w <= 0.0 || h <= 0.0 {
                return;
            }
            let data = self.data.borrow();
            let peak = if self.percent.get() { 100.0 } else { data.iter().flatten().copied().fold(1.0, f64::max) };
            let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, w as f32, h as f32));
            let n = S::HISTORY;
            let step = w / (n.max(2) - 1) as f64;
            for (i, vals) in data.iter().enumerate() {
                if vals.is_empty() {
                    continue;
                }
                let (token, alpha) = if i % 2 == 0 { ("label", 0.55) } else { ("accent", 0.8) };
                let c = sonata_ui::theme::rgba(token);
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
        }
    }
}

glib::wrapper! {
    /// A small live area graph (one or two series), drawn in the label
    /// colour; each new sample slides in from the right.
    pub struct Graph(ObjectSubclass<imp::Graph>) @extends gtk::Widget;
}

impl Graph {
    pub fn new(series: Vec<String>, width: i32, height: i32, percent: bool) -> Self {
        let g: Self = glib::Object::builder().property("valign", gtk::Align::Center).build();
        g.set_size_request(width, height);
        g.imp().data.replace(vec![Vec::new(); series.len()]);
        g.imp().series.replace(series);
        g.imp().percent.set(percent);
        g
    }

    pub fn push(&self, history: &BTreeMap<String, Vec<f64>>) {
        let data = self.imp().series.borrow().iter().map(|k| history.get(k).cloned().unwrap_or_default()).collect();
        self.imp().data.replace(data);
        self.queue_draw();
    }
}

// -- the menu bar's item and Control Center's module ----------------------------------------------
/// The menu bar's content for one figure: "CPU 12%" or a caption + graph.
pub fn menu_item(kind: &str, style: &str) -> gtk::Box {
    css();
    let b = gtk::Box::builder().valign(gtk::Align::Center).css_classes(["stat-item"]).build();
    let k = kind.to_string();
    let update: Listener = if style == "graph" {
        b.append(&gtk::Label::builder().label(caption(kind)).css_classes(["stat-caption"]).build());
        let graph = Graph::new(series(kind), 30, 14, percent(kind));
        b.append(&graph);
        let bx = b.downgrade();
        Rc::new(move |r: &Reading| {
            graph.push(&r.history);
            if let Some(bx) = bx.upgrade() {
                bx.set_tooltip_text(Some(&S::text(cards(), &k, r)));
            }
        })
    } else {
        let first = S::text(cards(), kind, &Reading::default());
        let label = gtk::Label::builder().label(&first).width_chars(first.chars().count() as i32).build();
        b.append(&label);
        Rc::new(move |r: &Reading| label.set_label(&S::text(cards(), &k, r)))
    };
    live(&b, update, false, Some(vec![kind.to_string()]));
    b
}

/// ui.panel.module: a Control Center module's box.
fn panel_module(children: &[&gtk::Widget], spacing: i32) -> gtk::Box {
    let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(spacing).css_classes(["panel-module"]).build();
    for c in children {
        b.append(*c);
    }
    b
}

/// Control Center module: icon, title, value and the graph.
pub fn module(kind: &str) -> gtk::Box {
    css();
    let head = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    head.append(&gtk::Image::builder().icon_name(icon(kind)).pixel_size(16).build());
    // nothing here may widen Control Center (its size is fixed): texts shrink with an ellipsis
    head.append(&gtk::Label::builder().label(title(kind)).xalign(0.0).hexpand(true).css_classes(["panel-module-title"]).ellipsize(pango::EllipsizeMode::End).width_chars(1).build());
    let value = gtk::Label::builder().label("–").xalign(1.0).css_classes(["cc-stat-value"]).ellipsize(pango::EllipsizeMode::Start).width_chars(1).max_width_chars(12).build();
    let graph = Graph::new(series(kind), 1, 26, percent(kind)); // as wide as the module, never wider
    graph.set_hexpand(true);
    graph.set_vexpand(true); // and as tall as its cells leave
    graph.set_valign(gtk::Align::Fill);
    let mut shown: gtk::Widget = graph.clone().upcast();
    let maker = card_maker(kind);
    let mut top_rows = Vec::new();
    if !maker.is_empty() || top_kind(kind) {
        // text inside the graph, at its left
        let over = gtk::Overlay::builder().child(&graph).hexpand(true).vexpand(true).build();
        let side = gtk::Box::builder().orientation(gtk::Orientation::Vertical).halign(gtk::Align::Start).valign(gtk::Align::Start).can_target(false).css_classes(["cc-stat-side"]).build();
        if !maker.is_empty() {
            // two cards: whose graph it is (the title gets cut -- Vini)
            side.append(&gtk::Label::builder().label(&maker).xalign(0.0).css_classes(["cc-stat-maker"]).build());
        }
        if top_kind(kind) {
            // the busiest programs (Vini), small, read only while shown
            for _ in 0..S::TOP_N {
                let l = gtk::Label::builder().xalign(0.0).css_classes(["cc-stat-top"]).visible(false).ellipsize(pango::EllipsizeMode::End).max_width_chars(14).width_chars(1).build();
                side.append(&l);
                top_rows.push(l);
            }
        }
        over.add_overlay(&side);
        shown = over.upcast();
    }
    let hint = gtk::Label::builder().xalign(0.0).css_classes(["cc-stat-hint"]).visible(false).wrap(true).width_chars(1).natural_wrap_mode(gtk::NaturalWrapMode::Word).build();
    let b = if kind == "net" {
        // two speeds: a line of their own under the title
        value.set_xalign(0.0);
        value.set_ellipsize(pango::EllipsizeMode::End);
        value.set_max_width_chars(-1);
        value.add_css_class("cc-stat-speeds");
        panel_module(&[head.upcast_ref(), value.upcast_ref(), &shown, hint.upcast_ref()], 4)
    } else {
        // the figure whole, the title cut instead (Vini: "Video …  … .5 GB" --
        // the two shared the room and the GB, what matters, lost its start)
        value.set_ellipsize(pango::EllipsizeMode::None);
        value.set_width_chars(-1);
        value.set_max_width_chars(-1);
        head.append(&value);
        panel_module(&[head.upcast_ref(), &shown, hint.upcast_ref()], 6)
    };
    b.add_css_class("cc-stat");
    let k = kind.to_string();
    let top = !top_rows.is_empty();
    let update: Listener = Rc::new(move |r: &Reading| {
        value.set_label(&module_value(cards(), &k, r));
        graph.push(&r.history);
        let h = if k == "fps" { fps_hint(&r.fps_state) } else { String::new() };
        hint.set_label(&h);
        hint.set_visible(!h.is_empty());
        let rows = r.top.get(&k).map(Vec::as_slice).unwrap_or(&[]);
        for (i, l) in top_rows.iter().enumerate() {
            l.set_visible(i < rows.len());
            if let Some((name, pct)) = rows.get(i) {
                l.set_label(&top_row(name, *pct));
            }
        }
    });
    live(&b, update, top, None);
    b
}

#[cfg(test)]
mod tests {
    use super::*;

    fn card(key: &str, maker: &str, n: usize) -> Card {
        Card { key: key.into(), maker: maker.into(), card: format!("card{n}") }
    }

    /// Vini: with two cards, Control Center's GPU, video memory and
    /// temperature modules couldn't be told apart (the title gets cut):
    /// the maker is inside the graph.
    #[test]
    fn test_each_card_named_in_its_graph() {
        let _env = crate::testenv::env();
        let two = [card("amd", "AMD", 0), card("nvidia", "NVIDIA", 1)];
        assert_eq!(card_maker_in(&two, "gpu_nvidia"), "NVIDIA");
        assert_eq!(card_maker_in(&two, "vram_amd"), "AMD");
        assert_eq!(card_maker_in(&two, "temp_nvidia"), "NVIDIA");
        assert_eq!(card_maker_in(&two, "cpu"), ""); // one of a kind: no tag
        assert_eq!(title_in(&two, "gpu_nvidia"), "GPU (NVIDIA)");
        assert_eq!(title_in(&two, "vram_amd"), "Video Memory (AMD)");
        assert_eq!(title_in(&two, "temp_nvidia"), "GPU Temperature (NVIDIA)");
        assert_eq!(caption_in(&two, "gpu_nvidia"), "NV");
        assert_eq!(caption_in(&two, "temp_amd"), "AMD°");
        assert_eq!(caption_in(&two, "vram_nvidia"), "VRAM NV");
        let one = [card("intel", "Intel", 0)];
        assert_eq!(caption_in(&one, "gpu"), "GPU");
        assert_eq!(title_in(&one, "temp_cpu"), "CPU Temperature");
    }

    /// Every kind the backend can offer has its look.
    #[test]
    fn every_kind_described() {
        let _env = crate::testenv::env();
        let two = [card("amd", "AMD", 0), card("nvidia", "NVIDIA", 1)];
        for k in S::kinds(&two, "/x").iter().chain(S::kinds(&two[..1], "/x").iter()) {
            assert!(!title_in(&two, k).is_empty() && title_in(&two, k) != *k, "{k}");
            assert!(!caption_in(&two, k).is_empty(), "{k}");
            assert!(icon(k).ends_with("-symbolic"), "{k}");
        }
        assert_eq!(series("net"), ["down", "up"]);
        assert!(percent("temp_nvidia") && percent("vram") && !percent("net") && !percent("fps"));
        assert!(top_kind("gpu_amd") && !top_kind("vram_amd") && !top_kind("ram"));
    }

    /// Control Center shows the figure alone (the title says what it is).
    #[test]
    fn module_values() {
        let _env = crate::testenv::env();
        let r = Reading { cpu: 12.4, down: 2048.0, ram_used: 1, ram_total: 4, ..Default::default() };
        assert_eq!(module_value(&[], "cpu", &r), "12%");
        assert_eq!(module_value(&[], "ram", &r), "25%");
        assert_eq!(module_value(&[], "net", &r), "↓ 2 KB/s ↑ 0 KB/s");
        assert_eq!(module_value(&[], "fps", &r), "–");
        assert_eq!(module_value(&[], "gpu", &r), "–");
        let one = [card("amd", "AMD", 0)];
        assert_eq!(module_value(&one, "vram", &r), "–");
        assert_eq!(fps_hint("no-app"), "No app in front");
        assert_eq!(fps_hint("ok"), "");
        assert_eq!(top_row("firefox", 12.4), "12%  firefox");
    }

    /// A menu bar figure asks only for its own kind, so only that is read
    /// (nvidia-smi, sensors... only when shown -- performance review).
    #[test]
    fn test_wanted_is_the_union() {
        let set = |k: &[&str]| Some(k.iter().map(|s| s.to_string()).collect::<HashSet<_>>());
        let mut each = vec![set(&["cpu"]), set(&["ram"])];
        assert_eq!(wanted(&each), set(&["cpu", "ram"]));
        each.push(None);
        assert_eq!(wanted(&each), None);
        assert_eq!(wanted(&[]), set(&[]));
    }

    #[test]
    fn not_locked_without_a_lock_screen() {
        let _env = crate::testenv::env();
        assert!(!locked());
        let marker = sonata_shell::lockdisplay::locked_marker();
        std::fs::create_dir_all(marker.parent().unwrap()).unwrap();
        std::fs::write(&marker, std::process::id().to_string()).unwrap();
        assert!(locked());
        std::fs::write(&marker, "0").unwrap(); // a lock screen that crashed left it behind
        assert!(!locked());
    }
}
