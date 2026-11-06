//! Wi-Fi, battery and volume for every display's bar (topbar.py's
//! _StatusPoll): read once per tick and handed to each live bar (it was
//! one set of nmcli/wpctl runs per bar, every POLL_S). Nothing runs while
//! the screen is locked or a fullscreen game has the focus
//! (Services::quiet_paused). The volume is read only while a bar shows the
//! Sound item, and then from `pactl subscribe` events when pactl is there
//! (no polling).
//!
//! Wi-Fi and battery come from NetworkManager's and UPower's signals when
//! they are there: no nmcli every POLL_S (up to three runs a tick, all day
//! long), only a look every PUSHED_POLL_S in case a signal was missed.
//! Without them (or without pactl) it polls as it always did.
//!
//! What it reads, waits on and watches goes through [`Ports`], so the
//! rules are tested without a bus, a pactl or a main loop.

use crate::bar::{bars, Bar, POLL_S, PUSHED_POLL_S};
use crate::kit::run_async;
use gtk::{gio, glib};
use sonata_backend::system;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::{Duration, Instant};

/// A burst of NetworkManager signals -> one read.
pub const NET_SETTLE_MS: u64 = 1000;
/// A burst of `pactl subscribe` events -> one read.
pub const AUDIO_SETTLE_MS: u64 = 120;

thread_local! {
    static POLL: RefCell<Option<Rc<StatusPoll>>> = RefCell::default();
}

/// What a tick asks for; the real ports read it off the main loop and
/// hand it to every bar.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Read {
    Wifi,
    Battery,
    Volume,
    /// nobody shows Sound: every bar hides it, nothing is read
    NoSound,
}

type Later = Box<dyn Fn(Duration, Box<dyn FnOnce()>)>;
type Stop = Box<dyn FnOnce()>;

/// What the poller does to the world.
pub(crate) struct Ports {
    pub read: Box<dyn Fn(Read)>,
    /// once, after a while (glib's timeout)
    pub later: Later,
    /// `changed` on every sound change; how to stop watching, None without pactl
    pub watch_audio: Box<dyn Fn(Rc<dyn Fn()>) -> Option<Stop>>,
    pub showing_sound: Box<dyn Fn() -> bool>,
    pub paused: Box<dyn Fn() -> bool>,
}

enum Audio {
    /// nobody shows Sound: no reads, no subscription
    Off,
    Watching(Stop),
    /// no pactl: polled
    NoPactl,
}

pub(crate) struct StatusPoll {
    ports: Ports,
    /// NetworkManager's signals reach us (else Wi-Fi is polled)
    net: bool,
    /// UPower's signals reach us (bar.rs's power_watch; else the battery is polled)
    upower: bool,
    looked: Cell<Instant>,
    wifi_at: Cell<Instant>,
    net_due: Cell<bool>,
    audio: RefCell<Audio>,
    me: Weak<StatusPoll>,
}

impl StatusPoll {
    pub(crate) fn new(ports: Ports, net: bool, upower: bool) -> Rc<StatusPoll> {
        let me = Rc::new_cyclic(|me| StatusPoll {
            ports,
            net,
            upower,
            looked: Cell::new(Instant::now()),
            wifi_at: Cell::new(Instant::now()),
            net_due: Cell::new(false),
            audio: RefCell::new(Audio::Off),
            me: me.clone(),
        });
        me.schedule();
        me
    }

    /// The one poller of this process, with the real ports.
    pub(crate) fn shared(paused: Option<Rc<dyn Fn() -> bool>>) -> Rc<StatusPoll> {
        if let Some(p) = POLL.with(|p| p.borrow().clone()) {
            return p;
        }
        let net = watch_network(|| {
            if let Some(p) = POLL.with(|p| p.borrow().clone()) {
                p.net_changed();
            }
        });
        let upower = crate::bar::power_watch() && system::system_service("org.freedesktop.UPower");
        let busy: Rc<RefCell<Vec<Read>>> = Rc::default();
        let ports = Ports {
            read: Box::new(move |what| real_read(&busy, what)),
            later: Box::new(|wait, f| {
                glib::timeout_add_local_once(wait, f);
            }),
            watch_audio: Box::new(|changed| watch_audio(changed).map(|h| Box::new(move || h.stop()) as Stop)),
            showing_sound: Box::new(|| bars().iter().any(|b| b.cfg_bool("show_sound", false))),
            // (quiet.py's own answer when the host gave none)
            paused: Box::new(move || paused.as_ref().map_or_else(crate::quiet::paused, |p| p())),
        };
        let me = StatusPoll::new(ports, net, upower);
        POLL.with(|p| *p.borrow_mut() = Some(me.clone()));
        // it catches up the moment the pause ends (quiet.watch): a signal
        // that came while locked or under a game was dropped
        let w = Rc::downgrade(&me);
        crate::quiet::watch(move |paused| {
            if let Some(p) = w.upgrade() {
                p.pause_changed(paused);
            }
        });
        me
    }

    /// The process's poller, if one started (UPower's signals ask it).
    pub(crate) fn current() -> Option<Rc<StatusPoll>> {
        POLL.with(|p| p.borrow().clone())
    }

    /// Nothing to poll: Wi-Fi, battery and the volume all come as events.
    fn pushed(&self) -> bool {
        self.net && self.upower && !matches!(*self.audio.borrow(), Audio::NoPactl)
    }

    fn schedule(&self) {
        let secs = if self.pushed() { PUSHED_POLL_S } else { POLL_S };
        let w = self.me.clone();
        (self.ports.later)(Duration::from_secs(secs), Box::new(move || {
            if let Some(me) = w.upgrade() {
                me.tick();
            }
        }));
    }

    fn tick(&self) {
        if self.looked.get().elapsed().as_secs() >= PUSHED_POLL_S - POLL_S {
            self.poll(true, true); // now and then everything, signals or not
        } else {
            self.poll(!self.net, !self.upower);
        }
        self.schedule();
    }

    /// NetworkManager changed something: one read for a burst of signals,
    /// and never more often than the old poll (a scan sends one per network).
    pub(crate) fn net_changed(&self) {
        if self.net_due.replace(true) {
            return;
        }
        let since = self.wifi_at.get().elapsed().as_millis() as u64;
        let wait = NET_SETTLE_MS.max((POLL_S * 1000).saturating_sub(since));
        let w = self.me.clone();
        (self.ports.later)(Duration::from_millis(wait), Box::new(move || {
            if let Some(me) = w.upgrade() {
                me.net_due.set(false);
                if !(me.ports.paused)() {
                    me.wifi(); // (the end of a pause reads everything anyway)
                }
            }
        }));
    }

    fn wifi(&self) {
        self.wifi_at.set(Instant::now());
        (self.ports.read)(Read::Wifi);
    }

    pub(crate) fn poll(&self, wifi: bool, battery: bool) {
        if (self.ports.paused)() {
            return; // locked, or a fullscreen game/video has the focus: stay out of its way
        }
        if wifi && battery {
            self.looked.set(Instant::now());
        }
        if wifi {
            self.wifi();
        }
        if battery {
            self.battery();
        }
        self.volume();
    }

    /// quiet.watch: the pause ended -- everything read again at once.
    pub(crate) fn pause_changed(&self, paused: bool) {
        if !paused {
            self.poll(true, true);
        }
    }

    pub(crate) fn battery(&self) {
        (self.ports.read)(Read::Battery);
    }

    fn volume(&self) {
        if !(self.ports.showing_sound)() {
            // (Python calls kill() on its False -- no pactl -- here; nothing to stop then)
            if let Audio::Watching(stop) = self.audio.replace(Audio::Off) {
                stop();
            }
            (self.ports.read)(Read::NoSound);
            return;
        }
        let off = matches!(*self.audio.borrow(), Audio::Off);
        if off {
            let w = self.me.clone();
            let changed: Rc<dyn Fn()> = Rc::new(move || {
                if let Some(me) = w.upgrade() {
                    (me.ports.read)(Read::Volume);
                }
            });
            let a = match (self.ports.watch_audio)(changed) {
                Some(stop) => Audio::Watching(stop),
                None => Audio::NoPactl,
            };
            *self.audio.borrow_mut() = a;
            (self.ports.read)(Read::Volume);
        } else if matches!(*self.audio.borrow(), Audio::NoPactl) {
            (self.ports.read)(Read::Volume);
        }
    }
}

/// A read off the main loop, handed to every bar; a second ask while one
/// runs is dropped.
fn real_read(busy: &Rc<RefCell<Vec<Read>>>, what: Read) {
    fn deliver<T: Send + 'static>(busy: &Rc<RefCell<Vec<Read>>>, what: Read, work: impl FnOnce() -> T + Send + 'static, give: impl Fn(&Rc<Bar>, &T) + 'static) {
        if busy.borrow().contains(&what) {
            return;
        }
        busy.borrow_mut().push(what);
        let busy = busy.clone();
        run_async(work, move |res| {
            busy.borrow_mut().retain(|k| *k != what);
            for b in bars() {
                give(&b, &res);
            }
        });
    }
    match what {
        Read::Wifi => deliver(busy, what, system::wifi_status, |b, r| b.wifi_state(r)),
        Read::Battery => deliver(
            busy,
            what,
            || {
                let (pct, status) = system::battery(system::POWER_SUPPLY);
                (pct, status, system::on_ac(system::POWER_SUPPLY), system::power_profile_fast())
            },
            |b, r| b.battery_state(r),
        ),
        Read::Volume => deliver(busy, what, system::volume, |b, r| b.sound_state(*r)),
        Read::NoSound => {
            for b in bars() {
                b.sound_state(None);
            }
        }
    }
}

const NM: &str = "org.freedesktop.NetworkManager";
/// The interfaces whose changes the Wi-Fi icon shows (radio, devices,
/// connections, signal); only these reach this process (the bus filters).
const NM_IFACES: [&str; 4] = [
    "org.freedesktop.NetworkManager",
    "org.freedesktop.NetworkManager.Device",
    "org.freedesktop.NetworkManager.Device.Wireless",
    "org.freedesktop.NetworkManager.AccessPoint",
];

/// changed() on the main loop whenever NetworkManager reports a change
/// the Wi-Fi icon shows (system.watch_network). False without
/// NetworkManager: keep polling. Kept for the process (one poller).
pub fn watch_network(changed: impl Fn() + 'static) -> bool {
    if !system::system_service(NM) {
        return false;
    }
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return false };
    let changed = Rc::new(changed);
    for iface in NM_IFACES {
        let c = changed.clone();
        #[allow(deprecated)]
        bus.signal_subscribe(Some(NM), Some("org.freedesktop.DBus.Properties"), Some("PropertiesChanged"), None, Some(iface), gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| c());
    }
    let c = changed.clone();
    #[allow(deprecated)]
    bus.signal_subscribe(Some(NM), Some(NM), Some("StateChanged"), Some("/org/freedesktop/NetworkManager"), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| c());
    true
}

/// A `pactl subscribe` line about a sink or a source changing, or the
/// server (a new default device). Not "on sink-input": every stream that
/// plays sends those, many a second.
pub fn audio_event(text: &str) -> bool {
    text.contains("on sink #") || text.contains("on source #") || text.contains("on server #") // (pactl: "on server #-1", no quotes)
}

/// The line handler of watch_audio: a burst of events, one changed().
fn audio_lines(later: Later, changed: Rc<dyn Fn()>) -> impl Fn(&str) {
    let due = Rc::new(Cell::new(false));
    move |text| {
        if audio_event(text) && !due.replace(true) {
            let (d, c) = (due.clone(), changed.clone());
            later(Duration::from_millis(AUDIO_SETTLE_MS), Box::new(move || {
                d.set(false);
                c();
            }));
        }
    }
}

/// changed() on the main loop whenever a sink or source changes (volume,
/// mute, devices), through the process's `pactl subscribe`
/// (system.watch_audio). None without pactl.
pub fn watch_audio(changed: Rc<dyn Fn()>) -> Option<sonata_system::pactl_watch::Handle> {
    let later: Later = Box::new(|wait, f| {
        glib::timeout_add_local_once(wait, f);
    });
    sonata_system::pactl_watch::watch(audio_lines(later, changed))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A new default output (the server's event) never updated Sound:
    /// pactl prints "on server #...", and the filter looked for "'server'".
    #[test]
    fn test_new_default_device_never_noticed() {
        assert!(audio_event("Event 'change' on server #4294967295"));
        assert!(!audio_event("Event 'change' on sink-input #812"));
    }

    type Timers = Rc<RefCell<Vec<(Duration, Option<Box<dyn FnOnce()>>)>>>;

    struct Fake {
        reads: Rc<RefCell<Vec<Read>>>,
        timers: Timers,
        watches: Rc<RefCell<Vec<Rc<dyn Fn()>>>>,
        stopped: Rc<Cell<u32>>,
        sound: Rc<Cell<bool>>,
        paused: Rc<Cell<bool>>,
    }

    fn make(net: bool, upower: bool, pactl: bool) -> (Rc<StatusPoll>, Fake) {
        let f = Fake {
            reads: Rc::default(),
            timers: Rc::default(),
            watches: Rc::default(),
            stopped: Rc::default(),
            sound: Rc::default(),
            paused: Rc::default(),
        };
        let (r, t, w, s, snd, p) = (f.reads.clone(), f.timers.clone(), f.watches.clone(), f.stopped.clone(), f.sound.clone(), f.paused.clone());
        let ports = Ports {
            read: Box::new(move |what| r.borrow_mut().push(what)),
            later: Box::new(move |wait, fun| t.borrow_mut().push((wait, Some(fun)))),
            watch_audio: Box::new(move |changed| {
                if !pactl {
                    return None;
                }
                w.borrow_mut().push(changed);
                let s = s.clone();
                Some(Box::new(move || s.set(s.get() + 1)) as Stop)
            }),
            showing_sound: Box::new(move || snd.get()),
            paused: Box::new(move || p.get()),
        };
        (StatusPoll::new(ports, net, upower), f)
    }

    impl Fake {
        fn count(&self, what: Read) -> usize {
            self.reads.borrow().iter().filter(|r| **r == what).count()
        }
        fn waits(&self) -> Vec<u64> {
            self.timers.borrow().iter().map(|t| t.0.as_millis() as u64).collect()
        }
        /// Fires timer `i` (once).
        fn fire(&self, i: usize) {
            let f = self.timers.borrow_mut()[i].1.take().unwrap();
            f();
        }
    }

    /// Product-wide check: the menu bar ran nmcli every 10 s, all day (up
    /// to three runs a tick with Wi-Fi on), and woke a thread for the
    /// battery. With NetworkManager and UPower on the bus their signals
    /// say when to look.
    /// Vini's Rust session: after unlocking (or leaving a game) the Wi-Fi
    /// and battery icons kept what they showed before it -- the signals
    /// that came meanwhile were dropped and nothing read them again until
    /// the next look, two minutes later. The end of the pause reads them.
    #[test]
    fn test_icons_stale_after_unlock() {
        let (p, f) = make(true, true, true);
        f.paused.set(true);
        p.net_changed();
        f.fire(1); // the network's settle: paused, dropped
        assert_eq!(f.count(Read::Wifi), 0);
        p.pause_changed(true);
        assert_eq!(f.count(Read::Wifi), 0);
        f.paused.set(false);
        p.pause_changed(false);
        assert_eq!((f.count(Read::Wifi), f.count(Read::Battery)), (1, 1));
    }

    #[test]
    fn test_signals_not_a_poll() {
        let (p, f) = make(true, true, true);
        assert_eq!(f.waits(), [PUSHED_POLL_S * 1000]); // a look every 2 min, not 10 s
        f.fire(0); // the tick
        assert_eq!(f.count(Read::Wifi), 0);
        assert_eq!(f.count(Read::Battery), 0); // battery: UPower's signals
        for _ in 0..30 {
            p.net_changed(); // a scan: a signal per network
        }
        assert_eq!(f.timers.borrow().len(), 3); // the next tick, and one read for the burst
        assert!(f.waits()[2] >= NET_SETTLE_MS);
        f.fire(2);
        assert_eq!(f.count(Read::Wifi), 1);
        p.net_changed(); // a new burst: a new read
        assert_eq!(f.timers.borrow().len(), 4);
        // now and then: everything
        p.looked.set(Instant::now().checked_sub(Duration::from_secs(PUSHED_POLL_S)).unwrap());
        f.fire(1);
        assert_eq!(f.count(Read::Wifi), 2);
        assert_eq!(f.count(Read::Battery), 1);
    }

    #[test]
    fn test_without_network_manager_it_polls_as_before() {
        let (_p, f) = make(false, false, true);
        assert_eq!(f.waits(), [POLL_S * 1000]);
        f.fire(0);
        f.fire(1);
        assert_eq!(f.count(Read::Wifi), 2);
        assert_eq!(f.count(Read::Battery), 2);
        assert_eq!(*f.waits().last().unwrap(), POLL_S * 1000);
    }

    /// Two displays: one nmcli/battery read per tick for both bars, and no
    /// volume while Sound isn't shown.
    #[test]
    fn test_one_read_for_every_bar() {
        let (p, f) = make(false, false, true);
        p.poll(true, true);
        assert_eq!(*f.reads.borrow(), [Read::Wifi, Read::Battery, Read::NoSound]);
        assert!(f.watches.borrow().is_empty()); // no subscription either
    }

    #[test]
    fn test_nothing_while_paused() {
        let (p, f) = make(true, true, true);
        f.sound.set(true);
        f.paused.set(true);
        p.poll(true, true);
        p.net_changed();
        f.fire(1);
        assert!(f.reads.borrow().is_empty());
    }

    #[test]
    fn test_volume_from_events_when_sound_shown() {
        let (p, f) = make(true, true, true);
        f.sound.set(true);
        p.poll(false, false);
        p.poll(false, false);
        assert_eq!(f.watches.borrow().len(), 1);
        assert_eq!(f.count(Read::Volume), 1); // second tick: no poll, events only
        let changed = f.watches.borrow()[0].clone();
        changed(); // the volume changed elsewhere (keys, another app)
        assert_eq!(f.count(Read::Volume), 2);
        // Sound hidden: the subscription ends
        f.sound.set(false);
        p.poll(false, false);
        assert_eq!(f.stopped.get(), 1);
        assert_eq!(f.count(Read::NoSound), 1);
    }

    #[test]
    fn test_volume_polled_without_pactl() {
        let (p, f) = make(true, true, false);
        f.sound.set(true);
        p.poll(false, false);
        p.poll(false, false);
        assert_eq!(f.count(Read::Volume), 2);
        // no pactl: the volume needs the short tick
        p.tick();
        assert_eq!(*f.waits().last().unwrap(), POLL_S * 1000);
        f.sound.set(false);
        p.poll(false, false); // (nothing to stop: no panic)
        assert_eq!(f.stopped.get(), 0);
    }

    #[test]
    fn test_watch_audio_debounces_sink_events() {
        let timers: Timers = Rc::default();
        let got = Rc::new(Cell::new(0));
        let (t, g) = (timers.clone(), got.clone());
        let line = audio_lines(Box::new(move |wait, f| t.borrow_mut().push((wait, Some(f)))), Rc::new(move || g.set(g.get() + 1)));
        for _ in 0..5 {
            line("Event 'change' on sink #3");
        }
        line("Event 'change' on client #9");
        line("Event 'change' on sink-input #12");
        assert_eq!(timers.borrow().len(), 1);
        assert_eq!(timers.borrow()[0].0, Duration::from_millis(AUDIO_SETTLE_MS));
        let f = timers.borrow_mut()[0].1.take().unwrap();
        f();
        assert_eq!(got.get(), 1);
        line("Event 'change' on source #4"); // after the burst: heard again
        assert_eq!(timers.borrow().len(), 2);
    }

    #[test]
    fn audio_events() {
        assert!(audio_event("Event 'change' on source #50"));
        assert!(!audio_event("Event 'new' on sink-input #7"));
        assert!(!audio_event("Event 'change' on client #9"));
    }
}
