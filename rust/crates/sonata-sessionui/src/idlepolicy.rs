//! What keeps the session awake: only something playing (shell/idlepolicy.py;
//! Vini: "nothing should keep it awake, and the lock must always work,
//! unless audio or video is playing").
//!
//! Without input for the display's timeout: the keyboard's light and the
//! displays go off, and after `lock_after` more seconds the screen locks --
//! whatever an app asks (Chrome kept the session awake for hours). Unless
//! media plays (an MPRIS player playing: Spotify, a video in Chrome, a
//! music app; or a sound being played right now): then it waits and looks
//! again. Any key or move brings everything back.
//!
//! Needs a compositor with ext-idle-notify-v1 version 2 (input idle,
//! ignoring app inhibitors), behind the [`IdleWatch`] trait (wl/idlewatch.py
//! is not ported yet); otherwise IdleLock keeps swayidle as before.

use serde_json::Value;
use std::cell::RefCell;
use std::rc::{Rc, Weak};

/// idle while media plays: looked at again this often
pub const RECHECK_S: u32 = 30;
/// the display never sleeps: the lock counts from this much idle
pub const NO_DPMS_BASE_S: u32 = 600;

/// The compositor's idle watch (wl/idlewatch.py: ext-idle-notify-v1's
/// input idle, and wlr-output-power-management for the displays).
pub trait IdleWatch {
    /// on_idle after `seconds` without input, on_back at the next input; an id for unwatch.
    fn watch(&self, seconds: u32, on_idle: Rc<dyn Fn()>, on_back: Rc<dyn Fn()>) -> u32;
    fn unwatch(&self, id: u32);
    /// Every display (`only`: those whose connector it accepts) on / off.
    fn displays(&self, on: bool, only: Option<&dyn Fn(&str) -> bool>) -> bool;
}

/// The compositor's input idle (ext-idle-notify, written by hand in
/// sonata-shell): what the menu bar hands IdleLock when Wayfire has it.
impl IdleWatch for sonata_shell::idlewatch::IdleWatch {
    fn watch(&self, seconds: u32, on_idle: Rc<dyn Fn()>, on_back: Rc<dyn Fn()>) -> u32 {
        sonata_shell::idlewatch::IdleWatch::watch(self, seconds, on_idle, on_back)
    }
    fn unwatch(&self, id: u32) {
        sonata_shell::idlewatch::IdleWatch::unwatch(self, id)
    }
    fn displays(&self, on: bool, only: Option<&dyn Fn(&str) -> bool>) -> bool {
        sonata_shell::idlewatch::IdleWatch::displays(self, on, only)
    }
}

/// Something to do later: (seconds, what) -> how to call it off.
pub type Later = Rc<dyn Fn(u32, Box<dyn FnOnce()>) -> Box<dyn FnOnce()>>;

/// GLib's timeout: fires once, called off before that by the closure it returns.
pub fn glib_later() -> Later {
    Rc::new(|secs, f| {
        let id = Rc::new(RefCell::new(None::<glib::SourceId>));
        let f = RefCell::new(Some(f));
        let mine = id.clone();
        let src = glib::timeout_add_seconds_local_once(secs, move || {
            mine.borrow_mut().take();
            if let Some(f) = f.borrow_mut().take() {
                f();
            }
        });
        *id.borrow_mut() = Some(src);
        Box::new(move || {
            if let Some(s) = id.borrow_mut().take() {
                sonata_ui::cancel(s);
            }
        })
    })
}

/// An MPRIS player playing, or a sound playing right now.
pub fn media_playing() -> bool {
    if let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) {
        let names = bus
            .call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None, Some(glib::VariantTy::new("(as)").unwrap()), gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.child_value(0).get::<Vec<String>>())
            .unwrap_or_default();
        for n in names.iter().filter(|n| n.starts_with("org.mpris.MediaPlayer2.")) {
            let st = bus
                .call_sync(Some(n), "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get", Some(&("org.mpris.MediaPlayer2.Player", "PlaybackStatus").into()), Some(glib::VariantTy::new("(v)").unwrap()), gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
                .ok()
                .and_then(|v| v.child_value(0).as_variant())
                .and_then(|v| v.get::<String>());
            if st.as_deref() == Some("Playing") {
                return true;
            }
        }
    }
    sound_playing()
}

/// PulseAudio / PipeWire streams not paused (`pactl -f json list sink-inputs`).
pub fn sound_playing() -> bool {
    if sonata_backend::run::which("pactl").is_none() {
        return false;
    }
    output_streams_playing(&crate::output_within(&["pactl", "-f", "json", "list", "sink-inputs"], 2000).unwrap_or_default())
}

/// The streams' JSON: one playing, not muted, not Sonata's own sounds.
pub fn output_streams_playing(json: &str) -> bool {
    let streams: Value = serde_json::from_str(if json.trim().is_empty() { "[]" } else { json }).unwrap_or(Value::Null);
    let Some(list) = streams.as_array() else { return false };
    list.iter().any(|s| {
        let props = s.get("properties").filter(|p| p.is_object());
        let field = |k: &str| props.and_then(|p| p.get(k)).and_then(Value::as_str).unwrap_or("").to_string();
        let app = format!("{} {}", field("application.name"), field("application.process.binary"));
        // Sonata's own sounds don't count
        !app.to_lowercase().contains("sonata") && !s.get("corked").is_some_and(crate::truthy) && !s.get("mute").is_some_and(crate::truthy)
    })
}

#[derive(Default)]
struct State {
    wid: u32,
    timers: Vec<Box<dyn FnOnce()>>,
    is_dark: bool,
    dpms: i64,
    lock_after: i64,
}

/// watch: the input idle. dark(on) / lock() are the actions (the keyboard
/// light, the displays; the lock screen).
pub struct IdlePolicy {
    watch: Rc<dyn IdleWatch>,
    dark: Box<dyn Fn(bool)>,
    lock: Box<dyn Fn()>,
    playing: Box<dyn Fn() -> bool>,
    later: Later,
    st: RefCell<State>,
    me: Weak<IdlePolicy>,
}

impl IdlePolicy {
    pub fn new(watch: Rc<dyn IdleWatch>, dark: Box<dyn Fn(bool)>, lock: Box<dyn Fn()>) -> Rc<Self> {
        Self::with(watch, dark, lock, Box::new(media_playing), glib_later())
    }

    /// (tests: what "playing" and "later" are)
    pub fn with(watch: Rc<dyn IdleWatch>, dark: Box<dyn Fn(bool)>, lock: Box<dyn Fn()>, playing: Box<dyn Fn() -> bool>, later: Later) -> Rc<Self> {
        Rc::new_cyclic(|me| IdlePolicy { watch, dark, lock, playing, later, st: RefCell::new(State { lock_after: -1, ..State::default() }), me: me.clone() })
    }

    /// dpms: seconds before the display goes off (<= 0: never -- the lock
    /// still counts from 10 min); lock_after: seconds after that (-1 never).
    pub fn apply(&self, dpms: i64, lock_after: i64) {
        {
            let st = self.st.borrow();
            if (dpms, lock_after) == (st.dpms, st.lock_after) && st.wid != 0 {
                return;
            }
        }
        self.stop();
        {
            let mut st = self.st.borrow_mut();
            st.dpms = dpms;
            st.lock_after = lock_after;
        }
        if dpms <= 0 && lock_after < 0 {
            return;
        }
        let (a, b) = (self.me.clone(), self.me.clone());
        let secs = if dpms > 0 { dpms as u32 } else { NO_DPMS_BASE_S };
        let wid = self.watch.watch(
            secs,
            Rc::new(move || {
                if let Some(p) = a.upgrade() {
                    p.idle();
                }
            }),
            Rc::new(move || {
                if let Some(p) = b.upgrade() {
                    p.back();
                }
            }),
        );
        self.st.borrow_mut().wid = wid;
    }

    pub fn stop(&self) {
        let wid = std::mem::take(&mut self.st.borrow_mut().wid);
        if wid != 0 {
            self.watch.unwatch(wid);
        }
        self.clear();
    }

    pub fn is_dark(&self) -> bool {
        self.st.borrow().is_dark
    }

    fn clear(&self) {
        let timers = std::mem::take(&mut self.st.borrow_mut().timers);
        for cancel in timers {
            cancel();
        }
    }

    fn after(&self, secs: u32, f: impl FnOnce(&IdlePolicy) + 'static) {
        let me = self.me.clone();
        let cancel = (self.later)(
            secs,
            Box::new(move || {
                if let Some(p) = me.upgrade() {
                    f(&p);
                }
            }),
        );
        self.st.borrow_mut().timers.push(cancel);
    }

    fn idle(&self) {
        self.clear();
        if (self.playing)() {
            // playing: awake; looked at again
            self.after(RECHECK_S, |p| p.idle());
            return;
        }
        let (dpms, lock_after) = {
            let st = self.st.borrow();
            (st.dpms, st.lock_after)
        };
        if dpms > 0 {
            self.st.borrow_mut().is_dark = true;
            (self.dark)(true);
        }
        if lock_after == 0 {
            (self.lock)();
        } else if lock_after > 0 {
            self.after(lock_after as u32, |p| (p.lock)());
        }
    }

    fn back(&self) {
        self.clear();
        let was = std::mem::take(&mut self.st.borrow_mut().is_dark);
        if was {
            (self.dark)(false);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[derive(Default)]
    struct FakeWatch {
        watches: RefCell<Vec<(u32, Rc<dyn Fn()>, Rc<dyn Fn()>)>>,
    }

    impl IdleWatch for FakeWatch {
        fn watch(&self, s: u32, on_idle: Rc<dyn Fn()>, on_back: Rc<dyn Fn()>) -> u32 {
            *self.watches.borrow_mut() = vec![(s, on_idle, on_back)];
            1
        }
        fn unwatch(&self, _id: u32) {
            self.watches.borrow_mut().clear();
        }
        fn displays(&self, _on: bool, _only: Option<&dyn Fn(&str) -> bool>) -> bool {
            true
        }
    }

    type Timers = Rc<RefCell<Vec<(u32, Option<Box<dyn FnOnce()>>)>>>;

    struct Rig {
        w: Rc<FakeWatch>,
        events: Rc<RefCell<Vec<String>>>,
        timers: Timers,
        playing: Rc<Cell<bool>>,
        pol: Rc<IdlePolicy>,
    }

    fn make(playing: bool) -> Rig {
        let w = Rc::new(FakeWatch::default());
        let events = Rc::new(RefCell::new(Vec::new()));
        let timers: Timers = Rc::default();
        let play = Rc::new(Cell::new(playing));
        let (e1, e2, t, p) = (events.clone(), events.clone(), timers.clone(), play.clone());
        let pol = IdlePolicy::with(
            w.clone(),
            Box::new(move |on| e1.borrow_mut().push(format!("dark {on}"))),
            Box::new(move || e2.borrow_mut().push("lock".into())),
            Box::new(move || p.get()),
            Rc::new(move |s, f| {
                t.borrow_mut().push((s, Some(f)));
                Box::new(|| {})
            }),
        );
        Rig { w, events, timers, playing: play, pol }
    }

    fn fire(timers: &Timers, i: usize) {
        let f = timers.borrow_mut()[i].1.take().unwrap();
        f();
    }

    fn idle(r: &Rig) {
        let f = r.w.watches.borrow()[0].1.clone();
        f();
    }

    #[test]
    fn dark_then_lock_whatever_apps_ask() {
        let r = make(false);
        r.pol.apply(120, 0);
        assert_eq!(r.w.watches.borrow()[0].0, 120);
        idle(&r);
        assert_eq!(*r.events.borrow(), ["dark true", "lock"]);
        let back = r.w.watches.borrow()[0].2.clone();
        back();
        assert_eq!(r.events.borrow().last().unwrap(), "dark false");
    }

    #[test]
    fn lock_some_seconds_later() {
        let r = make(false);
        r.pol.apply(120, 60);
        idle(&r);
        assert_eq!(*r.events.borrow(), ["dark true"]);
        assert_eq!(r.timers.borrow()[0].0, 60);
        fire(&r.timers, 0);
        assert_eq!(r.events.borrow().last().unwrap(), "lock");
    }

    #[test]
    fn media_playing_keeps_it_awake() {
        let r = make(true);
        r.pol.apply(120, 0);
        idle(&r);
        assert!(r.events.borrow().is_empty()); // playing: nothing
        assert_eq!(r.timers.borrow()[0].0, RECHECK_S);
        r.playing.set(false); // it stopped, still nobody there
        fire(&r.timers, 0);
        assert_eq!(*r.events.borrow(), ["dark true", "lock"]);
    }

    #[test]
    fn never() {
        let r = make(false);
        r.pol.apply(0, -1);
        assert!(r.w.watches.borrow().is_empty());
    }

    #[test]
    fn display_never_sleeps_lock_counts_from_ten_minutes() {
        let r = make(false);
        r.pol.apply(0, 0);
        assert_eq!(r.w.watches.borrow()[0].0, 600);
        idle(&r);
        assert_eq!(*r.events.borrow(), ["lock"]); // never dark
    }

    #[test]
    fn streams() {
        assert!(output_streams_playing(r#"[{"corked": false, "mute": false, "properties": {"application.name": "Google Chrome"}}]"#));
        assert!(!output_streams_playing(r#"[{"corked": true, "mute": false, "properties": {}}]"#));
        assert!(!output_streams_playing(r#"[{"corked": false, "mute": false, "properties": {"application.name": "sonata2-topbar"}}]"#));
        assert!(!output_streams_playing("[]"));
        assert!(!output_streams_playing(""));
        assert!(!output_streams_playing("not json"));
    }
}
