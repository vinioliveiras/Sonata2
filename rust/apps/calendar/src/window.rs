//! Calendar (macOS Ventura Calendar): local calendars, one window
//! (calendar/window.py).
//!
//! Toolbar (continuing the compositor's glass title bar): calendar list
//! toggle, + (new event), Day / Week / Month / Year, search, and ‹ Today ›.
//! The translucent sidebar lists the calendars ("On My Computer": Home,
//! Work...) with coloured checkboxes that show / hide them -- right-click
//! to add, rename, recolour or delete one; the selected calendar receives
//! new events -- and a mini month to jump to a date.
//!
//! Double-click an empty slot to create an event there, an event to edit
//! it in a popover; drag an event to move it, its bottom edge to change
//! its end. Delete removes the selected event.
//!
//! Keys (⌘ is Ctrl or Super): ⌘1..4 Day / Week / Month / Year, ⌘T today,
//! ← / → previous / next, ⌘N new event, ⌘F search, ⌘Z undo, ⇧⌘Z redo,
//! Delete / Backspace delete, Esc deselects.
//!
//! Storage: one iCalendar file per calendar (ics.rs). .ics files opened
//! with Calendar or dropped on its window are imported. While Calendar
//! runs, alerts show as desktop notifications (alerts.rs).

use crate::ics::{self, midnight, Event, Occurrence, DT};
use crate::model::{self, Store};
use crate::views::{self, MiniMonth, MonthView, Timeline, YearView};
use chrono::{Datelike, Duration, NaiveDate, NaiveTime, Timelike};
use gtk::prelude::*;
use gtk::{gdk, pango};
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, n_, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap};
use std::rc::{Rc, Weak};

pub const VIEWS: [(&str, &str); 4] = [("day", n_!("Day")), ("week", n_!("Week")), ("month", n_!("Month")), ("year", n_!("Year"))];
const REPEATS: [(Option<&str>, &str); 5] = [(None, n_!("None")), (Some("DAILY"), n_!("Every Day")), (Some("WEEKLY"), n_!("Every Week")), (Some("MONTHLY"), n_!("Every Month")), (Some("YEARLY"), n_!("Every Year"))];
const ALERTS: [(Option<i64>, &str); 7] = [(None, n_!("None")), (Some(0), n_!("At time of event")), (Some(5), n_!("5 minutes before")), (Some(15), n_!("15 minutes before")), (Some(30), n_!("30 minutes before")), (Some(60), n_!("1 hour before")), (Some(1440), n_!("1 day before"))];
const SIDEBAR_W: i32 = 280;
const RESULTS_W: i32 = 270;
/// never smaller: a week's columns and some hours in sight
const MIN_SIZE: (i32, i32) = (720, 440);

fn defaults() -> Value {
    json!({"view": "week", "sidebar": true, "hidden": [], "default_calendar": "", "week_start": "locale"})
}

/// '9', '9:30', '0930', '9.30 pm', '21:30' -> a time, or None.
pub fn parse_time(text: &str) -> Option<NaiveTime> {
    let t = text.trim().to_lowercase().replace('.', ":");
    let re = regex::Regex::new(r"^(\d{1,2})(?::?(\d{2}))?\s*(am|pm|a|p)?$").unwrap();
    let c = re.captures(&t)?;
    let mut h: u32 = c[1].parse().ok()?;
    let mi: u32 = c.get(2).map_or(Some(0), |m| m.as_str().parse().ok())?;
    if let Some(ap) = c.get(3) {
        if !(1..=12).contains(&h) {
            return None;
        }
        h = h % 12 + if ap.as_str().starts_with('p') { 12 } else { 0 };
    }
    if h > 23 || mi > 59 {
        return None;
    }
    NaiveTime::from_hms_opt(h, mi, 0)
}

type Change = (Option<Event>, Option<Event>);

/// 09:30 / 9:30 AM (the menu bar clock's choice of 12 or 24 hours).
pub fn fmt_time(h24: bool, t: NaiveTime) -> String {
    if h24 {
        return format!("{:02}:{:02}", t.hour(), t.minute());
    }
    let h = if t.hour() % 12 == 0 { 12 } else { t.hour() % 12 };
    let ap = if t.hour() < 12 { "AM" } else { "PM" };
    if t.minute() == 0 { format!("{h} {ap}") } else { format!("{h}:{:02} {ap}", t.minute()) }
}

struct CalRow {
    row: gtk::ListBoxRow,
    cal_id: String,
    stack: gtk::Stack,
    entry: gtk::Entry,
}

pub struct CalendarWindow {
    pub window: gtk::ApplicationWindow,
    cfg: RefCell<Map<String, Value>>,
    first_weekday: u32,
    h24: bool,
    pub store: RefCell<Store>,
    hidden: RefCell<BTreeSet<String>>,
    date: Cell<NaiveDate>,
    view: RefCell<String>,
    selected: RefCell<Option<Occurrence>>,
    cache: RefCell<HashMap<(DT, DT), Rc<Vec<Occurrence>>>>,
    undo_stack: RefCell<Vec<Vec<Change>>>,
    redo_stack: RefCell<Vec<Vec<Change>>>,
    editor: RefCell<Option<Rc<Editor>>>,
    alerts: RefCell<Option<Rc<crate::alerts::Alerts>>>,
    add_btn: gtk::Button,
    view_buttons: Vec<(String, gtk::ToggleButton)>,
    search: gtk::SearchEntry,
    title: gtk::Label,
    subtitle: gtk::Label,
    sidebar_rev: gtk::Revealer,
    cal_list: gtk::ListBox,
    cal_rows: RefCell<Vec<CalRow>>,
    mini: RefCell<Option<Rc<MiniMonth>>>,
    stack: gtk::Stack,
    day: RefCell<Option<Rc<Timeline>>>,
    week: RefCell<Option<Rc<Timeline>>>,
    month: RefCell<Option<Rc<MonthView>>>,
    year: RefCell<Option<Rc<YearView>>>,
    results_rev: gtk::Revealer,
    results: gtk::ListBox,
    /// each result row's (time, event)
    result_targets: RefCell<Vec<(gtk::ListBoxRow, DT, Rc<Event>)>>,
    me: Weak<CalendarWindow>,
}

thread_local! {
    static WINDOWS: RefCell<Vec<Rc<CalendarWindow>>> = const { RefCell::new(Vec::new()) };
}

impl CalendarWindow {
    pub fn new(app: &gtk::Application, folder: std::path::PathBuf) -> Rc<Self> {
        let cfg = sonata_config::load("calendar", &defaults());
        let first_weekday = cfg.get("week_start").and_then(Value::as_u64).filter(|w| *w <= 6).map(|w| w as u32).unwrap_or_else(model::locale_first_weekday);
        let topbar = sonata_config::load("topbar", &json!({"clock_format": "%H:%M"}));
        let clock = topbar.get("clock_format").and_then(Value::as_str).unwrap_or("%H:%M").to_string();
        let h24 = ["%H", "%k", "%R", "%T"].iter().any(|x| clock.contains(x));
        let mut store = Store::new(folder);
        store.load();
        store.write_async();
        let hidden: BTreeSet<String> = cfg.get("hidden").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default();
        let view = cfg.get("view").and_then(Value::as_str).filter(|v| VIEWS.iter().any(|x| x.0 == *v)).unwrap_or("week").to_string();
        let sidebar_shown = cfg.get("sidebar").and_then(Value::as_bool).unwrap_or(true);
        let win = Rc::new_cyclic(|me: &Weak<CalendarWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Calendar")).css_classes(["sonata-calendar"]).build();
            sonata_ui::window::standard(&window);
            window.set_size_request(MIN_SIZE.0, MIN_SIZE.1);
            // its last size; the first time roomy, about two thirds of the
            // display (ui.window.remember_size(roomy=True), 0.20.3)
            sonata_ui::window::remember_size_roomy(window.upcast_ref(), "calendar", 1100, 720);
            let call = |f: fn(&Rc<CalendarWindow>)| {
                let w = me.clone();
                move || {
                    if let Some(t) = w.upgrade() {
                        f(&t);
                    }
                }
            };
            // -- toolbar
            let (toolbar, bar, _) = sonata_ui::window::glass_toolbar(&window, Vec::new());
            let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            let tool = |icon: &str, tip: String, f: Box<dyn Fn()>| {
                let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).can_focus(false).css_classes(["tool"]).build();
                b.connect_clicked(move |_| f());
                b
            };
            start.append(&tool("sidebar-show-symbolic", tr!("Show Calendar List"), Box::new(call(|t| t.toggle_sidebar()))));
            let add_btn = tool("list-add-symbolic", tr!("New Event"), Box::new(call(|t| t.new_event())));
            start.append(&add_btn);
            bar.set_start_widget(Some(&start));
            let seg = gtk::Box::builder().css_classes(["cal-seg"]).valign(gtk::Align::Center).homogeneous(true).build();
            let mut view_buttons: Vec<(String, gtk::ToggleButton)> = Vec::new();
            for (vid, label) in VIEWS {
                let b = gtk::ToggleButton::builder().label(sonata_i18n::gettext(label)).can_focus(false).build();
                if let Some((_, first)) = view_buttons.first() {
                    b.set_group(Some(first));
                }
                let w = me.clone();
                b.connect_toggled(move |b| {
                    if let Some(t) = w.upgrade() {
                        if b.is_active() && *t.view.borrow() != vid {
                            t.set_view(vid);
                        }
                    }
                });
                seg.append(&b);
                view_buttons.push((vid.to_string(), b));
            }
            bar.set_center_widget(Some(&seg));
            let end = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search")).width_chars(16).css_classes(["cal-search"]).valign(gtk::Align::Center).build();
            let f = call(|t| t.search_changed());
            search.connect_search_changed(move |_| f());
            let f = call(|t| t.close_search());
            search.connect_stop_search(move |_| f());
            end.append(&search);
            end.append(&tool("go-previous-symbolic", tr!("Previous"), Box::new(call(|t| t.step(-1)))));
            let today_btn = gtk::Button::builder().label(tr!("Today")).tooltip_text(tr!("Today")).can_focus(false).css_classes(["tool", "cal-today"]).build();
            let f = call(|t| t.today());
            today_btn.connect_clicked(move |_| f());
            end.append(&today_btn);
            end.append(&tool("go-next-symbolic", tr!("Next"), Box::new(call(|t| t.step(1)))));
            bar.set_end_widget(Some(&end));

            // -- sidebar
            let sidebar = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["sonata-sidebar", "cal-sidebar"]).build();
            sidebar.set_size_request(SIDEBAR_W, -1);
            sidebar.append(&gtk::Label::builder().label(tr!("On My Computer")).xalign(0.0).css_classes(["cal-sec"]).build());
            let cal_list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).build();
            let w = me.clone();
            cal_list.connect_row_selected(move |_, row| {
                if let (Some(t), Some(row)) = (w.upgrade(), row) {
                    t.calendar_selected(row);
                }
            });
            let scroll = gtk::ScrolledWindow::builder().child(&cal_list).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build();
            sidebar.append(&scroll);
            let click = gtk::GestureClick::builder().button(3).build();
            let w = me.clone();
            click.connect_pressed(move |g, _, x, y| {
                if let Some(t) = w.upgrade() {
                    g.set_state(gtk::EventSequenceState::Claimed);
                    t.sidebar_menu(g.widget().as_ref(), x, y);
                }
            });
            scroll.add_controller(click);
            let mini = MiniMonth::new(me.clone(), views::today());
            sidebar.append(&mini.root);
            let sidebar_rev = gtk::Revealer::builder().child(&sidebar).reveal_child(sidebar_shown).transition_type(gtk::RevealerTransitionType::SlideRight).transition_duration(sonata_ui::tokens::ms(250.0)).hexpand(false).build();

            // -- content
            let body = gtk::Box::builder().vexpand(true).build();
            body.append(&sidebar_rev);
            let content = gtk::Box::builder().orientation(gtk::Orientation::Vertical).hexpand(true).css_classes(["cal-content"]).build();
            let heading = gtk::Box::builder().css_classes(["cal-heading"]).spacing(10).build();
            let title = gtk::Label::builder().xalign(0.0).css_classes(["cal-title"]).valign(gtk::Align::Baseline).build();
            let subtitle = gtk::Label::builder().xalign(0.0).css_classes(["cal-subtitle"]).valign(gtk::Align::Baseline).build();
            heading.append(&title);
            heading.append(&subtitle);
            content.append(&heading);
            let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(sonata_ui::tokens::ms(150.0)).vexpand(true).hexpand(true).build();
            let day = Timeline::new(me.clone(), 1);
            let week = Timeline::new(me.clone(), 7);
            let month = MonthView::new(me.clone());
            let year = YearView::new(me.clone());
            stack.add_named(&day.root, Some("day"));
            stack.add_named(&week.root, Some("week"));
            stack.add_named(&month.canvas.area, Some("month"));
            stack.add_named(&year.canvas.area, Some("year"));
            content.append(&stack);
            body.append(&content);
            let rbox = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["cal-results"]).build();
            rbox.set_size_request(RESULTS_W, -1);
            let results = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).activate_on_single_click(true).build();
            results.set_placeholder(Some(&gtk::Label::builder().label(tr!("No Results")).css_classes(["cal-empty"]).build()));
            let w = me.clone();
            results.connect_row_activated(move |_, row| {
                if let Some(t) = w.upgrade() {
                    t.result_activated(row);
                }
            });
            rbox.append(&gtk::ScrolledWindow::builder().child(&results).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());
            let results_rev = gtk::Revealer::builder().child(&rbox).reveal_child(false).transition_type(gtk::RevealerTransitionType::SlideLeft).transition_duration(sonata_ui::tokens::ms(250.0)).build();
            body.append(&results_rev);
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            col.append(&body);
            window.set_child(Some(&col));

            CalendarWindow {
                window,
                cfg: RefCell::new(cfg),
                first_weekday,
                h24,
                store: RefCell::new(store),
                hidden: RefCell::new(hidden),
                date: Cell::new(views::today()),
                view: RefCell::new(view.clone()),
                selected: RefCell::default(),
                cache: RefCell::default(),
                undo_stack: RefCell::default(),
                redo_stack: RefCell::default(),
                editor: RefCell::default(),
                alerts: RefCell::default(),
                add_btn,
                view_buttons,
                search,
                title,
                subtitle,
                sidebar_rev,
                cal_list,
                cal_rows: RefCell::default(),
                mini: RefCell::new(Some(mini)),
                stack,
                day: RefCell::new(Some(day)),
                week: RefCell::new(Some(week)),
                month: RefCell::new(Some(month)),
                year: RefCell::new(Some(year)),
                results_rev,
                results,
                result_targets: RefCell::default(),
                me: me.clone(),
            }
        });
        win.fill_calendars();
        let keys = gtk::EventControllerKey::new();
        let w = win.me.clone();
        keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(t) if t.key(key, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        win.window.add_controller(keys);
        let drop = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY);
        let w = win.me.clone();
        drop.connect_drop(move |_, value, _, _| {
            let (Some(t), Ok(list)) = (w.upgrade(), value.get::<gdk::FileList>()) else { return false };
            let files: Vec<gio::File> = list.files().into_iter().filter(|f| f.basename().is_some_and(|b| {
                let b = b.to_string_lossy().to_lowercase();
                b.ends_with(".ics") || b.ends_with(".ical") || b.ends_with(".ifb")
            })).collect();
            if files.is_empty() {
                return false;
            }
            t.import_files(files);
            true
        });
        win.window.add_controller(drop);
        let (w1, w2) = (win.me.clone(), win.me.clone());
        let alerts = crate::alerts::Alerts::new(
            move || w1.upgrade().map(|t| t.store.borrow().events.values().cloned().collect()).unwrap_or_default(),
            move |occ| {
                if let Some(t) = w2.upgrade() {
                    t.notify(occ);
                }
            },
        );
        alerts.reschedule();
        win.alerts.replace(Some(alerts));
        let w = win.me.clone();
        win.window.connect_close_request(move |_| {
            if let Some(t) = w.upgrade() {
                if let Some(a) = t.alerts.borrow().as_ref() {
                    a.stop();
                }
                t.store.borrow_mut().flush(); // the last writes reach the disk
                WINDOWS.with(|l| l.borrow_mut().retain(|x| !Rc::ptr_eq(x, &t)));
            }
            glib::Propagation::Proceed
        });
        let view = win.view.borrow().clone();
        win.set_view(&view);
        let w = win.me.clone();
        win.window.connect_map(move |_| {
            let w = w.clone();
            glib::idle_add_local_once(move || {
                if let Some(t) = w.upgrade() {
                    gtk::prelude::GtkWindowExt::set_focus(&t.window, None::<&gtk::Widget>);
                }
            });
        });
        win
    }

    // -- the views' interface -----------------------------------------------------------------
    pub fn date(&self) -> NaiveDate {
        self.date.get()
    }

    pub fn first_weekday(&self) -> u32 {
        self.first_weekday
    }

    pub fn view(&self) -> String {
        self.view.borrow().clone()
    }

    pub fn selected_key(&self) -> Option<(String, DT)> {
        self.selected.borrow().as_ref().map(Occurrence::key)
    }

    pub fn occurrences(&self, a: DT, b: DT) -> Rc<Vec<Occurrence>> {
        if let Some(c) = self.cache.borrow().get(&(a, b)) {
            return c.clone();
        }
        let hidden: Vec<String> = self.hidden.borrow().iter().cloned().collect();
        let occs = Rc::new(ics::expand(&self.store.borrow().visible_events(&hidden), a, b));
        let mut cache = self.cache.borrow_mut();
        if cache.len() > 16 {
            cache.clear();
        }
        cache.insert((a, b), occs.clone());
        occs
    }

    pub fn calendar_color(&self, occ: &Occurrence) -> String {
        self.store.borrow().calendar(&occ.event.calendar).map(|c| c.color.clone()).unwrap_or_else(|| "blue".into())
    }

    pub fn fmt_time(&self, t: NaiveTime) -> String {
        fmt_time(self.h24, t)
    }

    pub fn fmt_hour(&self, hour: u32) -> String {
        if self.h24 {
            return format!("{hour:02}:00");
        }
        if hour == 12 {
            return tr!("Noon");
        }
        format!("{} {}", if hour % 12 == 0 { 12 } else { hour % 12 }, if hour < 12 { "AM" } else { "PM" })
    }

    // -- data -------------------------------------------------------------------------------------
    pub fn default_calendar(&self) -> String {
        let store = self.store.borrow();
        let ids: Vec<String> = store.calendars.iter().map(|c| c.id.clone()).collect();
        let cfg_default = self.cfg.borrow().get("default_calendar").and_then(Value::as_str).unwrap_or("").to_string();
        if ids.contains(&cfg_default) {
            return cfg_default;
        }
        let hidden = self.hidden.borrow();
        ids.iter().find(|i| !hidden.contains(*i)).or(ids.first()).cloned().unwrap_or_default()
    }

    fn data_changed(&self) {
        self.cache.borrow_mut().clear();
        let sel = self.selected.borrow().clone();
        if let Some(o) = sel {
            let ev = self.store.borrow().events.get(&o.event.uid).cloned();
            self.selected.replace(ev.map(|e| Occurrence { event: e, start: o.start, end: o.end }));
        }
        self.refresh();
        if let Some(a) = self.alerts.borrow().as_ref() {
            a.reschedule();
        }
        if self.results_rev.reveals_child() {
            self.search_changed();
        }
    }

    /// Apply [(before, after)] event changes (None = absent), undoably.
    fn apply(&self, pairs: Vec<Change>, record: bool) {
        {
            let mut store = self.store.borrow_mut();
            for (before, after) in &pairs {
                match after {
                    None => {
                        if let Some(b) = before {
                            store.remove(&b.uid);
                        }
                    }
                    Some(a) => store.put(a.clone()),
                }
            }
        }
        if record {
            let mut u = self.undo_stack.borrow_mut();
            u.push(pairs);
            let n = u.len();
            if n > 100 {
                u.drain(..n - 100);
            }
            self.redo_stack.borrow_mut().clear();
        }
        self.data_changed();
    }

    pub fn undo(&self) {
        let pairs = self.undo_stack.borrow_mut().pop();
        if let Some(pairs) = pairs {
            self.apply(pairs.iter().rev().map(|(b, a)| (a.clone(), b.clone())).collect(), false);
            self.redo_stack.borrow_mut().push(pairs);
        }
    }

    pub fn redo(&self) {
        let pairs = self.redo_stack.borrow_mut().pop();
        if let Some(pairs) = pairs {
            self.apply(pairs.clone(), false);
            self.undo_stack.borrow_mut().push(pairs);
        }
    }

    fn event(&self, uid: &str) -> Option<Event> {
        self.store.borrow().events.get(uid).map(|e| (**e).clone())
    }

    // -- calendars ------------------------------------------------------------------------------
    fn fill_calendars(&self) {
        self.cal_list.remove_all();
        self.cal_rows.borrow_mut().clear();
        let default = self.default_calendar();
        let cals = self.store.borrow().calendars.clone();
        for cal in cals {
            let row = gtk::ListBoxRow::builder().can_focus(false).build();
            let hb = gtk::Box::new(gtk::Orientation::Horizontal, 4);
            let chk = gtk::CheckButton::builder().active(!self.hidden.borrow().contains(&cal.id)).css_classes(["cal-check", &format!("c-{}", cal.color)]).valign(gtk::Align::Center).can_focus(false).build();
            let (w, cid) = (self.me.clone(), cal.id.clone());
            chk.connect_toggled(move |c| {
                if let Some(t) = w.upgrade() {
                    t.set_visible_calendar(&cid, c.is_active());
                }
            });
            hb.append(&chk);
            let stack = gtk::Stack::builder().hexpand(true).build();
            stack.add_named(&gtk::Label::builder().label(&cal.name).xalign(0.0).ellipsize(pango::EllipsizeMode::End).build(), Some("label"));
            let entry = gtk::Entry::builder().text(&cal.name).build();
            let (w, cid) = (self.me.clone(), cal.id.clone());
            entry.connect_activate(move |e| {
                if let Some(t) = w.upgrade() {
                    t.rename_done(&cid, &e.text());
                }
            });
            let focus = gtk::EventControllerFocus::new();
            let (w, cid, e) = (self.me.clone(), cal.id.clone(), entry.downgrade());
            focus.connect_leave(move |_| {
                if let (Some(t), Some(e)) = (w.upgrade(), e.upgrade()) {
                    t.rename_done(&cid, &e.text());
                }
            });
            entry.add_controller(focus);
            stack.add_named(&entry, Some("entry"));
            hb.append(&stack);
            row.set_child(Some(&hb));
            self.cal_list.append(&row);
            if cal.id == default {
                self.cal_list.select_row(Some(&row));
            }
            self.cal_rows.borrow_mut().push(CalRow { row, cal_id: cal.id, stack, entry });
        }
    }

    fn set_visible_calendar(&self, cal_id: &str, visible: bool) {
        {
            let mut h = self.hidden.borrow_mut();
            if visible {
                h.remove(cal_id);
            } else {
                h.insert(cal_id.to_string());
            }
            let _ = sonata_config::update("calendar", &json!({"hidden": h.iter().collect::<Vec<_>>()}));
        }
        self.data_changed();
    }

    fn calendar_selected(&self, row: &gtk::ListBoxRow) {
        let id = self.cal_rows.borrow().iter().find(|r| r.row == *row).map(|r| r.cal_id.clone());
        if let Some(id) = id {
            if self.cfg.borrow().get("default_calendar").and_then(Value::as_str) != Some(id.as_str()) {
                self.cfg.borrow_mut().insert("default_calendar".into(), json!(id));
                let _ = sonata_config::update("calendar", &json!({"default_calendar": id}));
            }
        }
    }

    fn sidebar_menu(&self, widget: Option<&gtk::Widget>, x: f64, y: f64) {
        use sonata_ui::menu::Item;
        let Some(widget) = widget else { return };
        let row = widget.compute_point(&self.cal_list, &gtk::graphene::Point::new(x as f32, y as f32)).and_then(|p| self.cal_list.row_at_y(p.y() as i32));
        let cal_id = row.and_then(|r| self.cal_rows.borrow().iter().find(|c| c.row == r).map(|c| c.cal_id.clone()));
        let w = self.me.clone();
        let mut sections = vec![vec![Item::new(&tr!("New Calendar"), move || {
            if let Some(t) = w.upgrade() {
                t.new_calendar();
            }
        })]];
        if let Some(cid) = cal_id {
            let cal = self.store.borrow().calendar(&cid).cloned();
            let Some(cal) = cal else { return };
            let colors: Vec<Item> = model::PALETTE
                .iter()
                .map(|(color, name, _)| {
                    let (w, c, color) = (self.me.clone(), cid.clone(), color.to_string());
                    Item::check(&sonata_i18n::gettext(name), cal.color == color, move |_| {
                        if let Some(t) = w.upgrade() {
                            t.recolor_calendar(&c, &color);
                        }
                    })
                })
                .collect();
            let (w1, w2, c1, c2) = (self.me.clone(), self.me.clone(), cid.clone(), cid.clone());
            let several = self.store.borrow().calendars.len() > 1;
            let mut s = vec![
                vec![
                    Item::new(&tr!("Rename"), move || {
                        if let Some(t) = w1.upgrade() {
                            t.rename_calendar(&c1);
                        }
                    }),
                    Item::submenu(&tr!("Color"), vec![colors]),
                ],
                vec![Item::new(&tr!("Delete"), move || {
                    if let Some(t) = w2.upgrade() {
                        t.delete_calendar(&c2);
                    }
                })
                .enabled(several)],
            ];
            s.append(&mut sections);
            sections = s;
        }
        sonata_ui::menu::popup(widget, sections, sonata_ui::menu::Options::at(x, y));
    }

    fn new_calendar(&self) {
        let cal = self.store.borrow_mut().add_calendar(&tr!("Untitled"), None);
        self.fill_calendars();
        let w = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(t) = w.upgrade() {
                t.rename_calendar(&cal.id);
            }
        });
        self.data_changed();
    }

    fn rename_calendar(&self, cal_id: &str) {
        let name = self.store.borrow().calendar(cal_id).map(|c| c.name.clone()).unwrap_or_default();
        if let Some(r) = self.cal_rows.borrow().iter().find(|r| r.cal_id == cal_id) {
            r.entry.set_text(&name);
            r.stack.set_visible_child_name("entry");
            r.entry.grab_focus();
        }
    }

    fn rename_done(&self, cal_id: &str, name: &str) {
        let editing = self.cal_rows.borrow().iter().any(|r| r.cal_id == cal_id && r.stack.visible_child_name().as_deref() == Some("entry"));
        if !editing {
            return;
        }
        let name = name.trim();
        let current = self.store.borrow().calendar(cal_id).map(|c| c.name.clone());
        if !name.is_empty() && current.as_deref().is_some_and(|c| c != name) {
            self.store.borrow_mut().update_calendar(cal_id, Some(name), None);
        }
        let w = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(t) = w.upgrade() {
                t.fill_calendars();
            }
        });
    }

    fn recolor_calendar(&self, cal_id: &str, color: &str) {
        self.store.borrow_mut().update_calendar(cal_id, None, Some(color));
        self.fill_calendars();
        self.data_changed();
    }

    fn delete_calendar(&self, cal_id: &str) {
        let Some(cal) = self.store.borrow().calendar(cal_id).cloned() else { return };
        if self.store.borrow().calendars.len() < 2 {
            return;
        }
        let n = self.store.borrow().events.values().filter(|e| e.calendar == cal_id).count();
        let body = if n > 0 {
            fmt(&trn!("Its {n} event will be deleted too. This can't be undone.", "Its {n} events will be deleted too. This can't be undone.", n), &[("n", &n.to_string())])
        } else {
            tr!("This can't be undone.")
        };
        let (w, cid) = (self.me.clone(), cal_id.to_string());
        sonata_ui::dialog::alert(
            &fmt(&tr!("Are you sure you want to delete the calendar “{name}”?"), &[("name", &cal.name)]),
            &body,
            &[("cancel", tr!("Cancel"), "default"), ("delete", tr!("Delete"), "destructive")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                if rid != "delete" {
                    return;
                }
                t.store.borrow_mut().delete_calendar(&cid);
                t.undo_stack.borrow_mut().retain(|p| !p.iter().any(|(b, a)| b.as_ref().or(a.as_ref()).is_some_and(|e| e.calendar == cid)));
                t.redo_stack.borrow_mut().clear();
                t.fill_calendars();
                t.data_changed();
            },
            Some(self.window.upcast_ref()),
        );
    }

    // -- navigation -----------------------------------------------------------------------------
    pub fn set_view(&self, view: &str) {
        self.view.replace(view.to_string());
        if let Some((_, b)) = self.view_buttons.iter().find(|(v, _)| v == view) {
            if !b.is_active() {
                b.set_active(true);
            }
        }
        self.stack.set_visible_child_name(view);
        if self.cfg.borrow().get("view").and_then(Value::as_str) != Some(view) {
            self.cfg.borrow_mut().insert("view".into(), json!(view));
            let _ = sonata_config::update("calendar", &json!({"view": view}));
        }
        self.refresh();
    }

    pub fn go(&self, date: NaiveDate, view: Option<&str>) {
        self.date.set(date);
        match view {
            Some(v) if v != *self.view.borrow() => self.set_view(v),
            _ => self.refresh(),
        }
    }

    pub fn step(&self, n: i32) {
        let d = self.date.get();
        let view = self.view.borrow().clone();
        let next = match view.as_str() {
            "day" => d + Duration::days(n as i64),
            "week" => d + Duration::days(7 * n as i64),
            "month" => {
                let total = d.month0() as i32 + n;
                ics::date(d.year() + total.div_euclid(12), total.rem_euclid(12) as u32 + 1, 1)
            }
            _ => ics::date(d.year() + n, 1, 1),
        };
        self.go(next, None);
    }

    pub fn today(&self) {
        self.go(views::today(), None);
        let now = crate::alerts::now();
        if let Some(tl) = self.timeline() {
            tl.scroll_to_hour((now.hour() as f64 - 2.0).max(0.0), true);
        }
    }

    fn timeline(&self) -> Option<Rc<Timeline>> {
        match self.view.borrow().as_str() {
            "day" => self.day.borrow().clone(),
            "week" => self.week.borrow().clone(),
            _ => None,
        }
    }

    fn toggle_sidebar(&self) {
        let show = !self.sidebar_rev.reveals_child();
        self.sidebar_rev.set_reveal_child(show);
        let _ = sonata_config::update("calendar", &json!({"sidebar": show}));
    }

    pub fn refresh(&self) {
        let mut d = self.date.get();
        let size = sonata_ui::theme::values().get("text_title").and_then(|v| v.trim_end_matches("px").parse::<f64>().ok()).unwrap_or(15.0) * 1.75;
        let esc = |s: &str| glib::markup_escape_text(s).to_string();
        let view = self.view.borrow().clone();
        let title = match view.as_str() {
            "day" => {
                self.subtitle.set_label(&views::strftime(d, "%A"));
                fmt(&tr!("<b>{day} {month}</b> {year}"), &[("day", &d.day().to_string()), ("month", &esc(&views::strftime(d, "%B"))), ("year", &d.year().to_string())])
            }
            "year" => {
                self.subtitle.set_label("");
                format!("<b>{}</b>", d.year())
            }
            _ => {
                if view == "week" {
                    d = model::week_start(d, self.first_weekday) + Duration::days(3); // the month most of the week is in
                }
                self.subtitle.set_label("");
                fmt(&tr!("<b>{month}</b> {year}"), &[("month", &esc(&views::strftime(d, "%B"))), ("year", &d.year().to_string())])
            }
        };
        self.title.set_markup(&format!("<span size='{}'>{title}</span>", (size * pango::SCALE as f64) as i64));
        self.subtitle.set_visible(!self.subtitle.label().is_empty());
        match view.as_str() {
            "day" => self.day.borrow().as_ref().map(|t| t.refresh()),
            "week" => self.week.borrow().as_ref().map(|t| t.refresh()),
            "month" => self.month.borrow().as_ref().map(|m| m.refresh()),
            _ => self.year.borrow().as_ref().map(|y| y.refresh()),
        };
        if let Some(m) = self.mini.borrow().as_ref() {
            m.follow();
        }
    }

    // -- selection and editing ------------------------------------------------------------------
    pub fn select(&self, occ: Option<Occurrence>) {
        self.selected.replace(occ);
        if GtkWindowExt::focus(&self.window).is_some_and(|f| f.is::<gtk::Text>()) {
            gtk::prelude::GtkWindowExt::set_focus(&self.window, None::<&gtk::Widget>); // keys (Delete, arrows) now go to the calendar
        }
        self.redraw();
    }

    fn redraw(&self) {
        match self.view.borrow().as_str() {
            "day" | "week" => {
                if let Some(t) = self.timeline() {
                    t.head.area.queue_draw();
                    t.grid.area.queue_draw();
                }
            }
            "month" => {
                if let Some(m) = self.month.borrow().as_ref() {
                    m.canvas.area.queue_draw();
                }
            }
            _ => {
                if let Some(y) = self.year.borrow().as_ref() {
                    y.canvas.area.queue_draw();
                }
            }
        }
    }

    pub fn create(&self, start: DT, end: DT, all_day: bool, widget: Option<&gtk::Widget>, rect: Option<gdk::Rectangle>) {
        let cal = self.default_calendar();
        if cal.is_empty() {
            return;
        }
        if self.hidden.borrow().contains(&cal) {
            self.set_visible_calendar(&cal, true);
            self.fill_calendars();
        }
        let mut ev = Event::new(&ics::new_uid(), start, end);
        ev.summary = tr!("New Event");
        ev.all_day = all_day;
        ev.calendar = cal;
        let uid = ev.uid.clone();
        self.apply(vec![(None, Some(ev))], true);
        let Some(stored) = self.store.borrow().events.get(&uid).cloned() else { return };
        let occ = Occurrence { event: stored, start, end };
        self.select(Some(occ.clone()));
        if let Some(w) = widget {
            self.edit(&occ, w, rect, true);
        }
    }

    /// ⌘N / +: a one-hour event on the day shown, at the next hour (today)
    /// or 9 AM, edited from the + button.
    pub fn new_event(&self) {
        let now = crate::alerts::now();
        let start = if self.date.get() == now.date() {
            let hour = now.with_minute(0).and_then(|t| t.with_second(0)).and_then(|t| t.with_nanosecond(0)).unwrap();
            let next = hour + Duration::hours(1);
            if next.date() != now.date() { hour } else { next }
        } else {
            midnight(self.date.get()) + Duration::hours(9)
        };
        if *self.view.borrow() == "year" {
            self.set_view("day");
        }
        let btn = self.add_btn.clone();
        self.create(start, start + Duration::hours(1), false, Some(btn.upcast_ref()), None);
    }

    pub fn edit(&self, occ: &Occurrence, widget: &gtk::Widget, rect: Option<gdk::Rectangle>, new: bool) {
        if let Some(e) = self.editor.take() {
            e.pop.popdown();
        }
        if let Some(ed) = Editor::new(self, occ, widget, rect, new) {
            self.editor.replace(Some(ed));
        }
    }

    /// Drag result: the event (the whole series, for a repeating one) moves
    /// by dstart / its end by dend.
    pub fn move_occ(&self, occ: &Occurrence, dstart: Duration, dend: Duration) {
        let Some(ev) = self.event(&occ.event.uid) else { return };
        let mut new = ev.clone();
        new.start = ev.start + dstart;
        new.end = ev.end + dend;
        if new.end < new.start {
            new.end = new.start;
        }
        if !dstart.is_zero() {
            for d in &mut new.exdates {
                *d += dstart;
            }
            // changed instances move with their series
            for o in &mut new.overrides {
                o.recurrence_id = o.recurrence_id.map(|r| r + dstart);
                o.start += dstart;
                o.end += dstart;
            }
        }
        self.apply(vec![(Some(ev.clone()), Some(new))], true);
        let stored = self.store.borrow().events.get(&ev.uid).cloned();
        if let Some(e) = stored {
            self.select(Some(Occurrence { event: e, start: occ.start + dstart, end: occ.end + dend }));
        }
    }

    pub fn delete_selected(&self) {
        let Some(occ) = self.selected.borrow().clone() else { return };
        let Some(ev) = self.event(&occ.event.uid) else { return };
        if ev.freq.is_none() {
            self.apply(vec![(Some(ev), None)], true);
            self.select(None);
            return;
        }
        let w = self.me.clone();
        let summary = ev.summary.clone();
        sonata_ui::dialog::alert(
            &fmt(&tr!("You're deleting an event “{name}”."), &[("name", &summary)]),
            &tr!("Do you want to delete this and all future occurrences of this event, or only the selected occurrence?"),
            &[("cancel", tr!("Cancel"), ""), ("future", tr!("Delete All Future"), "destructive"), ("this", tr!("Delete Only This Event"), "default")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                match rid {
                    "this" => {
                        let mut new = ev.clone();
                        // a changed instance (also when the selection was rebound to the
                        // series): drop it and exclude its slot
                        let rec = occ.event.recurrence_id.or_else(|| ev.overrides.iter().find(|o| o.start == occ.start).and_then(|o| o.recurrence_id));
                        if let Some(r) = rec {
                            new.overrides.retain(|o| o.recurrence_id != Some(r));
                        }
                        new.exdates.push(rec.unwrap_or(occ.start));
                        t.apply(vec![(Some(ev.clone()), Some(new))], true);
                    }
                    "future" => {
                        let mut new = ev.clone();
                        new.until = Some(occ.start - Duration::seconds(1));
                        new.count = None;
                        let after = (occ.start > ev.start).then_some(new);
                        t.apply(vec![(Some(ev.clone()), after)], true);
                    }
                    "all" => t.apply(vec![(Some(ev.clone()), None)], true),
                    _ => {}
                }
                if rid != "cancel" {
                    t.select(None);
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    pub fn duplicate(&self, occ: &Occurrence) {
        let Some(mut new) = self.event(&occ.event.uid) else { return };
        new.uid = ics::new_uid();
        self.apply(vec![(None, Some(new))], true);
    }

    pub fn event_menu(&self, occ: &Occurrence, widget: &gtk::Widget, x: f64, y: f64) {
        use sonata_ui::menu::Item;
        let Some(ev) = self.event(&occ.event.uid) else { return };
        let cals: Vec<Item> = self
            .store
            .borrow()
            .calendars
            .iter()
            .map(|c| {
                let (w, o, cid) = (self.me.clone(), occ.clone(), c.id.clone());
                Item::check(&c.name, c.id == ev.calendar, move |_| {
                    if let Some(t) = w.upgrade() {
                        t.move_to_calendar(&o, &cid);
                    }
                })
            })
            .collect();
        let (w1, w2, w3) = (self.me.clone(), self.me.clone(), self.me.clone());
        let (o1, o2, wd) = (occ.clone(), occ.clone(), widget.downgrade());
        sonata_ui::menu::popup(
            widget,
            vec![
                vec![Item::new(&tr!("Get Info"), move || {
                    if let (Some(t), Some(wd)) = (w1.upgrade(), wd.upgrade()) {
                        t.edit(&o1, &wd, Some(gdk::Rectangle::new(x as i32, y as i32, 1, 1)), false);
                    }
                })],
                vec![
                    Item::new(&tr!("Duplicate"), move || {
                        if let Some(t) = w2.upgrade() {
                            t.duplicate(&o2);
                        }
                    }),
                    Item::new(&tr!("Delete"), move || {
                        if let Some(t) = w3.upgrade() {
                            t.delete_selected();
                        }
                    }),
                ],
                vec![Item::submenu(&tr!("Calendar"), vec![cals])],
            ],
            sonata_ui::menu::Options::at(x, y),
        );
    }

    fn move_to_calendar(&self, occ: &Occurrence, cal_id: &str) {
        let Some(ev) = self.event(&occ.event.uid) else { return };
        if ev.calendar != cal_id {
            let mut new = ev.clone();
            new.calendar = cal_id.to_string();
            self.apply(vec![(Some(ev), Some(new))], true);
        }
    }

    pub fn empty_menu(&self, widget: &gtk::Widget, x: f64, y: f64, start: DT, all_day: bool) {
        use sonata_ui::menu::Item;
        let end = start + if all_day { Duration::days(1) } else { Duration::hours(1) };
        let (w1, w2, wd) = (self.me.clone(), self.me.clone(), widget.downgrade());
        sonata_ui::menu::popup(
            widget,
            vec![
                vec![Item::new(&tr!("New Event"), move || {
                    if let (Some(t), Some(wd)) = (w1.upgrade(), wd.upgrade()) {
                        t.create(start, end, all_day, Some(&wd), Some(gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
                    }
                })],
                vec![Item::new(&tr!("Go to Today"), move || {
                    if let Some(t) = w2.upgrade() {
                        t.today();
                    }
                })],
            ],
            sonata_ui::menu::Options::at(x, y),
        );
    }

    // -- search ---------------------------------------------------------------------------------
    fn search_changed(&self) {
        let q = self.search.text().trim().to_lowercase();
        self.results_rev.set_reveal_child(!q.is_empty());
        self.results.remove_all();
        self.result_targets.borrow_mut().clear();
        if q.is_empty() {
            return;
        }
        let now = crate::alerts::now();
        let hidden: Vec<String> = self.hidden.borrow().iter().cloned().collect();
        let mut found: Vec<(DT, Rc<Event>)> = Vec::new();
        for ev in self.store.borrow().visible_events(&hidden) {
            if ev.summary.to_lowercase().contains(&q) || ev.location.to_lowercase().contains(&q) || ev.description.to_lowercase().contains(&q) {
                let mut when = ev.start;
                if ev.freq.is_some() {
                    when = ics::occurrences(&ev, now, now + Duration::days(800)).first().map(|o| o.start).unwrap_or(ev.start);
                }
                found.push((when, ev));
            }
        }
        found.sort_by_key(|x| x.0);
        for (when, ev) in found.into_iter().take(200) {
            let row = gtk::ListBoxRow::new();
            let hb = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            let color = self.store.borrow().calendar(&ev.calendar).map(|c| c.color.clone()).unwrap_or_else(|| "blue".into());
            hb.append(&gtk::Box::builder().css_classes(["cal-dot", &format!("c-{color}")]).valign(gtk::Align::Center).build());
            let vb = gtk::Box::new(gtk::Orientation::Vertical, 0);
            let title = if ev.summary.is_empty() { tr!("New Event") } else { ev.summary.clone() };
            vb.append(&gtk::Label::builder().label(title).xalign(0.0).ellipsize(pango::EllipsizeMode::End).build());
            let d = when.date();
            let mut date = format!("{} {} {}", views::strftime(d, "%a"), d.day(), views::strftime(d, "%b %Y"));
            if !ev.all_day {
                date.push_str("  ");
                date.push_str(&self.fmt_time(when.time()));
            }
            if ev.freq.is_some() {
                date.push_str("  ↻");
            }
            vb.append(&gtk::Label::builder().label(date).xalign(0.0).css_classes(["cal-res-date"]).build());
            hb.append(&vb);
            row.set_child(Some(&hb));
            self.results.append(&row);
            self.result_targets.borrow_mut().push((row, when, ev));
        }
    }

    fn result_activated(&self, row: &gtk::ListBoxRow) {
        let Some((when, ev)) = self.result_targets.borrow().iter().find(|r| r.0 == *row).map(|r| (r.1, r.2.clone())) else { return };
        let view = self.view.borrow().clone();
        self.go(when.date(), Some(if view == "year" || view == "month" { "day" } else { &view }));
        self.select(Some(Occurrence { event: ev.clone(), start: when, end: when + ev.duration() }));
        if !ev.all_day {
            if let Some(tl) = self.timeline() {
                tl.scroll_to_hour((when.hour() as f64 - 1.0).max(0.0), true);
            }
        }
    }

    fn close_search(&self) {
        self.search.set_text("");
        self.results_rev.set_reveal_child(false);
        gtk::prelude::GtkWindowExt::set_focus(&self.window, None::<&gtk::Widget>);
    }

    // -- import ---------------------------------------------------------------------------------
    pub fn import_files(&self, files: Vec<gio::File>) {
        for f in files {
            let w = self.me.clone();
            // read off the main loop
            glib::spawn_future_local(async move {
                let res = f.load_contents_future().await;
                let Some(t) = w.upgrade() else { return };
                let name = f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
                match res {
                    Ok((data, _)) => t.ask_import(&name, &String::from_utf8_lossy(&data)),
                    Err(e) => {
                        sonata_ui::dialog::alert(&fmt(&tr!("“{name}” couldn't be imported."), &[("name", &name)]), e.message(), &[("ok", tr!("OK"), "default")], |_| {}, Some(t.window.upcast_ref()));
                    }
                }
            });
        }
    }

    fn ask_import(&self, name: &str, text: &str) {
        let (info, events) = ics::parse(text, "");
        if events.is_empty() {
            sonata_ui::dialog::alert(&fmt(&tr!("“{name}” has no events."), &[("name", name)]), &tr!("There is nothing to import."), &[("ok", tr!("OK"), "default")], |_| {}, Some(self.window.upcast_ref()));
            return;
        }
        let target = self.store.borrow().calendar(&self.default_calendar()).cloned();
        let n = events.len();
        let first = events.iter().map(|e| e.start).min().unwrap();
        let mut responses = vec![("cancel", tr!("Cancel"), ""), ("new", tr!("New Calendar"), "")];
        if let Some(t) = &target {
            responses.push(("add", fmt(&tr!("Add to “{name}”"), &[("name", &t.name)]), "default"));
        }
        let (w, text, name_owned) = (self.me.clone(), text.to_string(), name.to_string());
        let target_id = target.map(|t| t.id);
        sonata_ui::dialog::alert(
            &fmt(&trn!("Import {n} event from “{name}”?", "Import {n} events from “{name}”?", n), &[("n", &n.to_string()), ("name", name)]),
            &tr!("Add them to a calendar you have or to a new calendar."),
            &responses,
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                let cal_id = match rid {
                    "new" => {
                        let stem = std::path::Path::new(&name_owned).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
                        let color = match (&info.color, &info.hex) {
                            (Some(c), _) if model::palette_ids().any(|p| p == c) => Some(c.clone()),
                            (_, Some(h)) if !h.is_empty() => Some(model::color_from_hex(h).to_string()),
                            _ => None,
                        };
                        let cal_name = info.name.clone().filter(|n| !n.is_empty()).unwrap_or(stem);
                        t.store.borrow_mut().add_calendar(&cal_name, color.as_deref()).id
                    }
                    "add" => match &target_id {
                        Some(id) => id.clone(),
                        None => return,
                    },
                    _ => return,
                };
                t.store.borrow_mut().import_text(&text, &cal_id);
                t.hidden.borrow_mut().remove(&cal_id);
                t.fill_calendars();
                t.go(first.date(), None);
                t.data_changed();
            },
            Some(self.window.upcast_ref()),
        );
    }

    // -- alerts ---------------------------------------------------------------------------------
    fn notify(&self, occ: &Occurrence) {
        let ev = &occ.event;
        let mut body = if ev.all_day { tr!("All day") } else { format!("{} – {}", self.fmt_time(occ.start.time()), self.fmt_time(occ.end.time())) };
        if !ev.location.is_empty() {
            body.push('\n');
            body.push_str(&ev.location);
        }
        let summary = if ev.summary.is_empty() { tr!("New Event") } else { ev.summary.clone() };
        crate::alerts::send_notification(&summary, &body, crate::app_id());
    }

    // -- keys -----------------------------------------------------------------------------------
    fn key(&self, keyval: gdk::Key, state: gdk::ModifierType) -> bool {
        use gdk::Key;
        let cmd = state.intersects(gdk::ModifierType::CONTROL_MASK | gdk::ModifierType::SUPER_MASK);
        let shift = state.contains(gdk::ModifierType::SHIFT_MASK);
        let k = keyval.to_lower();
        let focus = GtkWindowExt::focus(&self.window);
        let typing = focus.as_ref().is_some_and(|f| f.is::<gtk::Text>() || f.is::<gtk::TextView>());
        let act: Option<Box<dyn Fn(&Self)>> = if cmd {
            if k == Key::z && focus.as_ref().is_some_and(|f| f.is::<gtk::Text>()) {
                return false; // the search field's own undo
            }
            match k {
                Key::_1 | Key::KP_1 => Some(Box::new(|t| t.set_view("day"))),
                Key::_2 | Key::KP_2 => Some(Box::new(|t| t.set_view("week"))),
                Key::_3 | Key::KP_3 => Some(Box::new(|t| t.set_view("month"))),
                Key::_4 | Key::KP_4 => Some(Box::new(|t| t.set_view("year"))),
                Key::t => Some(Box::new(|t| t.today())),
                Key::n => Some(Box::new(|t| t.new_event())),
                Key::w => Some(Box::new(|t| t.window.close())),
                Key::f => Some(Box::new(|t| {
                    t.search.grab_focus();
                })),
                Key::y => Some(Box::new(|t| t.redo())),
                Key::z if shift => Some(Box::new(|t| t.redo())),
                Key::z => Some(Box::new(|t| t.undo())),
                Key::d => Some(Box::new(|t| {
                    let sel = t.selected.borrow().clone();
                    if let Some(o) = sel {
                        t.duplicate(&o);
                    }
                })),
                Key::Left => Some(Box::new(|t| t.step(-1))),
                Key::Right => Some(Box::new(|t| t.step(1))),
                _ => None,
            }
        } else {
            if typing {
                return false;
            }
            match keyval {
                Key::Left => Some(Box::new(|t| t.step(-1))),
                Key::Right => Some(Box::new(|t| t.step(1))),
                Key::Delete | Key::BackSpace | Key::KP_Delete => Some(Box::new(|t| t.delete_selected())),
                Key::Escape => Some(Box::new(|t| t.select(None))),
                _ => None,
            }
        };
        match act {
            Some(f) => {
                f(self);
                true
            }
            None => false,
        }
    }
}

/// One Calendar window: opening it again brings it forward; .ics files
/// given are imported into it, "sonata-date:2026-10-01" (the menu bar's
/// Calendar widget) shows that day.
pub fn open(app: &gtk::Application, args: &[String]) {
    let existing = WINDOWS.with(|w| w.borrow().first().cloned());
    let win = existing.unwrap_or_else(|| {
        let w = CalendarWindow::new(app, model::data_dir());
        WINDOWS.with(|l| l.borrow_mut().push(w.clone()));
        w
    });
    win.window.present();
    let mut files = Vec::new();
    for a in args {
        match a.strip_prefix("sonata-date:") {
            Some(d) => {
                if let Ok(date) = NaiveDate::parse_from_str(d, "%Y-%m-%d") {
                    win.go(date, Some("day"));
                }
            }
            None => files.push(gio::File::for_commandline_arg(a)),
        }
    }
    if !files.is_empty() {
        let w = Rc::downgrade(&win);
        glib::timeout_add_local_once(std::time::Duration::from_millis(200), move || {
            if let Some(t) = w.upgrade() {
                t.import_files(files);
            }
        });
    }
}

/// The event popover (macOS: title, location, all-day, starts / ends,
/// repeat, alert, calendar, notes). Changes apply when it closes, as one
/// undoable step; for a repeating event, the whole series follows the
/// change of the occurrence shown.
pub struct Editor {
    win: Weak<CalendarWindow>,
    occ: Occurrence,
    new: bool,
    master: Event,
    pub pop: gtk::Popover,
    title: gtk::Entry,
    location: gtk::Entry,
    all_day: gtk::Switch,
    s_date: DateButton,
    s_time: TimeEntry,
    e_date: DateButton,
    e_time: TimeEntry,
    repeat: gtk::DropDown,
    alert: gtk::DropDown,
    alert_vals: Vec<Option<i64>>,
    calendar: gtk::DropDown,
    cal_ids: Vec<String>,
    notes: gtk::TextView,
    dur: Cell<Duration>,
}

#[derive(Clone)]
struct DateButton {
    btn: gtk::MenuButton,
    label: gtk::Label,
    date: Rc<Cell<NaiveDate>>,
}

#[derive(Clone)]
struct TimeEntry {
    entry: gtk::Entry,
    time: Rc<Cell<NaiveTime>>,
    h24: bool,
}

fn date_label(d: NaiveDate) -> String {
    views::strftime(d, "%x")
}

impl Editor {
    fn new(win: &CalendarWindow, occ: &Occurrence, widget: &gtk::Widget, rect: Option<gdk::Rectangle>, new: bool) -> Option<Rc<Self>> {
        let master = win.event(&occ.event.uid)?;
        let ev = master.clone();
        let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["cal-editor"]).build();
        let title = gtk::Entry::builder().text(&ev.summary).placeholder_text(tr!("New Event")).css_classes(["cal-ed-title"]).build();
        let location = gtk::Entry::builder().text(&ev.location).placeholder_text(tr!("Add Location")).css_classes(["cal-ed-flat"]).build();
        bx.append(&title);
        bx.append(&location);
        bx.append(&sonata_ui::panel::separator());
        let grid = gtk::Grid::builder().row_spacing(6).column_spacing(8).margin_start(4).margin_end(4).build();
        let all_day = sonata_ui::controls::switch(ev.all_day);
        all_day.set_halign(gtk::Align::Start);
        let end_shown = if ev.all_day { occ.end - Duration::days(1) } else { occ.end };
        let s_date = DateButton::new(occ.start.date());
        let s_time = TimeEntry::new(win, occ.start.time());
        let e_date = DateButton::new(occ.start.date().max(end_shown.date()));
        let e_time = TimeEntry::new(win, occ.end.time());
        let rep_names: Vec<String> = REPEATS.iter().map(|r| sonata_i18n::gettext(r.1)).collect();
        let rep_sel = REPEATS.iter().position(|r| r.0 == ev.freq.as_deref()).unwrap_or(0) as u32;
        let repeat = sonata_ui::controls::popup_button(&rep_names, rep_sel, |_| {});
        let mut alert_vals: Vec<Option<i64>> = ALERTS.iter().map(|a| a.0).collect();
        let mut alert_names: Vec<String> = ALERTS.iter().map(|a| sonata_i18n::gettext(a.1)).collect();
        if !alert_vals.contains(&ev.alarm) {
            let n = ev.alarm.unwrap_or(0);
            alert_vals.push(ev.alarm);
            alert_names.push(fmt(&trn!("{n} minute before", "{n} minutes before", n), &[("n", &n.to_string())]));
        }
        let alert = sonata_ui::controls::popup_button(&alert_names, alert_vals.iter().position(|a| *a == ev.alarm).unwrap_or(0) as u32, |_| {});
        let cals = win.store.borrow().calendars.clone();
        let cal_ids: Vec<String> = cals.iter().map(|c| c.id.clone()).collect();
        let calendar = sonata_ui::controls::popup_button(&cals.iter().map(|c| c.name.clone()).collect::<Vec<_>>(), cal_ids.iter().position(|c| *c == ev.calendar).unwrap_or(0) as u32, |_| {});
        let pair = |a: &gtk::Widget, b: &gtk::Widget| {
            let hb = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            hb.append(a);
            hb.append(b);
            hb.upcast::<gtk::Widget>()
        };
        let rows: [(String, gtk::Widget); 6] = [
            (tr!("all-day:"), all_day.clone().upcast()),
            (tr!("starts:"), pair(s_date.btn.upcast_ref(), s_time.entry.upcast_ref())),
            (tr!("ends:"), pair(e_date.btn.upcast_ref(), e_time.entry.upcast_ref())),
            (tr!("repeat:"), repeat.clone().upcast()),
            (tr!("alert:"), alert.clone().upcast()),
            (tr!("calendar:"), calendar.clone().upcast()),
        ];
        for (i, (key, w)) in rows.iter().enumerate() {
            grid.attach(&gtk::Label::builder().label(key).xalign(1.0).css_classes(["cal-ed-key"]).build(), 0, i as i32, 1, 1);
            w.set_halign(gtk::Align::Start);
            grid.attach(w, 1, i as i32, 1, 1);
        }
        bx.append(&grid);
        bx.append(&sonata_ui::panel::separator());
        let notes = gtk::TextView::builder().css_classes(["cal-ed-notes"]).wrap_mode(gtk::WrapMode::WordChar).left_margin(4).right_margin(4).top_margin(2).bottom_margin(2).build();
        notes.buffer().set_text(&ev.description);
        let over = gtk::Overlay::builder().child(&notes).build();
        let placeholder = gtk::Label::builder().label(tr!("Add Notes")).xalign(0.0).valign(gtk::Align::Start).can_target(false).css_classes(["cal-ed-placeholder"]).margin_start(4).margin_top(2).visible(ev.description.is_empty()).build();
        over.add_overlay(&placeholder);
        notes.buffer().connect_changed(move |b| placeholder.set_visible(b.char_count() == 0));
        let scroll = gtk::ScrolledWindow::builder().child(&over).hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).max_content_height(120).build();
        scroll.set_size_request(-1, 48);
        bx.append(&scroll);
        // point at the part of the event on screen
        let (mut widget, mut rect) = (widget.clone(), rect);
        if let Some(sw) = widget.ancestor(gtk::ScrolledWindow::static_type()).and_downcast::<gtk::ScrolledWindow>() {
            let adj = sw.vadjustment();
            let (top, bottom) = (adj.value(), adj.value() + adj.page_size());
            match rect {
                Some(r) if (r.y() + r.height()) as f64 >= top && r.y() as f64 <= bottom => {
                    let y0 = r.y().max(top as i32);
                    let y1 = (r.y() + r.height()).min(bottom as i32);
                    rect = Some(gdk::Rectangle::new(r.x(), y0, r.width(), (y1 - y0).max(1)));
                }
                _ => {
                    // scrolled away: hang from the + button
                    widget = win.add_btn.clone().upcast();
                    rect = None;
                }
            }
        }
        // the close dot of forms (panel.popup(..., close=True))
        let position = if rect.is_some() { gtk::PositionType::Right } else { gtk::PositionType::Bottom };
        let pop = sonata_ui::panel::popup_with(&widget, &bx, sonata_ui::panel::Options { position, close: true, ..Default::default() });
        if let Some(r) = rect {
            pop.set_pointing_to(Some(&r));
        }
        let ed = Rc::new(Editor { win: win.me.clone(), occ: occ.clone(), new, master, pop, title, location, all_day, s_date, s_time, e_date, e_time, repeat, alert, alert_vals, calendar, cal_ids, notes, dur: Cell::new(Duration::zero()) });
        Self::wire(&ed);
        ed.set_all_day(ed.master.all_day, true);
        ed.title.grab_focus();
        if new {
            ed.title.select_region(0, -1);
        }
        Some(ed)
    }

    fn wire(ed: &Rc<Self>) {
        for e in [&ed.title, &ed.location] {
            let p = ed.pop.downgrade();
            e.connect_activate(move |_| {
                if let Some(p) = p.upgrade() {
                    p.popdown();
                }
            });
        }
        let w = Rc::downgrade(ed);
        ed.all_day.connect_active_notify(move |s| {
            if let Some(e) = w.upgrade() {
                e.set_all_day(s.is_active(), false);
            }
        });
        for (db, start) in [(&ed.s_date, true), (&ed.e_date, false)] {
            let w = Rc::downgrade(ed);
            db.on_pick(move || {
                if let Some(e) = w.upgrade() {
                    e.dates_changed(start);
                }
            });
        }
        for (te, start) in [(&ed.s_time, true), (&ed.e_time, false)] {
            let w = Rc::downgrade(ed);
            te.on_done(move || {
                if let Some(e) = w.upgrade() {
                    e.dates_changed(start);
                }
            });
        }
        let w = Rc::downgrade(ed);
        ed.pop.connect_closed(move |_| {
            if let Some(e) = w.upgrade() {
                e.commit();
            }
        });
    }

    fn range(&self) -> (DT, DT) {
        let (s, e) = if self.all_day.is_active() {
            (midnight(self.s_date.date.get()), midnight(self.e_date.date.get()) + Duration::days(1))
        } else {
            (self.s_date.date.get().and_time(self.s_time.time.get()), self.e_date.date.get().and_time(self.e_time.time.get()))
        };
        (s, e.max(s))
    }

    /// Changing the start keeps the duration (macOS); the end can't go
    /// before the start.
    fn dates_changed(&self, from_start: bool) {
        let (s, e) = self.range();
        if from_start {
            let ne = s + self.dur.get();
            self.e_date.set(if self.all_day.is_active() { (ne - Duration::days(1)).date() } else { ne.date() });
            self.e_time.set(ne.time());
        } else if e < s {
            self.e_date.set(s.date());
            self.e_time.set(s.time());
        }
        let (s, e) = self.range();
        self.dur.set(e - s);
    }

    fn set_all_day(&self, on: bool, init: bool) {
        self.s_time.entry.set_visible(!on);
        self.e_time.entry.set_visible(!on);
        let midnight_t = NaiveTime::MIN;
        if !init && !on && self.s_time.time.get() == midnight_t && self.e_time.time.get() == midnight_t {
            self.s_time.set(NaiveTime::from_hms_opt(9, 0, 0).unwrap());
            self.e_time.set(NaiveTime::from_hms_opt(10, 0, 0).unwrap());
            self.e_date.set(self.s_date.date.get());
        }
        let (s, e) = self.range();
        self.dur.set(e - s);
    }

    fn commit(&self) {
        let Some(win) = self.win.upgrade() else { return };
        if win.editor.borrow().as_ref().is_some_and(|e| std::ptr::eq(e.as_ref(), self)) {
            win.editor.replace(None);
        }
        for te in [&self.s_time, &self.e_time] {
            // a time typed without Enter
            if let Some(v) = parse_time(&te.entry.text()) {
                te.time.set(v);
            }
        }
        let Some(cur) = win.event(&self.master.uid) else { return };
        let mut new = cur.clone();
        let t = self.title.text().trim().to_string();
        new.summary = if t.is_empty() { tr!("New Event") } else { t };
        new.location = self.location.text().trim().to_string();
        let buf = self.notes.buffer();
        new.description = buf.text(&buf.start_iter(), &buf.end_iter(), false).trim().to_string();
        new.all_day = self.all_day.is_active();
        let (s, e) = self.range();
        new.start = cur.start + (s - self.occ.start);
        new.end = new.start + (e - s);
        if !new.exdates.is_empty() && s != self.occ.start {
            let shift = s - self.occ.start;
            for d in &mut new.exdates {
                *d += shift;
            }
        }
        let freq = REPEATS.get(self.repeat.selected() as usize).and_then(|r| r.0).map(str::to_string);
        if freq != cur.freq {
            new.interval = 1;
            new.until = None;
            new.count = None;
            if freq.is_none() {
                new.exdates.clear();
            }
            new.freq = freq;
        }
        new.alarm = self.alert_vals.get(self.alert.selected() as usize).copied().flatten();
        if let Some(c) = self.cal_ids.get(self.calendar.selected() as usize) {
            new.calendar = c.clone();
        }
        if new != cur {
            let first_edit = self.new && win.undo_stack.borrow().last().is_some_and(|l| l.len() == 1 && l[0].0.is_none() && l[0].1.as_ref() == Some(&self.master));
            if first_edit {
                // creating + first edit: one undo step
                win.undo_stack.borrow_mut().pop();
                win.apply(vec![(None, Some(new.clone()))], true);
            } else {
                win.apply(vec![(Some(cur), Some(new.clone()))], true);
            }
            let stored = win.store.borrow().events.get(&new.uid).cloned();
            if let Some(ev) = stored {
                win.select(Some(Occurrence { event: ev, start: s, end: e }));
            }
        }
    }
}

impl DateButton {
    fn new(d: NaiveDate) -> Self {
        let btn = gtk::MenuButton::builder().css_classes(["cal-ed-date"]).build();
        let label = gtk::Label::new(Some(&date_label(d)));
        btn.set_child(Some(&label)); // a plain field, no arrow
        Self { btn, label, date: Rc::new(Cell::new(d)) }
    }

    fn set(&self, d: NaiveDate) {
        self.date.set(d);
        self.label.set_label(&date_label(d));
    }

    fn on_pick(&self, picked: impl Fn() + 'static) {
        let d = self.date.get();
        let cal = gtk::Calendar::new();
        if let Ok(when) = glib::DateTime::from_local(d.year(), d.month() as i32, d.day() as i32, 0, 0, 0.0) {
            #[allow(deprecated)]
            cal.select_day(&when);
        }
        let pop = gtk::Popover::builder().child(&cal).css_classes(["sonata-panel"]).has_arrow(false).build();
        let (me, p) = (self.clone(), pop.downgrade());
        cal.connect_day_selected(move |c| {
            let g = c.date();
            me.set(ics::date(g.year(), g.month() as u32, g.day_of_month() as u32));
            if let Some(p) = p.upgrade() {
                p.popdown();
            }
            picked();
        });
        self.btn.set_popover(Some(&pop));
    }
}

impl TimeEntry {
    fn new(win: &CalendarWindow, t: NaiveTime) -> Self {
        let entry = gtk::Entry::builder().text(win.fmt_time(t)).width_chars(8).max_width_chars(8).css_classes(["cal-ed-time"]).build();
        Self { entry, time: Rc::new(Cell::new(t)), h24: win.h24 }
    }

    fn set(&self, t: NaiveTime) {
        self.time.set(t);
        self.entry.set_text(&fmt_time(self.h24, t));
    }

    /// Enter or leaving the field: the time read (kept when unreadable)
    /// and shown again in the usual format.
    fn on_done(&self, changed: impl Fn() + 'static) {
        let me = self.clone();
        let done = Rc::new(move || {
            let t = parse_time(&me.entry.text()).unwrap_or(me.time.get());
            me.set(t);
            changed();
        });
        let d = done.clone();
        self.entry.connect_activate(move |_| d());
        let focus = gtk::EventControllerFocus::new();
        focus.connect_leave(move |_| done());
        self.entry.add_controller(focus);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The event editor (a form) opened without the close dot every form
    /// panel has (tests/test_dialog_close.py's test_forms_ask_for_it).
    #[test]
    fn test_event_editor_had_no_close_dot() {
        let src = include_str!("window.rs");
        let i = src.find("panel::popup_with(&widget, &bx").expect("the event editor");
        assert!(src[i..i + 160].contains("close: true"));
    }

    /// Calendar opened at 1100 x 720 the first time on any display: the
    /// Python one opens roomy, about two thirds of it.
    #[test]
    fn test_first_size_not_roomy() {
        use sonata_ui::window::first_size;
        assert_eq!(first_size(None, 1100, 720, true, Some((1920, 1080))), (1267, 720, false));
        assert_eq!(first_size(None, 1100, 720, true, Some((2560, 1440))), (1689, 936, false));
    }

    #[test]
    fn times_typed_in_any_usual_way() {
        let t = |h, m| NaiveTime::from_hms_opt(h, m, 0);
        assert_eq!(parse_time("9"), t(9, 0));
        assert_eq!(parse_time("9:30"), t(9, 30));
        assert_eq!(parse_time("0930"), t(9, 30));
        assert_eq!(parse_time("9.30 pm"), t(21, 30));
        assert_eq!(parse_time("21:30"), t(21, 30));
        assert_eq!(parse_time("12 am"), t(0, 0));
        assert_eq!(parse_time("12p"), t(12, 0));
        assert_eq!(parse_time("13 pm"), None);
        assert_eq!(parse_time("24:00"), None);
        assert_eq!(parse_time("9:60"), None);
        assert_eq!(parse_time("noon"), None);
    }
}
