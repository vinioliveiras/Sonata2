//! When the menu bar's background work steps back (sonata2/quiet.py): the
//! screen is locked (nobody sees the menu bar) or a fullscreen app -- a
//! game -- has the focus (it gets the machine to itself).
//!
//! Its readers ask paused() before each run and catch up when it ends:
//! watch(f) calls f(paused) whenever it changes. Both states are files
//! other processes write (the lock screen's marker, gamemode's state), so
//! it works the same in every process; the file monitors are made once.
//!
//! The Rust bar asked paused() (Services::quiet_paused) but never heard the
//! pause end: after an unlock or a game, Wi-Fi, the battery and the mic /
//! camera dots stayed as they were before it until the next look -- two
//! minutes when the states come as signals (quiet.watch in Python).

use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;

/// The lock screen holds the session: its marker names a live process (a
/// lock screen that crashed left it behind).
pub fn locked() -> bool {
    let Ok(text) = std::fs::read_to_string(sonata_shell::lockdisplay::locked_marker()) else { return false };
    let pid: i32 = text.trim().parse().unwrap_or(0);
    pid > 0 && std::path::Path::new(&format!("/proc/{pid}")).exists()
}

/// A fullscreen app has the focus (gamemode.py's state file).
pub fn fullscreen() -> bool {
    sonata_tweaks::gamemode::active()
}

pub fn paused() -> bool {
    locked() || fullscreen()
}

/// The files whose changes may change paused().
pub fn files() -> Vec<PathBuf> {
    vec![sonata_tweaks::gamemode::state(), sonata_shell::lockdisplay::locked_marker()]
}

type Listener = Rc<dyn Fn(bool)>;

struct Watch {
    listeners: RefCell<Vec<Listener>>,
    last: Cell<bool>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
}

thread_local! {
    static WATCH: RefCell<Option<Rc<Watch>>> = const { RefCell::new(None) };
}

impl Watch {
    /// Look again: the listeners hear a change only.
    fn changed(&self, now: bool) {
        if now == self.last.replace(now) {
            return;
        }
        let all: Vec<Listener> = self.listeners.borrow().clone();
        for f in all {
            f(now);
        }
    }
}

fn shared() -> Rc<Watch> {
    WATCH.with(|w| {
        w.borrow_mut()
            .get_or_insert_with(|| {
                let me = Rc::new(Watch { listeners: RefCell::default(), last: Cell::new(paused()), monitors: RefCell::default() });
                for path in files() {
                    if let Some(dir) = path.parent() {
                        let _ = std::fs::create_dir_all(dir);
                    }
                    let Ok(mon) = gio::File::for_path(&path).monitor_file(gio::FileMonitorFlags::WATCH_MOVES, gio::Cancellable::NONE) else { continue };
                    let w = Rc::downgrade(&me);
                    mon.connect_changed(move |_, _, _, _| {
                        if let Some(w) = w.upgrade() {
                            w.changed(paused());
                        }
                    });
                    me.monitors.borrow_mut().push(mon);
                }
                me
            })
            .clone()
    })
}

/// f(paused) on the main loop whenever paused() changes (for the process).
pub fn watch(f: impl Fn(bool) + 'static) {
    shared().listeners.borrow_mut().push(Rc::new(f));
}

/// The pause's state looked at again now (tests; a monitor that missed it).
pub fn recheck() {
    let w = shared();
    w.changed(paused());
}

#[cfg(test)]
mod tests {
    use super::*;

    /// After an unlock (or the end of a game) the bar's readers hear it at
    /// once, and only on a change.
    #[test]
    fn test_end_of_a_pause_never_heard() {
        let _e = crate::testenv::env();
        WATCH.with(|w| w.replace(None)); // (a watch of this test's own files)
        let heard: Rc<RefCell<Vec<bool>>> = Rc::default();
        let h = heard.clone();
        watch(move |p| h.borrow_mut().push(p));
        let state = sonata_tweaks::gamemode::state();
        std::fs::create_dir_all(state.parent().unwrap()).unwrap();
        std::fs::write(&state, "1").unwrap();
        recheck();
        recheck(); // the same state: nothing new
        std::fs::write(&state, "0").unwrap();
        recheck();
        assert_eq!(*heard.borrow(), vec![true, false]);
        // the lock screen's marker: a live pid is locked, a stale one isn't
        let marker = sonata_shell::lockdisplay::locked_marker();
        std::fs::write(&marker, std::process::id().to_string()).unwrap();
        assert!(locked() && paused());
        std::fs::write(&marker, "999999999").unwrap();
        assert!(!locked());
        WATCH.with(|w| w.replace(None));
    }
}
