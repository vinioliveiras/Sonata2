//! Clock (macOS Ventura Clock): Alarms, Stopwatch, Timers, ported from
//! `sonata2/clock/window.py`.
//!
//! The glass toolbar continues the compositor's title bar: the three pages
//! as tabs in its middle, + (new alarm) at its end on Alarms. Each alarm is
//! a row: the time in large light figures, its name and repeat days under
//! it, a switch to turn it on or off. A click edits it in a popover (time,
//! repeat days, name, snooze, sound, Delete); right-click deletes. Alarms
//! ring from the menu bar process, so Clock may be closed.

use crate::alarms::{self as A, Alarm};
use crate::pages::{two_digit_spin, State, StopwatchPage, TimerPage};
use crate::sound::Ringer;
use gtk::gdk::{Key, ModifierType};
use gtk::{glib, pango, prelude::*};
use serde_json::{json, Value};
use sonata_i18n::{gettext, n_, tr};
use sonata_ui::{controls, menu};
use std::cell::RefCell;
use std::rc::{Rc, Weak};

pub const PAGES: [(&str, &str); 3] = [("alarms", n_!("Alarms")), ("stopwatch", n_!("Stopwatch")), ("timers", n_!("Timers"))];

pub struct Clock {
    pub window: gtk::ApplicationWindow,
    pub tabs: controls::Segmented,
    pub pages: gtk::Stack,
    pub add_btn: gtk::Button,
    list: gtk::ListBox,
    scroller: gtk::ScrolledWindow,
    empty: gtk::Label,
    alarms: RefCell<Vec<Alarm>>,
    pub stopwatch: Rc<StopwatchPage>,
    pub timer: Rc<TimerPage>,
    previewer: RefCell<Ringer>,
    monitor: RefCell<Option<gtk::gio::FileMonitor>>,
    me: Weak<Clock>,
}

impl Clock {
    pub fn new(app: &impl IsA<gtk::Application>) -> Rc<Self> {
        sonata_ui::css::register_module("clock/window.py"); // the Python Clock's own CSS (css.json)
        sonata_ui::css::register_module("clock/pages.py");
        if glib::application_name().is_none() {
            glib::set_application_name(&tr!("Clock"));
        }
        let clock = Rc::new_cyclic(|me: &Weak<Clock>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Clock")).build();
            window.add_css_class("sonata-clock");
            sonata_ui::window::standard(&window);
            sonata_ui::window::remember_size(window.upcast_ref(), "clock", 460, 560);
            window.set_size_request(380, 360);
            let w = me.clone();
            let (toolbar, bar, tools) = sonata_ui::window::glass_toolbar(
                &window,
                vec![("list-add-symbolic", tr!("New Alarm"), Box::new(move || {
                    if let Some(c) = w.upgrade() {
                        c.add();
                    }
                }))],
            );
            let add_btn = tools[0].clone();
            let choices: Vec<(&str, String)> = PAGES.iter().map(|(k, t)| (*k, gettext(t))).collect();
            let w = me.clone();
            let tabs = controls::Segmented::new(&choices, None, move |name: &str| {
                if let Some(c) = w.upgrade() {
                    c.show_page(name, true);
                }
            }, true);
            bar.set_center_widget(Some(&tabs.root));
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            let pages = gtk::Stack::builder().vexpand(true).build(); // (no animation between them, Vini)
            col.append(&pages);
            let main = gtk::Box::builder().orientation(gtk::Orientation::Vertical).vexpand(true).build();
            main.add_css_class("ck-main");
            let title = gtk::Label::builder().label(tr!("Alarms")).xalign(0.0).build();
            title.add_css_class("ck-title");
            main.append(&title);
            let list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::None).build();
            list.add_css_class("ck-list");
            let w = me.clone();
            list.connect_row_activated(move |_, row| {
                if let Some(c) = w.upgrade() {
                    let a = c.alarms.borrow().get(row.index() as usize).cloned();
                    if let Some(a) = a {
                        c.edit(a, row.upcast_ref(), false);
                    }
                }
            });
            let empty = gtk::Label::builder().label(tr!("No Alarms")).vexpand(true).valign(gtk::Align::Center).build();
            empty.add_css_class("ck-empty");
            main.append(&empty);
            let scroller = gtk::ScrolledWindow::builder().child(&list).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build();
            main.append(&scroller);
            pages.add_named(&main, Some("alarms"));
            let state = State::new();
            let stopwatch = StopwatchPage::new(state.clone());
            let timer = TimerPage::new(state);
            pages.add_named(&stopwatch.root, Some("stopwatch"));
            pages.add_named(&timer.root, Some("timers"));
            window.set_child(Some(&col));
            Clock {
                window,
                tabs,
                pages,
                add_btn,
                list,
                scroller,
                empty,
                alarms: RefCell::default(),
                stopwatch,
                timer,
                previewer: RefCell::default(),
                monitor: RefCell::default(),
                me: me.clone(),
            }
        });
        let page = sonata_config::load("clock", &json!({"page": "alarms"}));
        clock.show_page(page.get("page").and_then(Value::as_str).unwrap_or("alarms"), false);
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(&clock);
        keys.connect_key_pressed(move |_, keyval, _code, state| match w.upgrade() {
            Some(c) if c.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        clock.window.add_controller(keys);
        let w = Rc::downgrade(&clock);
        // a one-time alarm went off (the menu bar turned it off)
        *clock.monitor.borrow_mut() = sonata_config::watch_path(&A::path(), move || {
            if let Some(c) = w.upgrade() {
                c.refresh();
            }
        });
        let w = Rc::downgrade(&clock);
        clock.window.connect_close_request(move |_| {
            if let Some(c) = w.upgrade() {
                c.previewer.borrow_mut().stop();
            }
            glib::Propagation::Proceed
        });
        clock.refresh();
        clock
    }

    pub fn show_page(&self, name: &str, remember: bool) {
        let name = if PAGES.iter().any(|(k, _)| *k == name) { name } else { "alarms" };
        self.pages.set_visible_child_name(name);
        self.tabs.select(name);
        self.add_btn.set_visible(name == "alarms");
        if remember {
            let _ = sonata_config::update("clock", &json!({"page": name})); // opens where you left it (macOS)
        }
    }

    fn key(&self, keyval: Key, state: ModifierType) -> bool {
        let cmd = state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK);
        if !cmd {
            return false;
        }
        match keyval {
            Key::_1 | Key::_2 | Key::_3 => {
                // Cmd+1..3: the tabs
                let i = match keyval { Key::_1 => 0, Key::_2 => 1, _ => 2 };
                self.show_page(PAGES[i].0, true);
                true
            }
            Key::n | Key::N if self.pages.visible_child_name().as_deref() == Some("alarms") => {
                self.add();
                true
            }
            Key::w | Key::W => {
                self.window.close();
                true
            }
            _ => false,
        }
    }

    // -- list ------------------------------------------------------------------------------
    pub fn refresh(&self) {
        let alarms = A::load();
        while let Some(row) = self.list.first_child() {
            self.list.remove(&row);
        }
        let h24 = A::h24();
        for a in &alarms {
            self.list.append(&self.row(a, h24));
        }
        self.empty.set_visible(alarms.is_empty());
        self.scroller.set_visible(!alarms.is_empty());
        *self.alarms.borrow_mut() = alarms;
    }

    fn row(&self, a: &Alarm, h24: bool) -> gtk::ListBoxRow {
        let row_box = gtk::Box::new(gtk::Orientation::Horizontal, 12);
        row_box.add_css_class("ck-row");
        if !a.enabled {
            row_box.add_css_class("off");
        }
        let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).hexpand(true).build();
        let time = gtk::Label::builder().label(A::time_text(a, h24)).xalign(0.0).build();
        time.add_css_class("ck-time");
        texts.append(&time);
        let sub: Vec<String> = [a.label.clone(), A::repeat_text(&a.repeat)].into_iter().filter(|t| !t.is_empty()).collect();
        let sub = gtk::Label::builder().label(sub.join(", ")).xalign(0.0).ellipsize(pango::EllipsizeMode::End).build();
        sub.add_css_class("ck-sub");
        texts.append(&sub);
        row_box.append(&texts);
        let sw = gtk::Switch::builder().active(a.enabled).valign(gtk::Align::Center).build();
        let (w, id) = (self.me.clone(), a.id.clone());
        sw.connect_active_notify(move |s| {
            if let Some(c) = w.upgrade() {
                c.set_enabled(&id, s.is_active());
            }
        });
        row_box.append(&sw);
        let row = gtk::ListBoxRow::builder().child(&row_box).build();
        let click = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
        let (w, id) = (self.me.clone(), a.id.clone());
        click.connect_pressed(move |g, _n, x, y| {
            let (Some(widget), Some(c)) = (g.widget(), w.upgrade()) else { return };
            let (w2, id) = (Rc::downgrade(&c), id.clone());
            menu::popup(&widget, vec![vec![menu::Item::new(&tr!("Delete Alarm"), move || {
                if let Some(c) = w2.upgrade() {
                    c.delete(&id);
                }
            })]], Some((x, y)));
        });
        row.add_controller(click);
        row
    }

    // -- changes ---------------------------------------------------------------------------
    fn store(&self, alarms: &[Alarm]) {
        if let Err(e) = A::save(alarms) {
            eprintln!("sonata2-rs clock: alarms.json: {e}");
        }
        // after the switch's signal and the popover's closing: the row they belong to is rebuilt
        let w = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(c) = w.upgrade() {
                c.refresh();
            }
        });
    }

    pub fn set_enabled(&self, id: &Value, on: bool) {
        let mut alarms = A::load();
        if let Some(a) = alarms.iter_mut().find(|a| a.same_id(id) && a.enabled != on) {
            a.enabled = on;
            self.store(&alarms);
        }
    }

    pub fn delete(&self, id: &Value) {
        let alarms: Vec<Alarm> = A::load().into_iter().filter(|a| !a.same_id(id)).collect();
        self.store(&alarms);
    }

    pub fn put(&self, alarm: Alarm) {
        let mut alarms: Vec<Alarm> = A::load().into_iter().filter(|a| !a.same_id(&alarm.id)).collect();
        alarms.push(alarm);
        alarms.sort_by_key(|a| (a.hour, a.minute));
        self.store(&alarms);
    }

    /// Play an alarm sound once (None: stop the one playing).
    fn preview(&self, sound: Option<&str>) {
        let mut p = self.previewer.borrow_mut();
        match sound {
            Some(s) => p.start(s),
            None => p.stop(),
        }
    }

    pub fn add(&self) {
        let now = glib::DateTime::now_local().ok();
        let (h, m) = now.map(|n| (n.hour() as u32, n.minute() as u32)).unwrap_or((7, 0));
        self.edit(Alarm::new(h, m), self.add_btn.upcast_ref(), true);
    }

    // -- edit popover ------------------------------------------------------------------------
    pub fn edit(&self, alarm: Alarm, anchor: &gtk::Widget, new: bool) -> gtk::Popover {
        let pop = gtk::Popover::new();
        pop.set_parent(anchor);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 10);
        col.add_css_class("ck-edit");
        let (hour, minute) = (two_digit_spin(alarm.hour as f64, 23.0), two_digit_spin(alarm.minute as f64, 59.0));
        let times = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).build();
        times.append(&hour);
        let colon = gtk::Label::new(Some(":"));
        colon.add_css_class("ck-colon");
        times.append(&colon);
        times.append(&minute);
        col.append(&times);
        let days = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).build();
        let toggles: Vec<gtk::ToggleButton> = (0..7)
            .map(|i| {
                let first: String = A::day_name(i).chars().take(1).collect();
                let t = gtk::ToggleButton::builder().label(first).active(alarm.repeat.contains(&(i as u8))).tooltip_text(A::day_abbr(i)).build();
                t.add_css_class("ck-day");
                days.append(&t);
                t
            })
            .collect();
        col.append(&days);
        let label = gtk::Entry::builder().text(alarm.label.as_str()).placeholder_text(tr!("Alarm")).build();
        col.append(&label);
        let snooze = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        snooze.append(&gtk::Label::builder().label(tr!("Snooze")).hexpand(true).xalign(0.0).build());
        let snooze_sw = gtk::Switch::builder().active(alarm.snooze).build();
        snooze.append(&snooze_sw);
        col.append(&snooze);
        // the sound: picking one plays it once (a preview), like the iPhone
        let keys: Vec<&str> = A::SOUNDS.iter().map(|s| s.0).collect();
        let names: Vec<String> = A::SOUNDS.iter().map(|s| gettext(s.1)).collect();
        let sound = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        sound.append(&gtk::Label::builder().label(tr!("Sound")).hexpand(true).xalign(0.0).build());
        let w = self.me.clone();
        let current = keys.iter().position(|k| *k == alarm.sound).unwrap_or(0) as u32;
        let sound_dd = controls::popup_button(&names, current, move |i| {
            if let Some(c) = w.upgrade() {
                c.preview(A::SOUNDS.get(i as usize).map(|s| s.0));
            }
        });
        sound.append(&sound_dd);
        col.append(&sound);
        let btns = gtk::Box::builder().spacing(8).margin_top(4).build();
        if !new {
            let (w, p, id) = (self.me.clone(), pop.clone(), alarm.id.clone());
            btns.append(&controls::push_button(&tr!("Delete"), move || {
                p.popdown();
                if let Some(c) = w.upgrade() {
                    c.delete(&id);
                }
            }, "destructive"));
        }
        btns.append(&gtk::Box::builder().hexpand(true).build());
        let p = pop.clone();
        btns.append(&controls::push_button(&tr!("Cancel"), move || p.popdown(), ""));
        let save: Rc<dyn Fn()> = {
            let (w, p, base) = (self.me.clone(), pop.clone(), alarm.clone());
            let (hour, minute, label, snooze_sw, sound_dd, toggles) =
                (hour.clone(), minute.clone(), label.clone(), snooze_sw.clone(), sound_dd.clone(), toggles.clone());
            Rc::new(move || {
                let mut a = base.clone();
                a.hour = hour.value() as u32;
                a.minute = minute.value() as u32;
                a.repeat = toggles.iter().enumerate().filter(|(_, t)| t.is_active()).map(|(i, _)| i as u8).collect();
                a.label = label.text().trim().to_string();
                a.snooze = snooze_sw.is_active();
                a.sound = A::SOUNDS.get(sound_dd.selected() as usize).map(|s| s.0).unwrap_or(A::SOUND).to_string();
                a.enabled = true;
                p.popdown();
                if let Some(c) = w.upgrade() {
                    c.put(a);
                }
            })
        };
        let s = save.clone();
        btns.append(&controls::push_button(&tr!("Save"), move || s(), "default"));
        col.append(&btns);
        let s = save.clone();
        label.connect_activate(move |_| s());
        pop.set_child(Some(&col));
        let w = self.me.clone();
        pop.connect_closed(move |p| {
            if let Some(c) = w.upgrade() {
                c.preview(None);
            }
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        pop.popup();
        pop
    }
}

/// Run Clock: one window (opening it again brings it forward).
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(crate::app_id()).build();
    let state: Rc<RefCell<Option<Rc<Clock>>>> = Rc::default();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    app.connect_activate(move |app| {
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        let mut s = state.borrow_mut();
        // a closed window is gone: a new one (the Python Clock does the same)
        if s.as_ref().is_some_and(|c| c.window.application().is_none()) {
            *s = None;
        }
        let clock = s.get_or_insert_with(|| Clock::new(app)).clone();
        drop(s);
        clock.window.present();
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}
