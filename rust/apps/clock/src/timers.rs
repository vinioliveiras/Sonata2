//! Clock's stopwatch and timer (sonata2/clock/timers.py), in
//! `~/.local/share/sonata2-data/clock/timers.json` -- shared with the Python
//! Clock; the menu bar rings the timer, so both keep going with Clock
//! closed. Times are wall-clock seconds: a running stopwatch or timer is its
//! start / end, so nothing ticks while nobody looks. No GTK here.

use crate::alarms;
use serde_json::{json, Map, Value};
use std::path::PathBuf;

pub const FILE: &str = "timers.json";
/// 1, 5, 10, 15, 30 min, 1 h
pub const PRESETS: [u32; 6] = [60, 300, 600, 900, 1800, 3600];
pub const SOUND: &str = "chimes";

pub fn path() -> PathBuf {
    sonata_config::data_dir("clock").join(FILE)
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Stopwatch {
    pub running: bool,
    /// when it last started
    pub since: f64,
    /// seconds counted before that
    pub before: f64,
    pub laps: Vec<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimerState {
    Idle,
    Running,
    Paused,
}

impl TimerState {
    fn name(self) -> &'static str {
        match self {
            TimerState::Idle => "idle",
            TimerState::Running => "running",
            TimerState::Paused => "paused",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Timer {
    pub duration: f64,
    pub label: String,
    pub sound: String,
    /// running: when it rings
    pub ends: f64,
    /// paused: seconds left
    pub left: f64,
    pub state: TimerState,
}

impl Default for Stopwatch {
    fn default() -> Self {
        Stopwatch { running: false, since: 0.0, before: 0.0, laps: Vec::new() }
    }
}

impl Default for Timer {
    fn default() -> Self {
        Timer { duration: 300.0, label: String::new(), sound: SOUND.into(), ends: 0.0, left: 0.0, state: TimerState::Idle }
    }
}

/// timers.py's _num: a number >= 0, `default` where float() fails.
fn num(v: Option<&Value>, default: f64) -> f64 {
    let f = match v {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::Bool(b)) => Some(*b as u8 as f64),
        Some(Value::String(s)) => s.trim().parse().ok(),
        None => return default,
        _ => None,
    };
    f.filter(|f| !f.is_nan()).map(|f| f.max(0.0)).unwrap_or(default)
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        _ => false,
    }
}

/// (stopwatch, timer), cleaned: a damaged file reads as new ones.
pub fn load() -> (Stopwatch, Timer) {
    let data = std::fs::read(path()).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()).unwrap_or(Value::Null);
    let empty = Map::new();
    let sw = data.get("stopwatch").and_then(Value::as_object);
    let sw = match sw {
        None => Stopwatch::default(),
        Some(s) => Stopwatch {
            running: truthy(s.get("running")),
            since: num(s.get("since"), 0.0),
            before: num(s.get("before"), 0.0),
            laps: s.get("laps").and_then(Value::as_array).map(|l| l.iter().map(|x| num(Some(x), 0.0)).collect()).unwrap_or_default(),
        },
    };
    let t = data.get("timer").and_then(Value::as_object).unwrap_or(&empty);
    let tm = if data.get("timer").and_then(Value::as_object).is_none() {
        Timer::default()
    } else {
        let d = num(t.get("duration"), 300.0);
        Timer {
            duration: if d == 0.0 { 300.0 } else { d },
            label: match t.get("label") {
                Some(Value::String(s)) => s.clone(),
                Some(v) if truthy(Some(v)) => v.to_string(),
                _ => String::new(),
            },
            sound: t.get("sound").and_then(Value::as_str).filter(|s| alarms::is_sound(s)).unwrap_or(SOUND).to_string(),
            ends: num(t.get("ends"), 0.0),
            left: num(t.get("left"), 0.0),
            state: match t.get("state").and_then(Value::as_str) {
                Some("running") => TimerState::Running,
                Some("paused") => TimerState::Paused,
                _ => TimerState::Idle,
            },
        }
    };
    (sw, tm)
}

/// timers.py's text: json.dumps({"stopwatch": sw, "timer": tm}, indent=1).
pub fn to_value(sw: &Stopwatch, tm: &Timer) -> Value {
    let mut s = Map::new();
    s.insert("running".into(), json!(sw.running));
    s.insert("since".into(), json!(sw.since));
    s.insert("before".into(), json!(sw.before));
    s.insert("laps".into(), json!(sw.laps));
    let mut t = Map::new();
    t.insert("duration".into(), json!(tm.duration));
    t.insert("label".into(), json!(tm.label));
    t.insert("sound".into(), json!(tm.sound));
    t.insert("ends".into(), json!(tm.ends));
    t.insert("left".into(), json!(tm.left));
    t.insert("state".into(), json!(tm.state.name()));
    let mut m = Map::new();
    m.insert("stopwatch".into(), Value::Object(s));
    m.insert("timer".into(), Value::Object(t));
    Value::Object(m)
}

pub fn save(sw: &Stopwatch, tm: &Timer) -> std::io::Result<()> {
    let text = sonata_config::to_python_json_indent(&to_value(sw, tm), 1);
    sonata_config::atomic_write(&path(), text.as_bytes(), true)
}

// -- stopwatch -----------------------------------------------------------------------------
pub fn elapsed(sw: &Stopwatch, now: f64) -> f64 {
    sw.before + if sw.running { (now - sw.since).max(0.0) } else { 0.0 }
}

pub fn sw_start(sw: &mut Stopwatch, now: f64) {
    if !sw.running {
        sw.running = true;
        sw.since = now;
    }
}

pub fn sw_stop(sw: &mut Stopwatch, now: f64) {
    if sw.running {
        sw.before = elapsed(sw, now);
        sw.running = false;
        sw.since = 0.0;
    }
}

/// A lap: the time since the last one (laps are kept oldest first).
pub fn sw_lap(sw: &mut Stopwatch, now: f64) {
    if sw.running {
        let lap = elapsed(sw, now) - sw.laps.iter().sum::<f64>();
        sw.laps.push(lap);
    }
}

pub fn sw_reset(sw: &mut Stopwatch) {
    *sw = Stopwatch::default();
}

pub fn current_lap(sw: &Stopwatch, now: f64) -> f64 {
    elapsed(sw, now) - sw.laps.iter().sum::<f64>()
}

/// Indexes of the fastest and slowest finished laps (None under two laps:
/// nothing to compare, like macOS). The first one wins a tie (list.index).
pub fn best_worst(laps: &[f64]) -> (Option<usize>, Option<usize>) {
    if laps.len() < 2 {
        return (None, None);
    }
    let (mut best, mut worst) = (0, 0);
    for (i, l) in laps.iter().enumerate() {
        if *l < laps[best] {
            best = i;
        }
        if *l > laps[worst] {
            worst = i;
        }
    }
    (Some(best), Some(worst))
}

/// 01:23,45 -- minutes, seconds, hundredths (hours in front past one).
pub fn stopwatch_text(seconds: f64) -> String {
    let cs = (seconds * 100.0) as i64;
    let (h, rest) = (cs / 360000, cs % 360000);
    let (m, rest) = (rest / 6000, rest % 6000);
    let (s, c) = (rest / 100, rest % 100);
    format!("{}{m:02}:{s:02},{c:02}", if h > 0 { format!("{h}:") } else { String::new() })
}

// -- timer --------------------------------------------------------------------------------------
pub fn left(tm: &Timer, now: f64) -> f64 {
    match tm.state {
        TimerState::Running => (tm.ends - now).max(0.0),
        TimerState::Paused => tm.left,
        TimerState::Idle => tm.duration,
    }
}

pub fn tm_start(tm: &mut Timer, duration: Option<f64>, label: Option<&str>, now: f64) {
    if let Some(d) = duration {
        tm.duration = d;
    }
    if let Some(l) = label {
        tm.label = l.to_string();
    }
    tm.state = TimerState::Running;
    tm.ends = now + tm.duration;
    tm.left = 0.0;
}

pub fn tm_pause(tm: &mut Timer, now: f64) {
    if tm.state == TimerState::Running {
        tm.left = left(tm, now);
        tm.state = TimerState::Paused;
        tm.ends = 0.0;
    }
}

pub fn tm_resume(tm: &mut Timer, now: f64) {
    if tm.state == TimerState::Paused {
        tm.state = TimerState::Running;
        tm.ends = now + tm.left;
        tm.left = 0.0;
    }
}

pub fn tm_cancel(tm: &mut Timer) {
    tm.state = TimerState::Idle;
    tm.ends = 0.0;
    tm.left = 0.0;
}

pub fn due(tm: &Timer, now: f64) -> bool {
    tm.state == TimerState::Running && left(tm, now) <= 0.0
}

/// What's left of the timer, 1 (all) -> 0 (rings).
pub fn progress(tm: &Timer, now: f64) -> f64 {
    if tm.duration > 0.0 { (left(tm, now) / tm.duration).min(1.0) } else { 0.0 }
}

/// 06:32, or 1:06:32 past an hour; rounded up (macOS: 00:01 until it rings).
pub fn timer_text(seconds: f64) -> String {
    let s = seconds.ceil() as i64;
    let (h, rest) = (s / 3600, s % 3600);
    let (m, s) = (rest / 60, rest % 60);
    if h > 0 { format!("{h}:{m:02}:{s:02}") } else { format!("{m:02}:{s:02}") }
}

/// 5 min, 1 h, 1 h 30 min, 45 s.
pub fn duration_text(seconds: f64) -> String {
    let s = seconds as i64;
    let (h, rest) = (s / 3600, s % 3600);
    let (m, s) = (rest / 60, rest % 60);
    let mut parts = Vec::new();
    if h > 0 {
        parts.push(format!("{h} h"));
    }
    if m > 0 {
        parts.push(format!("{m} min"));
    }
    if s > 0 && h == 0 {
        parts.push(format!("{s} s"));
    }
    if parts.is_empty() { "0 s".into() } else { parts.join(" ") }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testenv;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-7
    }

    #[test]
    fn runs_stops_and_laps() {
        let mut sw = Stopwatch::default();
        sw_start(&mut sw, 100.0);
        assert!(close(elapsed(&sw, 103.5), 3.5));
        sw_lap(&mut sw, 103.5);
        sw_lap(&mut sw, 105.0);
        assert_eq!(sw.laps, vec![3.5, 1.5]);
        assert!(close(current_lap(&sw, 106.0), 1.0));
        sw_stop(&mut sw, 110.0);
        assert!(close(elapsed(&sw, 999.0), 10.0)); // stopped: no longer counts
        sw_start(&mut sw, 200.0);
        assert!(close(elapsed(&sw, 202.0), 12.0)); // goes on from there
        sw_stop(&mut sw, 203.0);
        sw_lap(&mut sw, 203.0);
        assert_eq!(sw.laps.len(), 2); // no lap while stopped
        sw_reset(&mut sw);
        assert_eq!(sw, Stopwatch::default());
    }

    #[test]
    fn best_and_worst() {
        assert_eq!(best_worst(&[5.0]), (None, None));
        assert_eq!(best_worst(&[5.0, 3.0, 8.0]), (Some(1), Some(2)));
    }

    #[test]
    fn texts() {
        assert_eq!(stopwatch_text(83.456), "01:23,45");
        assert_eq!(stopwatch_text(3725.5), "1:02:05,50");
        assert_eq!(timer_text(392.2), "06:33"); // rounded up
        assert_eq!(timer_text(3600.0), "1:00:00");
        assert_eq!(duration_text(300.0), "5 min");
        assert_eq!(duration_text(5400.0), "1 h 30 min");
        assert_eq!(duration_text(45.0), "45 s");
    }

    #[test]
    fn start_pause_resume_cancel() {
        let mut tm = Timer::default();
        tm_start(&mut tm, Some(60.0), Some("Pasta"), 1000.0);
        assert_eq!(left(&tm, 1015.0), 45.0);
        assert!(close(progress(&tm, 1015.0), 0.75));
        tm_pause(&mut tm, 1015.0);
        assert_eq!(left(&tm, 5000.0), 45.0); // paused: stays
        tm_resume(&mut tm, 2000.0);
        assert!(!due(&tm, 2044.0));
        assert!(due(&tm, 2045.0));
        tm_cancel(&mut tm);
        assert_eq!((tm.state, left(&tm, 0.0)), (TimerState::Idle, 60.0)); // ready again with the same time
    }

    #[test]
    fn storage_survives_damage() {
        let _e = testenv::env();
        std::fs::write(path(), r#"{"stopwatch": {"laps": "x", "since": "y"}, "timer": {"state": "boom", "duration": -3}}"#).unwrap();
        let (mut sw, tm) = load();
        assert!(sw.laps.is_empty());
        assert_eq!(tm.state, TimerState::Idle);
        assert!(tm.duration > 0.0);
        sw.running = true;
        sw.since = 5.0;
        save(&sw, &tm).unwrap();
        assert_eq!(load().0.since, 5.0);
    }

    #[test]
    fn saved_text_is_what_timers_py_writes() {
        let _e = testenv::env();
        let sw = Stopwatch { running: true, since: 1730000000.25, before: 0.0, laps: vec![3.5] };
        let mut tm = Timer::default();
        tm.label = "Eggs".into();
        save(&sw, &tm).unwrap();
        // json.dumps({"stopwatch": sw, "timer": tm}, indent=1), floats as Python writes them
        let want = "{\n \"stopwatch\": {\n  \"running\": true,\n  \"since\": 1730000000.25,\n  \"before\": 0.0,\n  \"laps\": [\n   3.5\n  ]\n },\n \"timer\": {\n  \"duration\": 300.0,\n  \"label\": \"Eggs\",\n  \"sound\": \"chimes\",\n  \"ends\": 0.0,\n  \"left\": 0.0,\n  \"state\": \"idle\"\n }\n}";
        assert_eq!(std::fs::read_to_string(path()).unwrap(), want);
    }
}
