//! Notification Center (click the clock): a full-screen transparent layer
//! with the column on the right -- the Today widgets fixed at the top, the
//! notifications, newest first, scrolling under them. A click anywhere
//! else (or Escape) closes it.
//!
//! Not here yet: its glass (shell/frost.py, the display's picture blurred
//! under the panels) -- the panels are solid (`.no-frost`), as the Python
//! one's are without a picture.

use crate::card;
use crate::model::{self, Note, BANNER_W, SHOWN};
use crate::server::Server;
use crate::today;
use chrono::{NaiveDate, NaiveDateTime};
use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use sonata_i18n::tr;
use sonata_ui::fixed::FixedWidth;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::time::Duration;

struct Slot {
    rev: gtk::Revealer,
    when: gtk::Label,
    /// the note its card was made for
    made: Note,
}

/// What the column was last built for: the list, the day, Calendar's
/// files, and whether Up Next's first event has ended since.
#[derive(Clone, PartialEq)]
struct StateKey {
    notes: Vec<(u32, u64)>,
    day: String,
    calendar: i128,
    ended: bool,
}

pub struct Center {
    pub win: gtk::Window,
    server: Weak<Server>,
    slide: gtk::Box,
    top: gtk::Box,
    col: gtk::Box,
    head: gtk::Box,
    title: gtk::Label,
    clear: gtk::Button,
    slots: RefCell<HashMap<u32, Slot>>,
    /// slots sliding out, removed when they've gone
    leaving: RefCell<Vec<gtk::Revealer>>,
    /// the Today widgets
    cal: RefCell<Option<gtk::Widget>>,
    cal_key: RefCell<Option<(String, i128)>>,
    /// when Up Next's first event ends (it then drops out)
    cal_until: Cell<Option<NaiveDateTime>>,
    built_for: RefCell<Option<StateKey>>,
    settle: RefCell<Option<glib::SourceId>>,
    /// a rebuild while closed, coalesced
    later: RefCell<Option<glib::SourceId>>,
    hiding: RefCell<Option<glib::SourceId>>,
    monitor: RefCell<Option<gtk::gdk::Monitor>>,
}

fn day_now() -> String {
    glib::DateTime::now_local().ok().and_then(|d| d.format("%Y-%m-%d").ok()).map(|s| s.to_string()).unwrap_or_default()
}

impl Center {
    pub fn new(app: &gtk::Application, server: &Rc<Server>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Notification Center").decorated(false).build();
        win.add_css_class("sonata-nc");
        win.add_css_class("no-frost");
        let gap = model::menubar_height() + 6; // under the menu bar
        // the Today widgets stay at the top, the notifications scroll under
        // them (Vini: the calendar fixed, however many notifications)
        let top = gtk::Box::builder().orientation(gtk::Orientation::Vertical).margin_top(gap - 12).margin_end(10).build();
        let scroller = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_width(true).vexpand(true).build();
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).margin_top(4).margin_end(10).margin_bottom(12).margin_start(6).build();
        scroller.set_child(Some(&col));
        let outer = gtk::Box::new(gtk::Orientation::Vertical, 0);
        outer.append(&top);
        outer.append(&scroller);
        // moved and faded, never laid out again each frame (a Revealer
        // re-measured every card and the calendar per frame -- Vini: the
        // opening lagged)
        let slide = gtk::Box::builder().css_classes(["nc-slide"]).halign(gtk::Align::End).valign(gtk::Align::Fill).build();
        slide.append(&outer);
        slide.set_can_target(false);
        win.set_child(Some(&slide));
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let title = gtk::Label::builder().xalign(0.0).hexpand(true).css_classes(["nc-head"]).build();
        head.append(&title);
        let clear = gtk::Button::builder().label(tr!("Clear All")).css_classes(["nc-clear"]).valign(gtk::Align::Center).build();
        head.append(&clear);
        col.append(&head);
        if sonata_shell::layer::available() {
            win.init_layer_shell();
            win.set_namespace(Some("sonata2-notification-center"));
            win.set_layer(Layer::Overlay);
            for e in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
                win.set_anchor(e, true);
            }
            win.set_exclusive_zone(-1);
            win.set_keyboard_mode(KeyboardMode::OnDemand);
        }
        let c = Rc::new(Center {
            win,
            server: Rc::downgrade(server),
            slide,
            top,
            col,
            head,
            title,
            clear,
            slots: RefCell::default(),
            leaving: RefCell::default(),
            cal: RefCell::default(),
            cal_key: RefCell::default(),
            cal_until: Cell::new(None),
            built_for: RefCell::default(),
            settle: RefCell::default(),
            later: RefCell::default(),
            hiding: RefCell::default(),
            monitor: RefCell::default(),
        });
        let w = Rc::downgrade(server);
        c.clear.connect_clicked(move |_| {
            if let Some(s) = w.upgrade() {
                s.clear();
            }
        });
        let outside = gtk::GestureClick::new();
        let w = Rc::downgrade(&c);
        outside.connect_released(move |_, _, x, y| {
            if let Some(c) = w.upgrade() {
                c.clicked(x, y);
            }
        });
        c.win.add_controller(outside);
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(&c);
        keys.connect_key_pressed(move |_, key, _, _| {
            if key == gtk::gdk::Key::Escape {
                if let Some(c) = w.upgrade() {
                    c.hide_center();
                }
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        c.win.add_controller(keys);
        let w = Rc::downgrade(&c);
        server.connect_changed(move || {
            if let Some(c) = w.upgrade() {
                c.changed();
            }
        });
        c
    }

    pub fn is_open(&self) -> bool {
        self.win.is_visible()
    }

    fn set_revealed(&self, on: bool) {
        if on {
            self.slide.add_css_class("shown");
        } else {
            self.slide.remove_css_class("shown");
        }
        self.slide.set_can_target(on);
    }

    fn clicked(self: &Rc<Self>, x: f64, y: f64) {
        // only the cards and widgets keep it open: the empty parts of the
        // column (it spans the full height, over the menu bar's clock and
        // icons too) close it like any other click outside
        let col: gtk::Widget = self.col.clone().upcast();
        let top: gtk::Widget = self.top.clone().upcast();
        let mut w = self.win.pick(x, y, gtk::PickFlags::DEFAULT);
        while let Some(x) = w.clone() {
            let p = x.parent();
            if p.as_ref() == Some(&col) || p.as_ref() == Some(&top) {
                break;
            }
            w = p;
        }
        if w.is_none() {
            self.hide_center();
        }
    }

    /// Open it -- on `monitor`, the display of the menu bar whose clock was
    /// clicked (Vini: every display's clock opens the same one).
    pub fn show_center(self: &Rc<Self>, monitor: Option<&gtk::gdk::Monitor>) {
        if let Some(src) = self.hiding.take() {
            src.remove(); // opened again while sliding out
        }
        if let Some(m) = monitor {
            if self.monitor.borrow().as_ref() != Some(m) {
                sonata_shell::layer::set_monitor(&self.win, m); // (closed, so it can move to that display)
                self.monitor.replace(Some(m.clone()));
            }
        }
        // already built (at start, and on every change while closed): only what
        // changed since is brought up to date -- opening is then instant
        if self.built_for.borrow().as_ref() != Some(&self.state_key()) {
            self.rebuild();
        }
        self.refresh_times();
        self.win.present();
        let w = Rc::downgrade(self);
        glib::idle_add_local_once(move || {
            if let Some(c) = w.upgrade() {
                c.set_revealed(true);
            }
        });
    }

    pub fn hide_center(self: &Rc<Self>) {
        self.set_revealed(false);
        if self.hiding.borrow().is_some() {
            return;
        }
        let w = Rc::downgrade(self);
        let src = glib::timeout_add_local_once(Duration::from_millis(260), move || {
            if let Some(c) = w.upgrade() {
                c.hiding.take();
                c.win.set_visible(false);
            }
        });
        self.hiding.replace(Some(src));
    }

    /// Built and laid out once, unseen, so its first opening slides in
    /// without a hitch.
    pub fn prewarm(self: &Rc<Self>) {
        self.rebuild();
    }

    fn notes(&self) -> Vec<Note> {
        self.server.upgrade().map(|s| s.notes()).unwrap_or_default()
    }

    /// Closed notes leave with an animation (staggered for Clear All);
    /// anything else updates the column at once.
    fn changed(self: &Rc<Self>) {
        if !self.is_open() {
            // closed: one update after a burst, not one per notification
            // (opening still finds it built; show_center catches anything newer)
            if self.later.borrow().is_none() {
                let w = Rc::downgrade(self);
                let src = glib::timeout_add_local_once(Duration::from_millis(1000), move || {
                    if let Some(c) = w.upgrade() {
                        c.later.take();
                        if !c.is_open() && c.built_for.borrow().as_ref() != Some(&c.state_key()) {
                            c.rebuild();
                        }
                    }
                });
                self.later.replace(Some(src));
            }
            return;
        }
        let notes = self.notes();
        let ids: std::collections::HashSet<u32> = notes.iter().map(|n| n.id).collect();
        let gone: Vec<u32> = self.slots.borrow().keys().filter(|id| !ids.contains(id)).copied().collect();
        let new = notes.iter().rev().take(SHOWN).any(|n| !self.slots.borrow().contains_key(&n.id));
        if new || gone.is_empty() {
            self.rebuild();
            return;
        }
        for (i, id) in gone.iter().enumerate() {
            let Some(slot) = self.slots.borrow_mut().remove(id) else { continue };
            let rev = slot.rev;
            self.leaving.borrow_mut().push(rev.clone());
            glib::timeout_add_local_once(Duration::from_millis(1 + i.min(8) as u64 * 45), move || {
                rev.add_css_class("leaving");
                glib::timeout_add_local_once(Duration::from_millis(120), move || rev.set_reveal_child(false));
            });
        }
        if let Some(src) = self.settle.take() {
            src.remove();
        }
        let wait = 120 + 260 + (gone.len() - 1).min(8) as u64 * 45 + 20;
        let w = Rc::downgrade(self);
        let src = glib::timeout_add_local_once(Duration::from_millis(wait), move || {
            if let Some(c) = w.upgrade() {
                c.settle.take();
                c.rebuild(); // header ("No Notifications"), Clear All
            }
        });
        self.settle.replace(Some(src));
    }

    fn state_key(&self) -> StateKey {
        let ended = self.cal_until.get().is_some_and(|u| today::now() >= u);
        StateKey { notes: self.notes().iter().map(|n| (n.id, n.at.to_bits())).collect(), day: day_now(), calendar: today::stamp(&today::folder()), ended }
    }

    /// The column brought up to the list: a new note gets its card, a
    /// closed one loses its own, the others stay as they are -- built and
    /// laid out already. (Every notification used to rebuild all 40 cards
    /// and the calendar, so the next opening laid them all out again
    /// before its first frame: Vini, it opened laggy whenever there were
    /// notifications.)
    fn rebuild(self: &Rc<Self>) {
        self.built_for.replace(Some(self.state_key()));
        let Some(server) = self.server.upgrade() else { return };
        let notes: Vec<Note> = server.notes().into_iter().rev().take(SHOWN).collect();
        self.title.set_label(&if notes.is_empty() { tr!("No Notifications") } else { tr!("Notifications") });
        self.clear.set_visible(!notes.is_empty());
        for rev in self.leaving.take() {
            // (done sliding out)
            if rev.parent().as_ref() == Some(self.col.upcast_ref()) {
                self.col.remove(&rev);
            }
        }
        let wanted: std::collections::HashSet<u32> = notes.iter().map(|n| n.id).collect();
        let mut slots = self.slots.borrow_mut();
        slots.retain(|id, slot| {
            let keep = wanted.contains(id);
            if !keep {
                self.col.remove(&slot.rev);
            }
            keep
        });
        let mut prev: gtk::Widget = self.head.clone().upcast();
        for n in &notes {
            if slots.get(&n.id).is_some_and(|s| s.made != *n) {
                // replaced in place (same id, new text)
                if let Some(old) = slots.remove(&n.id) {
                    self.col.remove(&old.rev);
                }
            }
            let rev = match slots.get(&n.id) {
                Some(slot) => {
                    if slot.rev.prev_sibling().as_ref() != Some(&prev) {
                        self.col.reorder_child_after(&slot.rev, Some(&prev));
                    }
                    slot.rev.clone()
                }
                None => {
                    let c = card::card(n, &server);
                    let rev = gtk::Revealer::builder()
                        .child(&c.root)
                        .reveal_child(true)
                        .css_classes(["nc-slot"])
                        .transition_type(gtk::RevealerTransitionType::SlideUp)
                        .transition_duration(260)
                        .build();
                    self.col.insert_child_after(&rev, Some(&prev));
                    slots.insert(n.id, Slot { rev: rev.clone(), when: c.when, made: n.clone() });
                    rev
                }
            };
            prev = rev.upcast();
        }
        drop(slots);
        self.calendar();
    }

    /// Each card's "5m ago" as of this opening (only the ones that changed
    /// are set: a label set anew is laid out again).
    fn refresh_times(&self) {
        let now = model::now();
        for slot in self.slots.borrow().values() {
            let text = model::ago(slot.made.at, now);
            if slot.when.label() != text {
                slot.when.set_label(&text);
            }
        }
    }

    /// Today widgets (fixed above the notifications: Vini), made again only
    /// on a new day, after Calendar's files changed, or when Up Next's
    /// first event has ended -- reading the calendar files was part of
    /// every notification's rebuild.
    fn calendar(self: &Rc<Self>) {
        let folder = today::folder();
        let key = (day_now(), today::stamp(&folder));
        let now = today::now();
        if self.cal.borrow().is_some() && self.cal_key.borrow().as_ref() == Some(&key) && self.cal_until.get().is_none_or(|u| now < u) {
            return;
        }
        let events = today::upcoming(&folder, now, 31);
        let cal = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["nc-widget"]).margin_start(6).margin_top(8).build();
        let w = Rc::downgrade(self);
        let on_day: Rc<dyn Fn(NaiveDate)> = Rc::new(move |d| {
            if let Some(c) = w.upgrade() {
                c.open_calendar(Some(d));
            }
        });
        cal.append(&today::month(&events, Some(on_day)));
        cal.append(&today::up_next(&events, now));
        // macOS: clicking the Calendar widget opens Calendar
        let click = gtk::GestureClick::new();
        let w = Rc::downgrade(self);
        click.connect_released(move |_, _, _, _| {
            if let Some(c) = w.upgrade() {
                c.open_calendar(None);
            }
        });
        cal.add_controller(click);
        if let Some(old) = self.cal.take() {
            self.top.remove(&old);
        }
        // its own width, never stretched: the cards' column (margins and all)
        // made the window 6 px wider than it, and it grew with them -- Vini:
        // clearing the notifications, the calendar shrank a little
        let fixed = FixedWidth::new(&cal, BANNER_W + 6); // (its margin_start); an event title never widens it
        fixed.set_halign(gtk::Align::Start);
        self.top.append(&fixed);
        self.cal.replace(Some(fixed.upcast()));
        self.cal_key.replace(Some(key));
        self.cal_until.set(today::until(&events, now));
    }

    /// Calendar, on `date` in the Day view, or as it was.
    fn open_calendar(self: &Rc<Self>, date: Option<NaiveDate>) {
        self.hide_center();
        let Ok(exe) = std::env::current_exe() else { return };
        let mut argv: Vec<std::ffi::OsString> = vec![exe.into(), "calendar".into()];
        if let Some(d) = date {
            argv.push(format!("sonata-date:{}", d.format("%Y-%m-%d")).into());
        }
        let argv: Vec<&std::ffi::OsStr> = argv.iter().map(|a| a.as_os_str()).collect();
        if let Err(e) = gio::Subprocess::newv(&argv, gio::SubprocessFlags::NONE) {
            eprintln!("sonata2-rs notifications: Calendar: {e}");
        }
    }
}
