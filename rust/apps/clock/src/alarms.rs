//! Clock's alarms (sonata2/clock/alarms.py): the list in
//! `~/.local/share/sonata2-data/clock/alarms.json` -- the same file the
//! Python Clock edits and the menu bar rings from (Vini's call: both Clocks
//! share it, so alarms set here ring). No GTK here.
//!
//! alarm = {"id", "hour", "minute", "label", "repeat": [weekday, ...] (0 =
//! Monday; empty: once), "enabled", "snooze": bool, "sound": SOUNDS key}
//!
//! When an alarm goes off next is here too (next_time, due, next_any): the
//! menu bar rings them (sonata-topbar's alarms.rs). Times are naive local
//! wall-clock times, as alarms.py's datetimes are.

use chrono::{Datelike, Duration, NaiveDateTime, Timelike};
use serde_json::{json, Map, Value};
use sonata_i18n::{gettext, n_, tr};
use std::path::PathBuf;

pub const FILE: &str = "alarms.json";

/// The alarm's sound: key, name shown, file in data/sounds. Soft ones first.
pub const SOUNDS: [(&str, &str, &str); 6] = [
    ("morning", n_!("Morning"), "sonata-alarm-morning"),
    ("chimes", n_!("Chimes"), "sonata-alarm-chimes"),
    ("harp", n_!("Harp"), "sonata-alarm-harp"),
    ("sunrise", n_!("Sunrise"), "sonata-alarm-sunrise"),
    ("pulse", n_!("Pulse"), "sonata-alarm-pulse"),
    ("classic", n_!("Classic"), "alarm-clock-elapsed"),
];
pub const SOUND: &str = "morning";
/// iPhone / macOS
pub const SNOOZE_MIN: i64 = 9;
/// An alarm nobody stops goes quiet after this.
pub const RING_MAX_S: u32 = 15 * 60;

pub const WEEKDAYS: [&str; 7] = [
    n_!("Monday"),
    n_!("Tuesday"),
    n_!("Wednesday"),
    n_!("Thursday"),
    n_!("Friday"),
    n_!("Saturday"),
    n_!("Sunday"),
];

pub fn is_sound(key: &str) -> bool {
    SOUNDS.iter().any(|s| s.0 == key)
}

/// Weekday i (0 = Monday) in the session's language.
pub fn day_name(i: usize) -> String {
    gettext(WEEKDAYS[i % 7])
}

/// "Mon" / "Seg": the first three letters of the day's name.
pub fn day_abbr(i: usize) -> String {
    day_name(i).chars().take(3).collect()
}

pub fn path() -> PathBuf {
    sonata_config::data_dir("clock").join(FILE)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Alarm {
    pub id: Value,
    pub hour: u32,
    pub minute: u32,
    pub label: String,
    pub repeat: Vec<u8>,
    pub enabled: bool,
    pub snooze: bool,
    pub sound: String,
    /// keys this version doesn't know: kept as they were
    pub extra: Map<String, Value>,
}

fn random_id() -> String {
    // uuid4().hex[:12]: 6 random bytes
    let mut b = [0u8; 6];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = f.read_exact(&mut b);
    }
    if b == [0; 6] {
        let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
        b.copy_from_slice(&t.to_le_bytes()[..6]);
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

impl Alarm {
    pub fn new(hour: u32, minute: u32) -> Self {
        Alarm {
            id: Value::String(random_id()),
            hour,
            minute,
            label: tr!("Alarm"),
            repeat: Vec::new(),
            enabled: true,
            snooze: true,
            sound: SOUND.into(),
            extra: Map::new(),
        }
    }

    pub fn same_id(&self, other: &Value) -> bool {
        &self.id == other
    }

    /// In alarms.py's key order, extra keys after.
    pub fn to_value(&self) -> Value {
        let mut m = Map::new();
        m.insert("id".into(), self.id.clone());
        m.insert("hour".into(), json!(self.hour));
        m.insert("minute".into(), json!(self.minute));
        m.insert("label".into(), json!(self.label));
        m.insert("repeat".into(), json!(self.repeat));
        m.insert("enabled".into(), json!(self.enabled));
        m.insert("snooze".into(), json!(self.snooze));
        m.insert("sound".into(), json!(self.sound));
        for (k, v) in &self.extra {
            m.insert(k.clone(), v.clone());
        }
        Value::Object(m)
    }
}

/// Python's int(): a whole number, a float cut toward zero, a numeric
/// string, a bool. None where int() raises.
fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python's truthiness.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// An alarm as stored, made safe (alarms.py's _clean); None for junk.
pub fn clean(v: &Value) -> Option<Alarm> {
    let a = v.as_object()?;
    let mut out = Alarm::new(7, 0);
    out.extra = a
        .iter()
        .filter(|(k, _)| !["id", "hour", "minute", "label", "repeat", "enabled", "snooze", "sound"].contains(&k.as_str()))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    if let Some(id) = a.get("id") {
        out.id = id.clone();
    }
    if let Some(h) = a.get("hour") {
        out.hour = py_int(h)?.rem_euclid(24) as u32;
    }
    if let Some(m) = a.get("minute") {
        out.minute = py_int(m)?.rem_euclid(60) as u32;
    }
    let mut days: Vec<u8> = Vec::new();
    match a.get("repeat") {
        None | Some(Value::Null) => {}
        Some(r) if !truthy(r) => {}
        Some(Value::Array(items)) => {
            for d in items {
                let d = py_int(d)?;
                if (0..=6).contains(&d) && !days.contains(&(d as u8)) {
                    days.push(d as u8);
                }
            }
        }
        Some(_) => return None, // not a list: alarms.py's int() / iteration fails
    }
    days.sort_unstable();
    out.repeat = days;
    if let Some(e) = a.get("enabled") {
        out.enabled = truthy(e);
    }
    if let Some(s) = a.get("snooze") {
        out.snooze = truthy(s);
    }
    out.label = match a.get("label") {
        Some(Value::String(s)) => s.clone(),
        Some(v) if truthy(v) => v.to_string(),
        Some(_) => String::new(),
        None => out.label,
    };
    out.sound = match a.get("sound").and_then(Value::as_str) {
        Some(s) if is_sound(s) => s.to_string(),
        _ => SOUND.to_string(),
    };
    Some(out)
}

fn sort(alarms: &mut [Alarm]) {
    alarms.sort_by_key(|a| (a.hour, a.minute)); // stable, as Python's sorted()
}

/// The alarms, cleaned and in time order; none when the file is missing or damaged.
pub fn load() -> Vec<Alarm> {
    let Some(data) = std::fs::read(path()).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) else {
        return Vec::new();
    };
    // {} or "alarms": null read as no alarms
    let mut alarms: Vec<Alarm> = data
        .get("alarms")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(clean).collect())
        .unwrap_or_default();
    sort(&mut alarms);
    alarms
}

/// The same text alarms.py writes (indent 1), unique temp + rename: the
/// Clock window and the menu bar may write at once.
pub fn save(alarms: &[Alarm]) -> std::io::Result<()> {
    let data = json!({"alarms": alarms.iter().map(Alarm::to_value).collect::<Vec<_>>()});
    sonata_config::atomic_write(&path(), sonata_config::to_python_json_indent(&data, 1).as_bytes(), true)
}

/// "Every day", "Weekdays", "Weekends", "Mon, Wed" -- or "" for once.
pub fn repeat_text(repeat: &[u8]) -> String {
    let mut r = repeat.to_vec();
    r.sort_unstable();
    if r.len() == 7 {
        return tr!("Every day");
    }
    if r == [0, 1, 2, 3, 4] {
        return tr!("Weekdays");
    }
    if r == [5, 6] {
        return tr!("Weekends");
    }
    r.iter().map(|d| day_abbr(*d as usize)).collect::<Vec<_>>().join(", ")
}

pub fn time_text(a: &Alarm, h24: bool) -> String {
    if h24 {
        return format!("{:02}:{:02}", a.hour, a.minute);
    }
    let h = if a.hour % 12 == 0 { 12 } else { a.hour % 12 };
    format!("{h}:{:02} {}", a.minute, if a.hour < 12 { "AM" } else { "PM" })
}

/// The menu bar's clock format is 24-hour (%H, %-H, %k: topbar_prefs.is_24h).
pub fn is_24h(fmt: &str) -> bool {
    let b = fmt.as_bytes();
    (0..b.len()).any(|i| {
        b[i] == b'%' && {
            let j = if b.get(i + 1).is_some_and(|c| b"-_0^#".contains(c)) { i + 2 } else { i + 1 };
            matches!(b.get(j), Some(b'H') | Some(b'k'))
        }
    })
}

/// The clock format the menu bar shows (topbar.json; Python's default).
pub fn h24() -> bool {
    let cfg = sonata_config::load("topbar", &json!({"clock_format": "%a %-d %b  %H:%M"}));
    match cfg.get("clock_format").and_then(Value::as_str) {
        Some(f) if !f.is_empty() => is_24h(f),
        _ => is_24h("%H"),
    }
}

/// The local wall-clock time of a Unix time (what Python's
/// datetime.fromtimestamp gives).
pub fn local(epoch: f64) -> NaiveDateTime {
    let secs = epoch.floor() as libc::time_t;
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe {
        libc::localtime_r(&secs, &mut tm);
    }
    let nanos = ((epoch - epoch.floor()) * 1e9) as u32;
    chrono::NaiveDate::from_ymd_opt(tm.tm_year + 1900, (tm.tm_mon + 1) as u32, tm.tm_mday as u32)
        .and_then(|d| d.and_hms_nano_opt(tm.tm_hour as u32, tm.tm_min as u32, (tm.tm_sec as u32).min(59), nanos))
        .unwrap_or_default()
}

/// Now, local (datetime.now()).
pub fn now() -> NaiveDateTime {
    local(crate::timers::now())
}

/// When the alarm goes off next, strictly after `after` (None when off).
pub fn next_time(a: &Alarm, after: NaiveDateTime) -> Option<NaiveDateTime> {
    if !a.enabled {
        return None;
    }
    let base = after.with_second(0)?.with_nanosecond(0)?;
    for days in 0..8 {
        let day = (base + Duration::days(days)).date();
        let t = day.and_hms_opt(a.hour, a.minute, 0)?;
        if t <= after {
            continue;
        }
        if a.repeat.is_empty() || a.repeat.contains(&(day.weekday().num_days_from_monday() as u8)) {
            return Some(t);
        }
    }
    None
}

/// Alarms going off in (since, now] -- a suspend in between: still rung
/// when the computer is back (at most once each).
pub fn due(alarms: &[Alarm], since: NaiveDateTime, now: NaiveDateTime) -> Vec<Alarm> {
    alarms.iter().filter(|a| next_time(a, since).is_some_and(|t| t <= now)).cloned().collect()
}

pub fn next_any(alarms: &[Alarm], after: NaiveDateTime) -> Option<NaiveDateTime> {
    alarms.iter().filter_map(|a| next_time(a, after)).min()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testenv;

    fn at(y: i32, mo: u32, d: u32, h: u32, mi: u32, s: u32) -> NaiveDateTime {
        chrono::NaiveDate::from_ymd_opt(y, mo, d).unwrap().and_hms_opt(h, mi, s).unwrap()
    }

    /// 2026-09-28 06:00, a Monday (tests/test_clock.py's MON).
    fn mon() -> NaiveDateTime {
        at(2026, 9, 28, 6, 0, 0)
    }

    fn alarm_on(h: u32, m: u32, repeat: &[u8], enabled: bool) -> Alarm {
        let mut a = Alarm::new(h, m);
        a.repeat = repeat.to_vec();
        a.enabled = enabled;
        a
    }

    #[test]
    fn test_next_time() {
        let _e = testenv::env();
        assert_eq!(next_time(&alarm_on(7, 0, &[], true), mon()), Some(at(2026, 9, 28, 7, 0, 0)));
        assert_eq!(next_time(&alarm_on(5, 0, &[], true), mon()), Some(at(2026, 9, 29, 5, 0, 0)));
        let sat = next_time(&alarm_on(7, 0, &[5, 6], true), mon()).unwrap();
        assert_eq!(sat.weekday(), chrono::Weekday::Sat);
        assert_eq!(next_time(&alarm_on(7, 0, &[], false), mon()), None);
        // 07:00:30: today's has gone off
        assert_eq!(next_time(&alarm_on(7, 0, &[], true), at(2026, 9, 28, 7, 0, 30)).unwrap().date(), at(2026, 9, 29, 0, 0, 0).date());
    }

    /// Asleep from 6:00 to 8:00: the 7:00 alarm rings when the computer is back.
    #[test]
    fn test_due_after_a_suspend() {
        let _e = testenv::env();
        let a = alarm_on(7, 0, &[], true);
        assert_eq!(due(&[a.clone()], mon(), at(2026, 9, 28, 8, 0, 0)), vec![a.clone()]);
        assert!(due(&[a], at(2026, 9, 28, 7, 1, 0), at(2026, 9, 28, 8, 0, 0)).is_empty());
    }

    /// A one-time alarm rings later today, else tomorrow; exactly now is not 'after'.
    #[test]
    fn test_one_time_today_or_tomorrow() {
        let _e = testenv::env();
        let m = at(2026, 10, 5, 8, 30, 15);
        assert_eq!(next_time(&alarm_on(9, 0, &[], true), m), Some(at(2026, 10, 5, 9, 0, 0)));
        assert_eq!(next_time(&alarm_on(8, 0, &[], true), m), Some(at(2026, 10, 6, 8, 0, 0)));
        assert_eq!(next_time(&alarm_on(9, 0, &[], true), at(2026, 10, 5, 9, 0, 0)), Some(at(2026, 10, 6, 9, 0, 0)));
    }

    /// Repeating alarms skip to their weekday, wrapping past Sunday to the same day next week.
    #[test]
    fn test_repeat_days_and_week_wrap() {
        let _e = testenv::env();
        let m = at(2026, 10, 5, 8, 30, 15);
        assert_eq!(next_time(&alarm_on(7, 0, &[0], true), m), Some(at(2026, 10, 12, 7, 0, 0)));
        assert_eq!(next_time(&alarm_on(7, 0, &[5, 6], true), m), Some(at(2026, 10, 10, 7, 0, 0)));
        assert_eq!(next_time(&alarm_on(7, 0, &[0], false), m), None);
    }

    /// Alarms in (since, now] are due once; next_any picks the soonest enabled one.
    #[test]
    fn test_due_over_a_suspend_and_next_any() {
        let _e = testenv::env();
        let m = at(2026, 10, 5, 8, 30, 15);
        let (a, b, c) = (alarm_on(9, 0, &[], true), alarm_on(23, 0, &[], true), alarm_on(9, 30, &[], false));
        assert_eq!(due(&[a.clone(), b.clone(), c.clone()], m, at(2026, 10, 5, 12, 0, 0)), vec![a]);
        assert_eq!(next_any(&[b, c.clone()], m), Some(at(2026, 10, 5, 23, 0, 0)));
        assert_eq!(next_any(&[c], m), None);
    }

    #[test]
    fn local_time_of_now() {
        // the same wall clock as libc's, to the second
        let t = 1_790_000_000.25;
        let l = local(t);
        assert_eq!(l.nanosecond(), 250_000_000);
        assert_eq!(local(t + 60.0) - l, Duration::seconds(60));
    }

    fn alarm(h: u32, m: u32) -> Alarm {
        let mut a = Alarm::new(h, m);
        a.label = String::new();
        a
    }

    #[test]
    fn texts() {
        let _e = testenv::env();
        assert_eq!(repeat_text(&[0, 1, 2, 3, 4]), "Weekdays");
        assert_eq!(repeat_text(&[5, 6]), "Weekends");
        assert_eq!(repeat_text(&[0, 1, 2, 3, 4, 5, 6]), "Every day");
        assert_eq!(repeat_text(&[0, 2]), "Mon, Wed");
        assert_eq!(repeat_text(&[]), "");
        assert_eq!(time_text(&alarm(7, 5), true), "07:05");
        assert_eq!(time_text(&alarm(19, 5), false), "7:05 PM");
        assert_eq!(time_text(&alarm(0, 0), false), "12:00 AM");
    }

    #[test]
    fn storage_cleans() {
        let _e = testenv::env();
        std::fs::write(path(), r#"{"alarms": [{"hour": "25", "minute": 61, "repeat": [9, 1, 1]}, "junk", {"hour": "x"}]}"#).unwrap();
        let got = load();
        assert_eq!(got.len(), 1);
        assert_eq!((got[0].hour, got[0].minute, got[0].repeat.clone()), (1, 1, vec![1]));
        assert_eq!(got[0].sound, SOUND); // old alarms get the default
    }

    #[test]
    fn missing_or_empty_file_is_no_alarms() {
        let _e = testenv::env();
        assert!(load().is_empty());
        std::fs::write(path(), r#"{"alarms": null}"#).unwrap();
        assert!(load().is_empty());
    }

    #[test]
    fn saved_text_is_what_alarms_py_writes() {
        let _e = testenv::env();
        let a = Alarm {
            id: json!("abc"),
            hour: 7,
            minute: 30,
            label: "Café".into(),
            repeat: vec![0, 4],
            enabled: true,
            snooze: false,
            sound: "harp".into(),
            extra: Map::new(),
        };
        save(&[a.clone()]).unwrap();
        // json.dumps({"alarms": [a]}, indent=1)
        let want = "{\n \"alarms\": [\n  {\n   \"id\": \"abc\",\n   \"hour\": 7,\n   \"minute\": 30,\n   \"label\": \"Caf\\u00e9\",\n   \"repeat\": [\n    0,\n    4\n   ],\n   \"enabled\": true,\n   \"snooze\": false,\n   \"sound\": \"harp\"\n  }\n ]\n}";
        assert_eq!(std::fs::read_to_string(path()).unwrap(), want);
        assert_eq!(load(), vec![a]);
    }

    #[test]
    fn unknown_keys_are_kept() {
        let _e = testenv::env();
        std::fs::write(path(), r#"{"alarms": [{"id": 5, "hour": 6, "minute": 0, "future": [1]}]}"#).unwrap();
        let a = load().remove(0);
        assert_eq!(a.id, json!(5));
        assert_eq!(a.to_value()["future"], json!([1]));
    }

    #[test]
    fn clock_format_is_24h() {
        assert!(is_24h("%a %-d %b  %H:%M"));
        assert!(is_24h("%k:%M"));
        assert!(is_24h("%-H:%M"));
        assert!(!is_24h("%a %-I:%M %p"));
        assert!(!is_24h(""));
    }
}
