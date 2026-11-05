//! Clock's alarms going off, in the menu bar process (always running, so
//! an alarm rings with Clock closed) -- shell/alarmservice.py.
//!
//! One timer aims at the next alarm (at most 30 s ahead: after a suspend
//! the check runs at once and a missed alarm still rings, once). Ringing:
//! the alarm sound in a loop and a card at the top right (Snooze / Stop)
//! above everything -- Do Not Disturb never holds an alarm back (it isn't a
//! notification). Nobody stops it: quiet after RING_MAX_S. A one-time alarm
//! turns itself off; Snooze rings it again SNOOZE_MIN minutes later.
//!
//! Clock's timer rings here too, the same way: its card says TIMER, with
//! Stop only; the timer goes back to idle.

use chrono::{Datelike, Duration, NaiveDateTime, NaiveTime};
use gtk::prelude::*;
use sonata_clock::alarms::{self as A, Alarm};
use sonata_clock::timers::{self as T, TimerState};
use sonata_i18n::{gettext, n_, tr};
use std::cell::RefCell;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::rc::{Rc, Weak};

pub const CHECK_MAX_S: f64 = 30.0;
/// iPhone / macOS
pub const SNOOZE_MIN: i64 = 9;
/// an alarm nobody stops goes quiet after this
pub const RING_MAX_S: u32 = 15 * 60;
/// the first plays, softer; then full volume (a gentle wake-up)
pub const RAMP: [f64; 4] = [0.3, 0.45, 0.6, 0.8];
pub const HEAD_ALARM: &str = n_!("ALARM");
pub const HEAD_TIMER: &str = n_!("TIMER");

/// When the alarm goes off next, strictly after `after` (None when off).
pub fn next_time(a: &Alarm, after: NaiveDateTime) -> Option<NaiveDateTime> {
    if !a.enabled {
        return None;
    }
    let base = after.date();
    for days in 0..8 {
        let day = base + Duration::days(days);
        let t = day.and_time(NaiveTime::from_hms_opt(a.hour % 24, a.minute % 60, 0)?);
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

/// sh -c: play the sound getting louder, then on and on (loop) -- or once (a preview).
pub fn ring_command(path: &str, player: &str, looped: bool) -> Vec<String> {
    let play = |v: f64| if player == "pw-play" { format!("pw-play --volume {} \"$0\"", crate::monitors::py_float(v)) } else { format!("paplay --volume {} \"$0\"", (v * 65536.0) as i64) };
    let script = if looped {
        let ramp = RAMP.iter().map(|v| format!("{}; sleep 0.6", play(*v))).collect::<Vec<_>>().join("; ");
        format!("{ramp}; while :; do {}; sleep 0.6; done", play(1.0))
    } else {
        play(0.8)
    };
    vec!["sh".into(), "-c".into(), script, path.into()]
}

/// An alarm sound, looped until stop().
#[derive(Default)]
pub struct Ringer {
    child: Option<Child>,
}

impl Ringer {
    pub fn start(&mut self, sound: &str) {
        self.stop();
        let path = sonata_clock::sound::sound_path(sound);
        let Some(player) = ["pw-play", "paplay"].into_iter().find(|p| sonata_backend::run::which(p).is_some()) else { return };
        if !path.exists() {
            return;
        }
        let cmd = ring_command(&path.display().to_string(), player, true);
        // its own group: stop() ends the player with the loop
        self.child = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn().ok();
    }

    pub fn stop(&mut self) {
        if let Some(mut c) = self.child.take() {
            if matches!(c.try_wait(), Ok(None)) {
                unsafe { libc::kill(-(c.id() as i32), libc::SIGTERM) };
            }
            // (try_wait right after the signal came too soon: a zombie per alarm)
            sonata_backend::run::reap(c);
        }
    }

    pub fn ringing(&mut self) -> bool {
        self.child.as_mut().is_some_and(|c| matches!(c.try_wait(), Ok(None)))
    }
}

impl Drop for Ringer {
    fn drop(&mut self) {
        self.stop();
    }
}

/// What rings now: an alarm, or Clock's timer.
#[derive(Clone, Debug)]
pub struct Ringing {
    pub head: &'static str,
    pub label: String,
    pub time_text: String,
    pub snooze: bool,
    pub sound: String,
    pub alarm: Option<Alarm>,
}

fn css() {
    thread_local!(static DONE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) });
    if !DONE.with(|d| d.replace(true)) {
        sonata_ui::css::register_module("shell/alarmservice.py");
    }
}

/// Top right, above everything: the alarm's name and time, Snooze and Stop.
pub fn alarm_card(app: &gtk::Application, r: &Ringing, on_snooze: impl Fn() + 'static, on_stop: impl Fn() + 'static) -> gtk::Window {
    css();
    // (the window's title, never shown: "Alarm" / "Timer")
    let title: String = r.head.chars().take(1).chain(r.head.chars().skip(1).flat_map(char::to_lowercase)).collect();
    let win = gtk::Window::builder().application(app).title(title.as_str()).decorated(false).css_classes(["sonata-alarm"]).build();
    let card = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).css_classes(["alarm-card"]).width_request(320).build();
    let top = gtk::Box::new(gtk::Orientation::Horizontal, 6);
    top.append(&gtk::Image::builder().icon_name("alarm-symbolic").pixel_size(14).build());
    top.append(&gtk::Label::builder().label(gettext(r.head).as_str()).xalign(0.0).css_classes(["alarm-app"]).build());
    card.append(&top);
    let row = gtk::Box::builder().spacing(10).margin_top(4).build();
    let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).hexpand(true).build();
    texts.append(&gtk::Label::builder().label(r.time_text.as_str()).xalign(0.0).css_classes(["alarm-time"]).build());
    let label = if r.label.is_empty() { tr!("Alarm") } else { r.label.clone() };
    texts.append(&gtk::Label::builder().label(label.as_str()).xalign(0.0).css_classes(["alarm-label"]).ellipsize(gtk::pango::EllipsizeMode::End).build());
    row.append(&texts);
    let btns = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).valign(gtk::Align::Center).build();
    if r.snooze {
        btns.append(&sonata_ui::controls::push_button(&tr!("Snooze"), on_snooze, ""));
    }
    btns.append(&sonata_ui::controls::push_button(&tr!("Stop"), on_stop, "default"));
    row.append(&btns);
    card.append(&row);
    win.set_child(Some(&card));
    if sonata_shell::layer::available() {
        use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
        win.init_layer_shell();
        win.set_namespace(Some("sonata2-alarm"));
        win.set_layer(Layer::Overlay);
        win.set_anchor(Edge::Top, true);
        win.set_anchor(Edge::Right, true);
        win.set_margin(Edge::Top, 24);
        win.set_keyboard_mode(KeyboardMode::OnDemand);
    }
    win
}

/// How the service rings (tests: a fake that writes down what happened).
pub trait RingUi {
    /// The card and the sound; snooze / stop come back through the service.
    fn ring(&self, svc: Weak<AlarmService>, r: &Ringing);
    /// Card away; the sound too when `sound`.
    fn dismiss(&self, sound: bool);
    fn showing(&self) -> bool;
}

/// The card on screen and the looped sound.
pub struct CardUi {
    app: gtk::Application,
    card: RefCell<Option<gtk::Window>>,
    ringer: RefCell<Ringer>,
}

impl CardUi {
    pub fn new(app: &gtk::Application) -> Self {
        CardUi { app: app.clone(), card: RefCell::default(), ringer: RefCell::default() }
    }
}

impl RingUi for CardUi {
    fn ring(&self, svc: Weak<AlarmService>, r: &Ringing) {
        let s2 = svc.clone();
        let card = alarm_card(
            &self.app,
            r,
            move || {
                if let Some(s) = svc.upgrade() {
                    s.snooze();
                }
            },
            move || {
                if let Some(s) = s2.upgrade() {
                    s.stop();
                }
            },
        );
        card.present();
        *self.card.borrow_mut() = Some(card);
        self.ringer.borrow_mut().start(&r.sound);
    }

    fn dismiss(&self, sound: bool) {
        if sound {
            self.ringer.borrow_mut().stop();
        }
        if let Some(c) = self.card.borrow_mut().take() {
            c.destroy();
        }
    }

    fn showing(&self) -> bool {
        self.card.borrow().is_some()
    }
}

impl Drop for CardUi {
    fn drop(&mut self) {
        self.ringer.borrow_mut().stop();
    }
}

struct State {
    alarms: Vec<Alarm>,
    last: NaiveDateTime,
    /// (when, what)
    snoozed: Vec<(NaiveDateTime, Ringing)>,
    current: Option<Ringing>,
    timer: Option<glib::SourceId>,
    quiet: Option<glib::SourceId>,
    clock_timer: T::Timer,
    /// (tests, log)
    rung: Vec<String>,
}

pub struct AlarmService {
    ui: Box<dyn RingUi>,
    st: RefCell<State>,
    /// GLib timers (off in tests: they run no main loop)
    timers: bool,
    me: Weak<AlarmService>,
    _mons: RefCell<Vec<gio::FileMonitor>>,
}

fn now() -> NaiveDateTime {
    crate::nightshift::now_local().0
}

/// A local time as a Unix time (datetime.timestamp() of a naive datetime).
fn timestamp(t: NaiveDateTime) -> f64 {
    glib::DateTime::from_local(t.year(), t.month() as i32, t.day() as i32, chrono::Timelike::hour(&t) as i32, chrono::Timelike::minute(&t) as i32, chrono::Timelike::second(&t) as f64 + chrono::Timelike::nanosecond(&t) as f64 / 1e9)
        .map(|d| d.to_unix() as f64 + d.microsecond() as f64 / 1e6)
        .unwrap_or(0.0)
}

impl AlarmService {
    /// None on trial: the Python menu bar rings the same alarms.json (and
    /// turns one-time alarms off in it); two would ring every alarm twice.
    pub fn start(app: &gtk::Application) -> Option<Rc<Self>> {
        if sonata_config::side_by_side() {
            return None;
        }
        let me = Self::with(Box::new(CardUi::new(app)), now(), true);
        let mut mons = Vec::new();
        for path in [A::path(), T::path()] {
            let w = me.me.clone();
            mons.extend(sonata_config::watch_path(&path, move || {
                if let Some(me) = w.upgrade() {
                    me.reload();
                }
            }));
        }
        *me._mons.borrow_mut() = mons;
        Some(me)
    }

    pub fn with(ui: Box<dyn RingUi>, now: NaiveDateTime, timers: bool) -> Rc<Self> {
        let st = State { alarms: A::load(), last: now, snoozed: Vec::new(), current: None, timer: None, quiet: None, clock_timer: T::load().1, rung: Vec::new() };
        let me = Rc::new_cyclic(|w| AlarmService { ui, st: RefCell::new(st), timers, me: w.clone(), _mons: RefCell::default() });
        me.schedule();
        me
    }

    pub fn reload(&self) {
        {
            let mut st = self.st.borrow_mut();
            st.alarms = A::load();
            st.clock_timer = T::load().1;
        }
        self.schedule();
    }

    fn schedule(&self) {
        if !self.timers {
            return;
        }
        if let Some(id) = self.st.borrow_mut().timer.take() {
            sonata_ui::cancel(id);
        }
        let secs = {
            let st = self.st.borrow();
            let n = now();
            let mut left: Vec<f64> = next_any(&st.alarms, st.last).into_iter().chain(st.snoozed.iter().map(|s| s.0)).map(|t| (t - n).num_milliseconds() as f64 / 1000.0).collect();
            if st.clock_timer.state == TimerState::Running {
                left.push(st.clock_timer.ends - T::now());
            }
            left.into_iter().reduce(f64::min).map_or(CHECK_MAX_S, |s| s + 0.5).min(CHECK_MAX_S)
        };
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis((secs.max(0.2) * 1000.0) as u64), move || {
            if let Some(me) = w.upgrade() {
                me.st.borrow_mut().timer = None;
                me.check(now());
            }
        });
        self.st.borrow_mut().timer = Some(id);
    }

    /// What rings at `now` (and rings it); the alarms that went off.
    pub fn check(&self, now: NaiveDateTime) -> Vec<Alarm> {
        let (due_alarms, snoozed) = {
            let mut st = self.st.borrow_mut();
            let d = due(&st.alarms, st.last, now);
            let (ring, keep): (Vec<_>, Vec<_>) = std::mem::take(&mut st.snoozed).into_iter().partition(|(w, _)| *w <= now);
            st.snoozed = keep;
            st.last = now;
            (d, ring.into_iter().map(|(_, r)| r).collect::<Vec<_>>())
        };
        let once: Vec<serde_json::Value> = due_alarms.iter().filter(|a| a.repeat.is_empty()).map(|a| a.id.clone()).collect();
        if !once.is_empty() {
            // a one-time alarm turns itself off
            let mut all = A::load();
            for a in all.iter_mut().filter(|a| once.contains(&a.id)) {
                a.enabled = false;
            }
            if let Err(e) = A::save(&all) {
                eprintln!("sonata2: alarms: {e}");
            }
            self.st.borrow_mut().alarms = all;
        }
        let h24 = A::h24();
        for a in &due_alarms {
            self.ring(Ringing { head: HEAD_ALARM, label: a.label.clone(), time_text: A::time_text(a, h24), snooze: a.snooze, sound: a.sound.clone(), alarm: Some(a.clone()) });
        }
        let mut snoozed_alarms = Vec::new();
        for r in snoozed {
            snoozed_alarms.extend(r.alarm.clone());
            self.ring(r);
        }
        let ct = self.st.borrow().clock_timer.clone();
        if T::due(&ct, timestamp(now)) {
            self.ring_timer();
        }
        self.schedule();
        due_alarms.into_iter().chain(snoozed_alarms).collect()
    }

    /// Clock's timer is done: it goes back to idle (Clock shows it ready to
    /// start again) and rings like an alarm, with Stop only.
    pub fn ring_timer(&self) {
        let (sw, mut tm) = T::load();
        if tm.state != TimerState::Running {
            tm = self.st.borrow().clock_timer.clone();
        }
        T::tm_cancel(&mut tm);
        if let Err(e) = T::save(&sw, &tm) {
            eprintln!("sonata2: timer: {e}");
        }
        let label = if tm.label.is_empty() { tr!("Timer") } else { tm.label.clone() };
        let r = Ringing { head: HEAD_TIMER, label, time_text: T::duration_text(tm.duration), snooze: false, sound: tm.sound.clone(), alarm: None };
        {
            let mut st = self.st.borrow_mut();
            st.clock_timer = tm;
            st.rung.push("timer".into());
        }
        self.show(r);
    }

    fn ring(&self, r: Ringing) {
        let id = r.alarm.as_ref().map(|a| a.id.to_string()).unwrap_or_default();
        self.st.borrow_mut().rung.push(id);
        self.show(r);
    }

    fn show(&self, r: Ringing) {
        self.dismiss(false);
        self.ui.ring(self.me.clone(), &r);
        self.st.borrow_mut().current = Some(r);
        if self.timers {
            let w = self.me.clone();
            let id = glib::timeout_add_seconds_local_once(RING_MAX_S, move || {
                if let Some(me) = w.upgrade() {
                    me.st.borrow_mut().quiet = None;
                    me.stop();
                }
            });
            self.st.borrow_mut().quiet = Some(id);
        }
    }

    pub fn snooze(&self) {
        let cur = self.st.borrow().current.clone();
        if let Some(c) = cur {
            self.st.borrow_mut().snoozed.push((now() + Duration::minutes(SNOOZE_MIN), c));
        }
        self.stop();
    }

    pub fn stop(&self) {
        self.dismiss(true);
        self.schedule();
    }

    fn dismiss(&self, sound: bool) {
        self.ui.dismiss(sound);
        let quiet = self.st.borrow_mut().quiet.take();
        if let Some(id) = quiet {
            sonata_ui::cancel(id);
        }
        self.st.borrow_mut().current = None;
    }

    pub fn ringing(&self) -> Option<Ringing> {
        self.st.borrow().current.clone()
    }

    pub fn snoozed(&self) -> usize {
        self.st.borrow().snoozed.len()
    }

    /// (tests) when the last check was
    pub fn set_last(&self, t: NaiveDateTime) {
        self.st.borrow_mut().last = t;
    }

    pub fn clock_timer(&self) -> T::Timer {
        self.st.borrow().clock_timer.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    /// True once `pid` is gone, false while it is a zombie or still runs
    /// (waits up to 3 s).
    fn reaped(pid: u32) -> bool {
        let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while std::time::Instant::now() < end {
            if !std::path::Path::new(&format!("/proc/{pid}")).exists() {
                return true;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        false
    }

    #[test]
    fn test_stopped_alarm_stayed_a_zombie() {
        //! The menu bar kept a defunct player for every alarm stopped
        //! while ringing.
        let child = Command::new("sleep").arg("30").process_group(0).spawn().unwrap();
        let pid = child.id();
        let mut r = Ringer { child: Some(child) };
        r.stop();
        assert!(reaped(pid), "the stopped player is reaped");
    }

    fn mon() -> NaiveDateTime {
        crate::nightshift::parse_iso("2026-09-28T06:00").unwrap() // a Monday
    }

    fn alarm(h: u32, m: u32, repeat: &[u8], label: &str) -> Alarm {
        let mut a = Alarm::new(h, m);
        a.repeat = repeat.to_vec();
        a.label = label.into();
        a
    }

    #[test]
    fn next_time_of_an_alarm() {
        let m = mon();
        assert_eq!(next_time(&alarm(7, 0, &[], "A"), m), Some(m.with_hour(7).unwrap()));
        assert_eq!(next_time(&alarm(5, 0, &[], "A"), m), Some(m.with_hour(5).unwrap() + Duration::days(1)));
        let sat = next_time(&alarm(7, 0, &[5, 6], "A"), m).unwrap();
        assert_eq!(sat.weekday().num_days_from_monday(), 5);
        let mut off = alarm(7, 0, &[], "A");
        off.enabled = false;
        assert_eq!(next_time(&off, m), None);
        let at = m.with_hour(7).unwrap().with_second(30).unwrap(); // 07:00:30: today's has gone off
        assert_eq!(next_time(&alarm(7, 0, &[], "A"), at).unwrap().date(), (m + Duration::days(1)).date());
    }

    /// Asleep from 6:00 to 8:00: the 7:00 alarm rings when the computer is back.
    #[test]
    fn due_after_a_suspend() {
        let a = alarm(7, 0, &[], "A");
        let m = mon();
        assert_eq!(due(std::slice::from_ref(&a), m, m.with_hour(8).unwrap()), [a.clone()]);
        assert!(due(&[a], m.with_hour(7).unwrap().with_minute(1).unwrap(), m.with_hour(8).unwrap()).is_empty());
    }

    /// The default sound was the freedesktop alarm: high and sharp (Vini).
    /// Soft sounds now, louder step by step.
    #[test]
    fn ramp_then_loop() {
        let cmd = ring_command("/s.oga", "pw-play", true);
        assert!(cmd[2].contains(&format!("--volume {}", RAMP[0])));
        assert!(cmd[2].contains("while :"));
        assert!(cmd[2].find(&format!("--volume {}", RAMP[0])) < cmd[2].find("while :"));
        let once = ring_command("/s.oga", "paplay", false);
        assert!(!once[2].contains("while"));
        assert!(once[2].contains("paplay --volume"));
        // the same text as Python's (f"{v}" of 1.0 is "1.0")
        let full = ring_command("/s.oga", "pw-play", true)[2].clone();
        assert!(full.contains("while :; do pw-play --volume 1.0 \"$0\"; sleep 0.6; done"));
        assert!(full.starts_with("pw-play --volume 0.3 \"$0\"; sleep 0.6; pw-play --volume 0.45"));
    }

    #[derive(Default)]
    struct Fake {
        rang: RefCell<Vec<(String, bool, String)>>,
        card: RefCell<bool>,
    }

    impl RingUi for Rc<Fake> {
        fn ring(&self, _svc: Weak<AlarmService>, r: &Ringing) {
            self.rang.borrow_mut().push((r.label.clone(), r.snooze, r.sound.clone()));
            *self.card.borrow_mut() = true;
        }
        fn dismiss(&self, _sound: bool) {
            *self.card.borrow_mut() = false;
        }
        fn showing(&self) -> bool {
            *self.card.borrow()
        }
    }

    fn service(alarms: &[Alarm], now: NaiveDateTime) -> (Rc<AlarmService>, Rc<Fake>) {
        A::save(alarms).unwrap();
        let f = Rc::new(Fake::default());
        (AlarmService::with(Box::new(f.clone()), now, false), f)
    }

    #[test]
    fn rings_once_and_turns_one_time_off() {
        let _e = crate::testenv::env();
        let m = mon();
        let (sv, f) = service(&[alarm(7, 0, &[], "Once"), alarm(7, 0, &[0, 1, 2, 3, 4, 5, 6], "Daily")], m);
        assert!(sv.check(m.with_minute(59).unwrap().with_hour(6).unwrap()).is_empty());
        let rung = sv.check(m.with_hour(7).unwrap().with_second(5).unwrap());
        let mut labels: Vec<String> = rung.iter().map(|a| a.label.clone()).collect();
        labels.sort();
        assert_eq!(labels, ["Daily", "Once"]);
        assert!(!f.rang.borrow().is_empty());
        assert!(f.showing());
        let states: Vec<(String, bool)> = A::load().iter().map(|a| (a.label.clone(), a.enabled)).collect();
        assert!(states.contains(&("Once".into(), false)) && states.contains(&("Daily".into(), true)));
        assert!(sv.check(m.with_hour(7).unwrap().with_minute(2).unwrap()).is_empty()); // not twice
        sv.stop();
        assert!(!f.showing());
    }

    #[test]
    fn rings_with_do_not_disturb() {
        let _e = crate::testenv::env();
        sonata_config::save("notifications", serde_json::json!({"dnd": true, "apps": {}}).as_object().unwrap()).unwrap();
        let m = mon();
        let (sv, f) = service(&[alarm(7, 0, &[], "A")], m);
        assert_eq!(sv.check(m.with_hour(7).unwrap().with_second(1).unwrap()).len(), 1);
        assert!(f.showing());
        sv.stop();
    }

    #[test]
    fn snooze_rings_again_nine_minutes_on() {
        let _e = crate::testenv::env();
        let m = mon();
        let (sv, f) = service(&[alarm(7, 0, &[0, 1, 2, 3, 4, 5, 6], "A")], m);
        sv.check(m.with_hour(7).unwrap().with_second(1).unwrap());
        sv.snooze();
        assert!(!f.showing());
        assert_eq!(sv.snoozed(), 1);
        let later = now() + Duration::minutes(SNOOZE_MIN) + Duration::seconds(5);
        sv.set_last(later - Duration::seconds(30)); // (the daily one isn't due then)
        assert_eq!(sv.check(later).len(), 1); // again, 9 minutes on
        assert!(f.showing());
        sv.stop();
    }

    #[test]
    fn the_menu_bar_rings_the_timer() {
        let _e = crate::testenv::env();
        A::save(&[]).unwrap();
        let mut tm = T::Timer::default();
        T::tm_start(&mut tm, Some(60.0), Some("Tea"), T::now());
        T::save(&T::Stopwatch::default(), &tm).unwrap();
        let f = Rc::new(Fake::default());
        let sv = AlarmService::with(Box::new(f.clone()), now(), false);
        assert_eq!(sv.clock_timer().state, TimerState::Running);
        sv.check(now());
        assert!(f.rang.borrow().is_empty()); // not yet
        sv.check(now() + Duration::seconds(61));
        assert_eq!(*f.rang.borrow(), [("Tea".to_string(), false, T::SOUND.to_string())]); // Stop only
        assert_eq!(sv.ringing().unwrap().head, HEAD_TIMER);
        assert_eq!(T::load().1.state, TimerState::Idle); // back to idle for Clock
        sv.stop();
    }
}
