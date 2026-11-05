//! Reminders inside Notes (sonata2/notes/reminders.py; macOS Reminders):
//! the list view and the ⓘ detail panel.
//!
//! The view shows a smart list (Today: due today or overdue, Scheduled:
//! every dated reminder grouped by day, All: every open one grouped by list,
//! Completed) or a user list, under its name in the list's colour. A row:
//! round check (ticking it strikes the title through, then the row leaves
//! the list), the title (edit in place; Return adds the next reminder),
//! notes and due date (red when overdue), priority (!, !!, !!!) and flag.

use crate::store::{self as S, Moment, Rec, Store};
use adw::prelude::*;
use gtk::{glib, pango};
use serde_json::{json, Value};
use sonata_i18n::{fmt, gettext, n_, tr};
use sonata_ui::{controls, menu, panel};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

/// key: (name, icon, colour)
pub const SMART_INFO: [(&str, &str, &str, &str); 4] = [
    ("today", n_!("Today"), "calendar-symbolic", "blue"),
    ("scheduled", n_!("Scheduled"), "alarm-symbolic", "red"),
    ("all", n_!("All"), "inbox-symbolic", "graphite"),
    ("completed", n_!("Completed"), "object-select-symbolic", "gray"),
];
pub const PRIORITIES: [&str; 4] = [n_!("None"), n_!("Low"), n_!("Medium"), n_!("High")];

pub fn smart(key: &str) -> Option<(&'static str, &'static str, &'static str)> {
    SMART_INFO.iter().find(|s| s.0 == key).map(|s| (s.1, s.2, s.3))
}

/// The colour classes (`.c-blue`, `.bg-blue`...) made from the system colours.
pub fn register_css() {
    let mut colors = String::new();
    for c in ["black", "blue", "graphite", "gray", "green", "indigo", "orange", "pink", "pure_black", "purple", "red", "teal", "yellow"] {
        colors.push_str(&format!(
            ".c-{c} {{ color: %(sys_{c})s; }}\n.bg-{c} {{ background: %(sys_{c})s; }}\ncheckbutton.rem-check.c-{c} check:checked {{ background: %(sys_{c})s; border-color: %(sys_{c})s; }}\n"
        ));
    }
    sonata_ui::register("notes-reminder-colors", &colors);
    sonata_ui::css::register_module("notes/reminders.py");
}

thread_local! {
    /// Notes' menus open now (ui/menu.py's OPEN).
    static MENUS_OPEN: Cell<u32> = const { Cell::new(0) };
}

/// A context menu, counted while it is open: a right-click on a new, still
/// empty reminder takes the focus from its title, and that must not delete
/// it before the menu can be used (reminders.py checks ui.menu.OPEN).
pub fn popup_menu(widget: &impl IsA<gtk::Widget>, sections: Vec<Vec<menu::Item>>, opts: menu::Options) -> gtk::PopoverMenu {
    let pop = menu::popup(widget, sections, opts);
    MENUS_OPEN.with(|c| c.set(c.get() + 1));
    pop.connect_closed(|_| MENUS_OPEN.with(|c| c.set(c.get().saturating_sub(1))));
    pop
}

pub fn menu_open() -> bool {
    MENUS_OPEN.with(|c| c.get() > 0)
}

/// A new reminder left empty goes away once its title loses focus (macOS) --
/// unless it got a title meanwhile, or a menu is open.
fn leaves_empty(r: Option<&Rec>) -> bool {
    r.is_some_and(|x| S::s(x, "title").is_empty()) && !menu_open()
}

pub fn today() -> (i32, u32, u32) {
    glib::DateTime::now_local().map(|d| (d.year(), d.month() as u32, d.day_of_month() as u32)).unwrap_or((1970, 1, 1))
}

pub fn now_moment() -> Moment {
    glib::DateTime::now_local()
        .map(|d| Moment(d.year(), d.month() as u32, d.day_of_month() as u32, d.hour() as u32, d.minute() as u32))
        .unwrap_or(Moment(1970, 1, 1, 0, 0))
}

fn date_format(d: (i32, u32, u32), pattern: &str) -> String {
    glib::DateTime::from_local(d.0, d.1 as i32, d.2 as i32, 0, 0, 0.0).ok().and_then(|x| x.format(pattern).ok()).map(|s| s.to_string()).unwrap_or_default()
}

/// "Today, 14:00", "Tomorrow", "Yesterday", "12/10/2026, 09:30".
pub fn due_text(r: &Rec, today: (i32, u32, u32)) -> String {
    let Some(dt) = S::due_datetime(r) else { return String::new() };
    let delta = Moment::day_number(dt.date()) - Moment::day_number(today);
    let day = match delta {
        0 => tr!("Today"),
        1 => tr!("Tomorrow"),
        -1 => tr!("Yesterday"),
        _ => date_format(dt.date(), "%x"),
    };
    if S::has_time(r) { format!("{day}, {:02}:{:02}", dt.3, dt.4) } else { day }
}

pub fn is_overdue(r: &Rec, now: Moment) -> bool {
    let Some(dt) = S::due_datetime(r) else { return false };
    if S::b(r, "completed") {
        return false;
    }
    if S::has_time(r) { dt < now } else { dt.date() < now.date() }
}

pub fn day_heading(d: (i32, u32, u32), today: (i32, u32, u32)) -> String {
    if d < today {
        return tr!("Overdue");
    }
    match Moment::day_number(d) - Moment::day_number(today) {
        0 => tr!("Today"),
        1 => tr!("Tomorrow"),
        _ => date_format(d, &tr!("%A, %-d %B")),
    }
}

// -- rows ---------------------------------------------------------------------------------------------
struct ReminderRow {
    row: gtk::ListBoxRow,
    id: String,
    box_: gtk::Box,
    revealer: gtk::Revealer,
    check: gtk::CheckButton,
    title: gtk::Text,
    strike: gtk::DrawingArea,
    progress: Rc<Cell<f64>>,
    prio: gtk::Label,
    flag: gtk::Image,
    notes: gtk::Label,
    due: gtk::Label,
    anim: RefCell<Option<adw::TimedAnimation>>,
}

pub struct RemindersView {
    pub root: gtk::Box,
    pub store: Rc<RefCell<Store>>,
    pub key: RefCell<String>,
    pub query: RefCell<String>,
    title: gtk::Label,
    count: gtk::Label,
    pub list: gtk::ListBox,
    empty: gtk::Label,
    rows: RefCell<Vec<Rc<ReminderRow>>>,
    on_changed: RefCell<Option<Box<dyn Fn()>>>,
    me: Weak<RemindersView>,
}

impl RemindersView {
    pub fn new(store: Rc<RefCell<Store>>, on_changed: Box<dyn Fn()>) -> Rc<Self> {
        register_css();
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).hexpand(true).build();
        root.add_css_class("nt-rem");
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        head.add_css_class("nt-rem-head");
        let title = gtk::Label::builder().xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::End).build();
        title.add_css_class("nt-rem-title");
        let count = gtk::Label::new(None);
        count.add_css_class("nt-rem-count");
        head.append(&title);
        head.append(&count);
        root.append(&head);
        let list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).build();
        let over = gtk::Overlay::builder().vexpand(true).build();
        over.set_child(Some(&gtk::ScrolledWindow::builder().child(&list).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build()));
        let empty = gtk::Label::builder().can_target(false).visible(false).build();
        empty.add_css_class("nt-rem-empty");
        over.add_overlay(&empty);
        root.append(&over);
        let v = Rc::new_cyclic(|me| RemindersView {
            root,
            store,
            key: RefCell::new("today".into()),
            query: RefCell::default(),
            title,
            count,
            list,
            empty,
            rows: RefCell::default(),
            on_changed: RefCell::new(Some(on_changed)),
            me: me.clone(),
        });
        let w = Rc::downgrade(&v);
        v.list.set_header_func(move |row, before| {
            if let Some(v) = w.upgrade() {
                v.header(row, before);
            }
        });
        // a click on the empty space below the rows adds a reminder (macOS)
        let click = gtk::GestureClick::new();
        let w = Rc::downgrade(&v);
        click.connect_released(move |_, _n, _x, y| {
            if let Some(v) = w.upgrade() {
                if v.list.row_at_y(y as i32).is_none() && *v.key.borrow() != "completed" {
                    let waiting = v.rows.borrow().iter().any(|r| v.store.borrow().reminder(&r.id).is_some_and(|x| S::s(x, "title").is_empty()));
                    if !waiting {
                        v.new_reminder();
                    }
                }
            }
        });
        v.list.add_controller(click);
        v
    }

    fn notify_changed(&self) {
        if let Some(f) = self.on_changed.borrow().as_ref() {
            f();
        }
    }

    pub fn show(&self, key: &str, query: Option<&str>) {
        *self.key.borrow_mut() = key.to_string();
        if let Some(q) = query {
            *self.query.borrow_mut() = q.to_string();
        }
        self.refresh();
    }

    fn rows_data(&self) -> Vec<Rec> {
        let st = self.store.borrow();
        let key = self.key.borrow().clone();
        let q = self.query.borrow().clone();
        if q.trim().is_empty() {
            return st.reminders_in(&key, today());
        }
        let base: Vec<Rec> = if key == "completed" { st.reminders.clone() } else { st.reminders.iter().filter(|r| !S::b(r, "completed")).cloned().collect() };
        S::search_reminders(&base, &q)
    }

    pub fn refresh(&self) {
        let sel_id = self.list.selected_row().and_then(|r| self.rows.borrow().iter().find(|x| x.row == r).map(|x| x.id.clone()));
        self.list.remove_all();
        self.rows.borrow_mut().clear();
        let items = self.rows_data();
        for r in &items {
            let row = self.make_row(r);
            self.list.append(&row.row);
            if Some(S::s(r, "id")) == sel_id.as_deref() {
                self.list.select_row(Some(&row.row));
            }
            self.rows.borrow_mut().push(row);
        }
        let key = self.key.borrow().clone();
        let (mut name, color) = match smart(&key) {
            Some((n, _icon, c)) => (gettext(n), c.to_string()),
            None => {
                let st = self.store.borrow();
                let l = st.rlist(&key);
                (S::display_name(l), l.map(|x| S::s(x, "color").to_string()).filter(|c| !c.is_empty()).unwrap_or_else(|| "blue".into()))
            }
        };
        let q = self.query.borrow().trim().to_string();
        if !q.is_empty() {
            name = fmt(&tr!("Results for “{query}”"), &[("query", &q)]);
        }
        self.title.set_label(&name);
        for w in [&self.title, &self.count] {
            for c in w.css_classes() {
                if c.starts_with("c-") {
                    w.remove_css_class(&c);
                }
            }
            w.add_css_class(&format!("c-{color}"));
        }
        self.count.set_label(&if key != "completed" { items.len().to_string() } else { String::new() });
        self.empty.set_label(&if key == "completed" { tr!("No Completed Reminders") } else { tr!("No Reminders") });
        self.empty.set_visible(items.is_empty());
        self.list.invalidate_headers();
    }

    fn row_of(&self, row: &gtk::ListBoxRow) -> Option<Rc<ReminderRow>> {
        self.rows.borrow().iter().find(|x| &x.row == row).cloned()
    }

    fn header(&self, row: &gtk::ListBoxRow, before: Option<&gtk::ListBoxRow>) {
        let (Some(r), key) = (self.row_of(row), self.key.borrow().clone()) else { return };
        let st = self.store.borrow();
        let rec = st.reminder(&r.id).cloned().unwrap_or_default();
        let prev = before.and_then(|b| self.row_of(b)).and_then(|b| st.reminder(&b.id).cloned());
        let text = match key.as_str() {
            "scheduled" => {
                let h = S::due_date(&rec).map(|d| day_heading(d, today()));
                let ph = prev.as_ref().and_then(S::due_date).map(|d| day_heading(d, today()));
                h.filter(|h| prev.is_none() || ph.as_ref() != Some(h))
            }
            "all" | "completed" => {
                let lid = S::s(&rec, "list");
                let changed = prev.as_ref().is_none_or(|p| S::s(p, "list") != lid);
                changed.then(|| st.rlist(lid).map(|l| S::display_name(Some(l)))).flatten()
            }
            _ => None,
        };
        match text {
            None => row.set_header(None::<&gtk::Widget>),
            Some(t) => {
                let same = row.header().and_downcast::<gtk::Label>().is_some_and(|l| l.label() == t);
                if !same {
                    let l = gtk::Label::builder().label(t).xalign(0.0).build();
                    l.add_css_class("nt-rem-section");
                    row.set_header(Some(&l));
                }
            }
        }
    }

    fn make_row(&self, r: &Rec) -> Rc<ReminderRow> {
        let color = {
            let st = self.store.borrow();
            st.rlist(S::s(r, "list")).map(|l| S::s(l, "color").to_string()).filter(|c| !c.is_empty()).unwrap_or_else(|| "blue".into())
        };
        let row = gtk::ListBoxRow::builder().activatable(false).build();
        let revealer = gtk::Revealer::builder().reveal_child(true).transition_type(gtk::RevealerTransitionType::SlideUp).transition_duration(200).build();
        let box_ = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        box_.add_css_class("nt-rem-row");
        let check = gtk::CheckButton::builder().valign(gtk::Align::Start).active(S::b(r, "completed")).can_focus(false).build();
        check.add_css_class("rem-check");
        check.add_css_class(&format!("c-{color}"));
        box_.append(&check);
        let body = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(1).hexpand(true).build();
        body.add_css_class("nt-rem-row-body");
        let line = gtk::Box::new(gtk::Orientation::Horizontal, 4);
        let prio = gtk::Label::builder().visible(false).build();
        prio.add_css_class("rem-prio");
        prio.add_css_class(&format!("c-{color}"));
        line.append(&prio);
        let title = gtk::Text::builder().hexpand(true).placeholder_text(tr!("New Reminder")).build();
        title.add_css_class("rem-title");
        title.buffer().set_text(S::s(r, "title"));
        let strike = gtk::DrawingArea::builder().can_target(false).build();
        let progress = Rc::new(Cell::new(if S::b(r, "completed") { 1.0 } else { 0.0 }));
        let over = gtk::Overlay::builder().child(&title).hexpand(true).build();
        over.add_overlay(&strike);
        line.append(&over);
        let flag = gtk::Image::builder().icon_name("flag-filled-symbolic").visible(false).build();
        flag.add_css_class("c-orange");
        line.append(&flag);
        let info = gtk::Button::builder().icon_name("info-symbolic").tooltip_text(tr!("Show Details")).can_focus(false).valign(gtk::Align::Center).build();
        info.add_css_class("rem-info");
        line.append(&info);
        body.append(&line);
        let notes = gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::End).visible(false).build();
        notes.add_css_class("rem-sub");
        body.append(&notes);
        let due = gtk::Label::builder().xalign(0.0).visible(false).build();
        due.add_css_class("rem-sub");
        body.append(&due);
        box_.append(&body);
        revealer.set_child(Some(&box_));
        row.set_child(Some(&revealer));
        let rr = Rc::new(ReminderRow {
            row,
            id: S::s(r, "id").to_string(),
            box_,
            revealer,
            check,
            title,
            strike,
            progress,
            prio,
            flag,
            notes,
            due,
            anim: RefCell::new(None),
        });
        let (p, t) = (rr.progress.clone(), rr.title.clone());
        rr.strike.set_draw_func(move |_, cr, _w, h| {
            if p.get() <= 0.0 {
                return;
            }
            let text = t.buffer().text();
            let (tw, _th) = t.create_pango_layout(Some(&text)).pixel_size();
            let c = sonata_ui::rgba("label_secondary");
            cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
            cr.set_line_width(1.0);
            let y = (h / 2) as f64 + 0.5;
            cr.move_to(0.0, y);
            cr.line_to(tw as f64 * p.get(), y);
            let _ = cr.stroke();
        });
        let (v, id) = (self.me.clone(), rr.id.clone());
        info.connect_clicked(move |b| {
            if let Some(v) = v.upgrade() {
                v.detail_panel(b.upcast_ref(), &id);
            }
        });
        let click = gtk::GestureClick::new();
        let (v, id, d) = (self.me.clone(), rr.id.clone(), rr.due.clone());
        click.connect_released(move |_, _, _, _| {
            if let Some(v) = v.upgrade() {
                v.detail_panel(d.upcast_ref(), &id);
            }
        });
        rr.due.add_controller(click);
        let (v, w) = (self.me.clone(), Rc::downgrade(&rr));
        rr.title.connect_activate(move |_| {
            if let (Some(v), Some(r)) = (v.upgrade(), w.upgrade()) {
                v.commit_title(&r, true, false);
            }
        });
        let focus = gtk::EventControllerFocus::new();
        let (v, w) = (self.me.clone(), Rc::downgrade(&rr));
        focus.connect_leave(move |_| {
            if let (Some(v), Some(r)) = (v.upgrade(), w.upgrade()) {
                v.commit_title(&r, false, true);
            }
        });
        rr.title.add_controller(focus);
        let (v, w) = (self.me.clone(), Rc::downgrade(&rr));
        rr.check.connect_toggled(move |c| {
            if let (Some(v), Some(r)) = (v.upgrade(), w.upgrade()) {
                v.toggled(&r, c.is_active());
            }
        });
        let menu_click = gtk::GestureClick::builder().button(3).build();
        let (v, w) = (self.me.clone(), Rc::downgrade(&rr));
        menu_click.connect_pressed(move |_, _n, x, y| {
            if let (Some(v), Some(r)) = (v.upgrade(), w.upgrade()) {
                v.row_menu(&r, x, y);
            }
        });
        rr.row.add_controller(menu_click);
        self.update_row(&rr);
        rr
    }

    fn update_row(&self, rr: &ReminderRow) {
        let st = self.store.borrow();
        let Some(r) = st.reminder(&rr.id) else { return };
        if S::b(r, "completed") {
            rr.box_.add_css_class("done");
        } else {
            rr.box_.remove_css_class("done");
        }
        let p = S::f(r, "priority") as usize;
        rr.prio.set_label(&"!".repeat(p.min(3)));
        rr.prio.set_visible(p > 0);
        rr.flag.set_visible(S::b(r, "flagged"));
        let notes = S::s(r, "notes").trim().split('\n').next().unwrap_or("").to_string();
        rr.notes.set_label(&notes);
        rr.notes.set_visible(!notes.is_empty());
        let text = due_text(r, today());
        rr.due.set_label(&text);
        rr.due.set_visible(!text.is_empty());
        if is_overdue(r, now_moment()) {
            rr.due.add_css_class("overdue");
        } else {
            rr.due.remove_css_class("overdue");
        }
        rr.strike.queue_draw();
    }

    fn commit_title(&self, rr: &ReminderRow, then_new: bool, drop_empty: bool) {
        let text = rr.title.buffer().text().trim().to_string();
        let rec = self.store.borrow().reminder(&rr.id).cloned();
        let Some(r) = rec else { return };
        if drop_empty && text.is_empty() && S::s(&r, "notes").is_empty() && !S::b(&r, "due") {
            // a new reminder left empty goes away (macOS)
            let (v, id) = (self.me.clone(), rr.id.clone());
            glib::idle_add_local_once(move || {
                if let Some(v) = v.upgrade() {
                    let drop = leaves_empty(v.store.borrow().reminder(&id));
                    if drop {
                        v.delete(&id);
                    }
                }
            });
            return;
        }
        if text != S::s(&r, "title") {
            self.store.borrow_mut().update_reminder(&rr.id, obj(json!({"title": text})));
            self.changed(false);
        }
        if then_new && !text.is_empty() {
            self.new_reminder();
        }
    }

    fn toggled(&self, rr: &Rc<ReminderRow>, done: bool) {
        let was = self.store.borrow().reminder(&rr.id).is_some_and(|r| S::b(r, "completed"));
        if done == was {
            return;
        }
        self.store.borrow_mut().update_reminder(&rr.id, obj(json!({"completed": done})));
        self.update_row(rr);
        let p = rr.progress.clone();
        let s = rr.strike.clone();
        let target = adw::CallbackAnimationTarget::new(move |v| {
            p.set(v);
            s.queue_draw();
        });
        let anim = adw::TimedAnimation::new(&rr.strike, rr.progress.get(), if done { 1.0 } else { 0.0 }, sonata_ui::tokens::ms(300.0), target);
        anim.set_easing(adw::Easing::EaseOutCubic);
        anim.play();
        *rr.anim.borrow_mut() = Some(anim);
        // the row leaves a list it no longer belongs to (macOS: after a beat)
        let stays = (*self.key.borrow() == "completed") == done;
        if !stays {
            let (v, w) = (self.me.clone(), Rc::downgrade(rr));
            glib::timeout_add_local_once(Duration::from_millis(900), move || {
                let (Some(v), Some(rr)) = (v.upgrade(), w.upgrade()) else { return };
                let done_now = v.store.borrow().reminder(&rr.id).is_some_and(|r| S::b(r, "completed"));
                if done_now != (*v.key.borrow() == "completed") {
                    let v2 = v.me.clone();
                    rr.revealer.connect_child_revealed_notify(move |rv| {
                        if !rv.is_child_revealed() {
                            if let Some(v) = v2.upgrade() {
                                v.refresh();
                            }
                        }
                    });
                    rr.revealer.set_reveal_child(false);
                }
            });
        }
        self.changed(false);
    }

    fn row_menu(&self, rr: &Rc<ReminderRow>, x: f64, y: f64) {
        let Some(r) = self.store.borrow().reminder(&rr.id).cloned() else { return };
        let lists: Vec<menu::Item> = self
            .store
            .borrow()
            .lists
            .iter()
            .map(|l| {
                let (v, id, lid) = (self.me.clone(), rr.id.clone(), S::s(l, "id").to_string());
                menu::Item::check(&S::display_name(Some(l)), S::s(&r, "list") == lid, move |_| {
                    if let Some(v) = v.upgrade() {
                        v.store.borrow_mut().update_reminder(&id, obj(json!({"list": lid})));
                        v.changed(true);
                    }
                })
            })
            .collect();
        let flagged = S::b(&r, "flagged");
        let done = S::b(&r, "completed");
        let (v1, v2, v3, id1, id2, id3, w) = (self.me.clone(), self.me.clone(), self.me.clone(), rr.id.clone(), rr.id.clone(), rr.id.clone(), Rc::downgrade(rr));
        let anchor: gtk::Widget = if rr.due.is_visible() { rr.due.clone().upcast() } else { rr.row.clone().upcast() };
        popup_menu(
            &rr.row,
            vec![
                vec![menu::Item::new(&tr!("Show Details"), move || {
                    if let Some(v) = v1.upgrade() {
                        v.detail_panel(&anchor, &id1);
                    }
                })],
                vec![
                    menu::Item::new(&if flagged { tr!("Unflag") } else { tr!("Flag") }, move || {
                        if let Some(v) = v2.upgrade() {
                            v.store.borrow_mut().update_reminder(&id2, obj(json!({"flagged": !flagged})));
                            v.changed(false);
                        }
                    }),
                    menu::Item::new(&if done { tr!("Mark as Not Completed") } else { tr!("Mark as Completed") }, move || {
                        if let Some(rr) = w.upgrade() {
                            rr.check.set_active(!rr.check.is_active());
                        }
                    }),
                    menu::Item::submenu(&tr!("Move to"), vec![lists]),
                ],
                vec![menu::Item::new(&tr!("Delete"), move || {
                    if let Some(v) = v3.upgrade() {
                        v.delete(&id3);
                    }
                })],
            ],
            menu::Options::at(x, y),
        );
    }

    /// The ⓘ panel: notes, date & time, flag, priority, list.
    pub fn detail_panel(&self, anchor: &gtk::Widget, id: &str) {
        let Some(r) = self.store.borrow().reminder(id).cloned() else { return };
        let col = gtk::Box::new(gtk::Orientation::Vertical, 6);
        col.add_css_class("rem-detail");
        let title = gtk::Entry::builder().text(S::s(&r, "title")).placeholder_text(tr!("Title")).build();
        title.add_css_class("rem-detail-title");
        let notes = gtk::Entry::builder().text(S::s(&r, "notes")).placeholder_text(tr!("Notes")).build();
        col.append(&title);
        col.append(&notes);
        col.append(&panel::separator());
        let save = {
            let (v, id) = (self.me.clone(), id.to_string());
            Rc::new(move |changes: Value| {
                if let Some(v) = v.upgrade() {
                    v.store.borrow_mut().update_reminder(&id, obj(changes));
                    v.changed(false);
                }
            })
        };
        let s = save.clone();
        title.connect_changed(move |e| s(json!({"title": e.text().trim()})));
        let s = save.clone();
        notes.connect_changed(move |e| s(json!({"notes": e.text().as_str()})));
        let dt = S::due_datetime(&r);
        let cal = gtk::Calendar::new();
        if let Some(d) = dt {
            if let Ok(gd) = glib::DateTime::from_local(d.0, d.1 as i32, d.2 as i32, 0, 0, 0.0) {
                cal.select_day(&gd);
            }
        }
        let hour = gtk::SpinButton::with_range(0.0, 23.0, 1.0);
        let minute = gtk::SpinButton::with_range(0.0, 59.0, 5.0);
        for sp in [&hour, &minute] {
            sp.set_wrap(true);
            sp.set_orientation(gtk::Orientation::Horizontal);
        }
        let now = now_moment();
        let timed = S::has_time(&r);
        hour.set_value(if let (Some(d), true) = (dt, timed) { d.3 as f64 } else { ((now.3 + 1) % 24) as f64 });
        minute.set_value(if let (Some(d), true) = (dt, timed) { d.4 as f64 } else { 0.0 });
        let times = gtk::Box::builder().spacing(4).halign(gtk::Align::End).build();
        times.append(&hour);
        times.append(&gtk::Label::new(Some(":")));
        times.append(&minute);
        let cal_rev = gtk::Revealer::builder().child(&cal).reveal_child(dt.is_some()).build();
        let time_rev = gtk::Revealer::builder().child(&times).reveal_child(timed).build();
        let date_sw = controls::switch(dt.is_some());
        let time_sw = controls::switch(timed);
        let apply_due = {
            let (date_sw, time_sw, cal, hour, minute, save) = (date_sw.clone(), time_sw.clone(), cal.clone(), hour.clone(), minute.clone(), save.clone());
            Rc::new(move || {
                if !date_sw.is_active() {
                    save(json!({"due": null}));
                    return;
                }
                let d = cal.date();
                let day = Some((d.year(), d.month() as u32, d.day_of_month() as u32));
                let time = time_sw.is_active().then(|| (hour.value() as u32, minute.value() as u32));
                save(json!({"due": S::make_due(day, time)}));
            })
        };
        let (cr, ts, a) = (cal_rev.clone(), time_sw.clone(), apply_due.clone());
        date_sw.connect_active_notify(move |sw| {
            cr.set_reveal_child(sw.is_active());
            if !sw.is_active() && ts.is_active() {
                ts.set_active(false);
            }
            a();
        });
        let (tr_, ds, a) = (time_rev.clone(), date_sw.clone(), apply_due.clone());
        time_sw.connect_active_notify(move |sw| {
            tr_.set_reveal_child(sw.is_active());
            if sw.is_active() && !ds.is_active() {
                ds.set_active(true);
            }
            a();
        });
        let a = apply_due.clone();
        cal.connect_day_selected(move |_| a());
        let a = apply_due.clone();
        hour.connect_value_changed(move |_| a());
        let a = apply_due;
        minute.connect_value_changed(move |_| a());
        col.append(&panel::row(Some("calendar-symbolic"), &tr!("Date"), Some(date_sw.upcast_ref()), None).0);
        col.append(&cal_rev);
        col.append(&panel::row(Some("alarm-symbolic"), &tr!("Time"), Some(time_sw.upcast_ref()), None).0);
        col.append(&time_rev);
        col.append(&panel::separator());
        let flag = controls::switch(S::b(&r, "flagged"));
        let s = save.clone();
        flag.connect_active_notify(move |sw| s(json!({"flagged": sw.is_active()})));
        col.append(&panel::row(Some("flag-symbolic"), &tr!("Flag"), Some(flag.upcast_ref()), None).0);
        let prios: Vec<String> = PRIORITIES.iter().map(|p| gettext(p)).collect();
        let s = save.clone();
        let prio = controls::popup_button(&prios, (S::f(&r, "priority") as u32).min(3), move |i| s(json!({"priority": i})));
        col.append(&panel::row(None, &tr!("Priority"), Some(prio.upcast_ref()), None).0);
        let (ids, names): (Vec<String>, Vec<String>) = self.store.borrow().lists.iter().map(|l| (S::s(l, "id").to_string(), S::display_name(Some(l)))).unzip();
        let cur = ids.iter().position(|i| i == S::s(&r, "list")).unwrap_or(0) as u32;
        let s = save;
        let ids2 = ids.clone();
        let lists = controls::popup_button(&names, cur, move |i| {
            if let Some(lid) = ids2.get(i as usize) {
                s(json!({"list": lid}));
            }
        });
        col.append(&panel::row(None, &tr!("List"), Some(lists.upcast_ref()), None).0);
        let pos = if anchor.is::<gtk::Button>() { gtk::PositionType::Left } else { gtk::PositionType::Bottom };
        let pop = panel::popup(anchor, &col, pos);
        let v = self.me.clone();
        pop.connect_closed(move |_| {
            if let Some(v) = v.upgrade() {
                v.refresh();
            }
        });
    }

    pub fn changed(&self, refresh: bool) {
        if refresh {
            self.refresh();
        } else {
            for rr in self.rows.borrow().iter() {
                self.update_row(rr);
            }
            let key = self.key.borrow().clone();
            self.count.set_label(&if key != "completed" { self.rows_data().len().to_string() } else { String::new() });
        }
        self.notify_changed();
    }

    /// A new reminder in this list (Today / Scheduled: due today), its title
    /// ready for typing.
    pub fn new_reminder(&self) -> String {
        let key = self.key.borrow().clone();
        let due = (key == "today" || key == "scheduled").then(|| S::make_due(Some(today()), None)).flatten();
        let lid = if S::SMART.contains(&key.as_str()) { S::DEFAULT_LIST.to_string() } else { key.clone() };
        let id = self.store.borrow_mut().new_reminder(&lid, "", due, None);
        if key == "completed" {
            *self.key.borrow_mut() = lid;
        }
        self.query.borrow_mut().clear();
        self.refresh();
        let row = self.rows.borrow().iter().find(|r| r.id == id).cloned();
        if let Some(r) = row {
            self.list.select_row(Some(&r.row));
            r.title.grab_focus();
        }
        self.notify_changed();
        id
    }

    pub fn delete(&self, id: &str) {
        self.store.borrow_mut().delete_reminder(id);
        self.refresh();
        self.notify_changed();
    }

    pub fn delete_selected(&self) -> bool {
        let Some(row) = self.list.selected_row() else { return false };
        let Some(r) = self.row_of(&row) else { return false };
        self.delete(&r.id);
        true
    }
}

fn obj(v: Value) -> Rec {
    match v {
        Value::Object(m) => m,
        _ => Rec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(due: &str) -> Rec {
        obj(json!({"due": due}))
    }

    #[test]
    fn near_days_by_name() {
        sonata_i18n::use_language("en");
        let today = (2026, 10, 10);
        assert_eq!(due_text(&r("2026-10-10T14:00"), today), "Today, 14:00");
        assert_eq!(due_text(&r("2026-10-11"), today), "Tomorrow");
        assert_eq!(due_text(&r("2026-10-09T08:30"), today), "Yesterday, 08:30");
        assert_eq!(due_text(&r("2027-01-01"), (2026, 12, 31)), "Tomorrow");
        assert_eq!(due_text(&r("2026-10-31T10:00"), (2026, 11, 1)), "Yesterday, 10:00");
        assert_eq!(due_text(&r("soon"), today), "");
    }

    #[test]
    fn overdue() {
        let noon = Moment(2026, 10, 10, 12, 0);
        assert!(is_overdue(&r("2026-10-10T11:59"), noon));
        assert!(!is_overdue(&r("2026-10-10T12:00"), noon)); // due now: not late yet
        assert!(!is_overdue(&r("2026-10-10"), noon)); // all day: late only the next day
        assert!(is_overdue(&r("2026-10-09"), noon));
        let mut done = r("2026-10-01");
        done.insert("completed".into(), json!(true));
        assert!(!is_overdue(&done, noon));
    }

    /// Right-clicking a new reminder whose title was still empty deleted it
    /// as the menu opened (the title lost focus to the menu): Python keeps it
    /// while a menu is open.
    #[test]
    fn test_empty_reminder_deleted_when_its_menu_opened() {
        let empty = obj(json!({"id": "a", "title": ""}));
        assert!(leaves_empty(Some(&empty)));
        MENUS_OPEN.with(|c| c.set(1));
        assert!(!leaves_empty(Some(&empty)));
        MENUS_OPEN.with(|c| c.set(0));
        assert!(!leaves_empty(Some(&obj(json!({"id": "a", "title": "Milk"})))));
        assert!(!leaves_empty(None));
    }

    #[test]
    fn headings() {
        sonata_i18n::use_language("en");
        let today = (2026, 10, 10);
        assert_eq!(day_heading((2026, 9, 1), today), "Overdue");
        assert_eq!(day_heading(today, today), "Today");
        assert_eq!(day_heading((2026, 10, 11), today), "Tomorrow");
    }
}
