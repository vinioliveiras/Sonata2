//! A minimal iCalendar (RFC 5545) reader/writer for Calendar, and
//! recurrence expansion (calendar/ics.py). No GTK.
//!
//! Supported subset: VCALENDAR with X-WR-CALNAME / X-APPLE-CALENDAR-COLOR /
//! X-SONATA-COLOR, VEVENT with UID, SUMMARY, LOCATION, DESCRIPTION,
//! DTSTART / DTEND (floating local time, UTC "Z" or TZID -- both converted
//! to local time -- or VALUE=DATE), DURATION (when there is no DTEND),
//! RRULE FREQ / INTERVAL / UNTIL / COUNT (+ WEEKLY BYDAY), EXDATE,
//! RECURRENCE-ID (a changed instance, kept with its series) and one VALARM
//! TRIGGER (relative to the start). Times are kept as naive local
//! datetimes; an event's zone (UTC or TZID) is remembered, so a series
//! repeats in that zone (right across DST changes) and is written back in
//! it. Anything else -- other RRULE parts, properties, components
//! (VTIMEZONE, ATTENDEE...) -- is kept verbatim and written back. An
//! all-day event runs from midnight of its first day to midnight after
//! its last.

use chrono::{Datelike, Duration, NaiveDate, NaiveDateTime, Timelike};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

pub type DT = NaiveDateTime;

pub const FREQS: [&str; 4] = ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"];
pub const WEEKDAYS: [&str; 7] = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
const RRULE_KNOWN: [&str; 4] = ["FREQ", "INTERVAL", "COUNT", "UNTIL"];
/// properties parse() turns into fields (DTSTAMP is written fresh); the rest is kept verbatim
const EVENT_KNOWN: [&str; 11] = ["UID", "SUMMARY", "LOCATION", "DESCRIPTION", "DTSTART", "DTEND", "DURATION", "RRULE", "EXDATE", "RECURRENCE-ID", "DTSTAMP"];
const CAL_KNOWN: [&str; 7] = ["VERSION", "PRODID", "CALSCALE", "METHOD", "X-WR-CALNAME", "X-SONATA-COLOR", "X-APPLE-CALENDAR-COLOR"];

pub fn date(y: i32, m: u32, d: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(y, m, d).expect("a real date")
}

pub fn at(y: i32, m: u32, d: u32, h: u32, mi: u32) -> DT {
    date(y, m, d).and_hms_opt(h, mi, 0).expect("a real time")
}

pub fn midnight(d: NaiveDate) -> DT {
    d.and_hms_opt(0, 0, 0).unwrap()
}

#[derive(Clone, Debug, PartialEq)]
pub struct Event {
    pub uid: String,
    pub summary: String,
    pub start: DT,
    pub end: DT,
    pub all_day: bool,
    pub location: String,
    pub description: String,
    /// one of FREQS, None = no repeat
    pub freq: Option<String>,
    pub interval: i64,
    /// inclusive
    pub until: Option<DT>,
    pub count: Option<i64>,
    pub exdates: Vec<DT>,
    /// minutes before the start (0 = at the time), None = no alert
    pub alarm: Option<i64>,
    /// the calendar's id (its file name); not written to the file
    pub calendar: String,
    /// DTSTART's zone ("UTC" or a TZID), None = floating local time
    pub tzid: Option<String>,
    /// other RRULE parts ("BYDAY=MO,WE"), kept...
    pub rrule_extra: Vec<String>,
    /// ... for this FREQ (dropped when it changes)
    pub rrule_extra_freq: Option<String>,
    /// unknown properties/components, verbatim
    pub extra: Vec<String>,
    /// set on a changed instance of a series
    pub recurrence_id: Option<DT>,
    /// a series' changed instances
    pub overrides: Vec<Event>,
}

impl Event {
    pub fn new(uid: &str, start: DT, end: DT) -> Self {
        Event {
            uid: uid.into(),
            summary: "New Event".into(),
            start,
            end,
            all_day: false,
            location: String::new(),
            description: String::new(),
            freq: None,
            interval: 1,
            until: None,
            count: None,
            exdates: Vec::new(),
            alarm: None,
            calendar: String::new(),
            tzid: None,
            rrule_extra: Vec::new(),
            rrule_extra_freq: None,
            extra: Vec::new(),
            recurrence_id: None,
            overrides: Vec::new(),
        }
    }

    pub fn duration(&self) -> Duration {
        self.end - self.start
    }
}

/// One occurrence of an event in a view (recurring events have many).
#[derive(Clone, Debug)]
pub struct Occurrence {
    pub event: Rc<Event>,
    pub start: DT,
    pub end: DT,
}

impl Occurrence {
    pub fn key(&self) -> (String, DT) {
        (self.event.uid.clone(), self.start)
    }

    pub fn duration(&self) -> Duration {
        self.end - self.start
    }
}

impl PartialEq for Occurrence {
    fn eq(&self, other: &Self) -> bool {
        self.event.uid == other.event.uid && self.start == other.start
    }
}

impl Eq for Occurrence {}

impl std::hash::Hash for Occurrence {
    fn hash<H: std::hash::Hasher>(&self, h: &mut H) {
        self.event.uid.hash(h);
        self.start.hash(h);
    }
}

pub fn new_uid() -> String {
    glib::uuid_string_random().to_uppercase()
}

// -- zones ------------------------------------------------------------------------------------
thread_local! {
    static ZONES: RefCell<HashMap<String, Option<glib::TimeZone>>> = RefCell::new(HashMap::new());
    /// the local zone (tests set their own)
    static LOCAL: RefCell<Option<glib::TimeZone>> = const { RefCell::new(None) };
}

fn local_zone() -> glib::TimeZone {
    LOCAL.with(|l| l.borrow().clone()).unwrap_or_else(glib::TimeZone::local)
}

/// Run `f` with `tz` as the local zone (tests: `with_local("Europe/Berlin", ..)`).
pub fn with_local<R>(tz: &str, f: impl FnOnce() -> R) -> R {
    let old = LOCAL.with(|l| l.replace(glib::TimeZone::from_identifier(Some(tz))));
    let r = f();
    LOCAL.with(|l| l.replace(old));
    r
}

fn is_utc(tzid: &str) -> bool {
    matches!(tzid.to_uppercase().as_str(), "UTC" | "Z" | "GMT" | "ETC/UTC")
}

/// The zone of a name ("UTC" or IANA), None when unknown/floating.
pub fn zone(tzid: Option<&str>) -> Option<glib::TimeZone> {
    let tzid = tzid.filter(|t| !t.is_empty())?;
    if is_utc(tzid) {
        return Some(glib::TimeZone::utc());
    }
    ZONES.with(|z| {
        z.borrow_mut()
            .entry(tzid.to_string())
            .or_insert_with(|| {
                // (None for a name it can't find or load)
                glib::TimeZone::from_identifier(Some(tzid.trim_start_matches('/')))
            })
            .clone()
    })
}

fn gdt(tz: &glib::TimeZone, d: DT) -> Option<glib::DateTime> {
    let secs = d.second() as f64 + d.nanosecond() as f64 / 1e9;
    glib::DateTime::new(tz, d.year(), d.month() as i32, d.day() as i32, d.hour() as i32, d.minute() as i32, secs).ok()
}

fn naive(g: &glib::DateTime) -> DT {
    let (y, m, d) = g.ymd();
    date(y, m as u32, d as u32).and_hms_micro_opt(g.hour() as u32, g.minute() as u32, g.second() as u32, g.microsecond() as u32).unwrap()
}

fn convert(d: DT, from: &glib::TimeZone, to: &glib::TimeZone) -> DT {
    gdt(from, d).and_then(|g| g.to_timezone(to).ok()).map(|g| naive(&g)).unwrap_or(d)
}

/// A naive local time as the wall time in tzid (unchanged when floating/unknown).
pub fn to_zone(d: DT, tzid: Option<&str>) -> DT {
    match zone(tzid) {
        Some(z) => convert(d, &local_zone(), &z),
        None => d,
    }
}

/// A wall time in tzid as a naive local time.
pub fn from_zone(d: DT, tzid: Option<&str>) -> DT {
    match zone(tzid) {
        Some(z) => convert(d, &z, &local_zone()),
        None => d,
    }
}

// -- reading ----------------------------------------------------------------------------------
pub fn unfold(text: &str) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    for raw in text.replace("\r\n", "\n").replace('\r', "\n").split('\n') {
        if (raw.starts_with(' ') || raw.starts_with('\t')) && !lines.is_empty() {
            lines.last_mut().unwrap().push_str(&raw[1..]);
        } else if !raw.is_empty() {
            lines.push(raw.to_string());
        }
    }
    lines
}

/// 'NAME;P1=a;P2="b:c":value' -> ('NAME', {'P1': 'a', 'P2': 'b:c'}, 'value').
pub fn split(line: &str) -> (String, HashMap<String, String>, String) {
    let mut quoted = false;
    let mut cut = line.len();
    for (i, c) in line.char_indices() {
        if c == '"' {
            quoted = !quoted;
        } else if c == ':' && !quoted {
            cut = i;
            break;
        }
    }
    let head = &line[..cut];
    let value = line.get(cut + 1..).unwrap_or("").to_string();
    // the head's parts: split at ; outside quotes
    let mut parts = Vec::new();
    let mut cur = String::new();
    let mut q = false;
    for c in head.chars() {
        match c {
            '"' => {
                q = !q;
                cur.push(c);
            }
            ';' if !q => {
                if !cur.is_empty() {
                    parts.push(std::mem::take(&mut cur));
                }
            }
            _ => cur.push(c),
        }
    }
    if !cur.is_empty() {
        parts.push(cur);
    }
    let name = parts.first().map(|p| p.to_uppercase()).unwrap_or_default();
    let mut params = HashMap::new();
    for p in parts.iter().skip(1) {
        let (k, v) = p.split_once('=').unwrap_or((p, ""));
        params.insert(k.to_uppercase(), v.trim_matches('"').to_string());
    }
    (name, params, value)
}

pub fn unescape(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    let mut it = v.chars();
    while let Some(c) = it.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('n' | 'N') => out.push('\n'),
            Some('\n') => {
                out.push('\\');
                out.push('\n');
            }
            Some(o) => out.push(o),
            None => out.push('\\'),
        }
    }
    out
}

pub fn escape(v: &str) -> String {
    v.replace('\\', "\\\\").replace(';', "\\;").replace(',', "\\,").replace('\n', "\\n")
}

/// (naive local datetime, is_date, zone name or None).
fn parse_dt(value: &str, params: &HashMap<String, String>) -> Result<(DT, bool, Option<String>), ()> {
    let value = value.trim();
    let is_date_param = params.get("VALUE").is_some_and(|v| v.eq_ignore_ascii_case("DATE"));
    if is_date_param || (value.len() == 8 && value.bytes().all(|b| b.is_ascii_digit())) {
        let d = NaiveDate::parse_from_str(value.get(..8).ok_or(())?, "%Y%m%d").map_err(|_| ())?;
        return Ok((midnight(d), true, None));
    }
    let body = value.trim_end_matches('Z');
    let body = body.get(..15.min(body.len())).unwrap_or(body);
    let d = NaiveDateTime::parse_from_str(body, "%Y%m%dT%H%M%S").map_err(|_| ())?;
    let tzid = if value.ends_with('Z') { Some("UTC".to_string()) } else { params.get("TZID").filter(|t| !t.is_empty()).cloned() };
    // unknown zone: the wall time is kept
    Ok((from_zone(d, tzid.as_deref()), false, tzid))
}

/// (naive local datetime, is_date).
pub fn parse_datetime(value: &str, params: &HashMap<String, String>) -> Result<(DT, bool), ()> {
    parse_dt(value, params).map(|(d, b, _)| (d, b))
}

pub fn parse_duration(value: &str) -> Option<Duration> {
    let v = value.trim().to_uppercase();
    let (neg, rest) = match v.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, v.strip_prefix('+').unwrap_or(&v)),
    };
    let rest = rest.strip_prefix('P')?;
    let (date_part, time_part) = match rest.split_once('T') {
        Some((d, t)) => (d, Some(t)),
        None => (rest, None),
    };
    let mut total = 0i64;
    // the units in their order, each at most once (the Python regex)
    let mut take = |s: &str, units: &[(char, i64)]| -> Option<()> {
        let mut s = s;
        let mut idx = 0;
        while !s.is_empty() {
            let n_len = s.bytes().take_while(u8::is_ascii_digit).count();
            if n_len == 0 {
                return None;
            }
            let n: i64 = s[..n_len].parse().ok()?;
            let unit = s[n_len..].chars().next()?;
            let pos = units.iter().skip(idx).position(|u| u.0 == unit)? + idx;
            total += n * units[pos].1;
            idx = pos + 1;
            s = &s[n_len + 1..];
        }
        Some(())
    };
    take(date_part, &[('W', 7 * 86400), ('D', 86400)])?;
    if let Some(t) = time_part {
        take(t, &[('H', 3600), ('M', 60), ('S', 1)])?;
    }
    let d = Duration::seconds(total);
    Some(if neg { -d } else { d })
}

pub fn format_duration(td: Duration) -> String {
    let secs = td.num_seconds();
    let sign = if secs < 0 { "-" } else { "" };
    let secs = secs.abs();
    if secs == 0 {
        "PT0S".into()
    } else if secs % 86400 == 0 {
        format!("{sign}P{}D", secs / 86400)
    } else if secs % 3600 == 0 {
        format!("{sign}PT{}H", secs / 3600)
    } else {
        format!("{sign}PT{}M", secs / 60)
    }
}

/// The calendar-level facts of a file.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Info {
    pub name: Option<String>,
    pub color: Option<String>,
    pub hex: Option<String>,
    /// the file's other blocks (VTIMEZONE...), kept
    pub extra: Vec<Vec<String>>,
}

struct Building {
    ev: Event,
    start: Option<DT>,
    end: Option<DT>,
}

/// -> (the calendar's info, its events). A changed instance (RECURRENCE-ID)
/// goes into its series' `overrides`.
pub fn parse(text: &str, calendar: &str) -> (Info, Vec<Event>) {
    let mut info = Info::default();
    let mut events = Vec::new();
    let mut ev: Option<Building> = None;
    let mut alarm: Option<Vec<String>> = None; // lines of the VALARM being read
    let mut alarm_used = false; // ... it became ev.alarm
    let mut foreign: Option<Vec<String>> = None; // lines of an unknown component being read
    let mut depth = 0i32;
    let mut dur: Option<Duration> = None;
    for line in unfold(text) {
        let (name, params, value) = split(&line);
        let upper = value.trim().to_uppercase();
        if let Some(f) = foreign.as_mut() {
            // inside an unknown component: keep it whole
            f.push(line.clone());
            depth += match name.as_str() {
                "BEGIN" => 1,
                "END" => -1,
                _ => 0,
            };
            if depth == 0 {
                let f = foreign.take().unwrap();
                match ev.as_mut() {
                    Some(b) => b.ev.extra.extend(f),
                    None => info.extra.push(f),
                }
            }
            continue;
        }
        if name == "BEGIN" && upper == "VEVENT" {
            let mut e = Event::new("", midnight(date(1970, 1, 1)), midnight(date(1970, 1, 1)));
            e.summary = String::new();
            ev = Some(Building { ev: e, start: None, end: None });
            dur = None;
            alarm = None;
            continue;
        }
        if name == "BEGIN" && upper == "VCALENDAR" {
            continue;
        }
        if name == "BEGIN" && upper == "VALARM" && ev.is_some() && alarm.is_none() {
            alarm = Some(vec![line.clone()]);
            alarm_used = false;
            continue;
        }
        if name == "END" && upper == "VALARM" && alarm.is_some() {
            let mut a = alarm.take().unwrap();
            a.push(line.clone());
            if !alarm_used {
                // a second alert, or one Calendar can't show: kept
                if let Some(b) = ev.as_mut() {
                    b.ev.extra.extend(a);
                }
            }
            continue;
        }
        if name == "BEGIN" {
            foreign = Some(vec![line.clone()]);
            depth = 1;
            continue;
        }
        if name == "END" && upper == "VEVENT" && ev.is_some() {
            let b = ev.take().unwrap();
            if let Some(start) = b.start {
                let mut e = b.ev;
                e.start = start;
                e.end = b.end.unwrap_or_else(|| start + dur.unwrap_or(if e.all_day { Duration::days(1) } else { Duration::zero() }));
                if e.end < e.start {
                    e.end = e.start;
                }
                if e.uid.is_empty() {
                    e.uid = new_uid();
                }
                e.calendar = calendar.to_string();
                events.push(e);
            }
            continue;
        }
        if name == "END" {
            continue;
        }
        let Some(b) = ev.as_mut() else {
            match name.as_str() {
                "X-WR-CALNAME" => info.name = Some(unescape(&value)),
                "X-SONATA-COLOR" => info.color = Some(value.trim().to_lowercase()),
                "X-APPLE-CALENDAR-COLOR" => {
                    if info.hex.is_none() {
                        info.hex = Some(value.trim().chars().take(7).collect::<String>().to_lowercase());
                    }
                }
                n if !CAL_KNOWN.contains(&n) => info.extra.push(vec![line.clone()]),
                _ => {}
            }
            continue;
        };
        if let Some(a) = alarm.as_mut() {
            a.push(line.clone());
            let absolute = params.get("VALUE").is_some_and(|v| v.eq_ignore_ascii_case("DATE-TIME"));
            if name == "TRIGGER" && b.ev.alarm.is_none() && !absolute {
                let related_start = params.get("RELATED").is_none_or(|r| r.eq_ignore_ascii_case("START"));
                if let (Some(td), true) = (parse_duration(&value), related_start) {
                    b.ev.alarm = Some(0.max((-td.num_seconds()).div_euclid(60)));
                    alarm_used = true;
                }
            }
            continue;
        }
        let ok = (|| -> Result<(), ()> {
            match name.as_str() {
                "UID" => b.ev.uid = value.trim().to_string(),
                "SUMMARY" => b.ev.summary = unescape(&value),
                "LOCATION" => b.ev.location = unescape(&value),
                "DESCRIPTION" => b.ev.description = unescape(&value),
                "DTSTART" => {
                    let (d, all_day, tz) = parse_dt(&value, &params)?;
                    b.start = Some(d);
                    b.ev.all_day = all_day;
                    b.ev.tzid = tz;
                }
                "DTEND" => b.end = Some(parse_datetime(&value, &params)?.0),
                "DURATION" => dur = parse_duration(&value),
                "RRULE" => parse_rrule(&mut b.ev, &value)?,
                "EXDATE" => {
                    for v in value.split(',') {
                        b.ev.exdates.push(parse_datetime(v, &params)?.0);
                    }
                }
                "RECURRENCE-ID" => b.ev.recurrence_id = Some(parse_datetime(&value, &params)?.0),
                n if !EVENT_KNOWN.contains(&n) => b.ev.extra.push(line.clone()),
                _ => {}
            }
            Ok(())
        })();
        if ok.is_err() && !EVENT_KNOWN.contains(&name.as_str()) {
            b.ev.extra.push(line.clone()); // a malformed property: skip it, keep the event
        }
    }
    (info, attach_overrides(events))
}

/// Changed instances (RECURRENCE-ID) join their series; one without a
/// series stays a plain event.
fn attach_overrides(events: Vec<Event>) -> Vec<Event> {
    let masters: HashSet<String> = events.iter().filter(|e| e.recurrence_id.is_none()).map(|e| e.uid.clone()).collect();
    let mut out: Vec<Event> = Vec::new();
    let mut pending: Vec<Event> = Vec::new();
    for e in events {
        if e.recurrence_id.is_some() && masters.contains(&e.uid) {
            pending.push(e);
        } else {
            out.push(e);
        }
    }
    for o in pending {
        // the last series with that UID (a dict by UID in Python: the last one wins)
        if let Some(m) = out.iter_mut().rev().find(|m| m.recurrence_id.is_none() && m.uid == o.uid) {
            m.overrides.retain(|x| x.recurrence_id != o.recurrence_id);
            m.overrides.push(o);
        }
    }
    out
}

fn parse_rrule(ev: &mut Event, value: &str) -> Result<(), ()> {
    let mut parts: Vec<(String, String)> = Vec::new();
    for p in value.to_uppercase().split(';') {
        if let Some((k, v)) = p.split_once('=') {
            match parts.iter_mut().find(|x| x.0 == k) {
                Some(x) => x.1 = v.to_string(), // a dict: the last one wins, in its first place
                None => parts.push((k.to_string(), v.to_string())),
            }
        }
    }
    let get = |k: &str| parts.iter().find(|x| x.0 == k).map(|x| x.1.clone());
    let Some(freq) = get("FREQ").filter(|f| FREQS.contains(&f.as_str())) else { return Ok(()) };
    let interval = match get("INTERVAL") {
        Some(i) if !i.is_empty() => i.parse::<i64>().map_err(|_| ())?,
        _ => 1,
    };
    let count = match get("COUNT") {
        Some(c) => Some(c.parse::<i64>().map_err(|_| ())?.max(1)),
        None => None,
    };
    let until = match get("UNTIL") {
        Some(u) => {
            let (d, is_date) = parse_datetime(&u, &HashMap::new())?;
            Some(if is_date { d + Duration::days(1) - Duration::microseconds(1) } else { d })
        }
        None => None,
    };
    ev.freq = Some(freq.clone());
    ev.interval = interval.max(1);
    if count.is_some() {
        ev.count = count;
    }
    if until.is_some() {
        ev.until = until;
    }
    ev.rrule_extra = parts.iter().filter(|(k, _)| !RRULE_KNOWN.contains(&k.as_str())).map(|(k, v)| format!("{k}={v}")).collect();
    ev.rrule_extra_freq = if ev.rrule_extra.is_empty() { None } else { Some(freq) };
    Ok(())
}

// -- writing ----------------------------------------------------------------------------------
/// Lines longer than 75 octets continue on the next line after a space.
pub fn fold(line: &str) -> String {
    if line.len() <= 75 {
        return line.to_string();
    }
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    for ch in line.chars() {
        let limit = if out.is_empty() { 75 } else { 74 };
        if cur.len() + ch.len_utf8() > limit {
            out.push(std::mem::take(&mut cur));
        }
        cur.push(ch);
    }
    out.push(cur);
    out.join("\r\n ")
}

fn f(d: DT, pattern: &str) -> String {
    d.format(pattern).to_string()
}

fn dtprop(name: &str, d: DT, all_day: bool, tzid: Option<&str>) -> String {
    if all_day {
        return format!("{name};VALUE=DATE:{}", f(d, "%Y%m%d"));
    }
    let Some(tz) = tzid.filter(|t| !t.is_empty()) else { return format!("{name}:{}", f(d, "%Y%m%dT%H%M%S")) };
    let wall = to_zone(d, Some(tz)); // an unknown zone: d already is its wall time
    if is_utc(tz) {
        return format!("{name}:{}", f(wall, "%Y%m%dT%H%M%SZ"));
    }
    format!("{name};TZID={tz}:{}", f(wall, "%Y%m%dT%H%M%S"))
}

fn until_text(ev: &Event, until: DT) -> String {
    if ev.all_day {
        // RFC 5545: a DATE when DTSTART is a DATE
        return f(until, "%Y%m%d");
    }
    match zone(ev.tzid.as_deref()) {
        // with a zone, UNTIL is in UTC
        Some(_) => f(convert(until, &local_zone(), &glib::TimeZone::utc()), "%Y%m%dT%H%M%SZ"),
        None => f(until, "%Y%m%dT%H%M%S"),
    }
}

pub fn event_lines(ev: &Event, stamp: &str) -> Vec<String> {
    let tz = ev.tzid.as_deref();
    let mut lines = vec!["BEGIN:VEVENT".to_string(), format!("UID:{}", ev.uid), format!("DTSTAMP:{stamp}")];
    if let Some(r) = ev.recurrence_id {
        lines.push(dtprop("RECURRENCE-ID", r, ev.all_day, tz));
    }
    lines.push(dtprop("DTSTART", ev.start, ev.all_day, tz));
    lines.push(dtprop("DTEND", ev.end, ev.all_day, tz));
    lines.push(format!("SUMMARY:{}", escape(&ev.summary)));
    if !ev.location.is_empty() {
        lines.push(format!("LOCATION:{}", escape(&ev.location)));
    }
    if !ev.description.is_empty() {
        lines.push(format!("DESCRIPTION:{}", escape(&ev.description)));
    }
    if let Some(freq) = ev.freq.as_deref() {
        let mut rule = format!("FREQ={freq}");
        if ev.interval > 1 {
            rule.push_str(&format!(";INTERVAL={}", ev.interval));
        }
        if let Some(c) = ev.count.filter(|c| *c != 0) {
            rule.push_str(&format!(";COUNT={c}"));
        } else if let Some(u) = ev.until {
            rule.push_str(&format!(";UNTIL={}", until_text(ev, u)));
        }
        if !ev.rrule_extra.is_empty() && ev.rrule_extra_freq.as_deref() == Some(freq) {
            rule.push(';');
            rule.push_str(&ev.rrule_extra.join(";"));
        }
        lines.push(format!("RRULE:{rule}"));
        if !ev.exdates.is_empty() {
            let parts: Vec<String> = ev.exdates.iter().enumerate().map(|(i, d)| {
                let p = dtprop("EXDATE", *d, ev.all_day, tz);
                if i == 0 { p } else { p.split_once(':').map(|x| x.1.to_string()).unwrap_or_default() }
            }).collect();
            lines.push(parts.join(","));
        }
    }
    lines.extend(ev.extra.iter().cloned());
    if let Some(a) = ev.alarm {
        lines.extend(["BEGIN:VALARM".into(), "ACTION:DISPLAY".into(), format!("DESCRIPTION:{}", escape(&ev.summary)), format!("TRIGGER:{}", format_duration(Duration::minutes(-a))), "END:VALARM".into()]);
    }
    lines.push("END:VEVENT".into());
    if ev.freq.is_some() {
        for o in &ev.overrides {
            lines.extend(event_lines(o, stamp));
        }
    }
    lines
}

/// extra: calendar-level blocks kept from the file.
pub fn serialize<'a>(events: impl IntoIterator<Item = &'a Event>, name: &str, color: &str, hex_color: &str, extra: &[Vec<String>]) -> String {
    let stamp = glib::DateTime::now_utc().and_then(|d| d.format("%Y%m%dT%H%M%SZ")).map(|s| s.to_string()).unwrap_or_default();
    let mut lines: Vec<String> = vec!["BEGIN:VCALENDAR".into(), "VERSION:2.0".into(), "PRODID:-//Sonata//Calendar//EN".into(), "CALSCALE:GREGORIAN".into()];
    if !name.is_empty() {
        lines.push(format!("X-WR-CALNAME:{}", escape(name)));
    }
    if !color.is_empty() {
        lines.push(format!("X-SONATA-COLOR:{color}"));
    }
    if !hex_color.is_empty() {
        lines.push(format!("X-APPLE-CALENDAR-COLOR:{}", hex_color.to_uppercase()));
    }
    for block in extra {
        lines.extend(block.iter().cloned());
    }
    for ev in events {
        lines.extend(event_lines(ev, &stamp));
    }
    lines.push("END:VCALENDAR".into());
    lines.iter().map(|l| fold(l)).collect::<Vec<_>>().join("\r\n") + "\r\n"
}

// -- recurrence -------------------------------------------------------------------------------
/// d moved by whole months; None when that month has no such day (RFC
/// 5545: the 31st repeats only in months with a 31st).
fn add_months(d: DT, months: i64) -> Option<DT> {
    let total = d.month0() as i64 + months;
    let y = d.year() as i64 + total.div_euclid(12);
    let m = total.rem_euclid(12) as u32 + 1;
    NaiveDate::from_ymd_opt(y as i32, m, d.day()).map(|nd| nd.and_time(d.time()))
}

/// The n-th candidate start of the rule (None = skipped: no such date),
/// counted from `start`.
pub fn nth(ev: &Event, n: i64, start: DT) -> Option<DT> {
    let k = n * ev.interval;
    match ev.freq.as_deref() {
        Some("DAILY") => Some(start + Duration::days(k)),
        Some("WEEKLY") => Some(start + Duration::weeks(k)),
        Some("MONTHLY") => add_months(start, k),
        // 29 February in a common year: None
        _ => NaiveDate::from_ymd_opt(start.year() + k as i32, start.month(), start.day()).map(|d| d.and_time(start.time())),
    }
}

/// (weekdays, week start) of a WEEKLY rule with a plain BYDAY, else None
/// (other extra parts: repeat as if only FREQ/INTERVAL were there).
fn weekly_days(ev: &Event) -> Option<(Vec<i64>, i64)> {
    if ev.freq.as_deref() != Some("WEEKLY") || ev.rrule_extra.is_empty() || ev.rrule_extra_freq != ev.freq {
        return None;
    }
    let mut days: Option<Vec<i64>> = None;
    let mut wkst = 0;
    let idx = |c: &str| WEEKDAYS.iter().position(|w| *w == c).map(|i| i as i64);
    for p in &ev.rrule_extra {
        let (k, v) = p.split_once('=').unwrap_or((p, ""));
        if k == "BYDAY" && v.split(',').all(|c| idx(c).is_some()) {
            let mut d: Vec<i64> = v.split(',').filter_map(idx).collect();
            d.sort();
            d.dedup();
            days = Some(d);
        } else if k == "WKST" && idx(v).is_some() {
            wkst = idx(v).unwrap();
        } else {
            return None;
        }
    }
    days.filter(|d| !d.is_empty()).map(|d| (d, wkst))
}

fn weekday(d: DT) -> i64 {
    d.weekday().num_days_from_monday() as i64
}

/// Candidate starts (wall times in the event's zone) of the n-th period.
fn period(ev: &Event, n: i64, anchor: DT, byday: &Option<(Vec<i64>, i64)>) -> Vec<DT> {
    let Some((days, wkst)) = byday else { return nth(ev, n, anchor).into_iter().collect() };
    let week = anchor - Duration::days((weekday(anchor) - wkst).rem_euclid(7)) + Duration::weeks(n * ev.interval);
    let mut sorted = days.clone();
    sorted.sort_by_key(|d| (d - wkst).rem_euclid(7));
    sorted.into_iter().map(|d| week + Duration::days((d - wkst).rem_euclid(7))).filter(|s| *s >= anchor).collect()
}

/// Whole days in a span, rounded down (Python's timedelta.days).
fn days_floor(d: Duration) -> i64 {
    d.num_seconds().div_euclid(86400)
}

/// Occurrences of ev overlapping [a, b) (an empty event counts when it
/// starts inside), its changed instances included.
pub fn occurrences(ev: &Rc<Event>, a: DT, b: DT) -> Vec<Occurrence> {
    let mut out = series(ev, a, b);
    if ev.freq.is_some() {
        for o in &ev.overrides {
            out.extend(series(&Rc::new(o.clone()), a, b));
        }
    }
    out
}

fn series(ev: &Rc<Event>, a: DT, b: DT) -> Vec<Occurrence> {
    let dur = ev.end - ev.start;
    let hit = |s: DT| {
        let e = s + dur;
        s < b && (e > a || (e == s && s >= a))
    };
    let mut out = Vec::new();
    let Some(freq) = ev.freq.as_deref() else {
        if hit(ev.start) {
            out.push(Occurrence { event: ev.clone(), start: ev.start, end: ev.end });
        }
        return out;
    };
    let mut ex: HashSet<DT> = ev.exdates.iter().copied().collect();
    ex.extend(ev.overrides.iter().filter_map(|o| o.recurrence_id));
    // the rule runs on the wall time of the event's zone (DST-safe), shown in local time
    let tz = ev.tzid.as_deref().filter(|t| zone(Some(t)).is_some() && !ev.all_day);
    let anchor = if tz.is_some() { to_zone(ev.start, tz) } else { ev.start };
    let byday = weekly_days(ev);
    let mut n: i64 = 0;
    if ev.count.is_none() && (freq == "DAILY" || freq == "WEEKLY") {
        // jump close to the range: no need to walk from the first occurrence
        let step = ev.interval * if freq == "DAILY" { 1 } else { 7 };
        n = 0.max(days_floor(a - dur - ev.start).div_euclid(step) - 1);
    } else if ev.count.is_none() && freq == "MONTHLY" {
        let months = (a.year() - ev.start.year()) as i64 * 12 + a.month() as i64 - ev.start.month() as i64;
        n = 0.max(months.div_euclid(ev.interval) - 2);
    } else if ev.count.is_none() {
        n = 0.max(((a.year() - ev.start.year()) as i64).div_euclid(ev.interval) - 2);
    }
    let mut produced = 0;
    let mut limit = 100_000; // safety net against broken rules
    while limit > 0 {
        limit -= 1;
        let cands = period(ev, n, anchor, &byday);
        n += 1;
        for s in cands {
            let s = if tz.is_some() { from_zone(s, tz) } else { s };
            if ev.until.is_some_and(|u| s > u) {
                return out;
            }
            if let Some(c) = ev.count {
                produced += 1;
                if produced > c {
                    return out;
                }
            }
            if s >= b {
                return out;
            }
            if !ex.contains(&s) && hit(s) {
                out.push(Occurrence { event: ev.clone(), start: s, end: s + dur });
            }
        }
    }
    out
}

pub fn expand(events: &[Rc<Event>], a: DT, b: DT) -> Vec<Occurrence> {
    let mut out: Vec<Occurrence> = events.iter().flat_map(|e| occurrences(e, a, b)).collect();
    out.sort_by(|x, y| (x.start, -x.duration(), &x.event.summary).cmp(&(y.start, -y.duration(), &y.event.summary)));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn starts(ev: &Event, a: DT, b: DT) -> Vec<DT> {
        occurrences(&Rc::new(ev.clone()), a, b).iter().map(|o| o.start).collect()
    }

    fn d(y: i32, m: u32, dd: u32) -> DT {
        midnight(date(y, m, dd))
    }

    fn p() -> HashMap<String, String> {
        HashMap::new()
    }

    fn ev(uid: &str, s: DT, e: DT) -> Event {
        Event::new(uid, s, e)
    }

    #[test]
    fn round_trip() {
        let mut a = ev("a", at(2026, 9, 30, 12, 30), at(2026, 9, 30, 13, 30));
        a.summary = "Lunch; with, Ana\\x".into();
        a.location = "Café Central".into();
        a.description = format!("Line one\nLine two {}", "é".repeat(60));
        a.alarm = Some(15);
        let mut b = ev("b", d(2026, 10, 5), d(2026, 10, 8));
        b.summary = "Holiday".into();
        b.all_day = true;
        b.freq = Some("YEARLY".into());
        b.count = Some(3);
        b.alarm = Some(0);
        let mut c = ev("c", at(2026, 9, 1, 9, 0), at(2026, 9, 1, 9, 15));
        c.summary = "Standup".into();
        c.freq = Some("WEEKLY".into());
        c.interval = 2;
        c.until = Some(date(2026, 12, 31).and_hms_opt(23, 59, 59).unwrap());
        c.exdates = vec![at(2026, 9, 15, 9, 0)];
        c.alarm = Some(1440);
        let mut evs = vec![a, b, c];
        for e in &mut evs {
            e.calendar = "home".into();
        }
        let text = serialize(&evs, "Home", "blue", "#007aff");
        assert!(text.split("\r\n").all(|l| l.len() <= 75));
        let (info, back) = parse(&text, "home");
        assert_eq!(info, Info { name: Some("Home".into()), color: Some("blue".into()), hex: Some("#007aff".into()), extra: vec![] });
        assert_eq!(back, evs);
    }

    fn serialize(evs: &[Event], name: &str, color: &str, hex: &str) -> String {
        super::serialize(evs.iter(), name, color, hex, &[])
    }

    #[test]
    fn foreign_file() {
        with_local("America/Sao_Paulo", || {
            let text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x@y\nSUMMARY:Flight\nDTSTART:20260310T080000Z\nDURATION:PT2H30M\nRRULE:FREQ=DAILY;UNTIL=20260312\nBEGIN:VALARM\nTRIGGER:-PT1H\nEND:VALARM\nEND:VEVENT\nBEGIN:VEVENT\nUID:z\nSUMMARY:Zoned\nDTSTART;TZID=Europe/Berlin:20260701T100000\nDTEND;TZID=Europe/Berlin:20260701T110000\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:No uid\nDTSTART;VALUE=DATE:20260101\nEND:VEVENT\nEND:VCALENDAR\n";
            let (_, evs) = parse(text, "");
            let (fl, z, n) = (&evs[0], &evs[1], &evs[2]);
            assert_eq!(fl.start, at(2026, 3, 10, 5, 0)); // 08:00Z in São Paulo
            assert_eq!(fl.duration(), Duration::minutes(150));
            assert_eq!(fl.alarm, Some(60));
            assert_eq!(starts(fl, d(2026, 1, 1), d(2027, 1, 1)).len(), 3); // UNTIL a date: that whole day
            assert_eq!(z.duration(), Duration::hours(1));
            assert!(n.all_day && !n.uid.is_empty() && n.end == d(2026, 1, 2));
        });
    }

    #[test]
    fn monthly_31st() {
        let mut e = ev("m", at(2026, 1, 31, 10, 0), at(2026, 1, 31, 11, 0));
        e.freq = Some("MONTHLY".into());
        e.count = Some(5);
        assert_eq!(starts(&e, d(2026, 1, 1), d(2028, 1, 1)), [1, 3, 5, 7, 8].map(|m| at(2026, m, 31, 10, 0)));
    }

    #[test]
    fn until_and_count() {
        let mut e = ev("d", at(2026, 9, 1, 9, 0), at(2026, 9, 1, 10, 0));
        e.freq = Some("DAILY".into());
        e.until = Some(at(2026, 9, 5, 9, 0));
        assert_eq!(starts(&e, d(2026, 1, 1), d(2027, 1, 1)).len(), 5);
        e.until = None;
        e.count = Some(3);
        assert_eq!(starts(&e, d(2026, 9, 2), d(2027, 1, 1)), [at(2026, 9, 2, 9, 0), at(2026, 9, 3, 9, 0)]);
        e.exdates = vec![at(2026, 9, 2, 9, 0)]; // an excluded date still counts
        assert_eq!(starts(&e, d(2026, 1, 1), d(2027, 1, 1)), [at(2026, 9, 1, 9, 0), at(2026, 9, 3, 9, 0)]);
    }

    #[test]
    fn yearly_leap_day_and_weekly_interval() {
        let mut e = ev("y", d(2024, 2, 29), d(2024, 3, 1));
        e.all_day = true;
        e.freq = Some("YEARLY".into());
        assert_eq!(starts(&e, d(2024, 1, 1), d(2033, 1, 1)), [d(2024, 2, 29), d(2028, 2, 29), d(2032, 2, 29)]);
        let mut w = ev("w", at(2026, 9, 1, 9, 0), at(2026, 9, 1, 10, 0));
        w.freq = Some("WEEKLY".into());
        w.interval = 2;
        assert_eq!(starts(&w, d(2026, 9, 1), d(2026, 10, 1)), [at(2026, 9, 1, 9, 0), at(2026, 9, 15, 9, 0), at(2026, 9, 29, 9, 0)]);
    }

    #[test]
    fn far_range_matches_walk() {
        // ranges far from the first occurrence skip ahead: same result
        for freq in FREQS {
            let mut e = ev(freq, at(2001, 1, 31, 23, 0), at(2001, 2, 1, 1, 0));
            e.freq = Some(freq.into());
            e.interval = 3;
            let (a, b) = (d(2026, 1, 1), d(2027, 1, 1));
            let mut walked = Vec::new();
            let mut n = 0;
            loop {
                let s = nth(&e, n, e.start);
                n += 1;
                let Some(s) = s else { continue };
                if s >= b {
                    break;
                }
                if s + e.duration() > a {
                    walked.push(s);
                }
            }
            assert_eq!(starts(&e, a, b), walked, "{freq}");
        }
        for (freq, interval) in [("MONTHLY", 1), ("MONTHLY", 5), ("YEARLY", 1), ("YEARLY", 3)] {
            let mut e = ev("j", at(2001, 3, 31, 8, 0), at(2001, 3, 31, 9, 0));
            e.freq = Some(freq.into());
            e.interval = interval;
            let far = starts(&e, d(2040, 1, 1), d(2045, 1, 1));
            let walked: Vec<DT> = starts(&e, d(2001, 1, 1), d(2045, 1, 1)).into_iter().filter(|s| *s >= d(2040, 1, 1)).collect();
            assert_eq!(far, walked, "{freq} {interval}");
        }
    }

    #[test]
    fn overlapping_range() {
        let e = ev("o", at(2026, 9, 29, 23, 0), at(2026, 9, 30, 1, 0));
        assert_eq!(starts(&e, d(2026, 9, 30), d(2026, 10, 1)).len(), 1); // started the day before
        assert!(starts(&e, at(2026, 9, 30, 1, 0), d(2026, 10, 1)).is_empty());
        let mut m = ev("x", d(2026, 10, 1), d(2026, 10, 4));
        m.all_day = true;
        m.freq = Some("WEEKLY".into());
        assert_eq!(starts(&m, d(2026, 10, 10), d(2026, 10, 11)), [d(2026, 10, 8)]);
    }

    #[test]
    fn exdate_and_count_with_skipped_dates() {
        let mut e = ev("d", at(2026, 10, 1, 9, 0), at(2026, 10, 1, 10, 0));
        e.freq = Some("DAILY".into());
        e.exdates = vec![at(2026, 10, 2, 9, 0)];
        assert_eq!(starts(&e, d(2026, 10, 1), d(2026, 10, 4)), [at(2026, 10, 1, 9, 0), at(2026, 10, 3, 9, 0)]);
        let mut m = ev("m", d(2026, 1, 31), at(2026, 1, 31, 1, 0));
        m.freq = Some("MONTHLY".into());
        m.count = Some(3);
        assert_eq!(starts(&m, d(2026, 1, 1), d(2030, 1, 1)), [d(2026, 1, 31), d(2026, 3, 31), d(2026, 5, 31)]);
    }

    #[test]
    fn unsupported_rrule_parts_survive_round_trip() {
        let text = "BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\nDTEND:20261005T100000\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\nEND:VEVENT\n";
        let e = parse(text, "").1.remove(0);
        assert!(serialize(&[e.clone()], "", "", "").contains("BYDAY=MO,WE,FR"));
        let mut m = e;
        m.freq = Some("MONTHLY".into()); // BYDAY belongs to the rule it came with
        assert!(!serialize(&[m], "", "", "").contains("BYDAY"));
    }

    #[test]
    fn weekly_byday_expands() {
        let text = "BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\nDTEND:20261005T100000\nRRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=4\nEND:VEVENT\n";
        let e = parse(text, "").1.remove(0);
        assert_eq!(starts(&e, d(2026, 10, 1), d(2026, 11, 1)), [5, 7, 9, 12].map(|n| at(2026, 10, n, 9, 0)));
    }

    #[test]
    fn recurring_utc_event_follows_local_dst() {
        // a weekly 13:00Z meeting is 14:00 in Berlin in winter and 15:00 after the DST switch
        with_local("Europe/Berlin", || {
            let e = parse("BEGIN:VEVENT\nUID:z\nDTSTART:20260305T130000Z\nDURATION:PT1H\nRRULE:FREQ=WEEKLY\nEND:VEVENT\n", "").1.remove(0);
            assert_eq!(e.start, at(2026, 3, 5, 14, 0));
            assert_eq!(starts(&e, d(2026, 4, 2), d(2026, 4, 3)), [at(2026, 4, 2, 15, 0)]);
        });
    }

    #[test]
    fn tzid_converted_to_local() {
        with_local("America/Sao_Paulo", || {
            let mut params = p();
            params.insert("TZID".into(), "Europe/Berlin".into());
            assert_eq!(parse_datetime("20260701T100000", &params), Ok((at(2026, 7, 1, 5, 0), false)));
            params.insert("TZID".into(), "Mars/Base".into()); // unknown: the wall time is kept
            assert_eq!(parse_datetime("20260701T100000", &params).unwrap().0, at(2026, 7, 1, 10, 0));
        });
    }

    #[test]
    fn tzid_written_back_in_its_zone() {
        with_local("America/Sao_Paulo", || {
            let text = "BEGIN:VEVENT\nUID:z\nDTSTART;TZID=Europe/Berlin:20260301T100000\nDURATION:PT1H\nRRULE:FREQ=WEEKLY;UNTIL=20260501T000000Z\nEXDATE;TZID=Europe/Berlin:20260308T100000\nEND:VEVENT\n";
            let e = parse(text, "").1.remove(0);
            let out = serialize(&[e.clone()], "", "", "");
            assert!(out.contains("DTSTART;TZID=Europe/Berlin:20260301T100000"), "{out}");
            assert!(out.contains("UNTIL=20260501T000000Z"));
            assert!(out.contains("EXDATE;TZID=Europe/Berlin:20260308T100000"));
            let back = parse(&out, "").1.remove(0);
            let (a, b) = (d(2026, 3, 1), d(2026, 5, 2));
            assert_eq!(starts(&back, a, b), starts(&e, a, b));
            // Berlin 10:00 is 06:00 in São Paulo before 29 March and 05:00 after
            assert_eq!(starts(&e, d(2026, 3, 15), d(2026, 3, 16)), [at(2026, 3, 15, 6, 0)]);
            assert_eq!(starts(&e, d(2026, 4, 5), d(2026, 4, 6)), [at(2026, 4, 5, 5, 0)]);
        });
    }

    const SERIES: &str = "BEGIN:VCALENDAR\nBEGIN:VTIMEZONE\nTZID:Custom Zone\nEND:VTIMEZONE\nBEGIN:VEVENT\nUID:s\nSUMMARY:Series\nDTSTART:20261001T090000\nDURATION:PT1H\nRRULE:FREQ=DAILY\nATTENDEE;CN=Ana:mailto:ana@x.org\nX-FOO:bar\nEND:VEVENT\nBEGIN:VEVENT\nUID:s\nRECURRENCE-ID:20261003T090000\nSUMMARY:Moved one\nDTSTART:20261003T120000\nDURATION:PT1H\nEND:VEVENT\nEND:VCALENDAR\n";

    #[test]
    fn override_replaces_its_slot() {
        let (info, evs) = parse(SERIES, "");
        assert_eq!(info.extra, vec![vec!["BEGIN:VTIMEZONE".to_string(), "TZID:Custom Zone".into(), "END:VTIMEZONE".into()]]);
        let occ = expand(&[Rc::new(evs[0].clone())], d(2026, 10, 2), d(2026, 10, 5));
        let got: Vec<(DT, &str)> = occ.iter().map(|o| (o.start, o.event.summary.as_str())).collect();
        assert_eq!(got, [(at(2026, 10, 2, 9, 0), "Series"), (at(2026, 10, 3, 12, 0), "Moved one"), (at(2026, 10, 4, 9, 0), "Series")]);
        let out = unfold(&serialize(&evs, "", "", ""));
        for line in ["ATTENDEE;CN=Ana:mailto:ana@x.org", "X-FOO:bar", "RECURRENCE-ID:20261003T090000", "SUMMARY:Moved one"] {
            assert!(out.iter().any(|l| l == line), "{line}");
        }
    }

    #[test]
    fn unknown_valarm_kept() {
        let text = "BEGIN:VEVENT\nUID:a\nDTSTART:20261001T100000\nBEGIN:VALARM\nACTION:AUDIO\nTRIGGER;VALUE=DATE-TIME:20261001T090000Z\nEND:VALARM\nEND:VEVENT\n";
        let out = unfold(&serialize(&parse(text, "").1, "", "", ""));
        assert!(out.iter().any(|l| l == "TRIGGER;VALUE=DATE-TIME:20261001T090000Z"));
        assert_eq!(out.iter().filter(|l| *l == "BEGIN:VALARM").count(), 1);
    }

    #[test]
    fn durations_and_lines() {
        assert_eq!(parse_duration("-PT1H"), Some(Duration::hours(-1)));
        assert_eq!(parse_duration("P1W2DT3H4M5S"), Some(Duration::seconds(9 * 86400 + 3 * 3600 + 4 * 60 + 5)));
        assert_eq!(parse_duration("PT1M1H"), None); // out of order
        assert_eq!(parse_duration("P"), Some(Duration::zero()));
        assert_eq!(format_duration(Duration::minutes(-1440)), "-P1D");
        assert_eq!(format_duration(Duration::minutes(-90)), "-PT90M");
        let (n, params, v) = split("DTSTART;TZID=\"A:B\";X=1:20260101T000000");
        assert_eq!((n.as_str(), params["TZID"].as_str(), params["X"].as_str(), v.as_str()), ("DTSTART", "A:B", "1", "20260101T000000"));
        assert_eq!(unescape("a\\nb\\,c\\\\d"), "a\nb,c\\d");
        let long = "X:".to_string() + &"é".repeat(80);
        assert!(fold(&long).split("\r\n").all(|l| l.len() <= 75));
        assert_eq!(unfold(&fold(&long)).concat(), long);
    }
}
