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

/// The ledger as notifier.py writes it (json.dumps: `{"k": v, "k2": v2}`),
/// so the two Notes and the menu bar keep one file the same.
fn ledger_text(sent: &Map<String, Value>) -> String {
    let items: Vec<String> = sent
        .iter()
        .map(|(k, v)| {
            let k = sonata_config::to_python_json_indent(&Value::String(k.clone()), 0);
            format!("{k}: {}", sonata_config::to_python_json_indent(v, 0))
        })
        .collect();
    format!("{{{}}}", items.join(", "))
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
        Self::new_at(store, now_unix())
    }

    /// One that started at `now` (unix seconds): it catches up on what fell
    /// due in the GRACE minutes before.
    pub fn new_at(store: Rc<RefCell<Store>>, now: i64) -> Rc<Self> {
        let n = Rc::new(Notifier { store, last: Cell::new(now - GRACE_S), timer: RefCell::default(), sent: RefCell::default() });
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
        self.check_at(now_unix());
    }

    /// The reminders that fell due since the last sweep, up to `now`,
    /// announced (the ones no other process announced yet).
    pub fn check_at(self: &Rc<Self>, now: i64) -> Vec<Rec> {
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
        due
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
            let _ = sonata_config::atomic_write(&path, ledger_text(&sent).as_bytes(), false);
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

/// Reminder alerts with Notes closed (notifier.py's ReminderWatch), for the
/// menu bar process: reminders.json read-only, re-aimed on every save Notes
/// makes (it saves by rename).
pub struct ReminderWatch {
    pub notifier: Rc<Notifier>,
    monitor: RefCell<Option<gio::FileMonitor>>,
}

impl ReminderWatch {
    pub fn start() -> Rc<Self> {
        Self::start_in(S::data_dir(), now_unix())
    }

    pub fn start_in(dir: std::path::PathBuf, now: i64) -> Rc<Self> {
        let store = Rc::new(RefCell::new(Store::open_read_only(dir)));
        let path = store.borrow().reminders_path.clone();
        let w = Rc::new(ReminderWatch { notifier: Notifier::new_at(store, now), monitor: RefCell::default() });
        let weak = Rc::downgrade(&w);
        *w.monitor.borrow_mut() = sonata_config::watch_path(&path, move || {
            if let Some(w) = weak.upgrade() {
                w.reload();
            }
        });
        w
    }

    pub fn reload(&self) {
        self.notifier.store.borrow_mut().load();
        self.notifier.schedule();
    }

    pub fn stop(&self) {
        self.notifier.stop();
        if let Some(m) = self.monitor.borrow_mut().take() {
            m.cancel();
        }
    }
}

#[cfg(test)]
mod tests {
    //! tests/test_reminder_alerts.py, the Notes window's half (the menu bar's
    //! watcher is sonata-sessionui's).
    use super::*;
    use serde_json::json;
    use std::sync::Mutex;

    /// glib's default main context: one test at a time owns it.
    static MAIN: Mutex<()> = Mutex::new(());

    fn stamp(m: Moment) -> String {
        format!("{:04}-{:02}-{:02}T{:02}:{:02}", m.0, m.1, m.2, m.3, m.4)
    }

    fn at(unix: i64) -> Moment {
        moment_of(unix)
    }

    struct Fixture {
        _dir: tempfile::TempDir,
        path: std::path::PathBuf,
        now: i64,
    }

    impl Fixture {
        fn new() -> Self {
            let dir = tempfile::tempdir().unwrap();
            let now = unix_of(Moment(2026, 10, 10, 12, 0));
            let f = Fixture { path: dir.path().to_path_buf(), _dir: dir, now };
            f.write(json!([
                {"id": "old", "title": "Last week", "due": stamp(at(now - 7 * 86400))},
                {"id": "recent", "title": "Two minutes ago", "due": stamp(at(now - 120))},
                {"id": "soon", "title": "Soon", "notes": "milk", "due": stamp(at(now + 600))},
                {"id": "done", "title": "Done", "due": stamp(at(now + 300)), "completed": true},
            ]));
            f
        }

        fn write(&self, reminders: Value) {
            let data = json!({"lists": [{"id": "reminders", "name": "Reminders", "color": "blue"}], "reminders": reminders});
            std::fs::write(self.path.join("reminders.json"), data.to_string()).unwrap();
        }

        fn notifier(&self, now: i64) -> Rc<Notifier> {
            Notifier::new_at(Rc::new(RefCell::new(Store::open(self.path.clone()))), now)
        }
    }

    fn titles(n: &Notifier) -> Vec<String> {
        n.sent.borrow().iter().map(|s| s.0.clone()).collect()
    }

    #[test]
    fn notifies_once_and_no_storm() {
        let _m = MAIN.lock().unwrap_or_else(|e| e.into_inner());
        let f = Fixture::new();
        let n = f.notifier(f.now);
        assert!(n.timer.borrow().is_some()); // aimed at "recent" (catch-up)
        n.check_at(f.now);
        assert_eq!(titles(&n), ["Two minutes ago"]); // not last week's
        n.check_at(f.now + 11 * 60);
        assert_eq!(titles(&n), ["Two minutes ago", "Soon"]);
        assert_eq!(n.sent.borrow().last().unwrap().1, "milk");
        // another Notifier on the same folder (the menu bar's) doesn't repeat them
        let other = f.notifier(f.now);
        other.check_at(f.now + 12 * 60);
        assert!(other.sent.borrow().is_empty());
        n.stop();
        other.stop();
    }

    #[test]
    fn rescheduled_reminder_notifies_again() {
        let _m = MAIN.lock().unwrap_or_else(|e| e.into_inner());
        let f = Fixture::new();
        let n = f.notifier(f.now);
        n.check_at(f.now + 11 * 60);
        f.write(json!([{"id": "soon", "title": "Soon", "due": stamp(at(f.now + 30 * 60))}]));
        n.store.borrow_mut().load();
        n.check_at(f.now + 31 * 60);
        assert_eq!(titles(&n).iter().filter(|t| *t == "Soon").count(), 2);
        n.stop();
    }

    /// The menu bar's alerts read reminders.json once at login (a reminder
    /// added in Notes later never notified with Notes closed) through a full,
    /// writable Store; notifier.py's ReminderWatch rereads it on every save
    /// and never writes Notes' files.
    #[test]
    fn test_menu_bar_alerts_missed_reminders_added_later() {
        let _m = MAIN.lock().unwrap_or_else(|e| e.into_inner());
        let f = Fixture::new();
        let w = ReminderWatch::start_in(f.path.clone(), f.now);
        let path = f.path.join("reminders.json");
        f.write(json!([{"id": "new", "title": "Added in Notes", "due": stamp(at(f.now + 60))}]));
        w.reload();
        let ids: Vec<String> = w.notifier.store.borrow().reminders.iter().map(|r| S::s(r, "id").to_string()).collect();
        assert_eq!(ids, ["new"]);
        w.notifier.check_at(f.now + 120);
        assert!(titles(&w.notifier).contains(&"Added in Notes".to_string()));
        std::fs::write(&path, "{broken").unwrap(); // mid-write / damaged
        w.reload();
        assert_eq!(w.notifier.store.borrow().reminders.len(), 1); // keeps what it had
        assert!(path.exists()); // never set aside by the menu bar
        assert!(!f.path.join("notes.json").exists()); // nor any of Notes' files written
        w.stop();
    }

    /// The ledger the Python Notes and the menu bar share was written as
    /// compact JSON (`{"a":1}`); notifier.py writes json.dumps' text, so the
    /// file changed shape every time the other side wrote it.
    #[test]
    fn test_ledger_was_not_written_as_notifier_py_does() {
        let _m = MAIN.lock().unwrap_or_else(|e| e.into_inner());
        let f = Fixture::new();
        let n = f.notifier(f.now);
        n.check_at(f.now);
        let text = std::fs::read_to_string(f.path.join(LEDGER)).unwrap();
        let due = stamp(at(f.now - 120));
        assert!(text.starts_with(&format!("{{\"recent|{due}\": ")), "{text}");
        assert!(text.ends_with('}') && !text.contains('\n'));
        let mut m = Map::new();
        m.insert("a|None".into(), json!(1.5));
        m.insert("b|2026-10-10".into(), json!(1730000000.25));
        assert_eq!(ledger_text(&m), r#"{"a|None": 1.5, "b|2026-10-10": 1730000000.25}"#);
        assert_eq!(ledger_text(&Map::new()), "{}");
        n.stop();
    }
}
