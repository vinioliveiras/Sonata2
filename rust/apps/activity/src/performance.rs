//! Task Manager's Performance page (activity/performance.py; Windows 11
//! layout, macOS look): a list of resources with sparklines -- CPU,
//! Memory, each disk, each network interface, every GPU (a sleeping one
//! too) -- and, for the selected one, a big 60-second graph with its
//! details.

use crate::pages::Page;
use crate::widgets::{fmt_bits, CompositionBar, Graph, Lines, Series};
use crate::window::TaskManagerWindow;
use gtk::prelude::*;
use sonata_backend::procfs::{fmt_bytes, fmt_count, Snapshot};
use sonata_i18n::{fmt, n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

/// a GPU row goes after this many samples without its load
const GPU_GONE_SAMPLES: u32 = 5;

fn color(kind: &str) -> &'static str {
    match kind {
        "cpu" => "sys_blue",
        "memory" => "sys_purple",
        "disk" => "sys_green",
        "net" => "sys_orange",
        _ => "sys_teal",
    }
}

pub fn fmt_ghz(mhz: f64) -> String {
    if mhz > 0.0 { format!("{:.2} GHz", mhz / 1000.0) } else { "–".into() }
}

/// Windows' d:hh:mm:ss.
pub fn fmt_uptime(seconds: f64) -> String {
    let s = seconds as i64;
    format!("{}:{:02}:{:02}:{:02}", s / 86400, (s % 86400) / 3600, (s % 3600) / 60, s % 60)
}

pub fn fmt_gb(n: f64) -> String {
    format!("{:.1} GB", n / 1024f64.powi(3))
}

pub fn iface_title(name: &str) -> String {
    if name.starts_with("wl") {
        "Wi-Fi".into()
    } else if name == "lo" {
        tr!("Loopback")
    } else if ["tun", "wg", "tap"].iter().any(|p| name.starts_with(p)) {
        "VPN".into()
    } else {
        "Ethernet".into()
    }
}

#[derive(Clone, Default)]
pub struct Stats {
    pub caption: String,
    pub top: String,
    pub big: Vec<(String, String)>,
    pub small: Vec<(String, String)>,
}

/// One entry of the list: its history (one or two lines) and texts.
pub struct Resource {
    pub key: String,
    pub kind: &'static str,
    pub title: String,
    pub detail: RefCell<String>,
    /// main line (%, read, receive)
    pub a: Rc<Series>,
    /// write / send
    pub b: Rc<Series>,
    pub subtitle: RefCell<String>,
    pub stats: RefCell<Stats>,
    pub maximum: Cell<f64>,
    pub composition: Cell<[f64; 4]>,
}

impl Resource {
    fn new(key: &str, kind: &'static str, title: &str, detail: &str) -> Rc<Self> {
        Rc::new(Self { key: key.into(), kind, title: title.into(), detail: RefCell::new(detail.into()), a: Rc::default(), b: Rc::default(), subtitle: RefCell::default(), stats: RefCell::default(), maximum: Cell::new(100.0), composition: Cell::new([0.0; 4]) })
    }

    fn lines(&self) -> Lines {
        if self.kind == "net" {
            vec![(color(self.kind), self.a.clone()), ("sys_red", self.b.clone())]
        } else {
            vec![(color(self.kind), self.a.clone())]
        }
    }

    fn maximum(&self) -> Option<f64> {
        (self.kind != "net").then(|| self.maximum.get())
    }
}

struct Row {
    row: gtk::ListBoxRow,
    spark: Graph,
    sub: gtk::Label,
}

struct Pane {
    model: gtk::Label,
    cap_left: gtk::Label,
    cap_right: gtk::Label,
    graph: Graph,
    bar: Option<CompositionBar>,
    big: gtk::Grid,
    small: gtk::Grid,
}

pub struct PerformancePage {
    win: Weak<TaskManagerWindow>,
    pub resources: RefCell<Vec<Rc<Resource>>>,
    rows: RefCell<HashMap<String, Row>>,
    gpu_missing: RefCell<HashMap<String, u32>>,
    pub current: RefCell<Option<String>>,
    pub list: gtk::ListBox,
    pane_box: gtk::Box,
    pane: RefCell<Option<Pane>>,
    pub stat_labels: RefCell<HashMap<String, gtk::Label>>,
    root: gtk::Box,
    buttons: gtk::Box,
}

impl PerformancePage {
    pub fn new(win: Weak<TaskManagerWindow>) -> Rc<Self> {
        let list = gtk::ListBox::builder().css_classes(["tm-perf-list"]).selection_mode(gtk::SelectionMode::Browse).build();
        let left = gtk::ScrolledWindow::builder().child(&list).hscrollbar_policy(gtk::PolicyType::Never).build();
        left.set_size_request(280, -1);
        let pane_box = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).hexpand(true).css_classes(["tm-perf-pane"]).build();
        let root = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        root.append(&left);
        root.append(&gtk::ScrolledWindow::builder().child(&pane_box).hexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());
        let page = Rc::new(Self { win, resources: RefCell::default(), rows: RefCell::default(), gpu_missing: RefCell::default(), current: RefCell::default(), list, pane_box, pane: RefCell::default(), stat_labels: RefCell::default(), root, buttons: gtk::Box::new(gtk::Orientation::Horizontal, 2) });
        let w = Rc::downgrade(&page);
        page.list.connect_row_selected(move |_, r| {
            let (Some(p), Some(r)) = (w.upgrade(), r) else { return };
            let key = p.rows.borrow().iter().find(|(_, row)| row.row == *r).map(|(k, _)| k.clone());
            if let Some(k) = key {
                p.select(&k);
            }
        });
        page.add(Resource::new("cpu", "cpu", "CPU", ""));
        page.add(Resource::new("memory", "memory", &tr!("Memory"), ""));
        page
    }

    pub fn resource(&self, key: &str) -> Option<Rc<Resource>> {
        self.resources.borrow().iter().find(|r| r.key == key).cloned()
    }

    pub fn row(&self, key: &str) -> Option<gtk::ListBoxRow> {
        self.rows.borrow().get(key).map(|r| r.row.clone())
    }

    fn add(&self, res: Rc<Resource>) {
        let row = gtk::ListBoxRow::new();
        let hb = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        let spark = Graph::new(res.lines(), res.maximum(), false, 64, 40, 3.0);
        spark.set_hexpand(false);
        hb.append(&spark);
        let v = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).spacing(1).build();
        v.append(&gtk::Label::builder().label(&res.title).xalign(0.0).css_classes(["tm-res-title"]).build());
        let sub = gtk::Label::builder().xalign(0.0).css_classes(["tm-res-sub"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(22).build();
        v.append(&sub);
        hb.append(&v);
        row.set_child(Some(&hb));
        self.list.append(&row);
        self.rows.borrow_mut().insert(res.key.clone(), Row { row: row.clone(), spark, sub });
        self.resources.borrow_mut().push(res);
        if self.current.borrow().is_none() {
            self.list.select_row(Some(&row));
        }
    }

    fn remove(&self, key: &str) {
        if let Some(r) = self.rows.borrow_mut().remove(key) {
            self.list.remove(&r.row);
        }
        self.resources.borrow_mut().retain(|r| r.key != key);
        if self.current.borrow().as_deref() == Some(key) {
            let cpu = self.row("cpu");
            self.list.select_row(cpu.as_ref());
        }
    }

    // -- detail pane ----------------------------------------------------------------------------
    pub fn select(&self, key: &str) {
        let Some(res) = self.resource(key) else { return };
        self.current.replace(Some(key.into()));
        while let Some(c) = self.pane_box.first_child() {
            self.pane_box.remove(&c);
        }
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        head.append(&gtk::Label::builder().label(&res.title).xalign(0.0).hexpand(true).css_classes(["tm-perf-title"]).build());
        let model = gtk::Label::builder().label(res.detail.borrow().as_str()).xalign(1.0).css_classes(["tm-perf-model"]).ellipsize(gtk::pango::EllipsizeMode::End).build();
        head.append(&model);
        self.pane_box.append(&head);
        let cap = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let cap_left = gtk::Label::builder().xalign(0.0).hexpand(true).css_classes(["tm-caption"]).build();
        let cap_right = gtk::Label::builder().xalign(1.0).css_classes(["tm-caption"]).build();
        cap.append(&cap_left);
        cap.append(&cap_right);
        self.pane_box.append(&cap);
        let graph = Graph::new(res.lines(), res.maximum(), true, 220, 220, 4.0);
        self.pane_box.append(&graph);
        let foot = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        foot.append(&gtk::Label::builder().label(tr!("60 seconds")).xalign(0.0).hexpand(true).css_classes(["tm-caption"]).build());
        foot.append(&gtk::Label::builder().label("0").xalign(1.0).css_classes(["tm-caption"]).build());
        self.pane_box.append(&foot);
        let mut bar = None;
        if res.kind == "memory" {
            self.pane_box.append(&gtk::Label::builder().label(tr!("Memory composition")).xalign(0.0).css_classes(["tm-caption"]).margin_top(6).build());
            let b = CompositionBar::new();
            self.pane_box.append(&b);
            bar = Some(b);
        }
        let stats = gtk::Box::builder().spacing(36).margin_top(10).build();
        let big = gtk::Grid::builder().row_spacing(10).column_spacing(28).build();
        let small = gtk::Grid::builder().row_spacing(3).column_spacing(16).valign(gtk::Align::Start).build();
        stats.append(&big);
        stats.append(&small);
        self.pane_box.append(&stats);
        self.stat_labels.borrow_mut().clear();
        self.pane.replace(Some(Pane { model, cap_left, cap_right, graph, bar, big, small }));
        self.fill_stats(&res);
    }

    fn fill_stats(&self, res: &Resource) {
        let pane = self.pane.borrow();
        let Some(pane) = pane.as_ref() else { return };
        let st = res.stats.borrow().clone();
        let mut labels = self.stat_labels.borrow_mut();
        let keys: std::collections::HashSet<&String> = st.big.iter().chain(st.small.iter()).map(|(k, _)| k).collect();
        let known: std::collections::HashSet<&String> = labels.keys().collect();
        if !labels.is_empty() && keys == known {
            for (k, v) in st.big.iter().chain(st.small.iter()) {
                if labels[k].label() != *v {
                    labels[k].set_label(v);
                }
            }
        } else {
            for grid in [&pane.big, &pane.small] {
                while let Some(c) = grid.first_child() {
                    grid.remove(&c);
                }
            }
            labels.clear();
            for (i, (k, v)) in st.big.iter().enumerate() {
                let cell = gtk::Box::new(gtk::Orientation::Vertical, 0);
                cell.append(&gtk::Label::builder().label(k).xalign(0.0).css_classes(["tm-stat-key"]).build());
                let lbl = gtk::Label::builder().label(v).xalign(0.0).css_classes(["tm-stat-big"]).build();
                cell.append(&lbl);
                pane.big.attach(&cell, (i % 3) as i32, (i / 3) as i32, 1, 1);
                labels.insert(k.clone(), lbl);
            }
            for (i, (k, v)) in st.small.iter().enumerate() {
                pane.small.attach(&gtk::Label::builder().label(format!("{k}:")).xalign(0.0).css_classes(["tm-stat-key"]).build(), 0, i as i32, 1, 1);
                let lbl = gtk::Label::builder().label(v).xalign(0.0).css_classes(["tm-stat-val"]).build();
                pane.small.attach(&lbl, 1, i as i32, 1, 1);
                labels.insert(k.clone(), lbl);
            }
        }
        pane.cap_left.set_label(&st.caption);
        pane.cap_right.set_label(&st.top);
        pane.model.set_label(&res.detail.borrow());
    }

    fn redraw(&self) {
        for res in self.resources.borrow().iter() {
            if let Some(row) = self.rows.borrow().get(&res.key) {
                let s = res.subtitle.borrow();
                if row.sub.label() != *s {
                    row.sub.set_label(&s);
                }
                row.spark.set_lines(res.lines(), res.maximum());
            }
        }
        let cur = self.current.borrow().clone();
        if let Some(res) = cur.and_then(|k| self.resource(&k)) {
            if let Some(p) = self.pane.borrow().as_ref() {
                p.graph.set_lines(res.lines(), res.maximum());
                if let Some(b) = &p.bar {
                    b.set_values(res.composition.get());
                }
            }
            self.fill_stats(&res);
        }
    }
}

fn kv(k: String, v: String) -> (String, String) {
    (k, v)
}

impl Page for PerformancePage {
    fn id(&self) -> &'static str {
        "performance"
    }
    fn title(&self) -> &'static str {
        n_!("Performance")
    }
    fn icon(&self) -> &'static str {
        "am-system-monitor-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.root.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }

    fn shown(&self) {
        if self.win.upgrade().is_some_and(|w| w.last.borrow().is_some()) {
            self.redraw();
        }
    }

    /// Always called (the history keeps filling while another page shows);
    /// redraws only when this page is the one on screen.
    fn update(&self, snap: &Snapshot) {
        let Some(win) = self.win.upgrade() else { return };
        let info = win.cpu_info();
        let first = snap.interval <= 0.0;
        let visible = win.page_id.borrow().as_deref() == Some("performance");
        // CPU
        let r = self.resource("cpu").unwrap();
        let util = 100.0 - snap.cpu.idle;
        r.detail.replace(sonata_i18n::gettext(&info.model));
        r.subtitle.replace(format!("{util:.0}%  {}", fmt_ghz(snap.cpu_mhz)));
        r.stats.replace(Stats {
            caption: tr!("% Utilization"),
            top: "100%".into(),
            big: vec![kv(tr!("Utilization"), format!("{util:.0}%")), kv(tr!("Speed"), fmt_ghz(snap.cpu_mhz)), kv(String::new(), String::new()), kv(tr!("Processes"), fmt_count(snap.procs.len() as f64)), kv(tr!("Threads"), fmt_count(snap.threads as f64)), kv(tr!("Up time"), fmt_uptime(snap.uptime))],
            small: vec![kv(tr!("Base speed"), fmt_ghz(info.base_mhz)), kv(tr!("Sockets"), info.sockets.to_string()), kv(tr!("Cores"), info.cores.to_string()), kv(tr!("Logical processors"), info.logical.to_string())],
        });
        if !first {
            r.a.push(util);
        }
        // Memory
        let m = &snap.memory;
        let r = self.resource("memory").unwrap();
        let total = m.total.max(1) as f64;
        r.maximum.set(total);
        r.detail.replace(fmt_gb(total));
        r.subtitle.replace(format!("{}/{} ({:.0}%)", fmt_gb(m.used as f64), fmt_gb(total), 100.0 * m.used as f64 / total));
        r.stats.replace(Stats {
            caption: tr!("Memory usage"),
            top: fmt_gb(total),
            big: vec![kv(tr!("In use"), fmt_gb(m.used as f64)), kv(tr!("Available"), fmt_gb(m.available as f64)), kv(tr!("Committed"), format!("{}/{}", fmt_gb(m.committed as f64), fmt_gb(m.commit_limit as f64))), kv(tr!("Cached"), fmt_gb(m.cached as f64)), kv(tr!("Swap used"), format!("{}/{}", fmt_gb(m.swap as f64), fmt_gb(m.swap_total as f64)))],
            small: vec![kv(tr!("Paged pool"), fmt_bytes(m.paged_pool as f64)), kv(tr!("Non-paged pool"), fmt_bytes(m.non_paged_pool as f64)), kv(tr!("Kernel"), fmt_bytes(m.wired as f64)), kv(tr!("Compressed"), fmt_bytes(m.compressed as f64))],
        });
        r.composition.set([m.used.saturating_sub(m.modified) as f64, m.modified as f64, m.cached as f64, m.free as f64]);
        if !first {
            r.a.push(m.used as f64);
        }
        // Disks
        for (i, (name, d)) in snap.disks.iter().enumerate() {
            let key = format!("disk:{name}");
            if self.resource(&key).is_none() {
                self.add(Resource::new(&key, "disk", &fmt(&tr!("Disk {n} ({name})"), &[("n", &i.to_string()), ("name", name)]), ""));
            }
            let r = self.resource(&key).unwrap();
            r.detail.replace(if d.model.is_empty() { name.clone() } else { d.model.clone() });
            r.subtitle.replace(format!("{} {:.0}%", d.kind, d.active).trim().to_string());
            r.stats.replace(Stats {
                caption: tr!("Active time"),
                top: "100%".into(),
                big: vec![kv(tr!("Active time"), format!("{:.0}%", d.active)), kv(tr!("Read speed"), fmt_bytes(d.read_bytes_ps) + "/s"), kv(tr!("Write speed"), fmt_bytes(d.write_bytes_ps) + "/s")],
                small: vec![kv(tr!("Capacity"), fmt_gb(d.capacity as f64)), kv(tr!("Type"), d.kind.clone()), kv(tr!("Reads"), fmt_count(d.reads as f64)), kv(tr!("Writes"), fmt_count(d.writes as f64))],
            });
            if !first {
                r.a.push(d.active);
            }
        }
        let gone: Vec<String> = self.resources.borrow().iter().filter(|r| r.key.strip_prefix("disk:").is_some_and(|n| !snap.disks.contains_key(n))).map(|r| r.key.clone()).collect();
        for k in gone {
            self.remove(&k);
        }
        // Network interfaces (not the loopback, not unused virtual adapters)
        for (name, n) in &snap.interfaces {
            let key = format!("net:{name}");
            if name == "lo" || (self.resource(&key).is_none() && n.rx_bytes == 0 && n.tx_bytes == 0) {
                continue;
            }
            if self.resource(&key).is_none() {
                self.add(Resource::new(&key, "net", &format!("{} ({name})", iface_title(name)), name));
            }
            let r = self.resource(&key).unwrap();
            let (send, recv) = (8.0 * n.tx_bytes_ps, 8.0 * n.rx_bytes_ps);
            r.subtitle.replace(fmt(&tr!("S: {sent}  R: {received}"), &[("sent", &fmt_bits(send)), ("received", &fmt_bits(recv))]));
            r.stats.replace(Stats {
                caption: tr!("Throughput"),
                top: fmt_bits(r.a.max().max(r.b.max()) * 1.15),
                big: vec![kv(tr!("Send"), fmt_bits(send)), kv(tr!("Receive"), fmt_bits(recv))],
                small: vec![kv(tr!("Adapter name"), name.clone()), kv(tr!("Sent"), fmt_bytes(n.tx_bytes as f64)), kv(tr!("Received"), fmt_bytes(n.rx_bytes as f64)), kv(tr!("Packets"), format!("{} / {}", fmt_count(n.tx_packets as f64), fmt_count(n.rx_packets as f64)))],
            });
            if !first {
                r.a.push(recv);
                r.b.push(send);
            }
        }
        let gone: Vec<String> = self.resources.borrow().iter().filter(|r| r.key.strip_prefix("net:").is_some_and(|n| !snap.interfaces.contains_key(n))).map(|r| r.key.clone()).collect();
        for k in gone {
            self.remove(&k);
        }
        // Every GPU (a Radeon 680M + NVIDIA laptop showed only one): a card
        // without a reading still gets its row -- "Sleeping" when runtime PM
        // has it suspended (never woken to ask), else "N/A".
        let mut cards: Vec<(&String, &Option<f64>)> = snap.gpus.iter().collect();
        cards.sort_by_key(|(c, _)| (c.len(), (*c).clone()));
        for (i, (card, busy)) in cards.iter().enumerate() {
            let key = format!("gpu:{card}");
            let gi = snap.gpu_info.get(*card).cloned().unwrap_or_default();
            if self.resource(&key).is_none() {
                self.add(Resource::new(&key, "gpu", &format!("GPU {i}"), card));
            }
            let r = self.resource(&key).unwrap();
            r.detail.replace(if gi.maker.is_empty() { (*card).clone() } else { format!("{} ({card})", gi.maker) });
            let state = match busy {
                Some(b) => format!("{b:.0}%"),
                None if gi.asleep => tr!("Sleeping"),
                None => tr!("N/A"),
            };
            let mut small = vec![kv(tr!("Card"), (*card).clone())];
            if !gi.maker.is_empty() {
                small.push(kv(tr!("Maker"), gi.maker.clone()));
            }
            let mut big = vec![kv(tr!("Utilization"), state.clone())];
            if let Some((u, t)) = gi.vram {
                big.push(kv(tr!("Dedicated memory"), format!("{}/{}", fmt_gb(u as f64), fmt_gb(t as f64))));
            }
            r.subtitle.replace(if gi.maker.is_empty() { state } else { format!("{}  {state}", gi.maker) });
            r.stats.replace(Stats { caption: tr!("Utilization"), top: "100%".into(), big, small });
            if !first {
                r.a.push(busy.unwrap_or(0.0)); // a sleeping card does no work: 0
            }
        }
        let gpu_keys: Vec<String> = self.resources.borrow().iter().filter(|r| r.key.starts_with("gpu:")).map(|r| r.key.clone()).collect();
        for key in gpu_keys {
            if snap.gpus.contains_key(&key[4..]) {
                self.gpu_missing.borrow_mut().remove(&key);
                continue;
            }
            // an unplugged card goes; one missed read (a card waking up) doesn't flicker the list
            let n = {
                let mut m = self.gpu_missing.borrow_mut();
                let e = m.entry(key.clone()).or_insert(0);
                *e += 1;
                *e
            };
            if n >= GPU_GONE_SAMPLES {
                self.gpu_missing.borrow_mut().remove(&key);
                self.remove(&key);
            }
        }
        if visible {
            self.redraw();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats() {
        assert_eq!(fmt_uptime(90061.0), "1:01:01:01");
        assert_eq!(fmt_ghz(2500.0), "2.50 GHz");
        assert_eq!(fmt_ghz(0.0), "–");
        assert_eq!(iface_title("wlan0"), "Wi-Fi");
        assert_eq!(iface_title("wg0"), "VPN");
        assert_eq!(iface_title("enp3s0"), "Ethernet");
    }
}
