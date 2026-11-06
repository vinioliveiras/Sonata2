//! Night Shift (macOS): warmer colours at night, through wlsunset (wlr
//! gamma control; the colours reset when it exits, so the menu bar process
//! keeps it running and restarts it when nightshift.json changes) --
//! shell/nightshift.py.
//!
//! Settings: schedule off | sunset (Sunset to Sunrise, from the time zone's
//! coordinates in zone1970.tab: no location service) | custom (from/to);
//! "manual_until" = Turn On Until Tomorrow (constant warmth until 07:00
//! local); warmth 0..100 (Less Warm .. More Warm).

use chrono::{NaiveDate, NaiveDateTime, NaiveTime, Timelike};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::f64::consts::PI;
use std::process::{Child, Command, Stdio};
use std::rc::Rc;

pub const NAME: &str = "nightshift";
pub const DAY_K: i64 = 6500;
pub const RESPAWN_CHECK_S: u32 = 60;
pub const ZONE_TAB: &str = "/usr/share/zoneinfo/zone1970.tab";

pub fn defaults() -> Value {
    json!({"schedule": "off", "from": "22:00", "to": "07:00", "warmth": 50, "manual_until": ""})
}

/// 0 -> 5500 K (Less Warm), 100 -> 2700 K (More Warm).
pub fn temperature(warmth: i64) -> i64 {
    (5500.0 - (warmth.clamp(0, 100) as f64 / 100.0) * 2800.0) as i64
}

fn dms(s: &str, deg_digits: usize) -> Option<f64> {
    let sign = if s.starts_with('-') { -1.0 } else { 1.0 };
    let s = s.get(1..)?;
    let d: f64 = s.get(..deg_digits)?.parse().ok()?;
    let m: f64 = s.get(deg_digits..deg_digits + 2)?.parse().ok()?;
    let rest = s.get(deg_digits + 2..).unwrap_or("");
    let sec: f64 = if rest.is_empty() { 0.0 } else { rest.parse().ok()? };
    Some(sign * (d + m / 60.0 + sec / 3600.0))
}

/// (lat, lon) of the time zone's principal city in this zone1970.tab text.
pub fn tz_location_in(tab: &str, tz: &str) -> Option<(f64, f64)> {
    for line in tab.lines() {
        if line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() >= 3 && parts[2].trim() == tz {
            let c = parts[1];
            let cut = c.rfind('+').into_iter().chain(c.rfind('-')).max()?;
            return Some((dms(&c[..cut], 2)?, dms(&c[cut..], 3)?));
        }
    }
    None
}

/// (lat, lon) of the local time zone's principal city, or None.
pub fn tz_location() -> Option<(f64, f64)> {
    tz_location_in(&std::fs::read_to_string(ZONE_TAB).ok()?, &local_tz())
}

fn local_tz() -> String {
    let tz = std::env::var("TZ").unwrap_or_default();
    let tz = tz.trim_start_matches(':');
    if !tz.is_empty() {
        return tz.to_string();
    }
    std::fs::read_link("/etc/localtime").ok().and_then(|l| l.to_string_lossy().split_once("zoneinfo/").map(|(_, z)| z.to_string())).unwrap_or_default()
}

/// datetime.fromisoformat for what Sonata writes ("2026-11-05T07:00") and
/// its usual relatives.
pub fn parse_iso(s: &str) -> Option<NaiveDateTime> {
    for f in ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%.f"] {
        if let Ok(t) = NaiveDateTime::parse_from_str(s, f) {
            return Some(t);
        }
    }
    NaiveDate::parse_from_str(s, "%Y-%m-%d").ok().map(|d| d.and_time(NaiveTime::MIN))
}

/// The local time now (and the matching Unix time, whole seconds as time.mktime).
pub fn now_local() -> (NaiveDateTime, f64) {
    let Ok(n) = glib::DateTime::now_local() else { return (NaiveDateTime::default(), 0.0) };
    let t = NaiveDate::from_ymd_opt(n.year(), n.month() as u32, n.day_of_month() as u32)
        .and_then(|d| d.and_hms_micro_opt(n.hour() as u32, n.minute() as u32, n.second() as u32, n.microsecond() as u32))
        .unwrap_or_default();
    (t, n.to_unix() as f64)
}

fn manual_until(cfg: &Map<String, Value>) -> Option<NaiveDateTime> {
    cfg.get("manual_until").and_then(Value::as_str).and_then(parse_iso)
}

pub fn manual_active_at(cfg: &Map<String, Value>, now: NaiveDateTime) -> bool {
    manual_until(cfg).is_some_and(|u| now < u)
}

pub fn manual_active(cfg: &Map<String, Value>) -> bool {
    manual_active_at(cfg, now_local().0)
}

/// macOS: Turn On Until Tomorrow = until 7 AM.
pub fn until_tomorrow(now: NaiveDateTime) -> String {
    let mut end = now.date().and_hms_opt(7, 0, 0).unwrap_or(now);
    if end <= now {
        end += chrono::Duration::days(1);
    }
    end.format("%Y-%m-%dT%H:%M").to_string()
}

fn text<'a>(cfg: &'a Map<String, Value>, key: &str, default: &'a str) -> &'a str {
    cfg.get(key).and_then(Value::as_str).unwrap_or(default)
}

/// The wlsunset command for these settings, or [] (Night Shift off).
/// loc: the time zone's coordinates (tz_location()).
pub fn command_at(cfg: &Map<String, Value>, now: NaiveDateTime, loc: Option<(f64, f64)>) -> Vec<String> {
    let warm = temperature(cfg.get("warmth").and_then(crate::py_int).unwrap_or(50));
    let mut base: Vec<String> = vec!["wlsunset".into(), "-t".into(), warm.to_string()];
    if manual_active_at(cfg, now) {
        // constant: both ends warm
        base.extend(["-T".into(), (warm + 1).to_string()]);
        return base;
    }
    let mut sched = text(cfg, "schedule", "");
    if sched == "sunset" {
        if let Some((lat, lon)) = loc {
            base.extend(["-T".into(), DAY_K.to_string(), "-l".into(), format!("{lat:.2}"), "-L".into(), format!("{lon:.2}")]);
            return base;
        }
        sched = "custom"; // unknown time zone: 22:00-07:00
    }
    if sched == "custom" {
        // wlsunset: -S sunrise (day starts), -s sunset (night starts)
        base.extend(["-T".into(), DAY_K.to_string(), "-S".into(), text(cfg, "to", "07:00").into(), "-s".into(), text(cfg, "from", "22:00").into(), "-d".into(), "1800".into()]);
        return base;
    }
    Vec::new()
}

pub fn command(cfg: &Map<String, Value>) -> Vec<String> {
    command_at(cfg, now_local().0, tz_location())
}

fn hm(s: &str) -> Option<i64> {
    let (h, m) = s.split_once(':')?;
    Some(h.trim().parse::<i64>().ok()? * 60 + m.trim().parse::<i64>().ok()?)
}

/// (sunrise, sunset) as Unix times for that date (sunrise equation, the
/// same model wlsunset uses); infinities during polar night / day.
pub fn sun_times(lat: f64, lon: f64, day: NaiveDate) -> (f64, f64) {
    let midnight = day.and_time(NaiveTime::MIN).and_utc().timestamp() as f64;
    let jd = midnight / 86400.0 + 2440587.5; // midnight UTC: n is this day's number
    let n = (jd - 2451545.0 + 0.0008).ceil();
    let j = n - lon / 360.0;
    let m = ((357.5291 + 0.98560028 * j).rem_euclid(360.0)).to_radians();
    let c = 1.9148 * m.sin() + 0.02 * (2.0 * m).sin() + 0.0003 * (3.0 * m).sin();
    let lam = ((m.to_degrees() + c + 180.0 + 102.9372).rem_euclid(360.0)).to_radians();
    let transit = 2451545.0 + j + 0.0053 * m.sin() - 0.0069 * (2.0 * lam).sin();
    let dec = (lam.sin() * 23.4397f64.to_radians().sin()).asin();
    let phi = lat.to_radians();
    let cos_w = ((-0.833f64).to_radians().sin() - phi.sin() * dec.sin()) / (phi.cos() * dec.cos());
    if cos_w > 1.0 {
        return (f64::INFINITY, f64::INFINITY); // polar night: the sun never rises
    }
    if cos_w < -1.0 {
        return (f64::NEG_INFINITY, f64::INFINITY); // polar day: it never sets
    }
    let w = cos_w.acos() * 180.0 / PI / 360.0;
    let unix = |jd: f64| (jd - 2440587.5) * 86400.0;
    (unix(transit - w), unix(transit + w))
}

/// True while the schedule makes the screen warm (manual mode aside).
/// now_ts: `now` as a Unix time.
pub fn in_schedule_at(cfg: &Map<String, Value>, now: NaiveDateTime, now_ts: f64, loc: Option<(f64, f64)>) -> bool {
    let mut sched = text(cfg, "schedule", "");
    if sched == "sunset" {
        if let Some((lat, lon)) = loc {
            let (rise, set) = sun_times(lat, lon, now.date());
            return now_ts < rise || now_ts >= set;
        }
        sched = "custom"; // same fallback as command()
    }
    if sched != "custom" {
        return false;
    }
    let (Some(start), Some(end)) = (hm(text(cfg, "from", "22:00")), hm(text(cfg, "to", "07:00"))) else { return false };
    let cur = now.hour() as i64 * 60 + now.minute() as i64;
    if start <= end {
        return start <= cur && cur < end;
    }
    cur >= start || cur < end // crosses midnight
}

pub fn in_schedule(cfg: &Map<String, Value>) -> bool {
    let (now, ts) = now_local();
    in_schedule_at(cfg, now, ts, tz_location())
}

/// Turn On Until Tomorrow (the Control Center's button, Settings).
pub fn set_manual(on: bool) {
    let mut cfg = sonata_config::load(NAME, &defaults());
    cfg.insert("manual_until".into(), json!(if on { until_tomorrow(now_local().0) } else { String::new() }));
    if let Err(e) = sonata_config::save(NAME, &cfg) {
        eprintln!("sonata2: Night Shift: {e}");
    }
}

/// On now (manual, or inside the schedule) -- for the Control Center button.
pub fn is_on() -> bool {
    let cfg = sonata_config::load(NAME, &defaults());
    manual_active(&cfg) || in_schedule(&cfg)
}

#[derive(Default)]
struct State {
    proc: Option<Child>,
    cmd: Option<Vec<String>>,
    gen: u64,
    waiting: bool,
    pending: Option<glib::SourceId>,
    expiry: Option<glib::SourceId>,
}

/// Keeps wlsunset in step with nightshift.json (menu bar process).
pub struct NightShift {
    st: RefCell<State>,
    me: std::rc::Weak<NightShift>,
    _mons: RefCell<Vec<gio::FileMonitor>>,
}

impl NightShift {
    /// None on trial: the Python menu bar's wlsunset holds the gamma; a
    /// second gamma client would fight it.
    pub fn start() -> Option<Rc<Self>> {
        let me = Rc::new_cyclic(|w| NightShift { st: RefCell::default(), me: w.clone(), _mons: RefCell::default() });
        let w = me.me.clone();
        *me._mons.borrow_mut() = sonata_config::watch(NAME, move || {
            if let Some(me) = w.upgrade() {
                me.later();
            }
        });
        me.apply();
        // wlsunset died: started again (a waitpid only). "Until tomorrow"
        // ends on its own one-shot timer (apply -> expire_at)
        let w = me.me.clone();
        glib::timeout_add_seconds_local(RESPAWN_CHECK_S, move || {
            let Some(me) = w.upgrade() else { return glib::ControlFlow::Break };
            let dead = {
                let mut st = me.st.borrow_mut();
                let waiting = st.waiting;
                !waiting && st.proc.as_mut().is_some_and(|p| !matches!(p.try_wait(), Ok(None)))
            };
            if dead {
                me.apply();
            }
            glib::ControlFlow::Continue
        });
        Some(me)
    }

    pub fn available() -> bool {
        sonata_backend::run::which("wlsunset").is_some()
    }

    /// A one-shot timer at manual_until (Turn On Until Tomorrow ending).
    fn expire_at(&self, cfg: &Map<String, Value>) {
        if let Some(id) = self.st.borrow_mut().expiry.take() {
            sonata_ui::cancel(id);
        }
        let now = now_local().0;
        let Some(until) = manual_until(cfg).filter(|u| now < *u) else { return };
        let left = (until - now).num_seconds();
        let w = self.me.clone();
        let id = glib::timeout_add_seconds_local_once((left + 1).max(1) as u32, move || {
            if let Some(me) = w.upgrade() {
                me.st.borrow_mut().expiry = None;
                me.apply();
            }
        });
        self.st.borrow_mut().expiry = Some(id);
    }

    /// Debounced: the warmth slider saves on every step.
    fn later(&self) {
        if let Some(id) = self.st.borrow_mut().pending.take() {
            sonata_ui::cancel(id);
        }
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(400), move || {
            if let Some(me) = w.upgrade() {
                me.st.borrow_mut().pending = None;
                me.apply();
            }
        });
        self.st.borrow_mut().pending = Some(id);
    }

    pub fn apply(&self) {
        let cfg = sonata_config::load(NAME, &defaults());
        self.expire_at(&cfg);
        let cmd = if Self::available() { command(&cfg) } else { Vec::new() };
        let gen = {
            let mut st = self.st.borrow_mut();
            let waiting = st.waiting;
            let running = st.proc.as_mut().is_some_and(|p| matches!(p.try_wait(), Ok(None)));
            if st.cmd.as_ref() == Some(&cmd) && (cmd.is_empty() || waiting || running) {
                return;
            }
            st.cmd = Some(cmd.clone());
            st.gen += 1;
            st.gen
        };
        if self.stop() && !cmd.is_empty() {
            self.st.borrow_mut().waiting = true; // the new one starts once the old one let go of the gamma
            return;
        }
        self.start_gen(gen);
    }

    fn start_gen(&self, gen: u64) {
        let mut st = self.st.borrow_mut();
        if gen != st.gen {
            return; // settings changed again meanwhile
        }
        st.waiting = false;
        if let Some(cmd) = st.cmd.clone().filter(|c| !c.is_empty()) {
            st.proc = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().ok();
        }
    }

    /// SIGTERM wlsunset and reap it off the main loop (it can take up to
    /// 2 s to fade the gamma back). True when one was running.
    pub fn stop(&self) -> bool {
        let (proc, gen) = {
            let mut st = self.st.borrow_mut();
            (st.proc.take(), st.gen)
        };
        let Some(mut proc) = proc else { return false };
        if !matches!(proc.try_wait(), Ok(None)) {
            return false;
        }
        unsafe { libc::kill(proc.id() as i32, libc::SIGTERM) };
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let _ = gio::spawn_blocking(move || {
                let end = std::time::Instant::now() + std::time::Duration::from_secs(2);
                while matches!(proc.try_wait(), Ok(None)) && std::time::Instant::now() < end {
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                let _ = proc.kill();
                let _ = proc.wait();
            })
            .await;
            if let Some(me) = w.upgrade() {
                me.start_gen(gen);
            }
        });
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg(v: Value) -> Map<String, Value> {
        let mut d = defaults().as_object().unwrap().clone();
        for (k, x) in v.as_object().unwrap() {
            d.insert(k.clone(), x.clone());
        }
        d
    }

    fn at(s: &str) -> NaiveDateTime {
        parse_iso(s).unwrap()
    }

    #[test]
    fn warmth_to_kelvin() {
        assert_eq!(temperature(0), 5500);
        assert_eq!(temperature(100), 2700);
        assert_eq!(temperature(50), 4100);
        assert_eq!(temperature(33), 4576); // int(): cut, not rounded
        assert_eq!(temperature(-5), 5500);
        assert_eq!(temperature(250), 2700);
    }

    #[test]
    fn time_zone_coordinates() {
        let tab = "# comment\nBR\t-2332-04637\tAmerica/Sao_Paulo\tS & SE Brazil\nPT\t+3843-00908\tEurope/Lisbon\tPortugal (mainland)\nUS\t+404251-0740023\tAmerica/New_York\tEastern\n";
        let (lat, lon) = tz_location_in(tab, "America/Sao_Paulo").unwrap();
        assert!((lat + 23.5333).abs() < 1e-3 && (lon + 46.6167).abs() < 1e-3);
        let (lat, lon) = tz_location_in(tab, "America/New_York").unwrap();
        assert!((lat - 40.7142).abs() < 1e-3 && (lon + 74.0064).abs() < 1e-3);
        assert!(tz_location_in(tab, "Mars/Olympus").is_none());
    }

    #[test]
    fn commands() {
        let now = at("2026-11-04T12:00");
        assert!(command_at(&cfg(json!({})), now, None).is_empty()); // off
        assert_eq!(command_at(&cfg(json!({"schedule": "custom", "warmth": 100})), now, None), ["wlsunset", "-t", "2700", "-T", "6500", "-S", "07:00", "-s", "22:00", "-d", "1800"]);
        assert_eq!(command_at(&cfg(json!({"schedule": "sunset"})), now, Some((-23.5333, -46.6167))), ["wlsunset", "-t", "4100", "-T", "6500", "-l", "-23.53", "-L", "-46.62"]);
        // unknown time zone: 22:00-07:00
        assert_eq!(command_at(&cfg(json!({"schedule": "sunset"})), now, None)[3..], ["-T", "6500", "-S", "07:00", "-s", "22:00", "-d", "1800"]);
        // until tomorrow: constant, both ends warm
        assert_eq!(command_at(&cfg(json!({"manual_until": "2026-11-05T07:00"})), now, None), ["wlsunset", "-t", "4100", "-T", "4101"]);
        assert!(command_at(&cfg(json!({"manual_until": "2026-11-04T07:00"})), now, None).is_empty()); // over
        assert!(command_at(&cfg(json!({"manual_until": "nonsense"})), now, None).is_empty());
    }

    #[test]
    fn until_seven_in_the_morning() {
        assert_eq!(until_tomorrow(at("2026-11-04T22:30")), "2026-11-05T07:00");
        assert_eq!(until_tomorrow(at("2026-11-04T03:10")), "2026-11-04T07:00");
        assert_eq!(until_tomorrow(at("2026-11-04T07:00")), "2026-11-05T07:00");
        assert_eq!(parse_iso("2026-11-05T07:00:00"), Some(at("2026-11-05T07:00")));
        assert_eq!(parse_iso("2026-11-05"), Some(at("2026-11-05T00:00")));
    }

    #[test]
    fn custom_schedule_across_midnight() {
        let c = cfg(json!({"schedule": "custom"}));
        assert!(in_schedule_at(&c, at("2026-11-04T23:00"), 0.0, None));
        assert!(in_schedule_at(&c, at("2026-11-04T06:59"), 0.0, None));
        assert!(!in_schedule_at(&c, at("2026-11-04T07:00"), 0.0, None));
        assert!(!in_schedule_at(&c, at("2026-11-04T12:00"), 0.0, None));
        let day = cfg(json!({"schedule": "custom", "from": "09:00", "to": "17:00"}));
        assert!(in_schedule_at(&day, at("2026-11-04T09:00"), 0.0, None));
        assert!(!in_schedule_at(&day, at("2026-11-04T17:00"), 0.0, None));
        assert!(!in_schedule_at(&cfg(json!({"schedule": "custom", "from": "x"})), at("2026-11-04T23:00"), 0.0, None));
        assert!(!in_schedule_at(&cfg(json!({})), at("2026-11-04T23:00"), 0.0, None));
    }

    #[test]
    fn sunrise_and_sunset() {
        // Lisbon, 2026-06-21: sun_times() in Python says 05:11:54 and 20:04:34 UTC
        let (rise, set) = sun_times(38.7167, -9.1333, NaiveDate::from_ymd_opt(2026, 6, 21).unwrap());
        let utc = |t: f64| chrono::DateTime::from_timestamp(t as i64, 0).unwrap().naive_utc();
        let (r, s) = (utc(rise), utc(set));
        assert_eq!((r.hour(), r.minute(), r.second()), (5, 11, 54), "{r}");
        assert_eq!((s.hour(), s.minute(), s.second()), (20, 4, 34), "{s}");
        // polar night / day
        assert_eq!(sun_times(80.0, 0.0, NaiveDate::from_ymd_opt(2026, 12, 21).unwrap()), (f64::INFINITY, f64::INFINITY));
        assert_eq!(sun_times(80.0, 0.0, NaiveDate::from_ymd_opt(2026, 6, 21).unwrap()), (f64::NEG_INFINITY, f64::INFINITY));
        let c = cfg(json!({"schedule": "sunset"}));
        let noon = at("2026-06-21T12:00");
        assert!(!in_schedule_at(&c, noon, (rise + set) / 2.0, Some((38.7167, -9.1333))));
        assert!(in_schedule_at(&c, noon, set + 60.0, Some((38.7167, -9.1333))));
    }

    #[test]
    fn manual_switch_saved() {
        let _e = crate::testenv::env();
        set_manual(true);
        let c = sonata_config::load(NAME, &defaults());
        assert!(manual_active(&c));
        assert!(is_on());
        set_manual(false);
        assert_eq!(sonata_config::load(NAME, &defaults())["manual_until"], "");
        assert!(!is_on());
    }
}
