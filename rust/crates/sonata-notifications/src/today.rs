//! Notification Center's Today widgets: Big Sur's small Calendar (the
//! month, today in a red circle, days with events in bold) and Up Next
//! (today's and tomorrow's next events), from Calendar's own files --
//! none written here.

use chrono::{Datelike, Duration, NaiveDate, NaiveDateTime};
use gtk::prelude::*;
use serde_json::{json, Value};
use sonata_calendar::alerts::now as local_now;
use sonata_calendar::{ics, model as cal};
use sonata_i18n::tr;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::rc::Rc;

/// An occurrence and its calendar's colour ("#ff3b30").
pub type Upcoming = Vec<(ics::Occurrence, String)>;

pub fn now() -> NaiveDateTime {
    local_now()
}

/// Calendar's folder: the one the Calendar this build opens reads.
pub fn folder() -> PathBuf {
    cal::data_dir()
}

/// When Calendar's files last changed (the widget is made again after an edit).
pub fn stamp(folder: &Path) -> i128 {
    std::fs::read_dir(folder)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| e.metadata().ok()?.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as i128)
        .max()
        .unwrap_or(0)
}

/// This month's and the next days' event occurrences, hidden calendars
/// left out. Nothing when Calendar has no files yet (a Store would make
/// its default calendars: not ours to write).
pub fn upcoming(folder: &Path, now: NaiveDateTime, days: i64) -> Upcoming {
    let has_ics = std::fs::read_dir(folder).into_iter().flatten().flatten().any(|e| e.file_name().to_string_lossy().ends_with(".ics"));
    if !has_ics {
        return Vec::new();
    }
    let mut store = cal::Store::new(folder.to_path_buf());
    store.load();
    let hidden: Vec<String> = sonata_config::load("calendar", &json!({"hidden": []}))
        .get("hidden")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect())
        .unwrap_or_default();
    let colour = |id: &str| store.calendar(id).map(|c| cal::hex_of(&c.color)).filter(|h| !h.is_empty()).unwrap_or("#007aff").to_string();
    let today = now.date();
    let a = ics::midnight(NaiveDate::from_ymd_opt(today.year(), today.month(), 1).unwrap_or(today));
    let b = ics::midnight(today) + Duration::days(days);
    ics::expand(&store.visible_events(&hidden), a, b).into_iter().map(|o| {
        let c = colour(&o.event.calendar);
        (o, c)
    }).collect()
}

/// Up Next: the events not over yet that start by tomorrow, at most 3.
pub fn soon(events: &Upcoming, now: NaiveDateTime) -> Vec<&(ics::Occurrence, String)> {
    let last = now.date() + Duration::days(1);
    events.iter().filter(|(o, _)| o.end > now && o.start.date() <= last).take(3).collect()
}

/// When Up Next's first event ends (it then drops out).
pub fn until(events: &Upcoming, now: NaiveDateTime) -> Option<NaiveDateTime> {
    let last = now.date() + Duration::days(1);
    events.iter().filter(|(o, _)| o.end > now && o.start.date() <= last).map(|(o, _)| o.end).min()
}

/// The month's weeks, Sunday first: the day of the month, 0 outside it.
pub fn weeks(year: i32, month: u32) -> Vec<[u32; 7]> {
    let Some(first) = NaiveDate::from_ymd_opt(year, month, 1) else { return Vec::new() };
    let next = if month == 12 { NaiveDate::from_ymd_opt(year + 1, 1, 1) } else { NaiveDate::from_ymd_opt(year, month + 1, 1) };
    let days = next.map(|n| (n - first).num_days() as u32).unwrap_or(31);
    let lead = first.weekday().num_days_from_sunday();
    let mut out = Vec::new();
    let mut week = [0u32; 7];
    for d in 1..=days {
        let i = ((lead + d - 1) % 7) as usize;
        week[i] = d;
        if i == 6 {
            out.push(week);
            week = [0; 7];
        }
    }
    if week.iter().any(|d| *d != 0) {
        out.push(week);
    }
    out
}

/// The days of this month with an event starting.
pub fn busy(events: &Upcoming, year: i32, month: u32) -> HashSet<u32> {
    events.iter().filter(|(o, _)| o.start.year() == year && o.start.month() == month).map(|(o, _)| o.start.day()).collect()
}

/// The small Calendar: month in red capitals, weekday initials, this
/// month's days, today in a red circle; days with events in bold; a day
/// opens Calendar on it.
pub fn month(events: &Upcoming, on_day: Option<Rc<dyn Fn(NaiveDate)>>) -> gtk::Widget {
    let now = glib::DateTime::now_local().ok();
    let today = now.as_ref().map(|d| (d.year(), d.month() as u32, d.day_of_month() as u32)).unwrap_or((2000, 1, 1));
    let (y, m, day_now) = today;
    let b = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let name = now.as_ref().and_then(|d| d.format("%B").ok()).map(|s| s.to_uppercase()).unwrap_or_default();
    b.append(&gtk::Label::builder().label(name).xalign(0.0).css_classes(["nc-month"]).build());
    let grid = gtk::Grid::builder().column_homogeneous(true).row_spacing(1).column_spacing(2).build();
    let busy = busy(events, y, m);
    // the weekday initials, Sunday first (one string: "S" and "T" are two days each)
    for (i, d) in tr!("S M T W T F S").split_whitespace().take(7).enumerate() {
        grid.attach(&gtk::Label::builder().label(d).css_classes(["nc-wd"]).build(), i as i32, 0, 1, 1);
    }
    for (r, week) in weeks(y, m).iter().enumerate() {
        for (c, &day) in week.iter().enumerate() {
            if day == 0 {
                continue;
            }
            let lab = gtk::Label::builder().label(day.to_string()).halign(gtk::Align::Center).css_classes(["nc-day"]).build();
            if day == day_now {
                lab.add_css_class("today");
            }
            if c == 0 || c == 6 {
                lab.add_css_class("weekend");
            }
            if busy.contains(&day) {
                lab.add_css_class("has-events");
            }
            if let (Some(f), Some(date)) = (on_day.clone(), NaiveDate::from_ymd_opt(y, m, day)) {
                let click = gtk::GestureClick::new();
                click.connect_released(move |g, _, _, _| {
                    g.set_state(gtk::EventSequenceState::Claimed);
                    f(date);
                });
                lab.add_controller(click);
            }
            grid.attach(&lab, c as i32, r as i32 + 1, 1, 1);
        }
    }
    b.append(&grid);
    b.upcast()
}

/// A colour bar (the event's calendar): drawn, no CSS made per colour.
fn bar(colour: &str) -> gtk::Widget {
    let rgba = gtk::gdk::RGBA::parse(colour).unwrap_or(gtk::gdk::RGBA::new(0.0, 0.48, 1.0, 1.0));
    let area = gtk::DrawingArea::builder().css_classes(["nc-event-bar"]).overflow(gtk::Overflow::Hidden).build();
    area.set_size_request(3, -1);
    area.set_draw_func(move |_, cr, w, h| {
        cr.set_source_rgba(rgba.red() as f64, rgba.green() as f64, rgba.blue() as f64, rgba.alpha() as f64);
        cr.rectangle(0.0, 0.0, w as f64, h as f64);
        let _ = cr.fill();
    });
    area.upcast()
}

/// Big Sur's Up Next: today's and tomorrow's next events (at most 3).
pub fn up_next(events: &Upcoming, now: NaiveDateTime) -> gtk::Widget {
    let b = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let soon = soon(events, now);
    if soon.is_empty() {
        b.append(&gtk::Label::builder().label(tr!("No more events today")).xalign(0.0).css_classes(["nc-event-day"]).build());
        return b.upcast();
    }
    let today = now.date();
    let mut shown_day = None;
    for (o, colour) in soon {
        let day = o.start.date().max(today);
        if shown_day != Some(day) {
            shown_day = Some(day);
            let text = if day == today { tr!("TODAY") } else { tr!("TOMORROW") };
            b.append(&gtk::Label::builder().label(text).xalign(0.0).css_classes(["nc-event-day"]).build());
        }
        let row = gtk::Box::builder().css_classes(["nc-event"]).build();
        row.append(&bar(colour));
        let texts = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let title = if o.event.summary.is_empty() { tr!("New Event") } else { o.event.summary.clone() };
        texts.append(
            &gtk::Label::builder().label(title).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(1).hexpand(true).css_classes(["nc-event-title"]).build(),
        );
        let when = if o.event.all_day { tr!("All day") } else { format!("{} – {}", o.start.format("%H:%M"), o.end.format("%H:%M")) };
        texts.append(&gtk::Label::builder().label(when).xalign(0.0).css_classes(["nc-event-time"]).build());
        row.append(&texts);
        b.append(&row);
    }
    b.upcast()
}
