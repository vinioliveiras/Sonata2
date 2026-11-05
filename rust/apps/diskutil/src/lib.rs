//! Disk Manager (diskutil/window.py; macOS Ventura Disk Utility): disks
//! and their volumes.
//!
//! Sidebar: Internal / External / Disk Images, each disk with its volumes
//! indented under it, an eject button on external disks. The main pane:
//! big icon, name, kind, size, a capacity bar split into coloured segments
//! (the disk's partitions; Used / Free of a mounted volume) with its
//! legend, and an info table.
//!
//! The glass toolbar acts on the selection: First Aid (check, then
//! repair), Partition (not available), Erase (Name, Format, Scheme for
//! disks; always confirmed twice; never the running system's volumes,
//! checked again on a fresh tree right before), Rename, Mount / Unmount /
//! Unlock, Eject (unmounts the disk's volumes first) and Info. The same
//! actions are on the sidebar's right-click menu. Everything goes through
//! UDisks2 (udisks.rs), so its polkit rules ask for a password when
//! needed; the window follows changes live. Without UDisks2 it says so.
//!
//! Keys (⌘ = Ctrl or Super): I Info, E Eject, R Rename, Shift+E Erase,
//! Shift+F First Aid, W close.

pub mod model;
pub mod udisks;

use adw::prelude::AlertDialogExt;
use gtk::prelude::*;
use model::{Disk, Tree, Volume};
use sonata_i18n::{n_, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use udisks::{dismissed, error_text, Client};

/// never smaller: the sidebar and a disk's details side by side (Vini)
const MIN_SIZE: (i32, i32) = (640, 420);
const SIDEBAR_W: i32 = 280;
const BAR_HEIGHT: i32 = 22;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.diskutil"
    } else {
        "io.github.vinioliveiras.sonata2.diskutil"
    }
}

/// diskutil's CSS, its swatch colours from the segment tokens.
fn register_css() {
    sonata_ui::css::register_module("diskutil/window.py");
    let swatches: String = model::SEGMENT_COLORS.iter().chain(["sys_gray"].iter()).map(|s| format!(".du-swatch.{s} {{ background: %({s})s; }}\n")).collect();
    sonata_ui::register("diskutil-swatches", &swatches);
}

/// 1234567 -> "1,234,567" (Python's f"{n:,}").
pub fn thousands(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// Segment widths filling `width`, each at least `min_w` (tiny partitions stay visible).
pub fn widths(sizes: &[u64], width: f64, min_w: f64) -> Vec<f64> {
    let total = sizes.iter().sum::<u64>().max(1) as f64;
    let raw: Vec<f64> = sizes.iter().map(|s| (width * *s as f64 / total).max(min_w)).collect();
    let k = width / raw.iter().sum::<f64>().max(1.0);
    raw.into_iter().map(|w| w * k).collect()
}

fn token(name: &str) -> gtk::gdk::RGBA {
    sonata_ui::rgba(name)
}

fn px(name: &str) -> f64 {
    sonata_ui::values().get(name).and_then(|v| v.trim_end_matches("px").trim().parse().ok()).unwrap_or(6.0)
}

/// The segmented capacity bar (Disk Utility's coloured strip).
fn capacity_bar(segments: Rc<RefCell<Vec<(String, u64, &'static str)>>>) -> gtk::DrawingArea {
    let bar = gtk::DrawingArea::builder().hexpand(true).css_classes(["du-bar"]).content_height(BAR_HEIGHT).build();
    bar.set_draw_func(move |_, cr, w, h| {
        let (w, h) = (w as f64, h as f64);
        let r = px("r_label");
        let rounded = |x: f64, y: f64, ww: f64, hh: f64| {
            use std::f64::consts::PI;
            cr.new_sub_path();
            cr.arc(x + ww - r, y + r, r, -PI / 2.0, 0.0);
            cr.arc(x + ww - r, y + hh - r, r, 0.0, PI / 2.0);
            cr.arc(x + r, y + hh - r, r, PI / 2.0, PI);
            cr.arc(x + r, y + r, r, PI, 3.0 * PI / 2.0);
            cr.close_path();
        };
        let set = |c: &gtk::gdk::RGBA| cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
        rounded(0.5, 0.5, w - 1.0, h - 1.0);
        let _ = cr.save();
        cr.clip_preserve();
        set(&token("content_bg"));
        let _ = cr.fill();
        let segs = segments.borrow();
        let segs: Vec<&(String, u64, &str)> = segs.iter().filter(|s| s.1 > 0).collect();
        let mut x = 0.0;
        for (s, sw) in segs.iter().zip(widths(&segs.iter().map(|s| s.1).collect::<Vec<_>>(), w, 4.0)) {
            set(&if s.2 == "free" { token("control_off") } else { token(s.2) });
            cr.rectangle(x, 0.0, sw, h);
            let _ = cr.fill();
            if x > 0.0 {
                // a hairline gap between segments
                set(&token("content_bg"));
                cr.rectangle(x - 0.5, 0.0, 1.0, h);
                let _ = cr.fill();
            }
            x += sw;
        }
        let _ = cr.restore();
        rounded(0.5, 0.5, w - 1.0, h - 1.0);
        set(&token("separator"));
        cr.set_line_width(1.0);
        let _ = cr.stroke();
    });
    sonata_ui::on_change({
        let b = bar.downgrade();
        move || {
            if let Some(b) = b.upgrade() {
                b.queue_draw();
            }
        }
    });
    bar
}

/// The selection: a disk, or a volume (with its disk).
#[derive(Clone)]
enum Item {
    Disk(Disk),
    Vol(Volume, Disk),
}

impl Item {
    fn key(&self) -> String {
        match self {
            Item::Disk(d) => d.block_path.clone(),
            Item::Vol(v, _) => v.path.clone(),
        }
    }

    fn disk(&self) -> &Disk {
        match self {
            Item::Disk(d) | Item::Vol(_, d) => d,
        }
    }

    fn name(&self) -> &str {
        match self {
            Item::Disk(d) => &d.name,
            Item::Vol(v, _) => &v.name,
        }
    }

    fn protected(&self) -> bool {
        match self {
            Item::Disk(d) => d.protected(),
            Item::Vol(v, _) => v.protected(),
        }
    }

    fn device(&self) -> &str {
        match self {
            Item::Disk(d) => &d.device,
            Item::Vol(v, _) => &v.device,
        }
    }

    fn size(&self) -> u64 {
        match self {
            Item::Disk(d) => d.size,
            Item::Vol(v, _) => v.size,
        }
    }
}

/// `key` (a disk's whole block, a volume's block) in a parse of the tree.
fn find(disks: &[Disk], key: &str, disk: bool) -> Option<Item> {
    for d in disks {
        if disk && d.block_path == key {
            return Some(Item::Disk(d.clone()));
        }
        if !disk {
            if let Some(v) = d.volumes.iter().find(|v| v.path == key) {
                return Some(Item::Vol(v.clone(), d.clone()));
            }
        }
    }
    None
}

/// Which actions an item allows, and the mount button's verb.
#[derive(Debug, PartialEq)]
pub struct Actions {
    pub first_aid: bool,
    pub erase: bool,
    pub rename: bool,
    pub mount: bool,
    pub eject: bool,
    pub info: bool,
    pub verb: &'static str,
}

fn actions_for(item: Option<&Item>, ok: bool) -> Actions {
    let ok = ok && item.is_some();
    let vol = match item {
        Some(Item::Vol(v, _)) => Some(v),
        _ => None,
    };
    let disk = item.map(Item::disk);
    let verb = match vol {
        Some(v) if v.locked() => n_!("Unlock"),
        Some(v) if v.mounted() => n_!("Unmount"),
        _ => n_!("Mount"),
    };
    let in_use = vol.is_some_and(|v| v.mounted() && v.protected()); // the running system
    Actions {
        first_aid: ok && vol.is_some_and(|v| v.has_fs && !v.locked()) && !in_use,
        erase: ok && item.is_some_and(|i| !i.protected()),
        rename: ok && vol.is_some_and(|v| v.has_fs && !v.locked()),
        mount: ok && vol.is_some_and(|v| v.locked() || (v.has_fs && !v.swap && !in_use)),
        eject: ok && disk.is_some_and(|d| (d.external() || d.is_image) && !d.protected()),
        info: ok,
        verb,
    }
}

struct DiskUtility {
    win: gtk::ApplicationWindow,
    client: Rc<Client>,
    disks: RefCell<Vec<Disk>>,
    selected: RefCell<Option<Item>>,
    /// (its block path, a disk?): kept across reloads
    sel_key: RefCell<Option<(String, bool)>>,
    /// mount point -> (used, free)
    usage: RefCell<HashMap<String, (u64, u64)>>,
    busy: RefCell<String>,
    available: Cell<bool>,
    filling: Cell<bool>,
    /// fs type -> mkfs available (Manager.CanFormat)
    can_format: RefCell<HashMap<String, bool>>,
    rows: RefCell<Vec<(gtk::ListBoxRow, Option<(String, bool)>)>>,
    btn: HashMap<&'static str, gtk::Button>,
    list: gtk::ListBox,
    sidebar: gtk::ScrolledWindow,
    stack: gtk::Stack,
    icon: gtk::Image,
    name_label: gtk::Label,
    sub_label: gtk::Label,
    sub2_label: gtk::Label,
    size_label: gtk::Label,
    size_sub: gtk::Label,
    bar: gtk::DrawingArea,
    segments: Rc<RefCell<Vec<(String, u64, &'static str)>>>,
    legend: gtk::Box,
    info_box: gtk::Box,
    status: gtk::Box,
    status_spin: sonata_ui::progress::Spinner,
    status_text: gtk::Label,
}

impl DiskUtility {
    fn new(app: &gtk::Application) -> Rc<Self> {
        Self::build(app, true)
    }

    /// start: false leaves UDisks alone (the tests hand the window a tree).
    fn build(app: &gtk::Application, start: bool) -> Rc<Self> {
        if glib::application_name().is_none() {
            glib::set_application_name("Disk Manager");
        }
        let win = gtk::ApplicationWindow::builder().application(app).title(tr!("Disk Manager").as_str()).css_classes(["sonata-diskutil"]).build();
        sonata_ui::window::standard(&win);
        win.set_size_request(MIN_SIZE.0, MIN_SIZE.1);
        // the first time about two thirds of the display (window.py's roomy=True)
        sonata_ui::window::remember_size_roomy(win.upcast_ref(), "diskutil", 900, 600);
        let cell: Rc<RefCell<Option<std::rc::Weak<DiskUtility>>>> = Rc::default();
        let act = |f: fn(&Rc<DiskUtility>)| -> Box<dyn Fn()> {
            let c = cell.clone();
            Box::new(move || {
                if let Some(me) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                    f(&me);
                }
            })
        };
        let tools: Vec<sonata_ui::window::Tool> = vec![
            ("workspacelistentryicon-bandaid-symbolic", tr!("First Aid"), act(|m| m.first_aid())),
            ("am-pie-symbolic", tr!("Partition — Not available"), Box::new(|| {})),
            ("draw-eraser-symbolic", tr!("Erase"), act(|m| m.erase())),
            ("layer-rename-symbolic", tr!("Rename"), act(|m| m.rename())),
            ("media-mount-symbolic", tr!("Mount"), act(|m| m.mount_toggle())),
            ("media-eject-symbolic", tr!("Eject"), act(|m| m.eject(None))),
            ("info-outline-symbolic", tr!("Info"), act(|m| m.info())),
        ];
        let (toolbar, bar_box, buttons) = sonata_ui::window::glass_toolbar(&win, tools);
        let side_btn = gtk::Button::builder().icon_name("sidebar-show-symbolic").tooltip_text(tr!("Show or Hide the Sidebar").as_str()).can_focus(false).valign(gtk::Align::Center).css_classes(["tool"]).build();
        let toggle = act(|m| m.toggle_sidebar());
        side_btn.connect_clicked(move |_| toggle());
        bar_box.set_start_widget(Some(&side_btn));
        let btn: HashMap<&'static str, gtk::Button> = ["first_aid", "partition", "erase", "rename", "mount", "eject", "info"].into_iter().zip(buttons).collect();
        btn["partition"].set_sensitive(false);

        // sidebar | main pane
        let list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).css_classes(["du-list"]).build();
        let sidebar = gtk::ScrolledWindow::builder().child(&list).hscrollbar_policy(gtk::PolicyType::Never).css_classes(["sonata-sidebar", "du-sidebar"]).build();
        sidebar.set_size_request(SIDEBAR_W, -1);
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).hexpand(true).css_classes(["du-main"]).build();

        // the detail pane
        let detail = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let head = gtk::Box::builder().spacing(14).css_classes(["du-header"]).build();
        let icon = gtk::Image::builder().pixel_size(64).build();
        head.append(&icon);
        let names = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).valign(gtk::Align::Center).hexpand(true).build();
        let label = |class: &str| gtk::Label::builder().xalign(0.0).css_classes([class]).ellipsize(gtk::pango::EllipsizeMode::End).build();
        let (name_label, sub_label, sub2_label) = (label("du-name"), label("du-sub"), label("du-sub"));
        for w in [&name_label, &sub_label, &sub2_label] {
            names.append(w);
        }
        head.append(&names);
        let sizes = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).valign(gtk::Align::Center).build();
        let size_label = gtk::Label::builder().xalign(1.0).css_classes(["du-size"]).build();
        let size_sub = gtk::Label::builder().xalign(1.0).css_classes(["du-size-sub"]).build();
        sizes.append(&size_label);
        sizes.append(&size_sub);
        head.append(&sizes);
        detail.append(&head);
        let segments: Rc<RefCell<Vec<(String, u64, &'static str)>>> = Rc::default();
        let bar = capacity_bar(segments.clone());
        bar.set_margin_start(24);
        bar.set_margin_end(24);
        detail.append(&bar);
        let legend = gtk::Box::builder().spacing(26).css_classes(["du-legend"]).margin_top(10).build();
        detail.append(&legend);
        let info_box = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["du-info"]).build();
        detail.append(&info_box);
        let status = gtk::Box::builder().spacing(8).css_classes(["du-status"]).visible(false).valign(gtk::Align::End).vexpand(true).build();
        let status_spin = sonata_ui::progress::Spinner::new(false);
        let status_text = gtk::Label::builder().xalign(0.0).build();
        status.append(&status_spin);
        status.append(&status_text);
        detail.append(&status);
        stack.add_named(&gtk::ScrolledWindow::builder().child(&detail).hscrollbar_policy(gtk::PolicyType::Never).build(), Some("detail"));
        stack.add_named(&gtk::Label::builder().label(tr!("No Selection").as_str()).css_classes(["du-empty"]).build(), Some("empty"));
        let missing = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).valign(gtk::Align::Center).halign(gtk::Align::Center).css_classes(["du-missing"]).build();
        missing.append(&gtk::Image::builder().icon_name("sonata-diskmanager").pixel_size(96).build());
        missing.append(&gtk::Label::builder().label(tr!("Disk Manager needs UDisks2").as_str()).css_classes(["du-missing-title"]).build());
        missing.append(&gtk::Label::builder().label(tr!("Install the udisks2 package and start its service (udisks2.service), then open Disk Manager again.").as_str()).css_classes(["du-missing-body"]).wrap(true).max_width_chars(48).justify(gtk::Justification::Center).build());
        stack.add_named(&missing, Some("missing"));
        let loading = sonata_ui::progress::Spinner::new(true);
        loading.set_size_request(32, 32);
        loading.set_halign(gtk::Align::Center);
        loading.set_valign(gtk::Align::Center);
        stack.add_named(&loading, Some("loading"));
        stack.set_visible_child_name("loading");
        let paned = gtk::Paned::builder().start_child(&sidebar).end_child(&stack).shrink_start_child(false).resize_start_child(false).position(SIDEBAR_W).vexpand(true).css_classes(["du-paned"]).build();
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&toolbar);
        col.append(&paned);
        win.set_child(Some(&col));

        let me = Rc::new(Self {
            win,
            client: Rc::new(Client::default()),
            disks: RefCell::default(),
            selected: RefCell::default(),
            sel_key: RefCell::default(),
            usage: RefCell::default(),
            busy: RefCell::default(),
            available: Cell::new(false),
            filling: Cell::new(false),
            can_format: RefCell::default(),
            rows: RefCell::default(),
            btn,
            list,
            sidebar,
            stack,
            icon,
            name_label,
            sub_label,
            sub2_label,
            size_label,
            size_sub,
            bar,
            segments,
            legend,
            info_box,
            status,
            status_spin,
            status_text,
        });
        cell.replace(Some(Rc::downgrade(&me)));
        me.wire();
        me.update_tools();
        let (w1, w2) = (Rc::downgrade(&me), Rc::downgrade(&me));
        if !start {
            return me;
        }
        me.client.start(
            move |tree| {
                if let Some(m) = w1.upgrade() {
                    m.load(&tree);
                }
            },
            move |_msg| {
                if let Some(m) = w2.upgrade() {
                    m.unavailable();
                }
            },
        );
        let c = me.client.clone();
        me.win.connect_destroy(move |_| c.stop());
        me
    }

    fn wire(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        self.list.connect_row_selected(move |_, row| {
            let Some(m) = weak.upgrade() else { return };
            if m.filling.get() {
                return;
            }
            let key = row.and_then(|r| m.rows.borrow().iter().find(|x| x.0 == *r).and_then(|x| x.1.clone()));
            m.select(key);
        });
        let menu = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
        let weak = Rc::downgrade(self);
        menu.connect_pressed(move |g, _, x, y| {
            if let Some(m) = weak.upgrade() {
                m.context_menu(g, x, y);
            }
        });
        self.list.add_controller(menu);
        let keys = gtk::EventControllerKey::new();
        let weak = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, k, _, state| match weak.upgrade() {
            Some(m) if m.key(k, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.win.add_controller(keys);
    }

    // -- data ------------------------------------------------------------------------------
    /// UDisks2 can't be reached: say so instead of the disks.
    fn unavailable(self: &Rc<Self>) {
        self.available.set(false);
        self.disks.replace(Vec::new());
        self.fill_sidebar();
        self.sidebar.set_visible(false);
        self.stack.set_visible_child_name("missing");
        self.update_tools();
    }

    /// A fresh UDisks tree (at start and after every change).
    fn load(self: &Rc<Self>, tree: &Tree) {
        self.available.set(true);
        self.sidebar.set_visible(true);
        self.disks.replace(model::parse(tree));
        self.fill_sidebar();
        self.query_formats();
    }

    fn fill_sidebar(self: &Rc<Self>) {
        let key = self.sel_key.borrow().clone();
        self.filling.set(true);
        while let Some(row) = self.list.row_at_index(0) {
            self.list.remove(&row);
        }
        self.rows.borrow_mut().clear();
        let mut target: Option<gtk::ListBoxRow> = None;
        let disks = self.disks.borrow().clone();
        for (title, ds) in model::grouped(&disks) {
            let head = gtk::ListBoxRow::builder().selectable(false).activatable(false).css_classes(["du-head"]).build();
            head.set_child(Some(&gtk::Label::builder().label(sonata_i18n::gettext(title).as_str()).xalign(0.0).build()));
            self.list.append(&head);
            self.rows.borrow_mut().push((head, None));
            for d in ds {
                let row = self.row(&Item::Disk(d.clone()));
                self.list.append(&row);
                let k = (d.block_path.clone(), true);
                if key.as_ref() == Some(&k) || target.is_none() {
                    target = Some(row.clone());
                }
                self.rows.borrow_mut().push((row, Some(k)));
                for v in d.volumes.iter().filter(|v| v.listed()) {
                    let vr = self.row(&Item::Vol(v.clone(), d.clone()));
                    self.list.append(&vr);
                    let k = (v.path.clone(), false);
                    if key.as_ref() == Some(&k) {
                        target = Some(vr.clone());
                    }
                    self.rows.borrow_mut().push((vr, Some(k)));
                }
            }
        }
        self.filling.set(false);
        match target {
            Some(t) => {
                self.list.select_row(Some(&t));
                let k = self.rows.borrow().iter().find(|x| x.0 == t).and_then(|x| x.1.clone());
                self.select(k);
            }
            None => self.select(None),
        }
    }

    fn row(self: &Rc<Self>, item: &Item) -> gtk::ListBoxRow {
        let disk = item.disk();
        let is_disk = matches!(item, Item::Disk(_));
        let bx = gtk::Box::builder().spacing(6).margin_start(if is_disk { 0 } else { 20 }).build();
        bx.append(&gtk::Image::builder().icon_name(disk.icon()).pixel_size(if is_disk { 18 } else { 16 }).build());
        bx.append(&gtk::Label::builder().label(item.name()).xalign(0.0).hexpand(true).css_classes(["du-row"]).ellipsize(gtk::pango::EllipsizeMode::End).build());
        if let Item::Vol(v, _) = item {
            if v.encrypted {
                bx.append(&gtk::Image::builder().icon_name(if v.locked() { "system-lock-screen-symbolic" } else { "padlock-open-symbolic" }).pixel_size(12).css_classes(["du-lock"]).build());
            }
        }
        if is_disk && (disk.external() || disk.is_image) {
            let ej = gtk::Button::builder().icon_name("media-eject-symbolic").css_classes(["du-eject"]).tooltip_text(tr!("Eject").as_str()).valign(gtk::Align::Center).can_focus(false).build();
            let (weak, key) = (Rc::downgrade(self), disk.block_path.clone());
            ej.connect_clicked(move |_| {
                if let Some(m) = weak.upgrade() {
                    let d = m.disks.borrow().iter().find(|d| d.block_path == key).cloned();
                    m.eject(d);
                }
            });
            bx.append(&ej);
        }
        gtk::ListBoxRow::builder().child(&bx).build()
    }

    fn select(self: &Rc<Self>, key: Option<(String, bool)>) {
        let item = key.as_ref().and_then(|(k, d)| find(&self.disks.borrow(), k, *d));
        if key.is_some() {
            self.sel_key.replace(key);
        }
        self.selected.replace(item.clone());
        if !self.available.get() {
            return;
        }
        match &item {
            None => self.stack.set_visible_child_name("empty"),
            Some(it) => {
                self.stack.set_visible_child_name("detail");
                self.show(it);
                if let Item::Vol(v, _) = it {
                    if v.mounted() {
                        self.read_usage(v.mount_point().to_string());
                    }
                }
            }
        }
        self.update_tools();
    }

    fn read_usage(self: &Rc<Self>, mp: String) {
        let me = self.clone();
        glib::spawn_future_local(async move {
            let path = mp.clone();
            let res = gio::spawn_blocking(move || {
                let c = std::ffi::CString::new(path).ok()?;
                let mut s: libc::statvfs = unsafe { std::mem::zeroed() };
                (unsafe { libc::statvfs(c.as_ptr(), &mut s) } == 0).then(|| (((s.f_blocks - s.f_bfree) as u64) * s.f_frsize as u64, s.f_bavail as u64 * s.f_frsize as u64))
            })
            .await
            .ok()
            .flatten();
            if let Some(u) = res {
                me.usage.borrow_mut().insert(mp.clone(), u);
                let sel = me.selected.borrow().clone();
                if let Some(ref it @ Item::Vol(ref v, _)) = sel {
                    if v.mount_point() == mp {
                        me.show(&it);
                    }
                }
            }
        });
    }

    // -- main pane ---------------------------------------------------------------------------
    fn show(&self, item: &Item) {
        let disk = item.disk();
        self.icon.set_icon_name(Some(disk.icon()));
        self.name_label.set_label(item.name());
        let place_bus = model::bus_name(&disk.bus);
        let table = model::table_name(&disk.table);
        let join = |parts: &[&str]| parts.iter().filter(|x| !x.is_empty()).copied().collect::<Vec<_>>().join(" • ");
        match item {
            Item::Disk(d) => {
                self.sub_label.set_label(&join(&[&d.kind(), &table]));
                let n = d.volumes.iter().filter(|v| v.listed()).count();
                self.size_sub.set_label(&if n > 0 { sonata_i18n::fmt(&trn!("{n} VOLUME", "{n} VOLUMES", n), &[("n", &n.to_string())]) } else { String::new() });
                self.sub2_label.set_label(&d.device);
            }
            Item::Vol(v, d) => {
                let t = if d.is_image {
                    tr!("{bus} Disk Image Volume")
                } else if d.external() {
                    tr!("{bus} External Physical Volume")
                } else {
                    tr!("{bus} Internal Physical Volume")
                };
                let place = sonata_i18n::fmt(&t, &[("bus", &place_bus)]).split_whitespace().collect::<Vec<_>>().join(" ");
                self.sub_label.set_label(&join(&[&place, &v.kind()]));
                self.sub2_label.set_label(&if v.locked() { sonata_i18n::fmt(&tr!("{device} — Locked"), &[("device", &v.device)]) } else { v.device.clone() });
                self.size_sub.set_label("");
            }
        }
        self.size_label.set_label(&sonata_ui::fmt::size(item.size()));
        let usage = match item {
            Item::Vol(v, _) if v.mounted() => self.usage.borrow().get(v.mount_point()).copied(),
            _ => None,
        };
        let segs = match item {
            Item::Disk(d) => model::segments(d, None, None),
            Item::Vol(v, d) => model::segments(d, Some(v), usage),
        };
        self.segments.replace(segs.clone());
        self.bar.queue_draw();
        self.fill_legend(&segs);
        self.fill_info(&info_rows(item, usage));
    }

    fn fill_legend(&self, segs: &[(String, u64, &'static str)]) {
        while let Some(c) = self.legend.first_child() {
            self.legend.remove(&c);
        }
        for (name, size, colour) in segs.iter().take(8) {
            let col = gtk::Box::new(gtk::Orientation::Vertical, 1);
            let top = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            top.append(&gtk::Box::builder().css_classes(["du-swatch", colour]).valign(gtk::Align::Center).build());
            top.append(&gtk::Label::builder().label(name.as_str()).xalign(0.0).css_classes(["du-legend-name"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(18).build());
            col.append(&top);
            col.append(&gtk::Label::builder().label(sonata_ui::fmt::size(*size).as_str()).xalign(0.0).margin_start(15).css_classes(["du-legend-size"]).build());
            self.legend.append(&col);
        }
    }

    fn fill_info(&self, rows: &[(String, String)]) {
        while let Some(c) = self.info_box.first_child() {
            self.info_box.remove(&c);
        }
        for pair in rows.chunks(2) {
            let line = gtk::Box::builder().homogeneous(true).css_classes(["du-info-row"]).build();
            for (key, value) in pair {
                let cell = gtk::Box::builder().spacing(12).css_classes(["du-cell"]).build();
                cell.append(&gtk::Label::builder().label(sonata_i18n::fmt(&tr!("{label}:"), &[("label", key)]).as_str()).xalign(0.0).css_classes(["du-key"]).build());
                let val = gtk::Label::builder().label(value.as_str()).xalign(1.0).hexpand(true).css_classes(["du-value"]).ellipsize(gtk::pango::EllipsizeMode::Middle).tooltip_text(value.as_str()).build();
                if *key == tr!("S.M.A.R.T. status") && *value == tr!("Failing") {
                    val.add_css_class("du-bad");
                }
                cell.append(&val);
                line.append(&cell);
            }
            self.info_box.append(&line);
        }
    }

    // -- toolbar state -------------------------------------------------------------------------
    fn actions(&self, item: Option<&Item>) -> Actions {
        let sel = self.selected.borrow().clone();
        actions_for(item.or(sel.as_ref()), self.available.get() && self.busy.borrow().is_empty())
    }

    fn update_tools(&self) {
        let a = self.actions(None);
        for (k, on) in [("first_aid", a.first_aid), ("erase", a.erase), ("rename", a.rename), ("mount", a.mount), ("eject", a.eject), ("info", a.info)] {
            self.btn[k].set_sensitive(on);
        }
        self.btn["mount"].set_icon_name(if a.verb == "Unlock" { "padlock-open-symbolic" } else { "media-mount-symbolic" });
        self.btn["mount"].set_tooltip_text(Some(&sonata_i18n::gettext(a.verb)));
    }

    fn toggle_sidebar(&self) {
        if self.available.get() {
            self.sidebar.set_visible(!self.sidebar.is_visible());
        }
    }

    // -- running UDisks calls ------------------------------------------------------------------
    fn set_busy(&self, text: &str) {
        self.busy.replace(text.into());
        self.status.set_visible(!text.is_empty());
        self.status_text.set_label(text);
        self.status_spin.set_spinning(!text.is_empty());
        self.update_tools();
    }

    fn alert(&self, heading: &str, body: &str, button: &str) {
        sonata_ui::dialog::alert(heading, body, &[("ok", button.to_string(), "default")], |_| {}, Some(self.win.upcast_ref()));
    }

    fn failed(&self, heading: &str, err: &glib::Error) {
        if !dismissed(err) {
            self.alert(heading, &error_text(err), &tr!("OK"));
        }
    }

    fn named(t: &str, name: &str) -> String {
        sonata_i18n::fmt(t, &[("name", name)])
    }

    fn mount_toggle(self: &Rc<Self>) {
        let Some(Item::Vol(v, _)) = self.selected.borrow().clone() else { return };
        if v.locked() {
            self.unlock(v);
            return;
        }
        let me = self.clone();
        glib::spawn_future_local(async move {
            let (res, heading) = if v.mounted() {
                (me.client.unmount(&v.fs_path).await, tr!("“{name}” couldn't be unmounted."))
            } else {
                (me.client.mount(&v.fs_path).await, tr!("“{name}” couldn't be mounted."))
            };
            if let Err(e) = res {
                me.failed(&Self::named(&heading, &v.name), &e);
            }
        });
    }

    fn unlock(self: &Rc<Self>, v: Volume) {
        let entry = gtk::PasswordEntry::builder().show_peek_icon(true).activates_default(true).css_classes(["du-form"]).build();
        let (me, e) = (self.clone(), entry.clone());
        let dlg = sonata_ui::dialog::alert(&Self::named(&tr!("Unlock “{name}”"), &v.name), &tr!("Enter the password to unlock this volume."), &[("cancel", tr!("Cancel"), ""), ("unlock", tr!("Unlock"), "default")], move |rid| {
            if rid != "unlock" {
                return;
            }
            let (me, v, pw) = (me.clone(), v.clone(), e.text().to_string());
            glib::spawn_future_local(async move {
                match me.client.unlock(&v.path, &pw).await {
                    // like macOS: unlocked volumes mount
                    Ok(ct) => {
                        let _ = me.client.mount(&ct).await;
                    }
                    Err(err) => me.failed(&Self::named(&tr!("“{name}” couldn't be unlocked."), &v.name), &err),
                }
            });
        }, Some(self.win.upcast_ref()));
        dlg.set_extra_child(Some(&entry));
        glib::idle_add_local_once(move || {
            entry.grab_focus();
        });
    }

    fn eject(self: &Rc<Self>, disk: Option<Disk>) {
        let d = disk.or_else(|| self.selected.borrow().as_ref().map(|i| i.disk().clone()));
        let Some(d) = d.filter(|d| !d.protected()) else { return };
        let me = self.clone();
        glib::spawn_future_local(async move {
            let c = &me.client;
            let run = async {
                for v in &d.volumes {
                    if v.mounted() {
                        c.unmount(&v.fs_path).await?;
                    }
                    if v.encrypted && !v.locked() {
                        c.lock(&v.path).await?;
                    }
                }
                if d.is_image {
                    c.delete_loop(&d.block_path).await?;
                } else {
                    if d.ejectable {
                        c.eject(&d.path).await?;
                    }
                    if d.can_power_off {
                        c.power_off(&d.path).await?;
                    }
                }
                Ok::<(), glib::Error>(())
            };
            if let Err(e) = run.await {
                me.failed(&Self::named(&tr!("“{name}” couldn't be ejected."), &d.name), &e);
            }
        });
    }

    fn rename(self: &Rc<Self>) {
        let Some(Item::Vol(v, _)) = self.selected.borrow().clone() else { return };
        if !v.has_fs {
            return;
        }
        let me = self.clone();
        let v2 = v.clone();
        sonata_ui::dialog::ask_text(&Self::named(&tr!("Rename “{name}”"), &v.name), &v.label, &tr!("Rename"), move |text| {
            let (me, v) = (me.clone(), v2.clone());
            glib::spawn_future_local(async move {
                if let Err(e) = me.client.set_label(&v.fs_path, &text).await {
                    me.failed(&Self::named(&tr!("“{name}” couldn't be renamed."), &v.name), &e);
                }
            });
        }, &tr!("Enter a new name for this volume."), Some(self.win.upcast_ref()));
    }

    // erase -----------------------------------------------------------------------------------
    fn query_formats(self: &Rc<Self>) {
        if !self.can_format.borrow().is_empty() {
            return;
        }
        let me = self.clone();
        glib::spawn_future_local(async move {
            for (t, _, _) in model::FORMATS {
                let (ok, _) = me.client.can("Format", t).await;
                me.can_format.borrow_mut().insert(t.to_string(), ok);
            }
        });
    }

    fn format_choices(&self) -> Vec<(&'static str, String)> {
        let can = self.can_format.borrow().clone();
        if can.contains_key("ntfs") {
            model::formats(&|t| can.get(t).copied().unwrap_or(false))
        } else {
            model::formats(&model::mkfs_installed)
        }
    }

    fn erase(self: &Rc<Self>) {
        let Some(item) = self.selected.borrow().clone() else { return };
        if item.protected() {
            self.refuse_erase(item.name());
            return;
        }
        let is_disk = matches!(item, Item::Disk(_));
        let choices = self.format_choices();
        let grid = gtk::Grid::builder().column_spacing(10).row_spacing(8).css_classes(["du-form"]).build();
        let label = match &item {
            Item::Disk(d) => d.volumes.first().map(|v| v.label.clone()).unwrap_or_default(),
            Item::Vol(v, _) => v.label.clone(),
        };
        let name = gtk::Entry::builder().text(if label.is_empty() { tr!("Untitled") } else { label }.as_str()).hexpand(true).activates_default(true).build();
        // removable media: ExFAT, like macOS
        let first = if item.disk().external() { choices.iter().position(|c| c.0 == "exfat").unwrap_or(0) } else { 0 };
        let fmt = sonata_ui::controls::popup_button(&choices.iter().map(|c| c.1.clone()).collect::<Vec<_>>(), first as u32, |_| {});
        let mut rows: Vec<(String, gtk::Widget)> = vec![(tr!("Name:"), name.clone().upcast()), (tr!("Format:"), fmt.clone().upcast())];
        let scheme = is_disk.then(|| sonata_ui::controls::popup_button(&model::SCHEMES.iter().map(|s| sonata_i18n::gettext(s.1)).collect::<Vec<_>>(), 0, |_| {}));
        if let Some(s) = &scheme {
            rows.push((tr!("Scheme:"), s.clone().upcast()));
        }
        for (i, (l, w)) in rows.iter().enumerate() {
            grid.attach(&gtk::Label::builder().label(l.as_str()).xalign(1.0).build(), 0, i as i32, 1, 1);
            grid.attach(w, 1, i as i32, 1, 1);
        }
        let me = self.clone();
        let it = item.clone();
        let dlg = sonata_ui::dialog::alert(
            &Self::named(&tr!("Erase “{name}”?"), item.name()),
            &Self::named(&tr!("Erasing “{name}” will permanently erase all data stored on it. Enter a name, choose a format and click Erase."), item.name()),
            &[("cancel", tr!("Cancel"), ""), ("erase", tr!("Erase"), "default")],
            move |rid| {
                if rid != "erase" {
                    return;
                }
                let label = name.text().trim().to_string();
                let fs_type = choices[(fmt.selected() as usize).min(choices.len() - 1)].0;
                let table = scheme.as_ref().map(|s| model::SCHEMES[(s.selected() as usize).min(1)].0);
                me.confirm_erase(it.clone(), label, fs_type, table);
            },
            Some(self.win.upcast_ref()),
        );
        dlg.set_extra_child(Some(&grid));
    }

    /// The last word before erasing: names the device and its size.
    fn confirm_erase(self: &Rc<Self>, item: Item, label: String, fs_type: &'static str, table: Option<&'static str>) {
        let what = format!("{} ({})", item.device(), sonata_ui::fmt::size(item.size()));
        let me = self.clone();
        let it = item.clone();
        sonata_ui::dialog::alert(
            &Self::named(&tr!("Are you sure you want to erase “{name}”?"), item.name()),
            &sonata_i18n::fmt(&tr!("All the data on {what} will be permanently erased. You can't undo this action."), &[("what", &what)]),
            &[("cancel", tr!("Cancel"), ""), ("erase", tr!("Erase"), "destructive")],
            move |rid| {
                if rid == "erase" {
                    me.do_erase(it.clone(), label.clone(), fs_type, table);
                }
            },
            Some(self.win.upcast_ref()),
        );
    }

    fn refuse_erase(&self, name: &str) {
        self.alert(&Self::named(&tr!("“{name}” can't be erased."), name), &tr!("It holds the running system (/, /boot, /home, swap or one of their devices)."), &tr!("OK"));
    }

    /// Checked again on a fresh tree (not the one the sheet was opened
    /// with: it may have been mounted as a system volume since).
    fn do_erase(self: &Rc<Self>, item: Item, label: String, fs_type: &'static str, table: Option<&'static str>) {
        let me = self.clone();
        glib::spawn_future_local(async move {
            let fresh = me.client.fetch().await.ok().map(|t| model::parse(&t));
            let cur = fresh.and_then(|ds| find(&ds, &item.key(), matches!(item, Item::Disk(_))));
            match cur {
                Some(c) if !c.protected() => me.erase_now(c, &label, fs_type, table).await,
                other => me.refuse_erase(other.as_ref().map(Item::name).unwrap_or(item.name())),
            }
        });
    }

    async fn erase_now(self: &Rc<Self>, item: Item, label: &str, fs_type: &str, table: Option<&str>) {
        let heading = Self::named(&tr!("“{name}” couldn't be erased."), item.name());
        self.set_busy(&Self::named(&tr!("Erasing “{name}”…"), item.name()));
        let c = &self.client;
        let res: Result<(), glib::Error> = async {
            match &item {
                Item::Vol(v, _) => {
                    c.format(&v.path, fs_type, label, true).await?;
                }
                Item::Disk(d) => {
                    c.format(&d.block_path, table.unwrap_or("gpt"), "", false).await?;
                    // the new table shows up on D-Bus a moment after Format
                    let mut tries = 0;
                    loop {
                        match c.create_partition(&d.block_path, fs_type, label).await {
                            Err(e) if tries < 5 && e.message().contains("PartitionTable") => {
                                tries += 1;
                                glib::timeout_future(std::time::Duration::from_millis(800)).await;
                            }
                            other => {
                                other?;
                                break;
                            }
                        }
                    }
                }
            }
            Ok(())
        }
        .await;
        self.set_busy("");
        if let Err(e) = res {
            self.failed(&heading, &e);
        }
    }

    // first aid ---------------------------------------------------------------------------------
    fn first_aid(self: &Rc<Self>) {
        let Some(Item::Vol(v, _)) = self.selected.borrow().clone() else { return };
        if !v.has_fs {
            return;
        }
        if v.mounted() && v.protected() {
            self.alert(&Self::named(&tr!("First Aid can't check “{name}” while it's in use."), &v.name), &tr!("It holds the running system. Check it from another system."), &tr!("OK"));
            return;
        }
        let mut body = tr!("First Aid will check the volume for errors.");
        if v.mounted() {
            body = format!("{body} {}", tr!("It will be unmounted while it's checked."));
        }
        let me = self.clone();
        sonata_ui::dialog::alert(&Self::named(&tr!("Would you like to run First Aid on “{name}”?"), &v.name), &body, &[("cancel", tr!("Cancel"), ""), ("run", tr!("Run"), "default")], move |rid| {
            if rid == "run" {
                let (me, v) = (me.clone(), v.clone());
                glib::spawn_future_local(async move { me.check(v).await });
            }
        }, Some(self.win.upcast_ref()));
    }

    async fn check(self: &Rc<Self>, v: Volume) {
        let (ok, util) = self.client.can("Check", &v.fs_type).await;
        if !ok {
            let body = if util.is_empty() { sonata_i18n::fmt(&tr!("{kind} volumes can't be checked."), &[("kind", &v.kind())]) } else { sonata_i18n::fmt(&tr!("Install {util} to check {kind} volumes."), &[("util", &util), ("kind", &v.kind())]) };
            self.alert(&tr!("First Aid isn't available for this volume."), &body, &tr!("OK"));
            return;
        }
        self.set_busy(&Self::named(&tr!("Checking “{name}”…"), &v.name));
        let was = v.mounted();
        let c = &self.client;
        let res = async {
            if was {
                c.unmount(&v.fs_path).await?;
            }
            c.check(&v.fs_path).await
        }
        .await;
        self.set_busy("");
        match res {
            Err(e) => self.failed(&tr!("First Aid failed."), &e),
            Ok(true) => {
                self.remount(&v, was).await;
                self.alert(&tr!("First Aid process has completed."), &Self::named(&tr!("The volume “{name}” appears to be OK."), &v.name), &tr!("Done"));
            }
            Ok(false) => {
                let (can, _) = self.client.can("Repair", &v.fs_type).await;
                if !can {
                    self.remount(&v, was).await;
                    self.alert(&Self::named(&tr!("First Aid found problems on “{name}”."), &v.name), &tr!("They can't be repaired here."), &tr!("Done"));
                    return;
                }
                let me = self.clone();
                sonata_ui::dialog::alert(&Self::named(&tr!("First Aid found problems on “{name}”."), &v.name), &tr!("Do you want to repair them now?"), &[("cancel", tr!("Not Now"), ""), ("repair", tr!("Repair"), "default")], move |rid| {
                    let (me, v, repair) = (me.clone(), v.clone(), rid == "repair");
                    glib::spawn_future_local(async move {
                        if repair {
                            me.repair(v, was).await;
                        } else {
                            me.remount(&v, was).await;
                        }
                    });
                }, Some(self.win.upcast_ref()));
            }
        }
    }

    async fn remount(&self, v: &Volume, was: bool) {
        if was {
            let _ = self.client.mount(&v.fs_path).await;
        }
    }

    async fn repair(self: &Rc<Self>, v: Volume, was: bool) {
        self.set_busy(&Self::named(&tr!("Repairing “{name}”…"), &v.name));
        let res = self.client.repair(&v.fs_path).await;
        self.set_busy("");
        match res {
            Err(e) => self.failed(&tr!("First Aid failed."), &e),
            Ok(ok) => {
                self.remount(&v, was).await;
                if ok {
                    self.alert(&tr!("First Aid process has completed."), &Self::named(&tr!("The volume “{name}” was repaired."), &v.name), &tr!("Done"));
                } else {
                    self.alert(&Self::named(&tr!("“{name}” couldn't be repaired."), &v.name), &tr!("Copy its data to another disk, then erase it."), &tr!("Done"));
                }
            }
        }
    }

    // info ---------------------------------------------------------------------------------------
    fn info(&self) {
        let Some(item) = self.selected.borrow().clone() else { return };
        let grid = gtk::Grid::builder().column_spacing(14).row_spacing(4).css_classes(["du-infopanel"]).build();
        for (i, (k, val)) in info_fields(&item).iter().enumerate() {
            grid.attach(&gtk::Label::builder().label(k.as_str()).xalign(1.0).css_classes(["du-key"]).build(), 0, i as i32, 1, 1);
            grid.attach(&gtk::Label::builder().label(val.as_str()).xalign(0.0).selectable(true).css_classes(["du-value"]).ellipsize(gtk::pango::EllipsizeMode::Middle).max_width_chars(40).build(), 1, i as i32, 1, 1);
        }
        // a form-like panel: the windows' close dot at its corner (as every dialog)
        sonata_ui::panel::popup_with(&self.btn["info"], &grid, sonata_ui::panel::Options { close: true, ..Default::default() });
    }

    // -- input -------------------------------------------------------------------------------------
    fn context_menu(self: &Rc<Self>, g: &gtk::GestureClick, x: f64, y: f64) {
        use sonata_ui::menu::{popup, Item as MI, Options};
        let Some(row) = self.list.row_at_y(y as i32) else { return };
        let Some(key) = self.rows.borrow().iter().find(|r| r.0 == row).and_then(|r| r.1.clone()) else { return };
        g.set_state(gtk::EventSequenceState::Claimed);
        self.list.select_row(Some(&row));
        let item = find(&self.disks.borrow(), &key.0, key.1);
        let a = self.actions(item.as_ref());
        let call = |f: fn(&Rc<DiskUtility>)| {
            let w = Rc::downgrade(self);
            move || {
                if let Some(m) = w.upgrade() {
                    f(&m);
                }
            }
        };
        popup(&self.list, vec![
            vec![MI::new(&sonata_i18n::gettext(a.verb), call(|m| m.mount_toggle())).enabled(a.mount), MI::new(&tr!("Eject"), call(|m| m.eject(None))).enabled(a.eject)],
            vec![MI::new(&tr!("Rename…"), call(|m| m.rename())).enabled(a.rename), MI::new(&tr!("Erase…"), call(|m| m.erase())).enabled(a.erase), MI::new(&tr!("Run First Aid…"), call(|m| m.first_aid())).enabled(a.first_aid)],
            vec![MI::new(&tr!("Get Info"), call(|m| m.info())).enabled(a.info)],
        ], Options::at(x, y));
    }

    fn key(self: &Rc<Self>, keyval: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType as M};
        if !state.intersects(M::CONTROL_MASK | M::SUPER_MASK) {
            return false;
        }
        let shift = state.contains(M::SHIFT_MASK);
        let a = self.actions(None);
        match keyval.to_lower() {
            Key::i if a.info => self.info(),
            Key::e if shift && a.erase => self.erase(),
            Key::e if !shift && a.eject => self.eject(None),
            Key::r if a.rename => self.rename(),
            Key::f if shift && a.first_aid => self.first_aid(),
            Key::w => self.win.close(),
            Key::i | Key::e | Key::r => {}
            Key::f if shift => {}
            _ => return false,
        }
        true
    }
}

fn yes_no(b: bool) -> String {
    if b { tr!("Yes") } else { tr!("No") }
}

fn connection(d: &Disk) -> String {
    let b = model::bus_name(&d.bus);
    if !b.is_empty() {
        b
    } else if d.is_image {
        "Loop".into()
    } else {
        tr!("Internal")
    }
}

fn table_or_none(t: &str) -> String {
    if t.is_empty() { tr!("None") } else { model::table_name(t) }
}

/// The info table under the bar: (key, value) pairs, two to a line.
fn info_rows(item: &Item, usage: Option<(u64, u64)>) -> Vec<(String, String)> {
    let dash = "—".to_string();
    let size = |n: u64| sonata_ui::fmt::size(n);
    match item {
        Item::Disk(d) => {
            let n = d.volumes.iter().filter(|v| v.listed()).count();
            vec![
                (tr!("Location"), if d.is_image { tr!("Disk Image") } else if d.external() { tr!("External") } else { tr!("Internal") }),
                (tr!("Capacity"), size(d.size)),
                (tr!("Connection"), connection(d)),
                (tr!("Child count"), n.to_string()),
                (tr!("Partition Map"), table_or_none(&d.table)),
                (tr!("Type"), d.media_kind()),
                (tr!("S.M.A.R.T. status"), sonata_i18n::gettext(if d.smart.is_empty() { n_!("Not Supported") } else { &d.smart })),
                (tr!("Device"), d.device.clone()),
            ]
        }
        Item::Vol(v, d) => vec![
            (tr!("Mount Point"), if v.mounted() { v.mount_point().into() } else if v.locked() { tr!("Locked") } else { tr!("Not Mounted") }),
            (tr!("Type"), v.kind()),
            (tr!("Capacity"), size(v.size)),
            (tr!("Label"), if v.label.is_empty() { dash.clone() } else { v.label.clone() }),
            (tr!("Available"), usage.map(|u| size(u.1)).unwrap_or(dash.clone())),
            (tr!("Connection"), connection(d)),
            (tr!("Used"), usage.map(|u| size(u.0)).unwrap_or(dash.clone())),
            (tr!("Device"), v.device.clone()),
            ("UUID".into(), if v.uuid.is_empty() { dash.clone() } else { v.uuid.clone() }),
            (tr!("Partition Map"), table_or_none(&d.table)),
        ],
    }
}

/// Get Info's partition map: an unknown scheme as UDisks names it (the
/// info table under the bar upper-cases it; window.py's info_fields doesn't).
fn info_table(t: &str) -> String {
    match t {
        "" => tr!("None"),
        "gpt" | "dos" => model::table_name(t),
        other => other.to_string(),
    }
}

/// Get Info's fields.
fn info_fields(item: &Item) -> Vec<(String, String)> {
    let dash = || "—".to_string();
    let capacity = |n: u64| sonata_i18n::fmt(&tr!("{size} ({bytes} bytes)"), &[("size", &sonata_ui::fmt::size(n)), ("bytes", &thousands(n))]);
    match item {
        Item::Disk(d) => {
            let mut out = vec![
                (tr!("Name"), d.name.clone()),
                (tr!("Type"), d.kind()),
                (tr!("Device"), d.device.clone()),
                (tr!("Model"), if d.model.is_empty() { dash() } else { d.model.clone() }),
                (tr!("Vendor"), if d.vendor.is_empty() { dash() } else { d.vendor.clone() }),
                (tr!("Capacity"), capacity(d.size)),
                (tr!("Partition Map"), info_table(&d.table)),
                (tr!("Media"), d.media_kind()),
                (tr!("Removable"), yes_no(d.removable)),
                (tr!("Ejectable"), yes_no(d.ejectable)),
                (tr!("S.M.A.R.T. Status"), sonata_i18n::gettext(if d.smart.is_empty() { n_!("Not Supported") } else { &d.smart })),
            ];
            if d.is_image {
                out.push((tr!("Image File"), d.image.clone()));
            }
            out
        }
        Item::Vol(v, d) => vec![
            (tr!("Name"), v.name.clone()),
            (tr!("Type"), v.kind()),
            (tr!("Device"), v.device.clone()),
            (tr!("Mount Point"), if v.mounted() { v.mount_point().into() } else { dash() }),
            (tr!("Capacity"), capacity(v.size)),
            (tr!("Label"), if v.label.is_empty() { dash() } else { v.label.clone() }),
            ("UUID".into(), if v.uuid.is_empty() { dash() } else { v.uuid.clone() }),
            (tr!("Partition Number"), if v.number > 0 { v.number.to_string() } else { dash() }),
            (tr!("Partition Offset"), sonata_i18n::fmt(&tr!("{bytes} bytes"), &[("bytes", &thousands(v.offset))])),
            (tr!("Encrypted"), yes_no(v.encrypted)),
            (tr!("System Volume"), yes_no(v.protected())),
            (tr!("Disk"), d.name.clone()),
        ],
    }
}

/// `sonata2-rs diskutil`: one Disk Manager window; opening it again brings it forward.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let state: Rc<RefCell<Option<Rc<DiskUtility>>>> = Rc::default();
    app.connect_activate(move |app| {
        if let Some(d) = state.borrow().as_ref() {
            d.win.present();
            return;
        }
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        register_css();
        let d = DiskUtility::new(app);
        d.win.present();
        state.replace(Some(d));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake() -> Vec<Disk> {
        sonata_i18n::use_language("en");
        model::parse(&model::tests_tree())
    }

    #[test]
    fn what_the_selection_allows() {
        let ds = fake();
        let root = find(&ds, "/org/freedesktop/UDisks2/block_devices/nvme0n1p2", false).unwrap();
        let a = actions_for(Some(&root), true);
        // the running system: no Erase, no First Aid, no Unmount
        assert!(!a.erase && !a.first_aid && !a.mount && a.rename && a.info);
        assert_eq!(a.verb, "Unmount");
        let nvme = find(&ds, "/org/freedesktop/UDisks2/block_devices/nvme0n1", true).unwrap();
        assert!(!actions_for(Some(&nvme), true).erase && !actions_for(Some(&nvme), true).eject);
        let vault = find(&ds, "/org/freedesktop/UDisks2/block_devices/sdb2", false).unwrap();
        let a = actions_for(Some(&vault), true);
        assert_eq!(a.verb, "Unlock");
        assert!(a.mount && a.erase && a.eject && !a.rename && !a.first_aid);
        let games = find(&ds, "/org/freedesktop/UDisks2/block_devices/sda1", false).unwrap();
        let a = actions_for(Some(&games), true);
        assert!(a.erase && a.first_aid && a.mount && !a.eject); // internal: no Eject
        assert_eq!(a.verb, "Mount");
        // busy (or UDisks gone): nothing
        assert_eq!(actions_for(Some(&games), false), Actions { first_aid: false, erase: false, rename: false, mount: false, eject: false, info: false, verb: "Mount" });
    }

    fn settle(ms: u64) {
        let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
        let ctx = glib::MainContext::default();
        while std::time::Instant::now() < end {
            while ctx.iteration(false) {}
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }

    fn rows(d: &DiskUtility) -> Vec<(Option<(String, bool)>, String)> {
        let label_of = |r: &gtk::ListBoxRow| {
            let c = r.child().unwrap();
            let lbl = c.downcast_ref::<gtk::Label>().cloned().or_else(|| c.first_child().and_then(|x| x.next_sibling()).and_downcast::<gtk::Label>());
            lbl.map(|l| l.label().to_string()).unwrap_or_default()
        };
        d.rows.borrow().iter().map(|(r, k)| (k.clone(), label_of(r))).collect()
    }

    /// test_diskutil.py's WindowTest: without UDisks the window says so;
    /// with a tree, the sidebar, the selection and what it allows.
    #[test]
    fn window_with_and_without_udisks() {
        if gtk::init().is_err() {
            return; // (no display)
        }
        sonata_i18n::use_language("en");
        sonata_ui::setup();
        register_css();
        // the real client with no UDisks to reach (a system bus that isn't there): the message
        std::env::set_var("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/nonexistent/sonata-test-bus");
        let app = gtk::Application::new(Some("io.test.diskutil.rs"), gio::ApplicationFlags::NON_UNIQUE);
        let _ = app.register(gio::Cancellable::NONE);
        let win = DiskUtility::new(&app);
        win.win.present();
        let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while win.stack.visible_child_name().as_deref() != Some("missing") && std::time::Instant::now() < end {
            settle(20);
        }
        assert_eq!(win.stack.visible_child_name().as_deref(), Some("missing"));
        assert!(!win.sidebar.is_visible());
        assert!(["erase", "mount", "eject", "info", "partition"].iter().all(|k| !win.btn[k].is_sensitive()));
        win.win.destroy();

        let win = DiskUtility::build(&app, false);
        win.win.present();
        win.load(&model::tests_tree());
        settle(100);
        assert_eq!(win.stack.visible_child_name().as_deref(), Some("detail"));
        let rs = rows(&win);
        let heads: Vec<&str> = rs.iter().filter(|r| r.0.is_none()).map(|r| r.1.as_str()).collect();
        assert_eq!(heads, ["Internal", "External", "Disk Images"]);
        let names: Vec<&str> = rs.iter().filter(|r| r.0.is_some()).map(|r| r.1.as_str()).collect();
        assert!(names.contains(&"Vault"));
        assert!(!names.contains(&"Swap")); // swap stays out of the sidebar
        // the system disk: nothing destructive
        let sel = win.selected.borrow().clone();
        assert!(matches!(&sel, Some(Item::Disk(d)) if d.device == "/dev/nvme0n1"));
        let a = win.actions(None);
        assert!(!a.erase && !a.eject && a.info);
        // the USB stick's ExFAT volume
        let i = rs.iter().position(|r| r.1 == "BACKUP").unwrap();
        let row = win.list.row_at_index(i as i32).unwrap();
        win.list.select_row(Some(&row));
        settle(100);
        let a = win.actions(None);
        assert!(a.erase && a.mount && a.eject && a.rename && a.first_aid);
        assert_eq!(a.verb, "Unmount");
        assert_eq!(win.name_label.label(), "BACKUP");
        assert!(win.sub_label.label().contains("ExFAT"));
        assert!(!win.btn["partition"].is_sensitive());
        test_info_panel_without_close_dot(&win);
        // live update: the stick is unplugged; something stays selected
        let tree: Tree = model::tests_tree().into_iter().filter(|(k, _)| !k.contains("sdb") && !k.contains("SanDisk")).collect();
        win.load(&tree);
        settle(50);
        assert!(!win.disks.borrow().iter().any(|d| d.device == "/dev/sdb"));
        assert!(win.selected.borrow().is_some());
        win.win.destroy();
    }

    /// Get Info's panel had no close dot: window.py opens it with
    /// close=True (every form-like panel has the windows' close dot).
    fn test_info_panel_without_close_dot(win: &DiskUtility) {
        win.info();
        let mut pop = None;
        let mut c = win.btn["info"].first_child();
        while let Some(x) = c {
            if let Some(p) = x.downcast_ref::<gtk::Popover>() {
                pop = Some(p.clone());
            }
            c = x.next_sibling();
        }
        let pop = pop.expect("the Info panel hangs from its button");
        assert!(sonata_ui::window::has_window_buttons(&pop.child().unwrap()));
        pop.popdown();
    }

    #[test]
    fn bar_widths_keep_tiny_partitions_visible() {
        let w = widths(&[1, 1_000_000, 1], 100.0, 4.0);
        assert!(w[0] > 3.0 && w[2] > 3.0);
        assert!((w.iter().sum::<f64>() - 100.0).abs() < 1e-9);
    }

    /// Disk Manager opened at 900x600 on a 2560x1440 display the first
    /// time: the Python opens a document window at about two thirds of it
    /// (roomy=True); never smaller than its own size on a small one.
    #[test]
    fn test_first_open_too_small_on_big_displays() {
        use sonata_ui::window::first_size;
        assert_eq!(first_size(None, 900, 600, true, Some((2560, 1440))), (1689, 936, false));
        assert_eq!(first_size(None, 900, 600, true, Some((1280, 720))), (900, 600, false));
    }

    /// Get Info showed an unknown partition scheme upper-cased ("ATARI");
    /// window.py's info_fields shows it as UDisks names it.
    #[test]
    fn test_info_partition_map_upper_cased() {
        sonata_i18n::use_language("en");
        assert_eq!(info_table("atari"), "atari");
        assert_eq!(info_table("gpt"), "GUID Partition Map");
        assert_eq!(info_table(""), "None");
    }

    #[test]
    fn numbers_with_commas() {
        assert_eq!(thousands(0), "0");
        assert_eq!(thousands(1_000_204_886_016), "1,000,204,886,016");
    }

    #[test]
    fn info_of_a_volume() {
        let ds = fake();
        let root = find(&ds, "/org/freedesktop/UDisks2/block_devices/nvme0n1p2", false).unwrap();
        let f = info_fields(&root);
        assert!(f.contains(&("System Volume".into(), "Yes".into())));
        assert!(f.contains(&("Partition Offset".into(), "513,000,000 bytes".into())));
        let rows = info_rows(&root, Some((50, 150)));
        assert_eq!(rows[0], ("Mount Point".into(), "/".into()));
    }
}
