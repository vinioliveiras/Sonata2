//! Reminder alerts (sonata2/notes/notifier.py): a desktop notification
//! (org.freedesktop.Notifications) when a reminder falls due (all-day ones
//! at 9:00), while Notes is open. Both Notes and the menu bar's watcher
//! share a small ledger file so a reminder is announced once. No storm at
//! start: only reminders due in the last GRACE minutes are caught up on.

use crate::store::{self as S, Moment, Rec, Store};
use gtk::{gio, glib, prelude::*};
use serde_json::{Map, Value};
use sonata_i18n::{gettext, n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;

const APP_NAME: &str = n_!("Reminders");
const GRACE_S: i64 = 5 * 60;
/// seconds: a suspend or a clock change can't make an alert late by more
const MAX_WAIT: i64 = 300;
const LEDGER: &str = "reminders-notified.json";
/// seconds a sent mark is kept
const LEDGER_KEEP: f64 = 3.0 * 86400.0;

pub fn desktop_entry() -> &'static str {
    crate::app_id() // Reminders lives in Notes
}

fn unix_of(m: Moment) -> i64 {
    glib::DateTime::from_local(m.0, m.1 as i32, m.2 as i32, m.3 as i32, m.4 as i32, 0.0).map(|d| d.to_unix()).unwrap_or(0)
}

fn moment_of(unix: i64) -> Moment {
    glib::DateTime::from_unix_local(unix)
        .map(|d| Moment(d.year(), d.month() as u32, d.day_of_month() as u32, d.hour() as u32, d.minute() as u32))
        .unwrap_or(Moment(1970, 1, 1, 0, 0))
}

pub fn now_unix() -> i64 {
    glib::DateTime::now_local().map(|d| d.to_unix()).unwrap_or(0)
}

fn key(r: &Rec) -> String {
    // the due time is part of the key: moving a reminder later notifies again
    format!("{}|{}", S::s(r, "id"), r.get("due").map(|v| v.as_str().map(String::from).unwrap_or_else(|| "None".into())).unwrap_or_else(|| "None".into()))
}

pub struct Notifier {
    store: Rc<RefCell<Store>>,
    /// unix time of the last sweep
    last: Cell<i64>,
    timer: RefCell<Option<glib::SourceId>>,
    pub sent: RefCell<Vec<(String, String)>>,
}

impl Notifier {
    pub fn new(store: Rc<RefCell<Store>>) -> Rc<Self> {
        let n = Rc::new(Notifier { store, last: Cell::new(now_unix() - GRACE_S), timer: RefCell::default(), sent: RefCell::default() });
        n.schedule();
        n
    }

    pub fn schedule(self: &Rc<Self>) {
        self.stop();
        let next = self.store.borrow().next_due_after(moment_of(self.last.get()));
        let Some(next) = next else { return };
        let now = now_unix();
        let at = unix_of(next);
        let secs = if at <= now { 0 } else { at - now + 1 };
        let w = Rc::downgrade(self);
        *self.timer.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_secs(secs.clamp(1, MAX_WAIT) as u64), move || {
            if let Some(n) = w.upgrade() {
                n.timer.borrow_mut().take();
                n.check();
            }
        }));
    }

    pub fn check(self: &Rc<Self>) {
        let now = now_unix();
        if now < self.last.get() - GRACE_S {
            self.last.set(now - GRACE_S); // the clock went back
        }
        let due = self.store.borrow().due_between(moment_of(self.last.get()), moment_of(now));
        self.last.set(now);
        let due = if due.is_empty() { due } else { self.claim(due) };
        for r in &due {
            let st = self.store.borrow();
            let body = if S::s(r, "notes").is_empty() { S::display_name(st.rlist(S::s(r, "list"))) } else { S::s(r, "notes").to_string() };
            let title = if S::s(r, "title").is_empty() { tr!("New Reminder") } else { S::s(r, "title").to_string() };
            drop(st);
            self.notify(&title, &body);
        }
        self.schedule();
    }

    /// The reminders not announced yet (by this or the other process),
    /// marked as announced.
    fn claim(&self, due: Vec<Rec>) -> Vec<Rec> {
        let path = self.store.borrow().dir.join(LEDGER);
        let sent: Map<String, Value> = std::fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default();
        let t = S::now();
        let mut sent: Map<String, Value> = sent.into_iter().filter(|(_, v)| v.as_f64().is_some_and(|x| t - x < LEDGER_KEEP)).collect();
        let fresh: Vec<Rec> = due.into_iter().filter(|r| !sent.contains_key(&key(r))).collect();
        if !fresh.is_empty() {
            for r in &fresh {
                sent.insert(key(r), serde_json::json!(t));
            }
            let _ = sonata_config::atomic_write(&path, serde_json::to_string(&Value::Object(sent)).unwrap_or_default().as_bytes(), false);
        }
        fresh
    }

    pub fn notify(&self, title: &str, body: &str) {
        self.sent.borrow_mut().push((title.to_string(), body.to_string()));
        let (title, body) = (title.to_string(), body.to_string());
        gio::bus_get(gio::BusType::Session, gio::Cancellable::NONE, move |res| {
            let Ok(bus) = res else { return };
            let mut hints: HashMap<String, glib::Variant> = HashMap::new();
            hints.insert("urgency".into(), 1u8.to_variant());
            hints.insert("desktop-entry".into(), desktop_entry().to_variant());
            let args = (gettext(APP_NAME), 0u32, "x-office-calendar", title, body, Vec::<String>::new(), hints, -1i32).to_variant();
            bus.call(
                Some("org.freedesktop.Notifications"),
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                Some(&args),
                None,
                gio::DBusCallFlags::NONE,
                -1,
                gio::Cancellable::NONE,
                |r| {
                    if let Err(e) = r {
                        eprintln!("sonata2-rs reminders: notification failed: {e}");
                    }
                },
            );
        });
    }

    pub fn stop(&self) {
        if let Some(id) = self.timer.borrow_mut().take() {
            id.remove();
        }
    }
}
