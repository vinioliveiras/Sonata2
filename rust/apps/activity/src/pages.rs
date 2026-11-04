//! Task Manager's list pages (activity/pages.py): Processes (Apps /
//! Background / System processes, apps expand to their processes,
//! heat-map cells), App history, Startup apps, Users, Details (every
//! process) and Services.
//!
//! Each page has a widget, toolbar buttons shown while it is the current
//! page, update(snapshot), search(text), shown(), and for the process
//! pages selected_rows() / context menus.

use crate::manage::{self, Service, StartupEntry};
use crate::rows::{sync_store, Kind, TaskRow, V};
use crate::widgets::{self as w, fmt_bits, name_column, row_at, sorter, table, text_column, tool_button, Fmt, Heat};
use crate::window::TaskManagerWindow;
use gtk::prelude::*;
use sonata_backend::procfs::{fmt_bytes, fmt_count, fmt_cpu_time, Snapshot};
use sonata_i18n::{fmt, n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

pub const GROUPS: [&str; 3] = [n_!("Apps"), n_!("Background processes"), n_!("System processes")];

pub trait Page {
    fn id(&self) -> &'static str;
    /// English; shown through gettext
    fn title(&self) -> &'static str;
    fn icon(&self) -> &'static str;
    fn widget(&self) -> gtk::Widget;
    fn buttons(&self) -> gtk::Box;
    fn update(&self, _snap: &Snapshot) {}
    fn search(&self, _text: &str) {}
    fn shown(&self) {}
    fn selected_rows(&self) -> Vec<TaskRow> {
        Vec::new()
    }
    /// A hidden page's table lets go of its model: an unallocated list view
    /// would otherwise build rows for up to 200 items on every change.
    fn attach(&self, _on: bool) {}
    fn view(&self) -> Option<gtk::ColumnView> {
        None
    }
    fn as_any(&self) -> &dyn std::any::Any;
}

fn scrolled(view: &gtk::ColumnView) -> gtk::ScrolledWindow {
    gtk::ScrolledWindow::builder().child(view).vexpand(true).hexpand(true).build()
}

fn f(func: fn(f64) -> String) -> Option<Fmt> {
    Some(Rc::new(move |v: &V| func(v.n())))
}

fn h(func: fn(f64) -> usize) -> Option<Heat> {
    Some(Rc::new(move |v: &V| func(v.n())))
}

fn translated() -> Option<Fmt> {
    Some(Rc::new(|v: &V| sonata_i18n::gettext(&v.s())))
}

/// Sort again; at the top, stay at the top (the list follows the row that
/// was at the top otherwise).
fn keep_top(view: &gtk::ColumnView, scroller: &gtk::ScrolledWindow, resort: impl FnOnce()) {
    let adj = scroller.vadjustment();
    let at_top = adj.value() < 1.0;
    resort();
    if at_top && view.model().is_some_and(|m| m.n_items() > 0) {
        view.scroll_to(0, None, gtk::ListScrollFlags::NONE, None);
        // ...and above the first section's header, once laid out
        glib::idle_add_local_full(glib::Priority::LOW, move || {
            adj.set_value(0.0);
            glib::ControlFlow::Break
        });
    }
}

fn attach_view(view: &gtk::ColumnView, selection: &gtk::SingleSelection, on: bool) {
    if on && view.model().is_none() {
        view.set_model(Some(selection));
    } else if !on {
        view.set_model(None::<&gtk::SelectionModel>);
    }
}

fn secondary_click(view: &gtk::ColumnView, f: impl Fn(&gtk::GestureClick, f64, f64) + 'static) {
    let g = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
    g.connect_pressed(move |g, _, x, y| f(g, x, y));
    view.add_controller(g);
}

fn menu(view: &gtk::ColumnView, sections: Vec<Vec<sonata_ui::menu::Item>>, x: f64, y: f64) {
    sonata_ui::menu::popup(view, sections, sonata_ui::menu::Options::at(x, y));
}

fn item<F: Fn() + 'static>(label: &str, f: F) -> sonata_ui::menu::Item {
    sonata_ui::menu::Item::new(label, f)
}

fn select_row(sorted: &gtk::SortListModel, selection: &gtk::SingleSelection, row: &TaskRow) {
    for i in 0..sorted.n_items() {
        if crate::widgets::row_of(sorted.item(i)).as_ref() == Some(row) {
            selection.set_selected(i);
            break;
        }
    }
}

// -- Processes --------------------------------------------------------------------------------
/// Windows' Processes: Apps (expandable), Background processes, System
/// processes; Name, Status, CPU, Memory, Disk, Network with heat tint.
pub struct ProcessesPage {
    win: Weak<TaskManagerWindow>,
    buttons: gtk::Box,
    end_btn: gtk::Button,
    props_btn: gtk::Button,
    root: gio::ListStore,
    pub nodes: RefCell<HashMap<String, TaskRow>>,
    pub counts: RefCell<[usize; 3]>,
    headers: Rc<RefCell<Vec<(glib::WeakRef<gtk::Label>, usize)>>>,
    filtered: gtk::FilterListModel,
    inner: gtk::CustomSorter,
    pub sorted: gtk::SortListModel,
    pub selection: gtk::SingleSelection,
    pub view: gtk::ColumnView,
    pub cols: Vec<(&'static str, gtk::ColumnViewColumn)>,
    order: Rc<RefCell<(&'static str, bool)>>,
    text: Rc<RefCell<String>>,
    scroller: gtk::ScrolledWindow,
}

fn group(r: &TaskRow) -> i64 {
    r.n("group") as i64
}

impl ProcessesPage {
    pub fn new(win: Weak<TaskManagerWindow>) -> Rc<Self> {
        let buttons = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let root = gio::ListStore::new::<TaskRow>();
        let tree = gtk::TreeListModel::new(root.clone(), false, false, |item| {
            let r = item.downcast_ref::<TaskRow>()?;
            r.data().children.clone().filter(|_| r.kind() == Kind::App).map(|c| c.upcast())
        });
        let text: Rc<RefCell<String>> = Rc::default();
        let t = text.clone();
        let filter = gtk::CustomFilter::new(move |o| {
            let text = t.borrow();
            if text.is_empty() {
                return true;
            }
            let Some(tr) = o.downcast_ref::<gtk::TreeListRow>() else { return true };
            let Some(row) = tr.item().and_downcast::<TaskRow>() else { return true };
            if row.name().to_lowercase().contains(&*text) || (row.kind() != Kind::App && row.pid().to_string().contains(&*text)) {
                return true;
            }
            if row.kind() == Kind::App {
                // an app shows when one of its processes matches
                return row.data().members.iter().any(|m| m.name().to_lowercase().contains(&*text));
            }
            tr.parent().and_then(|p| p.item().and_downcast::<TaskRow>()).is_some_and(|p| p.name().to_lowercase().contains(&*text))
        });
        let filtered = gtk::FilterListModel::new(Some(tree), Some(filter));
        let view = table();
        let order: Rc<RefCell<(&'static str, bool)>> = Rc::new(RefCell::new(("cpu", true)));
        let o = order.clone();
        // group first (fixed order), then the sorted column, either way
        let inner = gtk::CustomSorter::new(move |a, b| {
            let (Some(a), Some(b)) = (a.downcast_ref::<TaskRow>(), b.downcast_ref::<TaskRow>()) else { return gtk::Ordering::Equal };
            let g = group(a).cmp(&group(b));
            if g != std::cmp::Ordering::Equal {
                return g.into();
            }
            let (cid, desc) = *o.borrow();
            let mut r = a.get(cid).cmp(&b.get(cid));
            if desc {
                r = r.reverse();
            }
            r.then(a.pid().cmp(&b.pid())).into()
        });
        let section = gtk::CustomSorter::new(|a, b| {
            let g = |o: &glib::Object| o.downcast_ref::<gtk::TreeListRow>().and_then(|t| t.item().and_downcast::<TaskRow>()).map(|r| group(&r)).unwrap_or(0);
            g(a).cmp(&g(b)).into()
        });
        let sorted = gtk::SortListModel::new(Some(filtered.clone()), Some(gtk::TreeListRowSorter::new(Some(inner.clone()))));
        sorted.set_section_sorter(Some(&section));
        let selection = gtk::SingleSelection::builder().model(&sorted).autoselect(false).can_unselect(true).build();
        view.set_model(Some(&selection));
        let headers: Rc<RefCell<Vec<(glib::WeakRef<gtk::Label>, usize)>>> = Rc::default();
        let dummy = || Some(gtk::CustomSorter::new(|_, _| gtk::Ordering::Equal).upcast::<gtk::Sorter>()); // headers clickable; inner sorts
        let mut cols = vec![("name", name_column(&view, &tr!("Name"), 240, true, dummy()))];
        cols.push(("status", text_column(&view, &tr!("Status"), "status", translated(), 90, false, None, dummy(), false, true)));
        cols.push(("cpu", text_column(&view, &tr!("CPU"), "cpu", f(w::fmt_cpu), 80, true, h(w::heat_cpu), dummy(), false, false)));
        cols.push(("mem", text_column(&view, &tr!("Memory"), "mem", f(w::fmt_mem), 100, true, h(w::heat_mem), dummy(), false, false)));
        cols.push(("disk", text_column(&view, &tr!("Disk"), "disk", f(w::fmt_disk), 90, true, h(w::heat_disk), dummy(), false, false)));
        cols.push(("net", text_column(&view, &tr!("Network"), "net", f(w::fmt_net), 90, true, Some(Rc::new(|_: &V| 0)), dummy(), false, false)));
        cols.push(("gpu", text_column(&view, "GPU", "gpu", f(w::fmt_gpu), 70, true, h(w::heat_gpu), dummy(), false, false)));
        // which card draws it: two-GPU laptops, the integrated card for everyday apps
        cols.push(("gpu_on", text_column(&view, &tr!("GPU Engine"), "gpu_on", None, 96, false, None, dummy(), false, true)));
        let scroller = scrolled(&view);
        let end_btn = tool_button("process-stop-symbolic", &tr!("End task"), || {}, None);
        let props_btn = tool_button("info-outline-symbolic", "", || {}, Some(&tr!("Properties")));
        buttons.append(&end_btn);
        buttons.append(&props_btn);
        let page = Rc::new(Self { win, buttons, end_btn, props_btn, root, nodes: RefCell::default(), counts: RefCell::new([0; 3]), headers, filtered, inner, sorted, selection, view, cols, order, text, scroller });
        Self::wire(&page);
        page
    }

    fn wire(page: &Rc<Self>) {
        let w = Rc::downgrade(page);
        page.end_btn.connect_clicked(move |_| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.end_task(p.selected_rows(), false);
                }
            }
        });
        let w = Rc::downgrade(page);
        page.props_btn.connect_clicked(move |_| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.show_properties(p.selected_rows());
                }
            }
        });
        let w = Rc::downgrade(page);
        page.selection.connect_selection_changed(move |_, _, _| {
            if let Some(p) = w.upgrade() {
                p.buttons_state();
            }
        });
        let hf = gtk::SignalListItemFactory::new();
        hf.connect_setup(|_, h| h.downcast_ref::<gtk::ListHeader>().unwrap().set_child(Some(&gtk::Label::builder().xalign(0.0).build())));
        let w = Rc::downgrade(page);
        hf.connect_bind(move |_, h| {
            let Some(p) = w.upgrade() else { return };
            let h = h.downcast_ref::<gtk::ListHeader>().unwrap();
            let g = h.item().and_downcast::<gtk::TreeListRow>().and_then(|t| t.item().and_downcast::<TaskRow>()).map(|r| group(&r) as usize).unwrap_or(0).min(2);
            let Some(lbl) = h.child().and_downcast::<gtk::Label>() else { return };
            lbl.set_label(&format!("{} ({})", sonata_i18n::gettext(GROUPS[g]), p.counts.borrow()[g]));
            let mut hs = p.headers.borrow_mut();
            hs.retain(|(l, _)| l.upgrade().is_some_and(|l| l != lbl));
            hs.push((lbl.downgrade(), g));
        });
        page.view.set_header_factory(Some(&hf));
        let w = Rc::downgrade(page);
        if let Some(cs) = page.view.sorter() {
            cs.connect_changed(move |cs, _| {
                let Some(p) = w.upgrade() else { return };
                let Some(cs) = cs.downcast_ref::<gtk::ColumnViewSorter>() else { return };
                let cid = cs.primary_sort_column().and_then(|c| p.cols.iter().find(|(_, col)| *col == c).map(|(id, _)| *id)).unwrap_or("cpu");
                p.order.replace((cid, cs.primary_sort_order() == gtk::SortType::Descending));
                p.inner.changed(gtk::SorterChange::Different);
                p.reveal();
            });
        }
        page.view.sort_by_column(Some(&page.col("cpu")), gtk::SortType::Descending);
        let w = Rc::downgrade(page);
        page.view.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.show_properties(p.selected_rows());
                }
            }
        });
        let w = Rc::downgrade(page);
        secondary_click(&page.view, move |g, x, y| {
            if let Some(p) = w.upgrade() {
                p.menu(g, x, y);
            }
        });
        page.buttons_state();
    }

    pub fn col(&self, cid: &str) -> gtk::ColumnViewColumn {
        self.cols.iter().find(|(id, _)| *id == cid).map(|(_, c)| c.clone()).unwrap()
    }

    fn reveal(&self) {
        let pos = self.selection.selected();
        if self.view.model().is_some() && self.sorted.n_items() > 0 {
            self.view.scroll_to(if pos == gtk::INVALID_LIST_POSITION { 0 } else { pos }, None, gtk::ListScrollFlags::NONE, None);
        }
    }

    fn buttons_state(&self) {
        let on = !self.selected_rows().is_empty();
        self.end_btn.set_sensitive(on);
        self.props_btn.set_sensitive(on);
    }

    fn menu(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(row) = row_at(&self.view, x, y) else { return };
        let Some(win) = self.win.upgrade() else { return };
        g.set_state(gtk::EventSequenceState::Claimed);
        for i in 0..self.sorted.n_items() {
            if self.sorted.item(i).and_downcast::<gtk::TreeListRow>().and_then(|t| t.item().and_downcast::<TaskRow>()).as_ref() == Some(&row) {
                self.selection.set_selected(i);
                break;
            }
        }
        let rows = self.selected_rows();
        let first = rows.first().cloned();
        let has_exe = first.as_ref().is_some_and(|f| !f.data().exe.is_empty());
        let (w1, w2, w3, w4) = (Rc::downgrade(&win), Rc::downgrade(&win), Rc::downgrade(&win), Rc::downgrade(&win));
        let (r1, r4, name) = (rows.clone(), rows, row.name());
        menu(
            &self.view,
            vec![
                vec![item(&tr!("End task"), move || {
                    if let Some(w) = w1.upgrade() {
                        w.end_task(r1.clone(), false);
                    }
                })],
                vec![
                    item(&tr!("Open file location"), move || {
                        if let (Some(w), Some(f)) = (w2.upgrade(), first.as_ref()) {
                            w.open_location(f);
                        }
                    })
                    .enabled(has_exe),
                    item(&tr!("Search online"), move || {
                        if let Some(w) = w3.upgrade() {
                            w.search_online(&name);
                        }
                    }),
                ],
                vec![item(&tr!("Properties"), move || {
                    if let Some(w) = w4.upgrade() {
                        w.show_properties(r4.clone());
                    }
                })],
            ],
            x,
            y,
        );
    }
}

impl Page for ProcessesPage {
    fn id(&self) -> &'static str {
        "processes"
    }
    fn title(&self) -> &'static str {
        n_!("Processes")
    }
    fn icon(&self) -> &'static str {
        "view-app-grid-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.scroller.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.view, &self.selection, on);
    }

    fn search(&self, text: &str) {
        self.text.replace(text.trim().to_lowercase());
        if let Some(f) = self.filtered.filter() {
            f.changed(gtk::FilterChange::Different);
        }
        self.reveal();
    }

    fn selected_rows(&self) -> Vec<TaskRow> {
        let Some(row) = self.selection.selected_item().and_downcast::<gtk::TreeListRow>().and_then(|t| t.item().and_downcast::<TaskRow>()) else { return Vec::new() };
        if row.kind() == Kind::App {
            row.data().members.clone()
        } else {
            vec![row]
        }
    }

    fn update(&self, snap: &Snapshot) {
        let Some(win) = self.win.upgrade() else { return };
        w::MEM_TOTAL.with(|t| t.set(snap.memory.total.max(1) as f64));
        let rows = win.pid_rows.borrow().clone();
        let groups = win.groups.borrow().clone();
        let mut wanted = Vec::new();
        let mut nodes = self.nodes.borrow_mut();
        for (key, pids) in &groups.0 {
            let node = nodes.entry(key.clone()).or_insert_with(|| {
                let (icon, name) = win.app_info.borrow().get(key).cloned().unwrap_or((None, key.clone()));
                TaskRow::app(key, &name, icon)
            });
            let members: Vec<TaskRow> = pids.iter().filter_map(|p| rows.get(p).cloned()).collect();
            for m in &members {
                m.set_values(vec![("group", 0i64.into())]);
            }
            if let Some(c) = node.data().children.clone() {
                sync_store(&c, &members);
            }
            let sum = |k: &str| members.iter().map(|m| m.n(k)).sum::<f64>();
            let mut cards: Vec<String> = members.iter().flat_map(|m| m.s("gpu_on").split(", ").filter(|c| !c.is_empty()).map(str::to_string).collect::<Vec<_>>()).collect();
            cards.sort();
            cards.dedup();
            let status = members.iter().map(|m| m.s("status")).find(|s| !s.is_empty()).unwrap_or_default();
            let app_name = node.data().app_name.clone();
            node.set_values(vec![
                ("name", format!("{app_name} ({})", members.len()).into()),
                ("cpu", ((sum("cpu") * 10.0).round_ties_even() / 10.0).into()),
                ("mem", sum("mem").into()),
                ("disk", sum("disk").into()),
                ("gpu", ((sum("gpu").min(100.0) * 10.0).round_ties_even() / 10.0).into()),
                ("gpu_on", cards.join(", ").into()),
                ("status", status.into()),
            ]);
            node.data_mut().members = members;
            wanted.push(node.clone());
        }
        nodes.retain(|k, _| groups.0.contains_key(k));
        drop(nodes);
        for (g, list) in [(1i64, &groups.1), (2, &groups.2)] {
            for pid in list {
                if let Some(r) = rows.get(pid) {
                    r.set_values(vec![("group", g.into())]);
                    wanted.push(r.clone());
                }
            }
        }
        self.counts.replace([groups.0.len(), groups.1.len(), groups.2.len()]);
        win.restoring.set(true);
        sync_store(&self.root, &wanted);
        keep_top(&self.view, &self.scroller, || self.inner.changed(gtk::SorterChange::Different));
        win.restoring.set(false);
        for (lbl, g) in self.headers.borrow().iter() {
            if let Some(l) = lbl.upgrade() {
                l.set_label(&format!("{} ({})", sonata_i18n::gettext(GROUPS[*g]), self.counts.borrow()[*g]));
            }
        }
        let cpu = 100.0 - snap.cpu.idle;
        self.col("cpu").set_title(Some(&format!("{cpu:.0}%\n{}", tr!("CPU"))));
        let m = &snap.memory;
        self.col("mem").set_title(Some(&format!("{:.0}%\n{}", 100.0 * m.used as f64 / m.total.max(1) as f64, tr!("Memory"))));
        let active = snap.disks.values().map(|d| d.active).fold(0.0, f64::max);
        self.col("disk").set_title(Some(&format!("{active:.0}%\n{}", tr!("Disk"))));
        self.col("net").set_title(Some(&format!("{}\n{}", fmt_bits(8.0 * (snap.net.rx_bytes_ps + snap.net.tx_bytes_ps)), tr!("Network"))));
        let gpu = snap.gpus.values().flatten().copied().reduce(f64::max); // a sleeping dGPU: None
        self.col("gpu").set_title(Some(&match gpu {
            Some(g) => format!("{g:.0}%\n{}", tr!("GPU")),
            None => tr!("GPU"),
        }));
        self.buttons_state();
    }
}

// -- Details ----------------------------------------------------------------------------------
/// Every process in one table (Windows' Details).
pub struct DetailsPage {
    win: Weak<TaskManagerWindow>,
    buttons: gtk::Box,
    end_btn: gtk::Button,
    props_btn: gtk::Button,
    filtered: gtk::FilterListModel,
    pub sorted: gtk::SortListModel,
    pub selection: gtk::SingleSelection,
    pub view: gtk::ColumnView,
    pub cols: Vec<(&'static str, gtk::ColumnViewColumn)>,
    text: Rc<RefCell<String>>,
    scroller: gtk::ScrolledWindow,
    ticking: Cell<bool>,
    sel_key: Cell<Option<(i64, i64)>>,
}

impl DetailsPage {
    pub fn new(win: Weak<TaskManagerWindow>, all: &gio::ListStore) -> Rc<Self> {
        let buttons = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let text: Rc<RefCell<String>> = Rc::default();
        let t = text.clone();
        let filter = gtk::CustomFilter::new(move |o| {
            let t = t.borrow();
            let Some(r) = o.downcast_ref::<TaskRow>() else { return true };
            t.is_empty() || r.name().to_lowercase().contains(&*t) || r.pid().to_string().contains(&*t) || r.s("user").to_lowercase().contains(&*t)
        });
        let filtered = gtk::FilterListModel::new(Some(all.clone()), Some(filter));
        let view = table();
        let sorted = gtk::SortListModel::new(Some(filtered.clone()), view.sorter());
        let selection = gtk::SingleSelection::builder().model(&sorted).autoselect(false).can_unselect(true).build();
        view.set_model(Some(&selection));
        let mut cols = vec![("name", name_column(&view, &tr!("Name"), 220, false, Some(sorter("name").upcast())))];
        let specs: [(&'static str, String, Option<Fmt>, i32, bool, Option<Heat>); 10] = [
            ("pid", "PID".into(), f(|v| (v as i64).to_string()), 70, true, None),
            ("status", tr!("Status"), translated(), 110, false, None),
            ("user", tr!("User name"), None, 100, false, None),
            ("cpu", tr!("CPU"), f(w::fmt_cpu), 70, true, h(w::heat_cpu)),
            ("cpu_time", tr!("CPU time"), f(fmt_cpu_time), 90, true, None),
            ("threads", tr!("Threads"), f(fmt_count), 70, true, None),
            ("mem", tr!("Memory"), f(w::fmt_mem), 100, true, h(w::heat_mem)),
            ("read_bytes", tr!("Read"), f(fmt_bytes), 90, true, None),
            ("write_bytes", tr!("Written"), f(fmt_bytes), 90, true, None),
            ("command", tr!("Command line"), None, 320, false, None),
        ];
        for (cid, title, fm, width, numeric, heat) in specs {
            let dim = matches!(cid, "user" | "command" | "status");
            cols.push((cid, text_column(&view, &title, cid, fm, width, numeric, heat, Some(sorter(cid).upcast()), false, dim)));
        }
        let scroller = scrolled(&view);
        let end_btn = tool_button("process-stop-symbolic", &tr!("End task"), || {}, None);
        let props_btn = tool_button("info-outline-symbolic", "", || {}, Some(&tr!("Properties")));
        buttons.append(&end_btn);
        buttons.append(&props_btn);
        let page = Rc::new(Self { win, buttons, end_btn, props_btn, filtered, sorted, selection, view, cols, text, scroller, ticking: Cell::new(false), sel_key: Cell::new(None) });
        let w = Rc::downgrade(&page);
        page.end_btn.connect_clicked(move |_| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.end_task(p.selected_rows(), false);
                }
            }
        });
        let w = Rc::downgrade(&page);
        page.props_btn.connect_clicked(move |_| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.show_properties(p.selected_rows());
                }
            }
        });
        let w = Rc::downgrade(&page);
        page.selection.connect_selection_changed(move |s, _, _| {
            let Some(p) = w.upgrade() else { return };
            match s.selected_item().and_downcast::<TaskRow>() {
                Some(r) => p.sel_key.set(Some(r.data().key)),
                None if !p.win.upgrade().is_some_and(|w| w.restoring.get()) => p.sel_key.set(None),
                None => {}
            }
            p.buttons_state();
        });
        let w = Rc::downgrade(&page);
        if let Some(s) = page.view.sorter() {
            s.connect_changed(move |_, _| {
                if let Some(p) = w.upgrade() {
                    if !p.ticking.get() {
                        p.reveal();
                    }
                }
            });
        }
        page.view.sort_by_column(Some(&page.cols.iter().find(|c| c.0 == "cpu").unwrap().1), gtk::SortType::Descending);
        let w = Rc::downgrade(&page);
        page.view.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade() {
                if let Some(win) = p.win.upgrade() {
                    win.show_properties(p.selected_rows());
                }
            }
        });
        let w = Rc::downgrade(&page);
        secondary_click(&page.view, move |g, x, y| {
            if let Some(p) = w.upgrade() {
                p.menu(g, x, y);
            }
        });
        page.buttons_state();
        page
    }

    fn reveal(&self) {
        let pos = self.selection.selected();
        if self.view.model().is_some() && self.sorted.n_items() > 0 {
            self.view.scroll_to(if pos == gtk::INVALID_LIST_POSITION { 0 } else { pos }, None, gtk::ListScrollFlags::NONE, None);
        }
    }

    fn buttons_state(&self) {
        let on = !self.selected_rows().is_empty();
        self.end_btn.set_sensitive(on);
        self.props_btn.set_sensitive(on);
    }

    /// Keep the selected process selected when rows move or filter.
    pub fn restore_selection(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let row = self.selection.selected_item().and_downcast::<TaskRow>();
        let Some(key) = self.sel_key.get() else { return };
        if row.as_ref().is_some_and(|r| r.data().key == key) {
            return;
        }
        let target = win.rows.borrow().get(&key).cloned();
        win.restoring.set(true);
        match target {
            None => self.sel_key.set(None),
            Some(t) => {
                for i in 0..self.sorted.n_items() {
                    if self.sorted.item(i).and_downcast::<TaskRow>().as_ref() == Some(&t) {
                        self.selection.set_selected(i);
                        win.restoring.set(false);
                        self.buttons_state();
                        return;
                    }
                }
            }
        }
        self.selection.set_selected(gtk::INVALID_LIST_POSITION);
        win.restoring.set(false);
        self.buttons_state();
    }

    fn menu(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(row) = row_at(&self.view, x, y) else { return };
        let Some(win) = self.win.upgrade() else { return };
        g.set_state(gtk::EventSequenceState::Claimed);
        select_row(&self.sorted, &self.selection, &row);
        let tree: Vec<TaskRow> = win.rows.borrow().values().filter(|r| win.descends(r, &row)).cloned().collect();
        let has_exe = !row.data().exe.is_empty();
        let ws: Vec<Weak<TaskManagerWindow>> = (0..7).map(|_| Rc::downgrade(&win)).collect();
        let (a, b, c, d, e, ff) = (row.clone(), row.clone(), row.clone(), row.clone(), row.clone(), row.clone());
        let pid = row.pid();
        menu(
            &self.view,
            vec![
                vec![
                    item(&tr!("End task"), { let w = ws[0].clone(); move || if let Some(w) = w.upgrade() { w.end_task(vec![a.clone()], false) } }),
                    item(&tr!("End process tree"), { let w = ws[1].clone(); move || if let Some(w) = w.upgrade() { w.end_task([vec![b.clone()], tree.clone()].concat(), false) } }),
                    item(&tr!("Force quit"), { let w = ws[2].clone(); move || if let Some(w) = w.upgrade() { w.end_task(vec![c.clone()], true) } }),
                ],
                vec![
                    item(&tr!("Open file location"), { let w = ws[3].clone(); move || if let Some(w) = w.upgrade() { w.open_location(&d) } }).enabled(has_exe),
                    item(&tr!("Search online"), { let w = ws[4].clone(); move || if let Some(w) = w.upgrade() { w.search_online(&e.name()) } }),
                    item(&tr!("Copy PID"), { let w = ws[5].clone(); move || if let Some(w) = w.upgrade() { w.window.clipboard().set_text(&pid.to_string()) } }),
                ],
                vec![item(&tr!("Properties"), { let w = ws[6].clone(); move || if let Some(w) = w.upgrade() { w.show_properties(vec![ff.clone()]) } })],
            ],
            x,
            y,
        );
    }
}

impl Page for DetailsPage {
    fn id(&self) -> &'static str {
        "details"
    }
    fn title(&self) -> &'static str {
        n_!("Details")
    }
    fn icon(&self) -> &'static str {
        "view-list-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.scroller.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.view, &self.selection, on);
    }
    fn search(&self, text: &str) {
        self.text.replace(text.trim().to_lowercase());
        if let Some(f) = self.filtered.filter() {
            f.changed(gtk::FilterChange::Different);
        }
        self.restore_selection();
        self.reveal();
    }
    fn selected_rows(&self) -> Vec<TaskRow> {
        self.selection.selected_item().and_downcast::<TaskRow>().into_iter().collect()
    }
    fn update(&self, snap: &Snapshot) {
        w::MEM_TOTAL.with(|t| t.set(snap.memory.total.max(1) as f64));
        let win = self.win.upgrade();
        if let Some(w) = &win {
            w.restoring.set(true);
        }
        self.ticking.set(true);
        keep_top(&self.view, &self.scroller, || {
            if let Some(s) = self.view.sorter() {
                s.changed(gtk::SorterChange::Different);
            }
        });
        self.ticking.set(false);
        if let Some(w) = &win {
            w.restoring.set(false);
        }
        self.restore_selection();
    }
}

// -- simple tables: Users, App history, Startup apps, Services ---------------------------------
struct Simple {
    store: gio::ListStore,
    filtered: gtk::FilterListModel,
    sorted: gtk::SortListModel,
    selection: gtk::SingleSelection,
    view: gtk::ColumnView,
    scroller: gtk::ScrolledWindow,
    text: Rc<RefCell<String>>,
}

impl Simple {
    fn new(fields: &'static [&'static str]) -> Self {
        let store = gio::ListStore::new::<TaskRow>();
        let text: Rc<RefCell<String>> = Rc::default();
        let t = text.clone();
        let filter = gtk::CustomFilter::new(move |o| {
            let t = t.borrow();
            let Some(r) = o.downcast_ref::<TaskRow>() else { return true };
            t.is_empty() || fields.iter().any(|f| r.s(f).to_lowercase().contains(&*t))
        });
        let filtered = gtk::FilterListModel::new(Some(store.clone()), Some(filter));
        let view = table();
        let sorted = gtk::SortListModel::new(Some(filtered.clone()), view.sorter());
        let selection = gtk::SingleSelection::builder().model(&sorted).autoselect(false).can_unselect(true).build();
        view.set_model(Some(&selection));
        let scroller = scrolled(&view);
        Self { store, filtered, sorted, selection, view, scroller, text }
    }

    fn search(&self, text: &str) {
        self.text.replace(text.trim().to_lowercase());
        if let Some(f) = self.filtered.filter() {
            f.changed(gtk::FilterChange::Different);
        }
    }

    fn selected(&self) -> Option<TaskRow> {
        self.selection.selected_item().and_downcast::<TaskRow>()
    }
}

/// Per-user totals (Windows' Users).
pub struct UsersPage {
    t: Simple,
    buttons: gtk::Box,
    rows: RefCell<HashMap<String, TaskRow>>,
}

impl UsersPage {
    pub fn new() -> Rc<Self> {
        let t = Simple::new(&["name"]);
        name_column(&t.view, &tr!("User"), 240, false, Some(sorter("name").upcast()));
        text_column(&t.view, &tr!("Processes"), "procs", f(fmt_count), 90, true, None, Some(sorter("procs").upcast()), false, false);
        let cpu = text_column(&t.view, &tr!("CPU"), "cpu", f(w::fmt_cpu), 90, true, h(w::heat_cpu), Some(sorter("cpu").upcast()), false, false);
        text_column(&t.view, &tr!("Memory"), "mem", f(w::fmt_mem), 110, true, h(w::heat_mem), Some(sorter("mem").upcast()), false, false);
        text_column(&t.view, &tr!("Disk"), "disk", f(w::fmt_disk), 100, true, h(w::heat_disk), Some(sorter("disk").upcast()), false, false);
        t.view.sort_by_column(Some(&cpu), gtk::SortType::Descending);
        Rc::new(Self { t, buttons: gtk::Box::new(gtk::Orientation::Horizontal, 2), rows: RefCell::default() })
    }

    pub fn store(&self) -> gio::ListStore {
        self.t.store.clone()
    }
}

impl Page for UsersPage {
    fn id(&self) -> &'static str {
        "users"
    }
    fn title(&self) -> &'static str {
        n_!("Users")
    }
    fn icon(&self) -> &'static str {
        "system-users-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.t.scroller.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.t.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.t.view, &self.t.selection, on);
    }
    fn search(&self, text: &str) {
        self.t.search(text);
    }
    fn update(&self, snap: &Snapshot) {
        let users = manage::per_user(&snap.procs);
        let mut rows = self.rows.borrow_mut();
        for (name, u) in &users {
            let r = rows.entry(name.clone()).or_insert_with(|| {
                let r = TaskRow::new(Kind::User, name, Some(gio::ThemedIcon::new("avatar-default-symbolic").upcast()));
                self.t.store.append(&r);
                r
            });
            r.set_values(vec![("procs", u.procs.into()), ("cpu", ((u.cpu * 10.0).round_ties_even() / 10.0).into()), ("mem", u.mem.into()), ("disk", u.disk.round_ties_even().into())]);
        }
        let gone: Vec<String> = rows.keys().filter(|n| !users.contains_key(*n)).cloned().collect();
        for n in gone {
            if let Some(r) = rows.remove(&n) {
                if let Some(pos) = self.t.store.find(&r) {
                    self.t.store.remove(pos);
                }
            }
        }
        keep_top(&self.t.view, &self.t.scroller, || {
            if let Some(s) = self.t.view.sorter() {
                s.changed(gtk::SorterChange::Different);
            }
        });
    }
}

/// CPU time and disk use per app since a date (Windows' App history).
pub struct HistoryPage {
    win: Weak<TaskManagerWindow>,
    t: Simple,
    buttons: gtk::Box,
    rows: RefCell<HashMap<String, TaskRow>>,
    note: gtk::Label,
    root: gtk::Box,
}

impl HistoryPage {
    pub fn new(win: Weak<TaskManagerWindow>) -> Rc<Self> {
        let t = Simple::new(&["name"]);
        name_column(&t.view, &tr!("Name"), 300, false, Some(sorter("name").upcast()));
        let cpu = text_column(&t.view, &tr!("CPU time"), "cpu_time", f(fmt_cpu_time), 120, true, None, Some(sorter("cpu_time").upcast()), false, false);
        text_column(&t.view, &tr!("Disk"), "disk", f(|v| fmt_bytes(v.trunc())), 120, true, None, Some(sorter("disk").upcast()), false, false);
        t.view.sort_by_column(Some(&cpu), gtk::SortType::Descending);
        let note = gtk::Label::builder().xalign(0.0).css_classes(["tm-note"]).build();
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        root.append(&note);
        root.append(&t.scroller);
        let buttons = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let page = Rc::new(Self { win, t, buttons, rows: RefCell::default(), note, root });
        let w = Rc::downgrade(&page);
        page.buttons.append(&tool_button("edit-delete-symbolic", &tr!("Delete usage history"), move || {
            if let Some(p) = w.upgrade() {
                p.clear();
            }
        }, None));
        page
    }

    pub fn store(&self) -> gio::ListStore {
        self.t.store.clone()
    }

    pub fn clear(&self) {
        if let Some(win) = self.win.upgrade() {
            win.history.borrow_mut().clear();
            win.save_history();
        }
        self.t.store.remove_all();
        self.rows.borrow_mut().clear();
        self.refresh();
    }

    fn refresh(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let h = win.history.borrow().clone();
        let date = glib::DateTime::from_unix_local(h.since as i64).ok().and_then(|d| d.format("%d/%m/%Y").ok()).map(|s| s.to_string()).unwrap_or_default();
        self.note.set_label(&fmt(&tr!("Resource usage since {date} for the current user account."), &[("date", &date)]));
        let mut rows = self.rows.borrow_mut();
        for (key, a) in &h.apps {
            let r = rows.entry(key.clone()).or_insert_with(|| {
                let icon = win.app_info.borrow().get(key).and_then(|i| i.0.clone());
                let r = TaskRow::new(Kind::History, &a.name, icon);
                self.t.store.append(&r);
                r
            });
            r.set_values(vec![("cpu_time", ((a.cpu_time * 10.0).round_ties_even() / 10.0).into()), ("disk", ((a.disk * 10.0).round_ties_even() / 10.0).into())]);
        }
        if let Some(s) = self.t.view.sorter() {
            s.changed(gtk::SorterChange::Different);
        }
    }
}

impl Page for HistoryPage {
    fn id(&self) -> &'static str {
        "history"
    }
    fn title(&self) -> &'static str {
        n_!("App history")
    }
    fn icon(&self) -> &'static str {
        "document-open-recent-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.root.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.t.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.t.view, &self.t.selection, on);
    }
    fn search(&self, text: &str) {
        self.t.search(text);
    }
    fn update(&self, _snap: &Snapshot) {
        self.refresh();
    }
}

fn icon_named(name: &str) -> gio::Icon {
    if name.is_empty() {
        return gio::ThemedIcon::new("application-x-executable").upcast();
    }
    if name.starts_with('/') {
        return gio::FileIcon::new(&gio::File::for_path(name)).upcast();
    }
    gio::ThemedIcon::new(name).upcast()
}

/// Login items (~/.config/autostart over /etc/xdg/autostart) with an
/// Enabled switch (Windows' Startup apps).
pub struct StartupPage {
    win: Weak<TaskManagerWindow>,
    t: Simple,
    buttons: gtk::Box,
    enable_btn: gtk::Button,
    disable_btn: gtk::Button,
    empty: gtk::Label,
    root: gtk::Box,
    loaded: Cell<bool>,
}

fn startup_row(e: StartupEntry) -> TaskRow {
    let r = TaskRow::new(Kind::Startup, &e.name, Some(icon_named(&e.icon)));
    r.set_values(vec![("comment", e.comment.as_str().into()), ("command", e.command.as_str().into()), ("status", if e.enabled { tr!("Enabled") } else { tr!("Disabled") }.into())]);
    r.data_mut().enabled = e.enabled;
    r.data_mut().entry = Some(e);
    r
}

impl StartupPage {
    pub fn new(win: Weak<TaskManagerWindow>) -> Rc<Self> {
        let t = Simple::new(&["name", "command"]);
        let name = name_column(&t.view, &tr!("Name"), 240, false, Some(sorter("name").upcast()));
        text_column(&t.view, &tr!("Description"), "comment", None, 260, false, None, Some(sorter("comment").upcast()), false, true);
        text_column(&t.view, &tr!("Command"), "command", None, 240, false, None, Some(sorter("command").upcast()), false, true);
        text_column(&t.view, &tr!("Status"), "status", None, 90, false, None, Some(sorter("status").upcast()), false, false);
        t.view.sort_by_column(Some(&name), gtk::SortType::Ascending);
        let empty = gtk::Label::builder().label(tr!("No startup apps")).css_classes(["tm-note"]).visible(false).vexpand(true).build();
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        root.append(&t.scroller);
        root.append(&empty);
        let buttons = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let enable_btn = tool_button("media-playback-start-symbolic", &tr!("Enable"), || {}, None);
        let disable_btn = tool_button("media-playback-stop-symbolic", &tr!("Disable"), || {}, None);
        buttons.append(&enable_btn);
        buttons.append(&disable_btn);
        let page = Rc::new(Self { win, t, buttons, enable_btn, disable_btn, empty, root, loaded: Cell::new(false) });
        // the Enabled switch
        let fac = gtk::SignalListItemFactory::new();
        fac.connect_setup(|_, it| it.downcast_ref::<gtk::ListItem>().unwrap().set_child(Some(&gtk::Box::builder().halign(gtk::Align::Center).build())));
        let w = Rc::downgrade(&page);
        fac.connect_bind(move |_, it| {
            let it = it.downcast_ref::<gtk::ListItem>().unwrap();
            let (Some(bx), Some(row)) = (it.child().and_downcast::<gtk::Box>(), it.item().and_downcast::<TaskRow>()) else { return };
            while let Some(c) = bx.first_child() {
                bx.remove(&c);
            }
            let sw = sonata_ui::controls::switch(row.data().enabled);
            let (w, r) = (w.clone(), row.clone());
            sw.connect_active_notify(move |s| {
                if let Some(p) = w.upgrade() {
                    p.set_enabled(&r, s.is_active());
                }
            });
            bx.append(&sw);
        });
        let col = gtk::ColumnViewColumn::builder().title(tr!("Enabled")).factory(&fac).fixed_width(80).build();
        page.t.view.append_column(&col);
        for (b, on) in [(&page.enable_btn, true), (&page.disable_btn, false)] {
            let w = Rc::downgrade(&page);
            b.connect_clicked(move |_| {
                if let Some(p) = w.upgrade() {
                    if let Some(r) = p.t.selected() {
                        p.set_enabled(&r, on);
                    }
                }
            });
        }
        let w = Rc::downgrade(&page);
        page.t.selection.connect_selection_changed(move |_, _, _| {
            if let Some(p) = w.upgrade() {
                p.buttons_state();
            }
        });
        let w = Rc::downgrade(&page);
        secondary_click(&page.t.view, move |g, x, y| {
            if let Some(p) = w.upgrade() {
                p.menu(g, x, y);
            }
        });
        page.buttons_state();
        page
    }

    fn reload(self: &Rc<Self>) {
        self.loaded.set(true);
        let w = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            let entries = gio::spawn_blocking(|| manage::startup_entries(None, None)).await.unwrap_or_default();
            let Some(p) = w.upgrade() else { return };
            p.t.store.remove_all();
            let rows: Vec<TaskRow> = entries.iter().cloned().map(startup_row).collect();
            p.t.store.splice(0, 0, &rows);
            p.empty.set_visible(entries.is_empty());
        });
    }

    fn set_enabled(&self, row: &TaskRow, on: bool) {
        if row.data().enabled == on {
            return;
        }
        let Some(mut entry) = row.data().entry.clone() else { return };
        let w = self.win.clone();
        let r = row.clone();
        let store = self.t.store.downgrade();
        let (eb, db) = (self.enable_btn.downgrade(), self.disable_btn.downgrade());
        glib::spawn_future_local(async move {
            let res = gio::spawn_blocking(move || manage::set_startup_enabled(&mut entry, on, None).map(|_| entry)).await;
            match res {
                Ok(Ok(entry)) => {
                    r.data_mut().enabled = on;
                    r.data_mut().entry = Some(entry);
                    r.set_values(vec![("status", if on { tr!("Enabled") } else { tr!("Disabled") }.into())]);
                    if let Some(s) = store.upgrade() {
                        if let Some(pos) = s.find(&r) {
                            s.splice(pos, 1, &[r.clone()]); // the switch follows
                        }
                    }
                    if let (Some(e), Some(d)) = (eb.upgrade(), db.upgrade()) {
                        e.set_sensitive(!on);
                        d.set_sensitive(on);
                    }
                }
                _ => {
                    if let Some(win) = w.upgrade() {
                        sonata_ui::dialog::alert(&fmt(&tr!("“{name}” couldn't be changed."), &[("name", &r.name())]), &tr!("Its startup file couldn't be written."), &[("ok", tr!("OK"), "default")], |_| {}, Some(win.window.upcast_ref()));
                        if let Some(p) = win.startup.borrow().clone() {
                            p.reload();
                        }
                    }
                }
            }
        });
    }

    pub fn reload_now(&self) {
        if let Some(win) = self.win.upgrade() {
            if let Some(p) = win.startup.borrow().clone() {
                p.reload();
            }
        }
    }

    fn buttons_state(&self) {
        let row = self.t.selected();
        self.enable_btn.set_sensitive(row.as_ref().is_some_and(|r| !r.data().enabled));
        self.disable_btn.set_sensitive(row.as_ref().is_some_and(|r| r.data().enabled));
    }

    fn menu(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(row) = row_at(&self.t.view, x, y) else { return };
        g.set_state(gtk::EventSequenceState::Claimed);
        let Some(win) = self.win.upgrade() else { return };
        let on = row.data().enabled;
        let path = row.data().entry.as_ref().map(|e| e.path.clone()).unwrap_or_default();
        let me = win.startup.borrow().clone();
        let (m1, m2) = (me.clone(), me);
        let (r1, r2, name) = (row.clone(), row.clone(), row.name());
        let (w1, w2) = (Rc::downgrade(&win), Rc::downgrade(&win));
        menu(
            &self.t.view,
            vec![
                vec![
                    item(&tr!("Enable"), move || if let Some(p) = &m1 { p.set_enabled(&r1, true) }).enabled(!on),
                    item(&tr!("Disable"), move || if let Some(p) = &m2 { p.set_enabled(&r2, false) }).enabled(on),
                ],
                vec![
                    item(&tr!("Open file location"), move || if let Some(w) = w1.upgrade() { w.reveal_path(&path.to_string_lossy()) }),
                    item(&tr!("Search online"), move || if let Some(w) = w2.upgrade() { w.search_online(&name) }),
                ],
            ],
            x,
            y,
        );
    }
}

impl Page for StartupPage {
    fn id(&self) -> &'static str {
        "startup"
    }
    fn title(&self) -> &'static str {
        n_!("Startup apps")
    }
    fn icon(&self) -> &'static str {
        "system-run-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.root.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.t.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.t.view, &self.t.selection, on);
    }
    fn search(&self, text: &str) {
        self.t.search(text);
    }
    fn shown(&self) {
        if !self.loaded.get() {
            self.reload_now();
        }
    }
}

/// systemd services of the user and the system, Start / Stop / Restart
/// (system ones ask through polkit).
pub struct ServicesPage {
    win: Weak<TaskManagerWindow>,
    t: Simple,
    buttons: gtk::Box,
    start_btn: gtk::Button,
    stop_btn: gtk::Button,
    restart_btn: gtk::Button,
    message: gtk::Label,
    root: gtk::Box,
    loaded: Cell<bool>,
    busy: Cell<bool>,
}

impl ServicesPage {
    pub fn new(win: Weak<TaskManagerWindow>) -> Rc<Self> {
        let t = Simple::new(&["name", "description"]);
        let name = text_column(&t.view, &tr!("Name"), "name", None, 260, false, None, Some(sorter("name").upcast()), false, false);
        text_column(&t.view, &tr!("Description"), "description", None, 320, false, None, Some(sorter("description").upcast()), true, true);
        // the status, green when running, red when failed
        let fac = gtk::SignalListItemFactory::new();
        fac.connect_setup(|_, it| it.downcast_ref::<gtk::ListItem>().unwrap().set_child(Some(&gtk::Label::builder().xalign(0.0).css_classes(["tm-cell"]).build())));
        fac.connect_bind(|_, it| {
            let it = it.downcast_ref::<gtk::ListItem>().unwrap();
            let (Some(lbl), Some(row)) = (it.child().and_downcast::<gtk::Label>(), it.item().and_downcast::<TaskRow>()) else { return };
            let status = row.s("status");
            lbl.set_label(&sonata_i18n::gettext(&status));
            for (c, on) in [("tm-running", status == "Running"), ("tm-failed", status == "Failed")] {
                if on {
                    lbl.add_css_class(c);
                } else {
                    lbl.remove_css_class(c);
                }
            }
        });
        let status_col = gtk::ColumnViewColumn::builder().title(tr!("Status")).factory(&fac).resizable(true).fixed_width(100).build();
        status_col.set_sorter(Some(&sorter("status")));
        t.view.append_column(&status_col);
        text_column(&t.view, &tr!("Type"), "scope", None, 80, false, None, Some(sorter("scope").upcast()), false, true);
        t.view.sort_by_column(Some(&name), gtk::SortType::Ascending);
        let message = gtk::Label::builder().css_classes(["tm-note"]).visible(false).wrap(true).xalign(0.0).build();
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        root.append(&message);
        root.append(&t.scroller);
        let buttons = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let start_btn = tool_button("media-playback-start-symbolic", &tr!("Start"), || {}, None);
        let stop_btn = tool_button("media-playback-stop-symbolic", &tr!("Stop"), || {}, None);
        let restart_btn = tool_button("view-refresh-symbolic", &tr!("Restart"), || {}, None);
        for b in [&start_btn, &stop_btn, &restart_btn] {
            buttons.append(b);
        }
        let page = Rc::new(Self { win, t, buttons, start_btn, stop_btn, restart_btn, message, root, loaded: Cell::new(false), busy: Cell::new(false) });
        for (b, action) in [(&page.start_btn, "start"), (&page.stop_btn, "stop"), (&page.restart_btn, "restart")] {
            let w = Rc::downgrade(&page);
            b.connect_clicked(move |_| {
                if let Some(p) = w.upgrade() {
                    p.act(action, None);
                }
            });
        }
        let w = Rc::downgrade(&page);
        page.t.selection.connect_selection_changed(move |_, _, _| {
            if let Some(p) = w.upgrade() {
                p.buttons_state();
            }
        });
        let w = Rc::downgrade(&page);
        secondary_click(&page.t.view, move |g, x, y| {
            if let Some(p) = w.upgrade() {
                p.menu(g, x, y);
            }
        });
        page.buttons_state();
        page
    }

    fn reload(self: &Rc<Self>) {
        self.loaded.set(true);
        let w = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            let res = gio::spawn_blocking(|| {
                let run = |cmd: &[String]| manage::run(cmd, 20);
                (manage::list_services("user", &run), manage::list_services("system", &run))
            })
            .await
            .unwrap_or((None, None));
            if let Some(p) = w.upgrade() {
                p.listed(res);
            }
        });
    }

    fn listed(&self, (user, sys): (Option<Vec<Service>>, Option<Vec<Service>>)) {
        let sel = self.t.selected().map(|r| r.name());
        let items: Vec<TaskRow> = user.iter().flatten().chain(sys.iter().flatten()).map(|s| {
            let r = TaskRow::new(Kind::Service, &s.unit, None);
            r.set_values(vec![("description", s.description.as_str().into()), ("status", s.status().into()), ("scope", if s.scope == "user" { tr!("User") } else { tr!("System") }.into())]);
            r.data_mut().service = Some(s.clone());
            r
        }).collect();
        self.t.store.remove_all();
        self.t.store.splice(0, 0, &items);
        let text = match (&user, &sys) {
            (None, None) => tr!("Services aren't available: systemd isn't running on this computer."),
            (_, None) => tr!("Only user services are shown."),
            (None, _) => tr!("Only system services are shown."),
            _ => String::new(),
        };
        self.message.set_label(&text);
        self.message.set_visible(user.is_none() || sys.is_none());
        if let Some(n) = sel {
            for i in 0..self.t.sorted.n_items() {
                if self.t.sorted.item(i).and_downcast::<TaskRow>().is_some_and(|r| r.name() == n) {
                    self.t.selection.set_selected(i);
                    break;
                }
            }
        }
        self.buttons_state();
    }

    fn act(&self, action: &'static str, row: Option<TaskRow>) {
        let Some(row) = row.or_else(|| self.t.selected()) else { return };
        if self.busy.replace(true) {
            return;
        }
        let Some(s) = row.data().service.clone() else {
            self.busy.set(false);
            return;
        };
        let w = self.win.clone();
        glib::spawn_future_local(async move {
            let (unit, scope) = (s.unit.clone(), s.scope.clone());
            let res = gio::spawn_blocking(move || {
                // a system unit goes through polkit, which may show its password dialog too
                let run = |cmd: &[String]| manage::run(cmd, if scope == "system" { manage::AUTH_TIMEOUT } else { 20 });
                manage::service_action(&unit, action, &scope, &run)
            })
            .await
            .unwrap_or((false, String::new()));
            let Some(win) = w.upgrade() else { return };
            let Some(p) = win.services.borrow().clone() else { return };
            p.busy.set(false);
            if !res.0 {
                let head = match action {
                    "start" => tr!("“{name}” couldn't start."),
                    "stop" => tr!("“{name}” couldn't stop."),
                    _ => tr!("“{name}” couldn't restart."),
                };
                let body = if res.1.is_empty() { tr!("The service manager refused.") } else { res.1 };
                sonata_ui::dialog::alert(&fmt(&head, &[("name", &s.unit)]), &body, &[("ok", tr!("OK"), "default")], |_| {}, Some(win.window.upcast_ref()));
            }
            p.reload();
        });
    }

    fn buttons_state(&self) {
        let row = self.t.selected();
        let running = row.as_ref().and_then(|r| r.data().service.as_ref().map(|s| s.active == "active")).unwrap_or(false);
        self.start_btn.set_sensitive(row.is_some() && !running);
        self.stop_btn.set_sensitive(running);
        self.restart_btn.set_sensitive(row.is_some());
    }

    fn menu(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(row) = row_at(&self.t.view, x, y) else { return };
        g.set_state(gtk::EventSequenceState::Claimed);
        let Some(win) = self.win.upgrade() else { return };
        let running = row.data().service.as_ref().is_some_and(|s| s.active == "active");
        let me = win.services.borrow().clone();
        let mk = |action: &'static str| {
            let (m, r) = (me.clone(), row.clone());
            move || if let Some(p) = &m { p.act(action, Some(r.clone())) }
        };
        let (w, name) = (Rc::downgrade(&win), row.name());
        menu(
            &self.t.view,
            vec![
                vec![item(&tr!("Start"), mk("start")).enabled(!running), item(&tr!("Stop"), mk("stop")).enabled(running), item(&tr!("Restart"), mk("restart"))],
                vec![item(&tr!("Search online"), move || if let Some(w) = w.upgrade() { w.search_online(&name) })],
            ],
            x,
            y,
        );
    }
}

impl Page for ServicesPage {
    fn id(&self) -> &'static str {
        "services"
    }
    fn title(&self) -> &'static str {
        n_!("Services")
    }
    fn icon(&self) -> &'static str {
        "emblem-system-symbolic"
    }
    fn widget(&self) -> gtk::Widget {
        self.root.clone().upcast()
    }
    fn buttons(&self) -> gtk::Box {
        self.buttons.clone()
    }
    fn view(&self) -> Option<gtk::ColumnView> {
        Some(self.t.view.clone())
    }
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn attach(&self, on: bool) {
        attach_view(&self.t.view, &self.t.selection, on);
    }
    fn search(&self, text: &str) {
        self.t.search(text);
    }
    fn shown(&self) {
        if !self.loaded.get() {
            if let Some(p) = self.win.upgrade().and_then(|w| w.services.borrow().clone()) {
                p.reload();
            }
        }
    }
}
