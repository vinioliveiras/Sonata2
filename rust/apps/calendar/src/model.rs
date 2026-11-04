//! Calendar's data and layout logic (calendar/model.py; no GTK): the store
//! of calendars (one .ics file each), the macOS calendar colour palette,
//! month grids, week start, and how overlapping events share a column
//! (timeline) or a lane (month rows, all-day strip).

use crate::ics::{self, midnight, Event, Occurrence, DT};
use chrono::{Datelike, Duration, NaiveDate};
use sonata_i18n::{n_, nc_};
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::mpsc;

/// macOS Calendar's colour palette: (id, name, token or colour). The Apple
/// system colours already are tokens (sys_*); yellow and brown are not, so
/// they are given here -- the only colours of this module.
pub const PALETTE: [(&str, &str, &str); 7] = [
    ("red", n_!("Red"), "sys_red"),
    ("orange", n_!("Orange"), "sys_orange"),
    ("yellow", n_!("Yellow"), "#ffcc00"),
    ("green", n_!("Green"), "sys_green"),
    ("blue", n_!("Blue"), "sys_blue"),
    ("purple", n_!("Purple"), "sys_purple"),
    ("brown", n_!("Brown"), "#a2845e"),
];

/// the hex written as X-APPLE-CALENDAR-COLOR (other apps' view of the colour)
pub const PALETTE_HEX: [(&str, &str); 7] = [("red", "#ff3b30"), ("orange", "#ff9500"), ("yellow", "#ffcc00"), ("green", "#34c759"), ("blue", "#007aff"), ("purple", "#af52de"), ("brown", "#a2845e")];

pub fn palette_ids() -> impl Iterator<Item = &'static str> {
    PALETTE.iter().map(|p| p.0)
}

pub fn hex_of(color: &str) -> &'static str {
    PALETTE_HEX.iter().find(|p| p.0 == color).map(|p| p.1).unwrap_or("")
}

fn default_calendars() -> [(String, &'static str); 2] {
    let _ = nc_!("calendar", "Home");
    // created in the session's language
    [(sonata_i18n::pgettext("calendar", "Home"), "blue"), (sonata_i18n::pgettext("calendar", n_!("Work")), "red")]
}

fn rgb(hex: &str) -> Option<(i64, i64, i64)> {
    let h = hex.trim_start_matches('#');
    let c = |i: usize| h.get(i..i + 2).and_then(|s| i64::from_str_radix(s, 16).ok());
    Some((c(0)?, c(2)?, c(4)?))
}

/// The palette colour nearest to a hex colour (imported calendars).
pub fn color_from_hex(hex: &str) -> &'static str {
    let Some((r, g, b)) = rgb(hex) else { return "blue" };
    PALETTE_HEX
        .iter()
        .min_by_key(|(_, h)| {
            let (pr, pg, pb) = rgb(h).unwrap();
            (pr - r).pow(2) + (pg - g).pow(2) + (pb - b).pow(2)
        })
        .map(|p| p.0)
        .unwrap_or("blue")
}

// -- dates ------------------------------------------------------------------------------------
/// First day of the week of the current locale, Monday 0 ... Sunday 6;
/// Sunday when unknown (US, macOS default).
pub fn locale_first_weekday() -> u32 {
    // _NL_TIME_WEEK_1STDAY (a date as a number) and _NL_TIME_FIRST_WEEKDAY (1 = that date)
    unsafe {
        libc::setlocale(libc::LC_TIME, c"".as_ptr());
        let origin = libc::nl_langinfo(0x20066) as usize as u64 as u32;
        let first_p = libc::nl_langinfo(0x20068);
        if first_p.is_null() {
            return 6;
        }
        let first = *first_p as u8 as u32;
        let base = match origin {
            19971130 => 6, // a Sunday
            19971201 => 0, // a Monday
            _ => return 6,
        };
        if !(1..=7).contains(&first) {
            return 6;
        }
        (base + first - 1) % 7
    }
}

pub fn weekday(d: NaiveDate) -> u32 {
    d.weekday().num_days_from_monday()
}

pub fn week_start(d: NaiveDate, first_weekday: u32) -> NaiveDate {
    d - Duration::days(((weekday(d) + 7 - first_weekday) % 7) as i64)
}

/// The 42 dates (6 weeks) of a month page, starting on first_weekday.
pub fn month_grid(year: i32, month: u32, first_weekday: u32) -> Vec<NaiveDate> {
    let start = week_start(ics::date(year, month, 1), first_weekday);
    (0..42).map(|i| start + Duration::days(i)).collect()
}

pub fn day_start(d: DT) -> DT {
    midnight(d.date())
}

// -- layout -----------------------------------------------------------------------------------
/// Timeline layout: occurrences that overlap sit side by side.
/// {occurrence key: (column, columns in its group)}; a group is a run of
/// events that overlap one another (transitively).
pub fn columns(occs: &[Occurrence]) -> HashMap<(String, DT), (usize, usize)> {
    let mut out = HashMap::new();
    let mut sorted: Vec<&Occurrence> = occs.iter().collect();
    sorted.sort_by_key(|o| (o.start, -o.duration()));
    let mut group: Vec<(&Occurrence, usize)> = Vec::new();
    let mut cols: Vec<DT> = Vec::new(); // end time of the last event in each column
    let mut group_end: Option<DT> = None;
    let flush = |group: &mut Vec<(&Occurrence, usize)>, n: usize, out: &mut HashMap<(String, DT), (usize, usize)>| {
        for (o, c) in group.drain(..) {
            out.insert(o.key(), (c, n));
        }
    };
    for o in sorted {
        let end = o.end.max(o.start + Duration::minutes(15)); // tiny events still take room
        if group_end.is_some_and(|g| o.start >= g) {
            flush(&mut group, cols.len(), &mut out);
            cols.clear();
            group_end = None;
        }
        match cols.iter().position(|c_end| *c_end <= o.start) {
            Some(i) => {
                cols[i] = end;
                group.push((o, i));
            }
            None => {
                cols.push(end);
                group.push((o, cols.len() - 1));
            }
        }
        group_end = Some(group_end.map_or(end, |g| g.max(end)));
    }
    flush(&mut group, cols.len(), &mut out);
    out
}

/// Shown as a bar (all-day strip, month bars) rather than a timed pill.
pub fn is_multiday(o: &Occurrence) -> bool {
    o.event.all_day || (o.end - day_start(o.start)) > Duration::days(1)
}

/// Bars over a row of ndays days (a month week, the all-day strip):
/// [(occurrence, first column, last column, lane)], each in the first lane
/// free over all its days.
pub fn lanes(occs: &[Occurrence], first_day: NaiveDate, ndays: i64) -> Vec<(Occurrence, i64, i64, usize)> {
    let row_a = midnight(first_day);
    let mut used: Vec<BTreeSet<i64>> = Vec::new();
    let mut out = Vec::new();
    let mut sorted: Vec<&Occurrence> = occs.iter().collect();
    sorted.sort_by_key(|o| (day_start(o.start), -o.duration(), o.start));
    let days = |d: Duration| d.num_seconds().div_euclid(86400);
    for o in sorted {
        let s = 0.max(days(day_start(o.start) - row_a));
        let last_moment = if o.end > o.start { o.end - Duration::microseconds(1) } else { o.end };
        let e = (ndays - 1).min(days(day_start(last_moment) - row_a));
        if e < 0 || s > ndays - 1 {
            continue;
        }
        let span: BTreeSet<i64> = (s..=e).collect();
        match used.iter_mut().position(|cols| cols.is_disjoint(&span)) {
            Some(lane) => {
                used[lane].extend(&span);
                out.push((o.clone(), s, e, lane));
            }
            None => {
                used.push(span);
                out.push((o.clone(), s, e, used.len() - 1));
            }
        }
    }
    out
}

// -- store ------------------------------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Calendar {
    pub id: String,
    pub name: String,
    pub color: String,
    /// the file's other blocks (VTIMEZONE...), kept
    pub extra: Vec<Vec<String>>,
}

fn slug(name: &str) -> String {
    let mut out = String::new();
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let s = out.trim_matches('-').to_string();
    if s.is_empty() { "calendar".into() } else { s }
}

type Job = Box<dyn FnOnce() + Send>;

/// Calendars and their events, one .ics file per calendar in `folder`.
pub struct Store {
    pub folder: PathBuf,
    pub calendars: Vec<Calendar>,
    pub events: HashMap<String, Rc<Event>>,
    /// the window writes files off the main loop: one worker runs the file
    /// jobs in the order they were made, so an older text never wins and a
    /// delete can't be undone by a write
    writer: Option<(mpsc::Sender<Job>, std::thread::JoinHandle<()>)>,
}

impl Store {
    pub fn new(folder: PathBuf) -> Self {
        Self { folder, calendars: Vec::new(), events: HashMap::new(), writer: None }
    }

    /// Write files on a worker thread from now on (the window).
    pub fn write_async(&mut self) {
        if self.writer.is_none() {
            let (tx, rx) = mpsc::channel::<Job>();
            let h = std::thread::spawn(move || {
                for job in rx {
                    job();
                }
            });
            self.writer = Some((tx, h));
        }
    }

    /// Wait for the queued writes (quitting).
    pub fn flush(&mut self) {
        if let Some((tx, h)) = self.writer.take() {
            drop(tx);
            let _ = h.join();
        }
    }

    pub fn load(&mut self) {
        let _ = std::fs::create_dir_all(&self.folder);
        self.calendars.clear();
        self.events.clear();
        let mut names: Vec<String> = std::fs::read_dir(&self.folder).into_iter().flatten().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.ends_with(".ics")).collect();
        names.sort();
        for n in names {
            let Ok(bytes) = std::fs::read(self.folder.join(&n)) else { continue };
            let id = n[..n.len() - 4].to_string();
            let (info, events) = ics::parse(&String::from_utf8_lossy(&bytes), &id);
            let color = match (&info.color, &info.hex) {
                (Some(c), _) if palette_ids().any(|p| p == c) => c.clone(),
                (_, Some(h)) if !h.is_empty() => color_from_hex(h).to_string(),
                _ => "blue".into(),
            };
            let name = info.name.clone().filter(|n| !n.is_empty()).unwrap_or_else(|| title_case(&id));
            self.calendars.push(Calendar { id, name, color, extra: info.extra });
            for ev in events {
                self.events.insert(ev.uid.clone(), Rc::new(ev));
            }
        }
        if self.calendars.is_empty() {
            for (name, color) in default_calendars() {
                self.add_calendar(&name, Some(color));
            }
        }
    }

    pub fn calendar(&self, cal_id: &str) -> Option<&Calendar> {
        self.calendars.iter().find(|c| c.id == cal_id)
    }

    fn write(&self, cal_id: &str) {
        let Some(cal) = self.calendar(cal_id) else { return };
        let mut evs: Vec<&Event> = self.events.values().filter(|e| e.calendar == cal_id).map(|e| e.as_ref()).collect();
        evs.sort_by_key(|e| (e.start, e.uid.clone()));
        let text = ics::serialize(evs, &cal.name, &cal.color, hex_of(&cal.color), &cal.extra);
        let path = self.folder.join(format!("{cal_id}.ics"));
        self.submit(Box::new(move || {
            if let Err(e) = sonata_config::atomic_write(&path, text.as_bytes(), true) {
                eprintln!("sonata2 calendar: not saved: {e}");
            }
        }));
    }

    fn submit(&self, job: Job) {
        match &self.writer {
            Some((tx, _)) => {
                if let Err(e) = tx.send(job) {
                    (e.0)(); // the worker is gone: write here
                }
            }
            None => job(),
        }
    }

    // calendars
    pub fn add_calendar(&mut self, name: &str, color: Option<&str>) -> Calendar {
        let base = slug(name);
        let (mut cid, mut i) = (base.clone(), 2);
        while self.calendars.iter().any(|c| c.id == cid) || self.folder.join(format!("{cid}.ics")).exists() {
            cid = format!("{base}-{i}");
            i += 1;
        }
        let color = match color {
            Some(c) => c.to_string(),
            None => {
                let used: Vec<&str> = self.calendars.iter().map(|c| c.color.as_str()).collect();
                palette_ids().enumerate().min_by_key(|(i, p)| (used.iter().filter(|u| *u == p).count(), *i)).map(|(_, p)| p.to_string()).unwrap()
            }
        };
        let cal = Calendar { id: cid.clone(), name: name.to_string(), color, extra: Vec::new() };
        self.calendars.push(cal.clone());
        self.write(&cid);
        cal
    }

    pub fn update_calendar(&mut self, cal_id: &str, name: Option<&str>, color: Option<&str>) {
        let Some(cal) = self.calendars.iter_mut().find(|c| c.id == cal_id) else { return };
        if let Some(n) = name.filter(|n| !n.is_empty()) {
            cal.name = n.to_string();
        }
        if let Some(c) = color.filter(|c| palette_ids().any(|p| p == *c)) {
            cal.color = c.to_string();
        }
        self.write(cal_id);
    }

    pub fn delete_calendar(&mut self, cal_id: &str) {
        self.calendars.retain(|c| c.id != cal_id);
        self.events.retain(|_, e| e.calendar != cal_id);
        let path = self.folder.join(format!("{cal_id}.ics"));
        // after the writes queued before it
        self.submit(Box::new(move || {
            let _ = std::fs::remove_file(path);
        }));
    }

    // events
    /// Add or replace an event (by UID); both calendars' files are written
    /// when it moved from one to another.
    pub fn put(&mut self, ev: Event) {
        let (uid, cal) = (ev.uid.clone(), ev.calendar.clone());
        let old = self.events.insert(uid, Rc::new(ev));
        self.write(&cal);
        if let Some(o) = old.filter(|o| o.calendar != cal) {
            self.write(&o.calendar);
        }
    }

    pub fn remove(&mut self, uid: &str) -> Option<Rc<Event>> {
        let ev = self.events.remove(uid)?;
        self.write(&ev.calendar);
        Some(ev)
    }

    pub fn visible_events(&self, hidden: &[String]) -> Vec<Rc<Event>> {
        self.events.values().filter(|e| !hidden.contains(&e.calendar)).cloned().collect()
    }

    /// Add the events of an .ics text to a calendar (same UID: replaced).
    pub fn import_text(&mut self, text: &str, cal_id: &str) -> usize {
        let (info, events) = ics::parse(text, cal_id);
        if let Some(cal) = self.calendars.iter_mut().find(|c| c.id == cal_id) {
            // zone definitions the imported events refer to
            for b in info.extra {
                if b.first().is_some_and(|l| l.eq_ignore_ascii_case("BEGIN:VTIMEZONE")) && !cal.extra.contains(&b) {
                    cal.extra.push(b);
                }
            }
        }
        let n = events.len();
        for ev in events {
            if let Some(old) = self.events.get(&ev.uid).filter(|o| o.calendar != cal_id).cloned() {
                self.events.remove(&ev.uid);
                self.write(&old.calendar);
            }
            self.events.insert(ev.uid.clone(), Rc::new(ev));
        }
        if n > 0 {
            self.write(cal_id);
        }
        n
    }
}

impl Drop for Store {
    fn drop(&mut self) {
        self.flush(); // quitting waits for the last write
    }
}

/// "work-2" -> "Work-2" (str.title() on a file's name).
fn title_case(s: &str) -> String {
    let mut out = String::new();
    let mut up = true;
    for c in s.chars() {
        if c.is_alphabetic() {
            if up {
                out.extend(c.to_uppercase());
            } else {
                out.extend(c.to_lowercase());
            }
            up = false;
        } else {
            out.push(c);
            up = true;
        }
    }
    out
}

/// The folder: on trial its own copy of the Python Calendar's files, made once.
pub fn data_dir() -> PathBuf {
    let own = sonata_config::own_data_dir("calendar");
    let shared = sonata_config::data_dir("calendar");
    if own != shared && is_empty_dir(&own) {
        for e in std::fs::read_dir(&shared).into_iter().flatten().flatten() {
            let n = e.file_name();
            if n.to_string_lossy().ends_with(".ics") {
                let _ = std::fs::copy(e.path(), own.join(n));
            }
        }
    }
    own
}

fn is_empty_dir(p: &Path) -> bool {
    std::fs::read_dir(p).map(|mut r| r.next().is_none()).unwrap_or(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ics::{at, date};

    fn occ(uid: &str, s: DT, e: DT) -> Occurrence {
        Occurrence { event: Rc::new(Event::new(uid, s, e)), start: s, end: e }
    }

    #[test]
    fn columns_side_by_side() {
        let e = |u: &str, h, m, h2, m2| occ(u, at(2026, 9, 30, h, m), at(2026, 9, 30, h2, m2));
        let (a, b, c, d) = (e("a", 9, 0, 10, 0), e("b", 9, 30, 10, 30), e("c", 10, 0, 11, 0), e("d", 12, 0, 13, 0));
        let cols = columns(&[d.clone(), c.clone(), b.clone(), a.clone()]);
        assert_eq!(cols[&a.key()], (0, 2));
        assert_eq!(cols[&b.key()], (1, 2));
        assert_eq!(cols[&c.key()], (0, 2));
        assert_eq!(cols[&d.key()], (0, 1));
        let (x, y, z) = (e("x", 14, 0, 16, 0), e("y", 14, 0, 15, 0), e("z", 14, 30, 15, 30));
        let mut v: Vec<(usize, usize)> = columns(&[x, y, z]).into_values().collect();
        v.sort();
        assert_eq!(v, [(0, 3), (1, 3), (2, 3)]);
    }

    #[test]
    fn lanes_take_the_first_free_one() {
        let base = ics::midnight(date(2026, 9, 1));
        let ev = |u: &str, a: i64, b: i64| {
            let mut o = occ(u, base + Duration::days(a - 1), base + Duration::days(b - 1));
            Rc::make_mut(&mut o.event).all_day = true;
            o
        };
        let (long, one, two) = (ev("l", 28, 31), ev("o", 27, 28), ev("t", 29, 30));
        let out: HashMap<String, (i64, i64, usize)> = lanes(&[one, two, long], date(2026, 9, 27), 7).into_iter().map(|(o, s, e, l)| (o.event.uid.clone(), (s, e, l))).collect();
        assert_eq!(out, HashMap::from([("o".into(), (0, 0, 0)), ("l".into(), (1, 3, 0)), ("t".into(), (2, 2, 1))]));
    }

    #[test]
    fn month_grids() {
        let g = month_grid(2026, 9, 6); // Sunday first
        assert_eq!((g.len(), g[0], g[41]), (42, date(2026, 8, 30), date(2026, 10, 10)));
        assert_eq!(month_grid(2026, 9, 0)[0], date(2026, 8, 31)); // Monday first
        assert_eq!(month_grid(2026, 2, 6)[0], date(2026, 2, 1)); // starts on a Sunday
        assert!(month_grid(2027, 5, 6).iter().step_by(7).all(|d| weekday(*d) == 6));
        assert!(locale_first_weekday() < 7);
        assert_eq!(week_start(date(2026, 9, 30), 6), date(2026, 9, 27));
    }

    #[test]
    fn store_round_trip() {
        sonata_i18n::use_language("en");
        let dir = tempfile::tempdir().unwrap();
        let mut st = Store::new(dir.path().into());
        st.load();
        assert_eq!(st.calendars.iter().map(|c| (c.name.as_str(), c.color.as_str())).collect::<Vec<_>>(), [("Home", "blue"), ("Work", "red")]);
        let mut ev = Event::new("u1", at(2026, 10, 2, 11, 0), at(2026, 10, 2, 12, 0));
        ev.summary = "Dentist".into();
        ev.calendar = "home".into();
        st.put(ev.clone());
        let mut moved = ev;
        moved.calendar = "work".into();
        st.put(moved);
        st.update_calendar("work", Some("Office"), Some("purple"));
        let n = st.import_text("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:i1\nSUMMARY:Imported\nDTSTART:20261001T100000\nEND:VEVENT\nEND:VCALENDAR\n", "home");
        assert_eq!(n, 1);
        let mut again = Store::new(dir.path().into());
        again.load();
        assert_eq!(again.events["u1"].calendar, "work");
        assert!(!std::fs::read_to_string(dir.path().join("home.ics")).unwrap().contains("Dentist"));
        assert_eq!(again.calendar("work").map(|c| (c.name.as_str(), c.color.as_str())), Some(("Office", "purple")));
        assert!(again.events.contains_key("i1"));
        again.delete_calendar("work");
        assert!(!dir.path().join("work.ics").exists());
        assert_eq!(color_from_hex("#34C759"), "green");
        assert_eq!(color_from_hex("nonsense"), "blue");
    }

    #[test]
    fn import_replaces_same_uid_and_ids_stay_unique() {
        let dir = tempfile::tempdir().unwrap();
        let mut st = Store::new(dir.path().into());
        st.load();
        let first = st.calendars[0].id.clone();
        let a = st.add_calendar(&st.calendars[0].name.clone(), None);
        assert_ne!(a.id, first);
        let mut old = Event::new("dup", at(2026, 1, 1, 9, 0), at(2026, 1, 1, 10, 0));
        old.summary = "Old".into();
        old.calendar = first;
        st.put(old);
        assert_eq!(st.import_text("BEGIN:VEVENT\nUID:dup\nSUMMARY:New\nDTSTART:20260101T090000\nEND:VEVENT\n", &a.id), 1);
        let mut st2 = Store::new(dir.path().into());
        st2.load();
        assert_eq!((st2.events["dup"].summary.as_str(), st2.events["dup"].calendar.as_str()), ("New", a.id.as_str()));
        // a modified instance must not wipe out its series
        st2.import_text("BEGIN:VEVENT\nUID:s\nSUMMARY:Series\nDTSTART:20261001T090000\nDURATION:PT1H\nRRULE:FREQ=DAILY\nEND:VEVENT\nBEGIN:VEVENT\nUID:s\nRECURRENCE-ID:20261003T090000\nSUMMARY:Moved one\nDTSTART:20261003T120000\nDURATION:PT1H\nEND:VEVENT\n", &a.id);
        assert_eq!(st2.events["s"].freq.as_deref(), Some("DAILY"));
        assert_eq!(st2.events["s"].overrides.len(), 1);
    }

    #[test]
    fn async_writes_keep_order_and_a_delete_wins() {
        let dir = tempfile::tempdir().unwrap();
        let mut st = Store::new(dir.path().into());
        st.load();
        st.write_async();
        let cal = st.calendars[0].id.clone();
        for i in 0..20 {
            let mut e = Event::new("o", at(2026, 1, 1, 9, 0), at(2026, 1, 1, 10, 0));
            e.summary = format!("v{i}");
            e.calendar = cal.clone();
            st.put(e);
        }
        let temp = st.add_calendar("Temp", None);
        let mut t = Event::new("t", at(2026, 1, 1, 9, 0), at(2026, 1, 1, 10, 0));
        t.calendar = temp.id.clone();
        st.put(t);
        st.delete_calendar(&temp.id);
        st.flush();
        assert!(std::fs::read_to_string(dir.path().join(format!("{cal}.ics"))).unwrap().contains("SUMMARY:v19"));
        assert!(!dir.path().join(format!("{}.ics", temp.id)).exists());
    }

    #[test]
    fn slugs_and_titles() {
        assert_eq!(slug("Café & Work!"), "caf-work");
        assert_eq!(slug("***"), "calendar");
        assert_eq!(title_case("work-2"), "Work-2");
    }
}
