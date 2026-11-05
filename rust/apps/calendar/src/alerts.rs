//! Event alerts while Calendar runs (calendar/alerts.py): a desktop
//! notification (org.freedesktop.Notifications, over Gio D-Bus) at each
//! alert time.
//!
//! One GLib timeout waits for the next alert (at most 5 minutes, so a
//! suspend or a clock change is caught up soon); no polling in between.

use crate::ics::{self, Event, Occurrence, DT};
use chrono::Duration;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

const HORIZON_DAYS: i64 = 2;
/// seconds
const MAX_WAIT: i64 = 300;

/// [(alert time, occurrence)] with an alert time in (after, until], by time.
pub fn due(events: &[Rc<Event>], after: DT, until: DT) -> Vec<(DT, Occurrence)> {
    let mut out = Vec::new();
    for ev in events {
        let Some(alarm) = ev.alarm else { continue };
        let lead = Duration::minutes(alarm);
        // occurrences starting in (after + lead, until + lead]
        for o in ics::occurrences(ev, after + lead, until + lead + Duration::seconds(1)) {
            let t = o.start - lead;
            if after < t && t <= until {
                out.push((t, o));
            }
        }
    }
    out.sort_by_key(|x| x.0);
    out
}

/// The local time now, as Calendar keeps times (naive).
pub fn now() -> DT {
    glib::DateTime::now_local().map(|g| {
        let (y, m, d) = g.ymd();
        ics::date(y, m as u32, d as u32).and_hms_micro_opt(g.hour() as u32, g.minute() as u32, g.second() as u32, g.microsecond() as u32).unwrap()
    }).unwrap_or_default()
}

type Events = Box<dyn Fn() -> Vec<Rc<Event>>>;
type Notify = Box<dyn Fn(&Occurrence)>;

/// Fires the alerts of `events()` through `notify(occ)`.
pub struct Alerts {
    events: Events,
    notify: Notify,
    last: Cell<DT>,
    src: RefCell<Option<glib::SourceId>>,
    me: Weak<Alerts>,
}

impl Alerts {
    pub fn new(events: impl Fn() -> Vec<Rc<Event>> + 'static, notify: impl Fn(&Occurrence) + 'static) -> Rc<Self> {
        Rc::new_cyclic(|me| Alerts { events: Box::new(events), notify: Box::new(notify), last: Cell::new(now()), src: RefCell::new(None), me: me.clone() })
    }

    pub fn reschedule(&self) {
        self.stop();
        let t = now();
        let upcoming = due(&(self.events)(), self.last.get(), t + Duration::days(HORIZON_DAYS));
        let wait = match upcoming.first() {
            Some((at, _)) => {
                let ms = (*at - t).num_milliseconds();
                (ms + 999).div_euclid(1000).clamp(1, MAX_WAIT)
            }
            None => MAX_WAIT,
        };
        let me = self.me.clone();
        let id = glib::timeout_add_seconds_local_once(wait as u32, move || {
            if let Some(a) = me.upgrade() {
                a.src.replace(None);
                a.fire();
            }
        });
        self.src.replace(Some(id));
    }

    fn fire(&self) {
        let t = now();
        if t < self.last.get() {
            self.last.set(t); // the clock went back
        }
        for (_, occ) in due(&(self.events)(), self.last.get(), t) {
            (self.notify)(&occ);
        }
        self.last.set(t);
        self.reschedule();
    }

    pub fn stop(&self) {
        if let Some(id) = self.src.take() {
            sonata_ui::cancel(id);
        }
    }
}

/// Notify over the session bus, asynchronously (errors are ignored: no
/// notification daemon means no alert, never a crash).
pub fn send_notification(summary: &str, body: &str, app_id: &str) {
    let (summary, body, app_id) = (summary.to_string(), body.to_string(), app_id.to_string());
    gio::bus_get(gio::BusType::Session, gio::Cancellable::NONE, move |res| {
        let Ok(bus) = res else { return };
        let hints = glib::VariantDict::new(None);
        hints.insert_value("desktop-entry", &app_id.to_variant());
        hints.insert_value("category", &"x-calendar".to_variant());
        let actions: Vec<String> = Vec::new();
        let args = (tr!("Calendar"), 0u32, "x-office-calendar", summary, body, actions, hints.end(), -1i32).to_variant();
        bus.call(Some("org.freedesktop.Notifications"), "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify", Some(&args), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE, |_| {});
    });
}

use glib::prelude::*;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ics::at;

    #[test]
    fn due_once_in_its_window() {
        let mut ev = Event::new("a", at(2026, 9, 30, 10, 0), at(2026, 9, 30, 11, 0));
        ev.summary = "Call".into();
        ev.alarm = Some(15);
        ev.freq = Some("DAILY".into());
        let quiet = Event::new("q", at(2026, 9, 30, 10, 0), at(2026, 9, 30, 11, 0));
        let evs = [Rc::new(ev.clone()), Rc::new(quiet)];
        let got: Vec<DT> = due(&evs, at(2026, 9, 30, 9, 40), at(2026, 10, 1, 9, 45)).into_iter().map(|x| x.0).collect();
        assert_eq!(got, [at(2026, 9, 30, 9, 45), at(2026, 10, 1, 9, 45)]);
        assert!(due(&evs[..1], at(2026, 9, 30, 9, 45), at(2026, 9, 30, 9, 50)).is_empty());
        // reported exactly once over consecutive (after, until] windows
        ev.freq = None;
        let one = [Rc::new(ev)];
        assert_eq!(due(&one, at(2026, 9, 30, 9, 0), at(2026, 9, 30, 9, 45)).len(), 1);
        assert!(due(&one, at(2026, 9, 30, 9, 45), at(2026, 9, 30, 10, 30)).is_empty());
    }
}
