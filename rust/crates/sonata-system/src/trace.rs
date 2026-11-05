//! Startup timing for Sonata's processes (trace.py; their logs in
//! ~/.cache/sonata2).
//!
//! ```ignore
//! trace::start("dock");      // time 0; a note in the log if not ready in 8 s
//! trace::mark("activate");   // "[trace dock] +0.42s activate (22:40:59.512)"
//! trace::ready("ready");     // first frame reached: stop the watchdog
//! ```
//!
//! If a process is still starting after WATCHDOG seconds, the log says so
//! with the last step it reached, once, then again every WATCHDOG seconds
//! until ready() -- where a login hangs, in one look. (The Python one dumps
//! every thread's stack with faulthandler; Rust can't walk other threads'
//! stacks, and the last step named narrows it down as well.)

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const WATCHDOG: Duration = Duration::from_secs(8);

struct State {
    t0: Instant,
    name: String,
    last: String,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);
static DONE: AtomicBool = AtomicBool::new(false);

fn state() -> std::sync::MutexGuard<'static, Option<State>> {
    STATE.lock().unwrap_or_else(|e| e.into_inner())
}

pub fn start(name: &str) {
    *state() = Some(State { t0: Instant::now(), name: name.to_string(), last: "start".into() });
    DONE.store(false, Ordering::SeqCst);
    mark("start");
    let _ = std::thread::Builder::new().name("trace-watchdog".into()).spawn(|| loop {
        std::thread::sleep(WATCHDOG);
        if DONE.load(Ordering::SeqCst) {
            return;
        }
        if let Some(s) = state().as_ref() {
            eprintln!("[trace {}] still starting after {:.0}s: last step \"{}\"", s.name, s.t0.elapsed().as_secs_f64(), s.last);
        }
    });
}

/// The line mark() writes.
pub fn line(name: &str, elapsed: Duration, label: &str, wall: &str) -> String {
    format!("[trace {name}] +{:.2}s {label} ({wall})", elapsed.as_secs_f64())
}

fn wall() -> String {
    glib::DateTime::now_local().map(|d| format!("{}.{:03}", d.format("%H:%M:%S").unwrap_or_default(), d.microsecond() / 1000)).unwrap_or_default()
}

pub fn mark(label: &str) {
    let mut st = state();
    let Some(s) = st.as_mut() else { return };
    s.last = label.to_string();
    // start-up timings: detailed logs only
    if sonata_session::logs::verbose() {
        eprintln!("{}", line(&s.name, s.t0.elapsed(), label, &wall()));
    }
}

pub fn ready(label: &str) {
    if state().is_none() || DONE.swap(true, Ordering::SeqCst) {
        return;
    }
    mark(label);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lines_and_ready_once() {
        assert_eq!(line("dock", Duration::from_millis(420), "activate", "22:40:59.512"), "[trace dock] +0.42s activate (22:40:59.512)");
        ready("nothing started: nothing to do");
        assert!(!DONE.load(Ordering::SeqCst));
        start("test");
        mark("step");
        assert_eq!(state().as_ref().unwrap().last, "step");
        ready("ready");
        assert!(DONE.load(Ordering::SeqCst));
        assert_eq!(state().as_ref().unwrap().last, "ready");
        ready("again");
        assert_eq!(state().as_ref().unwrap().last, "ready"); // once
    }
}
