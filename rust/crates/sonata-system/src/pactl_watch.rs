//! One `pactl subscribe` reader per process for Sonata's audio watchers
//! (backend/pactl_watch.py: Settings' Sound page, the menu bar's mixer,
//! equalizer, in-use indicator and new-device follow): watch() adds a
//! callback to it.
//!
//! Lines arrive through linereader on the GTK main loop; if pactl dies
//! (PipeWire restarted, pipewire-pulse crashed) it is started again,
//! waiting longer each time it keeps dying. Main thread only.

use crate::linereader::read_lines;
use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::{Duration, Instant};

/// first wait, longest wait
pub const RESPAWN_MS: (u64, u64) = (1000, 30000);
/// ran this long: the next death waits the shortest again
pub const STABLE_S: u64 = 30;

struct Inner {
    cmd: Vec<String>,
    respawn: (u64, u64),
    on_line: Rc<dyn Fn(&str)>,
    proc: RefCell<Option<gio::Subprocess>>,
    cancel: RefCell<Option<gio::Cancellable>>,
    stopped: Cell<bool>,
    wait: Cell<u64>,
    src: RefCell<Option<glib::SourceId>>,
    started: Cell<Option<Instant>>,
}

/// on_line(text) for every line of `pactl subscribe` (or `cmd`); stop()
/// ends it for good.
#[derive(Clone)]
pub struct PactlWatch(Rc<Inner>);

impl PactlWatch {
    pub fn new(on_line: impl Fn(&str) + 'static) -> Self {
        Self::with_command(&["pactl", "subscribe"], RESPAWN_MS, on_line)
    }

    pub fn with_command(cmd: &[&str], respawn: (u64, u64), on_line: impl Fn(&str) + 'static) -> Self {
        Self(Rc::new(Inner {
            cmd: cmd.iter().map(|s| s.to_string()).collect(),
            respawn,
            on_line: Rc::new(on_line),
            proc: RefCell::default(),
            cancel: RefCell::default(),
            stopped: Cell::new(false),
            wait: Cell::new(respawn.0),
            src: RefCell::default(),
            started: Cell::new(None),
        }))
    }

    pub fn stopped(&self) -> bool {
        self.0.stopped.get()
    }

    pub fn start(&self) -> bool {
        let me = &self.0;
        if me.stopped.get() {
            return false;
        }
        let argv: Vec<&std::ffi::OsStr> = me.cmd.iter().map(std::ffi::OsStr::new).collect();
        let Ok(proc) = gio::Subprocess::newv(&argv, gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE) else {
            return false;
        };
        me.started.set(Some(Instant::now()));
        let cancel = gio::Cancellable::new();
        *me.cancel.borrow_mut() = Some(cancel.clone());
        let Some(out) = proc.stdout_pipe() else { return false };
        *me.proc.borrow_mut() = Some(proc);
        let on_line = me.on_line.clone();
        let weak = Rc::downgrade(&self.0);
        let c = cancel.clone();
        read_lines(&out, Some(&cancel), move |t| on_line(t), move || {
            // pactl ended
            if !c.is_cancelled() {
                if let Some(inner) = weak.upgrade() {
                    PactlWatch(inner).respawn();
                }
            }
        });
        true
    }

    fn respawn(&self) {
        let me = &self.0;
        self.end_proc();
        if me.stopped.get() || me.src.borrow().is_some() {
            return;
        }
        if me.started.get().is_some_and(|t| t.elapsed() >= Duration::from_secs(STABLE_S)) {
            me.wait.set(me.respawn.0);
        }
        let weak = Rc::downgrade(&self.0);
        let src = glib::timeout_add_local_once(Duration::from_millis(me.wait.get()), move || {
            if let Some(inner) = weak.upgrade() {
                inner.src.borrow_mut().take();
                let w = PactlWatch(inner);
                if !w.start() {
                    w.respawn();
                }
            }
        });
        *me.src.borrow_mut() = Some(src);
        me.wait.set((me.wait.get() * 2).min(me.respawn.1));
    }

    fn end_proc(&self) {
        if let Some(c) = self.0.cancel.borrow_mut().take() {
            c.cancel();
        }
        if let Some(p) = self.0.proc.borrow_mut().take() {
            p.force_exit();
            // reaped without blocking: no zombie
            p.wait_async(gio::Cancellable::NONE, |_| {});
        }
    }

    pub fn stop(&self) {
        self.0.stopped.set(true);
        if let Some(src) = self.0.src.borrow_mut().take() {
            sonata_ui::cancel(src);
        }
        self.end_proc();
    }
}

/// One watcher's subscription to the shared reader. stop() unsubscribes
/// it; the reader ends with the last one.
pub struct Handle {
    id: Cell<u64>,
}

impl Handle {
    pub fn stop(&self) {
        let id = self.id.replace(0);
        if id != 0 {
            HUB.with(|h| h.unwatch(id));
        }
    }
}

type Watcher = (u64, Rc<dyn Fn(&str)>);

/// The one `pactl subscribe` of this process, its lines fanned out to
/// every watcher (each used to start its own: five readers and five
/// wake-ups per event in the menu bar process).
#[derive(Default)]
struct Hub {
    handles: Rc<RefCell<Vec<Watcher>>>,
    reader: RefCell<Option<PactlWatch>>,
    next: Cell<u64>,
}

impl Hub {
    fn watch(&self, on_line: Rc<dyn Fn(&str)>) -> Option<Handle> {
        if self.reader.borrow().as_ref().is_none_or(|r| r.stopped()) {
            let handles = self.handles.clone();
            let reader = PactlWatch::new(move |text| {
                let all: Vec<Rc<dyn Fn(&str)>> = handles.borrow().iter().map(|(_, f)| f.clone()).collect();
                for f in all {
                    // one broken watcher doesn't starve the others
                    if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(text))).is_err() {
                        eprintln!("sonata2: pactl watcher failed");
                    }
                }
            });
            if !reader.start() {
                return None;
            }
            *self.reader.borrow_mut() = Some(reader);
        }
        let id = self.next.get() + 1;
        self.next.set(id);
        self.handles.borrow_mut().push((id, on_line));
        Some(Handle { id: Cell::new(id) })
    }

    fn unwatch(&self, id: u64) {
        self.handles.borrow_mut().retain(|(i, _)| *i != id);
        if self.handles.borrow().is_empty() {
            if let Some(r) = self.reader.borrow_mut().take() {
                r.stop();
            }
        }
    }
}

thread_local! {
    static HUB: Hub = Hub::default();
}

/// on_line(text) for every `pactl subscribe` line, from the process's one
/// shared reader (started on the first watch, restarted if pactl dies,
/// ended when the last watcher stops). None without pactl.
pub fn watch(on_line: impl Fn(&str) + 'static) -> Option<Handle> {
    sonata_backend::run::which("pactl")?;
    HUB.with(|h| h.watch(Rc::new(on_line)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A pactl that prints two lines (one of them not UTF-8) and dies: its
    /// lines come, it is started again, and never once stopped.
    #[test]
    fn restarted_when_pactl_dies_and_never_after_stop() {
        let dir = tempfile::tempdir().unwrap();
        let count = dir.path().join("count");
        let script = dir.path().join("pactl");
        std::fs::write(&script, format!("#!/bin/sh\necho x >> {}\necho \"Event 'change' on sink #1\"\nprintf '\\377\\n'\n", count.display())).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).unwrap();
        crate::on_main(move || {
            let ctx = glib::MainContext::default();
            let lines: Rc<RefCell<Vec<String>>> = Rc::default();
            let l = lines.clone();
            let w = PactlWatch::with_command(&[script.to_str().unwrap()], (50, 100), move |t| l.borrow_mut().push(t.to_string()));
            assert!(w.start());
            let lp = glib::MainLoop::new(Some(&ctx), false);
            let q = lp.clone();
            glib::timeout_add_local_once(Duration::from_millis(1500), move || q.quit());
            lp.run();
            w.stop();
            assert!(lines.borrow().iter().any(|l| l == "Event 'change' on sink #1"));
            assert!(lines.borrow().iter().any(|l| l == "\u{fffd}"));
            let n = std::fs::read_to_string(&count).unwrap().lines().count();
            assert!(n >= 2, "{n}");
            let lp = glib::MainLoop::new(Some(&ctx), false);
            let q = lp.clone();
            glib::timeout_add_local_once(Duration::from_millis(400), move || q.quit());
            lp.run();
            assert_eq!(std::fs::read_to_string(&count).unwrap().lines().count(), n);
        });
    }
}
