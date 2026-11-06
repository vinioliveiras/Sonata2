//! Task Manager's building blocks (activity/widgets.py): the history graph
//! (big graph and sidebar sparklines), the memory composition bar, table
//! columns with live cells and heat-map tint, toolbar buttons with a
//! label; and the cells' formats (pages.py). The CSS is widgets.py's own.

use crate::rows::{TaskRow, V};
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene, pango};
use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::rc::Rc;

/// graph points: the last ~60 s at 2 s a sample
pub const HISTORY: usize = 31;

pub fn ncpu() -> f64 {
    std::thread::available_parallelism().map(|n| n.get() as f64).unwrap_or(1.0)
}

// -- formats / heat ---------------------------------------------------------------------------
pub fn fmt_cpu(v: f64) -> String {
    format!("{:.1}%", v / ncpu())
}

pub fn fmt_mem(v: f64) -> String {
    format!("{:.1} MB", v / 1024f64.powi(2))
}

pub fn fmt_disk(v: f64) -> String {
    format!("{:.1} MB/s", v / 1024f64.powi(2))
}

pub fn fmt_net(v: f64) -> String {
    if v < 0.0 { "–".into() } else { fmt_bits(v * 8.0) }
}

/// Windows' network speeds: "0 Kbps", "12.4 Kbps", "3.1 Mbps".
pub fn fmt_bits(bits: f64) -> String {
    if bits < 1e6 {
        if bits >= 100.0 { format!("{:.1} Kbps", bits / 1000.0) } else { "0 Kbps".into() }
    } else if bits < 1e9 {
        format!("{:.1} Mbps", bits / 1e6)
    } else {
        format!("{:.2} Gbps", bits / 1e9)
    }
}

pub fn fmt_gpu(v: f64) -> String {
    format!("{v:.1}%")
}

fn level(frac: f64, steps: [f64; 5]) -> usize {
    steps.iter().filter(|s| frac >= **s).count()
}

pub fn heat_cpu(v: f64) -> usize {
    level(v / (100.0 * ncpu()), [0.005, 0.05, 0.15, 0.3, 0.6])
}

thread_local! {
    /// the memory a heat level is measured against (set before cells bind)
    pub static MEM_TOTAL: Cell<f64> = const { Cell::new(1.0) };
}

pub fn heat_mem(v: f64) -> usize {
    level(v / MEM_TOTAL.with(Cell::get).max(1.0), [0.005, 0.02, 0.05, 0.1, 0.25])
}

pub fn heat_disk(v: f64) -> usize {
    level(v, [1e4, 1e5, 1e6, 1e7, 5e7])
}

pub fn heat_gpu(v: f64) -> usize {
    level(v / 100.0, [0.005, 0.05, 0.15, 0.3, 0.6])
}

// -- toolbar ----------------------------------------------------------------------------------
/// A glass-toolbar button with a glyph and a word (Windows' command bar).
pub fn tool_button(icon: &str, label: &str, callback: impl Fn() + 'static, tooltip: Option<&str>) -> gtk::Button {
    let b = gtk::Button::builder().css_classes(["tool"]).can_focus(false).tooltip_text(tooltip.unwrap_or(label)).valign(gtk::Align::Center).build();
    let bx = gtk::Box::new(gtk::Orientation::Horizontal, 5);
    bx.append(&gtk::Image::from_icon_name(icon));
    if !label.is_empty() {
        bx.append(&gtk::Label::new(Some(label)));
    }
    b.set_child(Some(&bx));
    b.connect_clicked(move |_| callback());
    b
}

// -- graphs -----------------------------------------------------------------------------------
/// A value history shared by a sparkline and the big graph.
#[derive(Default)]
pub struct Series {
    pub values: RefCell<VecDeque<f64>>,
}

impl Series {
    pub fn push(&self, v: f64) {
        let mut q = self.values.borrow_mut();
        q.push_back(v);
        while q.len() > HISTORY {
            q.pop_front();
        }
    }

    pub fn max(&self) -> f64 {
        self.values.borrow().iter().copied().fold(0.0, f64::max)
    }
}

/// [(colour token, series)]
pub type Lines = Vec<(&'static str, Rc<Series>)>;

fn source(cr: &gtk::cairo::Context, c: &gdk::RGBA, alpha: f64) {
    cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64 * alpha);
}

fn rounded(cr: &gtk::cairo::Context, w: f64, h: f64, r: f64) {
    use std::f64::consts::PI;
    cr.new_sub_path();
    cr.arc(w - r, r, r, -PI / 2.0, 0.0);
    cr.arc(w - r, h - r, r, 0.0, PI / 2.0);
    cr.arc(r, h - r, r, PI / 2.0, PI);
    cr.arc(r, r, r, PI, 1.5 * PI);
    cr.close_path();
}

mod imp_graph {
    use super::*;

    #[derive(Default)]
    pub struct Graph {
        pub lines: RefCell<Lines>,
        pub maximum: Cell<Option<f64>>,
        pub grid: Cell<bool>,
        pub radius: Cell<f64>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Graph {
        const NAME: &'static str = "SonataRsTaskGraph";
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
            let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, w as f32, h as f32));
            let ink = |t: &str| sonata_ui::theme::rgba(t);
            let r = self.radius.get();
            rounded(&cr, w, h, r);
            cr.clip();
            source(&cr, &ink("content_bg"), 1.0);
            let _ = cr.paint();
            if self.grid.get() {
                let grid = ink("separator");
                source(&cr, &grid, 1.0);
                for i in 1..4 {
                    cr.rectangle(0.0, (h * i as f64 / 4.0).round(), w, 1.0);
                }
                for i in 1..6 {
                    cr.rectangle((w * i as f64 / 6.0).round(), 0.0, 1.0, h);
                }
                let _ = cr.fill();
            }
            let top = obj.top();
            let step = w / (HISTORY - 1) as f64;
            for (token, series) in self.lines.borrow().iter() {
                let vals: Vec<f64> = series.values.borrow().iter().copied().collect();
                if vals.len() < 2 {
                    continue;
                }
                let n = vals.len();
                let col = ink(token);
                let y_of = |v: f64| h - (v / top).min(1.0) * (h - 2.0) - 1.0;
                let pts: Vec<(f64, f64)> = vals.iter().enumerate().map(|(i, v)| (w - (n - 1 - i) as f64 * step, y_of(*v))).collect();
                cr.move_to(pts[0].0, pts[0].1);
                for (x, y) in &pts[1..] {
                    cr.line_to(*x, *y);
                }
                cr.line_to(pts[n - 1].0, y_of(0.0));
                cr.line_to(pts[0].0, y_of(0.0));
                cr.close_path();
                source(&cr, &col, 0.22);
                let _ = cr.fill();
                cr.move_to(pts[0].0, pts[0].1);
                for (x, y) in &pts[1..] {
                    cr.line_to(*x, *y);
                }
                source(&cr, &col, 1.0);
                cr.set_line_width(if self.grid.get() { 1.5 } else { 1.2 });
                let _ = cr.stroke();
            }
            cr.reset_clip();
            rounded(&cr, w, h, r);
            source(&cr, &ink("separator"), 1.0);
            cr.set_line_width(1.0);
            let _ = cr.stroke();
        }
    }
}

glib::wrapper! {
    /// A live history graph. maximum: a fixed top (100 for %) or None to
    /// follow the data; grid: quarter lines (the big graph).
    pub struct Graph(ObjectSubclass<imp_graph::Graph>) @extends gtk::Widget;
}

impl Graph {
    pub fn new(lines: Lines, maximum: Option<f64>, grid: bool, width: i32, height: i32, radius: f64) -> Self {
        let g: Self = glib::Object::builder().property("hexpand", true).property("vexpand", false).build();
        g.imp().lines.replace(lines);
        g.imp().maximum.set(maximum);
        g.imp().grid.set(grid);
        g.imp().radius.set(radius);
        g.set_size_request(width, height);
        let w = g.downgrade();
        sonata_ui::theme::on_change(move || {
            if let Some(g) = w.upgrade() {
                g.queue_draw();
            }
        });
        g
    }

    pub fn set_lines(&self, lines: Lines, maximum: Option<f64>) {
        self.imp().lines.replace(lines);
        self.imp().maximum.set(maximum);
        self.queue_draw();
    }

    pub fn top(&self) -> f64 {
        if let Some(m) = self.imp().maximum.get().filter(|m| *m > 0.0) {
            return m;
        }
        let peak = self.imp().lines.borrow().iter().map(|(_, s)| s.max()).fold(0.0, f64::max);
        (peak * 1.15).max(1.0)
    }
}

mod imp_bar {
    use super::*;

    #[derive(Default)]
    pub struct CompositionBar {
        pub values: RefCell<[f64; 4]>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for CompositionBar {
        const NAME: &'static str = "SonataRsTaskCompositionBar";
        type Type = super::CompositionBar;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for CompositionBar {}

    impl WidgetImpl for CompositionBar {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width() as f64, obj.height() as f64);
            if w <= 0.0 || h <= 0.0 {
                return;
            }
            let vals = *self.values.borrow();
            let total: f64 = vals.iter().map(|v| v.max(0.0)).sum();
            let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, w as f32, h as f32));
            let ink = |t: &str| sonata_ui::theme::rgba(t);
            rounded(&cr, w, h, 4.0);
            cr.clip();
            source(&cr, &ink("content_bg"), 1.0);
            let _ = cr.paint();
            let mut x = 0.0;
            if total > 0.0 {
                // in use | modified | standby | free
                for (i, (token, alpha)) in [("sys_purple", 1.0), ("sys_orange", 0.8), ("sys_purple", 0.35), ("separator", 1.0)].iter().enumerate() {
                    let part = w * vals[i].max(0.0) / total;
                    source(&cr, &ink(token), *alpha);
                    cr.rectangle(x, 0.0, part, h);
                    let _ = cr.fill();
                    x += part;
                    if i < 3 {
                        source(&cr, &ink("content_bg"), 1.0);
                        cr.rectangle(x - 1.0, 0.0, 1.0, h);
                        let _ = cr.fill();
                    }
                }
            }
            cr.reset_clip();
            rounded(&cr, w, h, 4.0);
            source(&cr, &ink("sys_purple"), 1.0);
            cr.set_line_width(1.0);
            let _ = cr.stroke();
        }
    }
}

glib::wrapper! {
    /// Windows' "Memory composition": in use | modified | standby | free.
    pub struct CompositionBar(ObjectSubclass<imp_bar::CompositionBar>) @extends gtk::Widget;
}

impl CompositionBar {
    pub fn new() -> Self {
        let b: Self = glib::Object::builder().property("hexpand", true).build();
        b.set_size_request(200, 34);
        // its colours are tokens: redrawn when Light / Dark or the accent change
        let w = b.downgrade();
        sonata_ui::theme::on_change(move || {
            if let Some(b) = w.upgrade() {
                b.queue_draw();
            }
        });
        b
    }

    pub fn set_values(&self, v: [f64; 4]) {
        if *self.imp().values.borrow() != v {
            self.imp().values.replace(v);
            self.queue_draw();
        }
    }
}

impl Default for CompositionBar {
    fn default() -> Self {
        Self::new()
    }
}

// -- table columns ----------------------------------------------------------------------------
const HEAT_CLASSES: [&str; 6] = ["heat-0", "heat-1", "heat-2", "heat-3", "heat-4", "heat-5"];

/// The row a list item shows (a tree row's own item).
pub fn row_of(obj: Option<glib::Object>) -> Option<TaskRow> {
    let obj = obj?;
    match obj.downcast::<gtk::TreeListRow>() {
        Ok(tr) => tr.item().and_downcast::<TaskRow>(),
        Err(o) => o.downcast::<TaskRow>().ok(),
    }
}

// A cell keeps the row it shows (row_at) and the handler that redraws it.
fn set_cell_row(w: &impl IsA<glib::Object>, row: Option<(TaskRow, Option<glib::SignalHandlerId>)>) {
    unsafe {
        if let Some((old, Some(id))) = w.steal_data::<(TaskRow, Option<glib::SignalHandlerId>)>("tm-row") {
            old.disconnect(id);
        }
        if let Some(r) = row {
            w.set_data("tm-row", r);
        }
    }
}

fn cell_row(w: &impl IsA<glib::Object>) -> Option<TaskRow> {
    unsafe { w.data::<(TaskRow, Option<glib::SignalHandlerId>)>("tm-row").map(|p| p.as_ref().0.clone()) }
}

pub type Fmt = Rc<dyn Fn(&V) -> String>;
pub type Heat = Rc<dyn Fn(&V) -> usize>;

/// A sorter on one value (ties: the pid).
pub fn sorter(key: &'static str) -> gtk::CustomSorter {
    gtk::CustomSorter::new(move |a, b| {
        let (Some(a), Some(b)) = (row_of(Some(a.clone())), row_of(Some(b.clone()))) else { return gtk::Ordering::Equal };
        a.get(key).cmp(&b.get(key)).then(a.pid().cmp(&b.pid())).into()
    })
}

/// A column whose label follows the row's value `prop` (formatted by
/// fmt); heat(value) -> 0..5 tints the cell.
#[allow(clippy::too_many_arguments)]
pub fn text_column(view: &gtk::ColumnView, title: &str, prop: &'static str, fmt: Option<Fmt>, width: i32, numeric: bool, heat: Option<Heat>, sorter: Option<gtk::Sorter>, expand: bool, dim: bool) -> gtk::ColumnViewColumn {
    let f = gtk::SignalListItemFactory::new();
    f.connect_setup(move |_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        let lbl = gtk::Label::builder().xalign(if numeric { 1.0 } else { 0.0 }).ellipsize(pango::EllipsizeMode::End).single_line_mode(true).build();
        lbl.add_css_class("tm-cell");
        if numeric {
            lbl.add_css_class("tm-num");
        }
        if dim {
            lbl.add_css_class("tm-dim");
        }
        item.set_child(Some(&lbl));
    });
    let refresh = Rc::new(move |row: &TaskRow, lbl: &gtk::Label| {
        let v = row.get(prop);
        let text = match &fmt {
            Some(f) => f(&v),
            None => v.s(),
        };
        if lbl.label() != text {
            lbl.set_label(&text);
        }
        let level = heat.as_ref().map(|h| h(&v));
        // restyle only when the tint changes
        for (i, c) in HEAT_CLASSES.iter().enumerate() {
            let on = level == Some(i);
            if on != lbl.has_css_class(c) {
                if on {
                    lbl.add_css_class(c);
                } else {
                    lbl.remove_css_class(c);
                }
            }
        }
    });
    let r = refresh.clone();
    f.connect_bind(move |_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        let (Some(row), Some(lbl)) = (row_of(item.item()), item.child().and_downcast::<gtk::Label>()) else { return };
        r(&row, &lbl);
        let (r2, l) = (r.clone(), lbl.downgrade());
        let id = row.connect_changed(move |row| {
            if let Some(l) = l.upgrade() {
                r2(row, &l);
            }
        });
        set_cell_row(&lbl, Some((row, Some(id))));
    });
    f.connect_unbind(|_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        if let Some(lbl) = item.child() {
            set_cell_row(&lbl, None);
        }
    });
    let c = gtk::ColumnViewColumn::builder().title(title).factory(&f).resizable(true).expand(expand).fixed_width(width).build();
    c.set_sorter(sorter.as_ref());
    view.append_column(&c);
    c
}

/// Icon + name, inside a tree expander when the model is a TreeListModel.
pub fn name_column(view: &gtk::ColumnView, title: &str, width: i32, tree: bool, sorter: Option<gtk::Sorter>) -> gtk::ColumnViewColumn {
    let f = gtk::SignalListItemFactory::new();
    f.connect_setup(move |_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        let bx = gtk::Box::builder().spacing(6).css_classes(["tm-name"]).build();
        bx.append(&gtk::Image::builder().pixel_size(16).build());
        bx.append(&gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::End).hexpand(true).build());
        if tree {
            item.set_child(Some(&gtk::TreeExpander::builder().child(&bx).indent_for_icon(true).build()));
        } else {
            item.set_child(Some(&bx));
        }
    });
    f.connect_bind(move |_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        let Some(child) = item.child() else { return };
        let bx = match child.downcast_ref::<gtk::TreeExpander>() {
            Some(exp) => {
                exp.set_list_row(item.item().and_downcast_ref::<gtk::TreeListRow>());
                exp.child().and_downcast::<gtk::Box>()
            }
            None => child.clone().downcast::<gtk::Box>().ok(),
        };
        let (Some(bx), Some(row)) = (bx, row_of(item.item())) else { return };
        let img = bx.first_child().and_downcast::<gtk::Image>().unwrap();
        let lbl = bx.last_child().and_downcast::<gtk::Label>().unwrap();
        match row.icon() {
            Some(i) => img.set_from_gicon(&i),
            None => img.clear(), // macOS/Windows: no icon for processes that aren't apps
        }
        lbl.set_label(&row.name());
        let l = lbl.downgrade();
        let id = row.connect_changed(move |r| {
            if let Some(l) = l.upgrade() {
                let n = r.name();
                if l.label() != n {
                    l.set_label(&n);
                }
            }
        });
        set_cell_row(&child, Some((row, Some(id))));
    });
    f.connect_unbind(move |_, item| {
        let item = item.downcast_ref::<gtk::ListItem>().unwrap();
        if let Some(child) = item.child() {
            if let Some(exp) = child.downcast_ref::<gtk::TreeExpander>() {
                exp.set_list_row(None);
            }
            set_cell_row(&child, None);
        }
    });
    let c = gtk::ColumnViewColumn::builder().title(title).factory(&f).resizable(true).expand(true).fixed_width(width).build();
    c.set_sorter(sorter.as_ref());
    view.append_column(&c);
    c
}

/// The row object under (x, y) of a ColumnView (cells carry it).
pub fn row_at(view: &gtk::ColumnView, x: f64, y: f64) -> Option<TaskRow> {
    let mut w = view.pick(x, y, gtk::PickFlags::DEFAULT);
    while let Some(cur) = w {
        if cur == *view.upcast_ref::<gtk::Widget>() {
            return None;
        }
        if let Some(r) = cell_row(&cur) {
            return Some(r);
        }
        w = cur.parent();
    }
    None
}

/// Only the last visible column expands -- kept so when columns are added,
/// shown or hidden.
pub fn fill_last(view: &gtk::ColumnView) {
    let cols = view.columns();
    let update = move |cols: &gio::ListModel| {
        let shown: Vec<gtk::ColumnViewColumn> = (0..cols.n_items()).filter_map(|i| cols.item(i).and_downcast::<gtk::ColumnViewColumn>()).filter(|c| c.is_visible()).collect();
        for (i, c) in shown.iter().enumerate() {
            let want = i == shown.len() - 1;
            if c.expands() != want {
                c.set_expand(want);
            }
        }
    };
    let u = update.clone();
    cols.connect_items_changed(move |cols, pos, _, added| {
        for i in pos..pos + added {
            if let Some(c) = cols.item(i).and_downcast::<gtk::ColumnViewColumn>() {
                let (u, cl) = (u.clone(), cols.downgrade());
                c.connect_visible_notify(move |_| {
                    if let Some(cl) = cl.upgrade() {
                        u(&cl);
                    }
                });
            }
        }
        u(cols.upcast_ref());
    });
    update(cols.upcast_ref());
}

pub fn table() -> gtk::ColumnView {
    let v = gtk::ColumnView::builder().show_column_separators(false).show_row_separators(false).reorderable(false).css_classes(["tm-list"]).build();
    fill_last(&v); // columns keep their widths; the last one fills
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_like_task_manager() {
        assert_eq!(fmt_bits(50.0), "0 Kbps");
        assert_eq!(fmt_bits(12_400.0), "12.4 Kbps");
        assert_eq!(fmt_bits(3_100_000.0), "3.1 Mbps");
        assert_eq!(fmt_bits(2.5e9), "2.50 Gbps");
        assert_eq!(fmt_net(-1.0), "–");
        assert_eq!(fmt_mem(1.5 * 1024.0 * 1024.0), "1.5 MB");
        assert_eq!(heat_disk(0.0), 0);
        assert_eq!(heat_disk(2e7), 4);
        assert_eq!(heat_gpu(70.0), 5);
    }
}
