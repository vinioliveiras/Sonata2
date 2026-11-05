//! Finds what freezes a Sonata process (sonata2/stallwatch.py; Vini: lag
//! when closing apps).
//!
//! Off unless ~/.cache/sonata2/stallwatch exists (tools/closelag.sh makes it
//! for a minute): then the process checks its main loop every 50 ms, and
//! when the loop doesn't answer within `STALL_MS`, a line goes to
//! stalls.log -- the process and how long it stayed stuck. Off, it costs
//! nothing: the flag is watched (inotify) and the thread runs only while
//! the flag exists (a stat() a second in every process, all session long,
//! was a wakeup a second each for a tool run now and then).
//!
//! The Python version also writes the main thread's stack; a Rust thread
//! can't read another thread's stack safely, so the line names the
//! process and the time only -- with the time, the component's own log
//! says what it was doing.
//!
//! Who starts it: every Sonata process, once, on its main thread after GTK
//! is set up (Python: ui/theme.setup). On trial too, unlike the menu bar's
//! services: it only writes a log line, and closelag.sh's report is more
//! useful with the Rust processes in it.

use gio::prelude::*;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub const STALL_MS: u64 = 80;
const EVERY: Duration = Duration::from_millis(50);
/// How long a stuck loop is waited for, to say how long it stayed stuck.
const GIVE_UP: Duration = Duration::from_secs(10);

static STARTED: AtomicBool = AtomicBool::new(false);

thread_local! {
    static KEPT: std::cell::RefCell<Option<Watch>> = const { std::cell::RefCell::new(None) };
}

/// The flag tools/closelag.sh makes (the Python Sonata's cache: one flag
/// for every process, on trial too).
pub fn flag_path() -> PathBuf {
    let cache = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from);
    cache.unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".cache")).join("sonata2").join("stallwatch")
}

/// stalls.log next to the Python processes' (the session's logs), so one
/// report has them all.
pub fn log_path() -> PathBuf {
    let d = crate::logs::session_log_dir();
    let _ = std::fs::create_dir_all(&d);
    d.join("stalls.log")
}

fn clock() -> String {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) as libc::time_t;
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe {
        libc::localtime_r(&t, &mut tm);
    }
    format!("{:02}:{:02}:{:02}", tm.tm_hour, tm.tm_min, tm.tm_sec)
}

fn append(log: &Path, text: &str) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(log) {
        let _ = f.write_all(text.as_bytes());
    }
}

pub fn write(log: &Path, label: &str, ms: f64) {
    append(log, &format!("{} {label} pid {}: main loop stuck {ms:.0} ms\n\n", clock(), std::process::id()));
}

/// One look: does `ctx` run a high-priority idle within STALL_MS? If not,
/// how long it took (up to GIVE_UP) is logged. True: it was stuck.
pub fn probe(label: &str, ctx: &glib::MainContext, log: &Path) -> bool {
    let (tx, rx) = std::sync::mpsc::channel::<()>();
    let t0 = Instant::now();
    // a source, never MainContext::invoke: that runs the closure right here
    // when the loop happens to be free of an owner, and "answers" for it
    glib::idle_source_new(None, glib::Priority::HIGH, move || {
        let _ = tx.send(());
        glib::ControlFlow::Break
    })
    .attach(Some(ctx));
    if rx.recv_timeout(Duration::from_millis(STALL_MS)).is_ok() {
        return false;
    }
    let _ = rx.recv_timeout(GIVE_UP);
    write(log, label, t0.elapsed().as_secs_f64() * 1000.0);
    true
}

/// What the thread and the flag's monitor share (any thread may check).
struct Shared {
    label: String,
    flag: PathBuf,
    log: PathBuf,
    ctx: glib::MainContext,
    running: AtomicBool,
}

fn check(sh: &Arc<Shared>) {
    if !sh.flag.exists() || sh.running.swap(true, Ordering::SeqCst) {
        return;
    }
    // the report shows which processes were watched
    append(&sh.log, &format!("{} watching {} pid {}\n", clock(), sh.label, std::process::id()));
    let me = sh.clone();
    let spawned = std::thread::Builder::new().name("stallwatch".into()).spawn(move || {
        while me.flag.exists() {
            probe(&me.label, &me.ctx, &me.log);
            std::thread::sleep(EVERY);
        }
        me.running.store(false, Ordering::SeqCst);
        check(&me); // made again as it ended: on again
    });
    if spawned.is_err() {
        sh.running.store(false, Ordering::SeqCst);
    }
}

/// The flag's monitor and the state; the monitor lives as long as this.
pub struct Watch {
    shared: Arc<Shared>,
    _mon: Option<gio::FileMonitor>,
}

impl Watch {
    /// Watches the main loop of the thread-default context (the main one
    /// on the main thread).
    pub fn new(label: &str, flag: PathBuf, log: PathBuf) -> Self {
        let shared = Arc::new(Shared { label: label.into(), flag, log, ctx: glib::MainContext::ref_thread_default(), running: AtomicBool::new(false) });
        // (the folder made now: GIO polls a missing one every few seconds)
        let mon = shared.flag.parent().and_then(|d| std::fs::create_dir_all(d).ok()).and_then(|_| gio::File::for_path(&shared.flag).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE).ok());
        match &mon {
            Some(m) => {
                let sh = shared.clone();
                m.connect_changed(move |_, _, _, _| check(&sh));
            }
            None => eprintln!("sonata2-rs: stallwatch: {} not watched", shared.flag.display()), // (a read-only cache)
        }
        check(&shared);
        Watch { shared, _mon: mon }
    }

    pub fn running(&self) -> bool {
        self.shared.running.load(Ordering::SeqCst)
    }
}

/// Watch this process's main loop: call once, from the main thread, after
/// GTK is set up. `label` names the process in the log ("dock",
/// "settings"...). Later calls do nothing; the watch stays for the
/// process's life.
pub fn start(label: &str) {
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    let w = Watch::new(label, flag_path(), log_path());
    KEPT.with(|k| *k.borrow_mut() = Some(w));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spin(ctx: &glib::MainContext, ms: u64) {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            while ctx.iteration(false) {}
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    /// Vini: lag when closing apps -- a main loop held up is logged, with
    /// the process's name and how long.
    #[test]
    fn test_logs_a_stall() {
        let d = tempfile::tempdir().unwrap();
        let log = d.path().join("stalls.log");
        let ctx = glib::MainContext::new();
        let _own = ctx.acquire().unwrap();
        let (c, l) = (ctx.clone(), log.clone());
        let t = std::thread::spawn(move || probe("dock", &c, &l));
        std::thread::sleep(Duration::from_millis(300)); // the main loop stuck here
        let end = Instant::now() + Duration::from_secs(3);
        while !t.is_finished() && Instant::now() < end {
            ctx.iteration(false);
        }
        assert!(t.join().unwrap());
        let text = std::fs::read_to_string(&log).unwrap();
        assert!(text.contains("dock pid ") && text.contains("main loop stuck"), "{text}");
        // an answering loop: nothing logged
        let (c, l) = (ctx.clone(), d.path().join("quiet.log"));
        let t = std::thread::spawn(move || probe("dock", &c, &l));
        while !t.is_finished() {
            ctx.iteration(false);
        }
        assert!(!t.join().unwrap());
        assert!(!d.path().join("quiet.log").exists());
    }

    /// Product-wide check (Oct 2026): every Sonata process woke up once a
    /// second all session long only to stat() the flag of a diagnostics
    /// tool. No thread until the flag comes; gone again when it goes.
    #[test]
    fn test_no_thread_until_the_flag_comes() {
        let d = tempfile::tempdir().unwrap();
        let (flag, log) = (d.path().join("sonata2/stallwatch"), d.path().join("stalls.log"));
        let ctx = glib::MainContext::new();
        ctx.with_thread_default(|| {
            let w = Watch::new("test", flag.clone(), log.clone());
            spin(&ctx, 200);
            assert!(!w.running()); // off: no thread, no timer
            std::fs::write(&flag, "").unwrap(); // tools/closelag.sh
            for _ in 0..30 {
                spin(&ctx, 100);
                if w.running() {
                    break;
                }
            }
            assert!(w.running());
            std::fs::remove_file(&flag).unwrap();
            for _ in 0..30 {
                spin(&ctx, 100);
                if !w.running() {
                    break;
                }
            }
            assert!(!w.running()); // off again: gone
        })
        .unwrap();
        assert!(std::fs::read_to_string(&log).unwrap().contains("watching test pid "));
    }
}
