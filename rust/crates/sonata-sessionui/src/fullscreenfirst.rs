//! Fullscreen first, the menu bar's side (gamemode.py's `Watcher`): it
//! watches Wayfire (focus and full-screen events) and, while a full-screen
//! window has the focus,
//!
//!   - publishes it in $XDG_RUNTIME_DIR/sonata2-fullscreen ("1"/"0"), which
//!     every Sonata process follows to pause its periodic work
//!     (sonata_tweaks::gamemode::active / watch);
//!   - registers the app with Feral GameMode when gamemoded is there;
//!   - tells its listeners (the menu bar's polling, the screen corners).
//!
//! Besides: Automatic energy mode is raised while anything is full screen
//! (gamemode::PowerBoost), and blur and animations go while a full-screen
//! game fills the graphics card (gamemode::LightEffects, fed the card's
//! memory by [`crate::vram::VramWatch`] through [`FullscreenFirst::memory`]).
//!
//! One per menu bar process, whatever the number of displays.

use crate::wfevents::{self, is_toplevel, Ipc, Later};
use serde_json::Value;
use sonata_tweaks::gamemode::{self, LightEffects, PowerBoost};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

/// a burst of events: one look this long after the first
pub const SETTLE_MS: u32 = 150;
pub const EVENTS: [&str; 5] = ["view-focused", "view-fullscreen", "view-unmapped", "view-mapped", "view-minimized"];

/// A listener's handle, to take it off again (a bar whose display was unplugged).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ListenerId(u64);

type Listener = Rc<dyn Fn(bool)>;

pub struct FullscreenFirst {
    ipc: Rc<dyn Ipc>,
    later: Later,
    /// writes the state file, raises the energy mode, lightens the
    /// effects, registers with GameMode; off on trial (the Python menu
    /// bar does all that already: only the listeners hear of it)
    acts: bool,
    power: PowerBoost,
    light: RefCell<LightEffects>,
    register: Box<dyn Fn(i32, bool)>,
    active: Cell<bool>,
    /// the display the full-screen app is on (its connector)
    output: RefCell<Option<String>>,
    /// the fullscreen app registered with GameMode
    pid: Cell<Option<i64>>,
    pending: Cell<bool>,
    listeners: RefCell<Vec<(ListenerId, Listener)>>,
    next_id: Cell<u64>,
    me: Weak<FullscreenFirst>,
}

impl FullscreenFirst {
    /// The menu bar's, made once. On trial it only follows Wayfire for its
    /// listeners: the Python menu bar writes the state, owns the energy
    /// mode and the effects, and registers games -- two copies would undo
    /// each other.
    pub fn start() -> Rc<Self> {
        Self::with(
            Rc::new(wfevents::WayfireIpc::default()),
            wfevents::main_loop_later(),
            PowerBoost::new(),
            LightEffects::new(),
            Box::new(gamemode::register_game),
            true,
        )
    }

    /// With this Wayfire, timer, energy mode and effects (tests hand in fakes).
    pub fn with(ipc: Rc<dyn Ipc>, later: Later, power: PowerBoost, light: LightEffects, register: Box<dyn Fn(i32, bool)>, acts: bool) -> Rc<Self> {
        let me = Rc::new_cyclic(|w| FullscreenFirst {
            ipc,
            later,
            acts,
            power,
            light: RefCell::new(light),
            register,
            active: Cell::new(false),
            output: RefCell::default(),
            pid: Cell::new(None),
            pending: Cell::new(false),
            listeners: RefCell::default(),
            next_id: Cell::new(0),
            me: w.clone(),
        });
        if acts {
            gamemode::write_state(false);
            // restarted while raised: lowered once nothing is full screen
            me.power.set_full(gamemode::boosted());
            // restarted while lightened: effects back until a game fills the card
            me.light.borrow_mut().update(Some(false), None, None);
        }
        if me.ipc.available() {
            let w = me.me.clone();
            me.ipc.watch(
                &EVENTS,
                Box::new(move |_| {
                    if let Some(me) = w.upgrade() {
                        me.soon();
                    }
                }),
            );
            me.soon();
        }
        me
    }

    /// A full-screen window has the focus.
    pub fn active(&self) -> bool {
        self.active.get()
    }

    /// The display (connector) of the full-screen window with the focus.
    pub fn output(&self) -> Option<String> {
        self.output.borrow().clone()
    }

    /// `callback(active)` each time it changes (or the full-screen app or
    /// its display does), on the main loop.
    pub fn connect(&self, callback: impl Fn(bool) + 'static) -> ListenerId {
        let id = ListenerId(self.next_id.get());
        self.next_id.set(id.0 + 1);
        self.listeners.borrow_mut().push((id, Rc::new(callback)));
        id
    }

    pub fn disconnect(&self, id: ListenerId) {
        self.listeners.borrow_mut().retain(|(i, _)| *i != id);
    }

    /// The graphics card's memory (MiB), from the VRAM watch: lighter
    /// effects while a full-screen game fills it.
    pub fn memory(&self, used: u64, total: u64) {
        if self.acts {
            self.light.borrow_mut().update(None, Some(used as f64), Some(total as f64));
        }
    }

    fn soon(&self) {
        if self.pending.replace(true) {
            return; // a burst of events: one look
        }
        let w = self.me.clone();
        (self.later)(
            SETTLE_MS,
            Box::new(move || {
                if let Some(me) = w.upgrade() {
                    me.check();
                }
            }),
        );
    }

    /// One look at Wayfire's windows.
    pub fn check(&self) {
        self.pending.set(false);
        let views = wfevents::views(&*self.ipc);
        let truthy = |v: &Value, k| v.get(k).is_some_and(crate::truthy);
        let front = views.iter().find(|v| truthy(v, "activated"));
        let full = front.is_some_and(|f| truthy(f, "fullscreen") && is_toplevel(f));
        let pid = front.filter(|_| full).and_then(|f| f.get("pid")).and_then(Value::as_i64);
        let output = front.filter(|_| full).and_then(|f| f.get("output-name")).and_then(Value::as_str).map(String::from);
        if self.acts {
            // a full-screen window that's minimized isn't being played: Balanced again
            // until it's back (Alt+Tab away keeps it: the game still runs on screen)
            self.power.update(views.iter().any(|v| truthy(v, "fullscreen") && v.get("mapped").is_none_or(crate::truthy) && !truthy(v, "minimized") && is_toplevel(v)));
            self.light.borrow_mut().update(Some(full), None, None);
        }
        if full != self.active.get() || pid != self.pid.get() || output != *self.output.borrow() {
            if self.acts {
                if let Some(old) = self.pid.get() {
                    (self.register)(old as i32, false);
                }
            }
            self.active.set(full);
            self.pid.set(pid);
            *self.output.borrow_mut() = output;
            if self.acts {
                if let Some(p) = pid {
                    (self.register)(p as i32, true);
                }
                gamemode::write_state(full);
            }
            let ls: Vec<Listener> = self.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
            for f in ls {
                f(full);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fake {
        views: RefCell<Vec<Value>>,
        cb: RefCell<Option<Box<dyn Fn(&Value)>>>,
    }

    impl Ipc for Fake {
        fn available(&self) -> bool {
            true
        }
        fn call(&self, method: &str, _data: Value) -> Option<Value> {
            (method == "window-rules/list-views").then(|| Value::Array(self.views.borrow().clone()))
        }
        fn watch(&self, _events: &[&str], callback: Box<dyn Fn(&Value)>) -> bool {
            *self.cb.borrow_mut() = Some(callback);
            true
        }
    }

    type Timers = Rc<RefCell<Vec<Box<dyn FnOnce()>>>>;

    struct Rig {
        ff: Rc<FullscreenFirst>,
        ipc: Rc<Fake>,
        timers: Timers,
        registered: Rc<RefCell<Vec<(i32, bool)>>>,
        lightened: Rc<RefCell<Vec<(String, Option<String>)>>>,
        profile: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    }

    fn rig(views: Vec<Value>, acts: bool, dir: &std::path::Path) -> Rig {
        let ipc = Rc::new(Fake { views: RefCell::new(views), cb: RefCell::default() });
        let timers: Timers = Rc::default();
        let t = timers.clone();
        let registered: Rc<RefCell<Vec<(i32, bool)>>> = Rc::default();
        let r = registered.clone();
        let lightened: Rc<RefCell<Vec<(String, Option<String>)>>> = Rc::default();
        let l = lightened.clone();
        let light = LightEffects::with(Box::new(|_, _| String::new()), Box::new(move |s, k, v| {
            l.borrow_mut().push((format!("{s}/{k}"), v.map(String::from)));
            true
        }), Box::new(|| true), dir.join("light.json"));
        let profile: std::sync::Arc<std::sync::Mutex<Vec<String>>> = Default::default();
        let p = profile.clone();
        let power = PowerBoost::with(std::sync::Arc::new(|| Some("balanced".into())), std::sync::Arc::new(move |m| {
            p.lock().unwrap().push(m.into());
            true
        }), Box::new(|work, then| then(work())));
        let ff = FullscreenFirst::with(ipc.clone(), Rc::new(move |_, f| t.borrow_mut().push(f)), power, light, Box::new(move |pid, on| r.borrow_mut().push((pid, on))), acts);
        Rig { ff, ipc, timers, registered, lightened, profile }
    }

    impl Rig {
        fn settle(&self) {
            let fs: Vec<_> = self.timers.borrow_mut().drain(..).collect();
            for f in fs {
                f();
            }
        }
        fn event(&self) {
            (self.ipc.cb.borrow().as_ref().unwrap())(&json!({"event": "view-focused"}));
        }
    }

    fn game(full: bool, focused: bool) -> Value {
        json!({"id": 3, "app-id": "skate3.exe", "pid": 4242, "role": "toplevel", "activated": focused, "fullscreen": full, "output-name": "DP-1"})
    }

    #[test]
    fn follows_the_focused_full_screen_window() {
        let e = crate::testenv::env();
        let r = rig(vec![game(true, true)], true, e.dir.path());
        let heard: Rc<RefCell<Vec<bool>>> = Rc::default();
        let h = heard.clone();
        r.ff.connect(move |on| h.borrow_mut().push(on));
        r.settle();
        assert!(r.ff.active());
        assert_eq!(r.ff.output().as_deref(), Some("DP-1"));
        assert!(gamemode::active());
        assert_eq!(*r.registered.borrow(), [(4242, true)]);
        assert_eq!(*r.profile.lock().unwrap(), ["performance"]);
        // Alt+Tab to a normal window: Sonata works as usual again
        *r.ipc.views.borrow_mut() = vec![game(true, false), json!({"id": 4, "activated": true, "role": "toplevel"})];
        r.event();
        r.event(); // a burst: one look
        assert_eq!(r.timers.borrow().len(), 1);
        r.settle();
        assert!(!r.ff.active());
        assert_eq!(r.ff.output(), None);
        assert!(!gamemode::active());
        assert_eq!(*r.registered.borrow(), [(4242, true), (4242, false)]);
        assert_eq!(*heard.borrow(), [true, false]);
        gamemode::set_boosted(false);
    }

    #[test]
    fn lighter_effects_only_while_a_full_screen_game_fills_the_card() {
        let e = crate::testenv::env();
        let r = rig(vec![game(false, true)], true, e.dir.path());
        r.settle();
        r.ff.memory(7600, 8000);
        assert!(r.lightened.borrow().is_empty()); // not full screen: nothing to make room for
        *r.ipc.views.borrow_mut() = vec![game(true, true)];
        r.event();
        r.settle();
        r.ff.memory(7600, 8000);
        assert!(r.lightened.borrow().iter().any(|(k, v)| k == "animate/open_animation" && v.as_deref() == Some("none")));
        gamemode::set_boosted(false);
    }

    #[test]
    fn listener_taken_off_hears_nothing() {
        let e = crate::testenv::env();
        let r = rig(vec![game(true, true)], true, e.dir.path());
        let heard = Rc::new(Cell::new(0));
        let h = heard.clone();
        let id = r.ff.connect(move |_| h.set(h.get() + 1));
        r.ff.disconnect(id);
        r.settle();
        assert_eq!(heard.get(), 0);
        gamemode::set_boosted(false);
    }

    /// On trial the Python menu bar owns the state file, the energy mode
    /// and GameMode: the Rust one only listens.
    #[test]
    fn trial_listens_and_changes_nothing() {
        let e = crate::testenv::env();
        gamemode::write_state(false);
        let r = rig(vec![game(true, true)], false, e.dir.path());
        let heard = Rc::new(Cell::new(false));
        let h = heard.clone();
        r.ff.connect(move |on| h.set(on));
        r.settle();
        r.ff.memory(8000, 8000);
        assert!(heard.get() && r.ff.active());
        assert!(!gamemode::active());
        assert!(r.registered.borrow().is_empty() && r.lightened.borrow().is_empty() && r.profile.lock().unwrap().is_empty());
    }
}
