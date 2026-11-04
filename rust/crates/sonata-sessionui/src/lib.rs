//! The session's background helpers (sonata2/shell/): what the menu bar
//! process runs besides the bar itself, what the Dock runs besides the
//! Dock, and two small commands of their own.
//!
//! | Python | here | runs in |
//! |---|---|---|
//! | idlelock.py | [`idlelock`] | the menu bar ([`idlelock::IdleLock`]); `lock-wait` ([`idlelock::run_lock_wait`]) |
//! | idlepolicy.py | [`idlepolicy`] | the menu bar, under IdleLock |
//! | blackout.py | [`blackout`] | the menu bar (IdleLock's darkening) |
//! | screencorners.py | [`screencorners`] | the menu bar ([`screencorners::ScreenCorners`]); lock / login screens ([`screencorners::CornersOverlay`]) |
//! | nightshift.py | [`nightshift`] | the menu bar ([`nightshift::NightShift`]); Control Center / Settings ([`nightshift::set_manual`], [`nightshift::is_on`]) |
//! | quitapps.py | [`quitapps`] | the menu bar and Settings ([`quitapps::end_session`]); Dock and app menu ([`quitapps::quit_app`]) |
//! | quitonclose.py | [`quitonclose`] | the Dock ([`quitonclose::QuitOnClose`]); every launcher ([`quitonclose::mark_launch`]) |
//! | monitors.py | [`monitors`] | wallpaper, menu bar, Dock |
//! | alarmservice.py | [`alarmservice`] | the menu bar ([`alarmservice::AlarmService`]) |
//! | updatenotify.py | [`updatenotify`] | the menu bar ([`updatenotify::UpdateNotifier`]) |
//! | setup.py | [`setup`] | `sonata2-rs setup [--preview]` ([`setup::run_setup`]) |
//!
//! welcome.py is `sonata-welcome` (apps/welcome).
//!
//! # Side by side (trial)
//!
//! While the Python shell runs, its menu bar already keeps swayidle, the
//! idle darkening, wlsunset, the screen corners, the alarms and the update
//! check; its Dock quits apps on close. A second copy would fight it (two
//! idle timers, two gamma clients, every alarm ringing twice). So on trial
//! (`sonata_config::side_by_side()`) the services' `start()` return None and
//! change nothing; `sonata2-rs session-try <what>` ([`run_try`]) shows the
//! parts that can be looked at safely.

pub mod alarmservice;
pub mod blackout;
pub mod idlelock;
pub mod idlepolicy;
pub mod monitors;
pub mod nightshift;
pub mod quitapps;
pub mod quitonclose;
pub mod screencorners;
pub mod setup;
pub mod trial;
pub mod updatenotify;

pub use trial::run_try;

use serde_json::Value;

/// Python's int() of a config value: a number (a float cut toward zero),
/// a numeric string, a bool; None where int() raises.
pub(crate) fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python's truthiness of a config value.
pub(crate) fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A command's output, or None when it can't run or takes longer than
/// `timeout_ms` (killed then). stderr is dropped: only the answer is parsed.
pub(crate) fn output_within(cmd: &[&str], timeout_ms: u64) -> Option<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new(cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = out.read_to_string(&mut s);
        s
    });
    let end = std::time::Instant::now() + std::time::Duration::from_millis(timeout_ms);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if std::time::Instant::now() < end => std::thread::sleep(std::time::Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    reader.join().ok()
}

/// What runs `sonata2-rs`: this binary (a component started from the
/// session), else the name on PATH.
pub(crate) fn self_exe() -> String {
    std::env::current_exe().ok().filter(|p| !p.to_string_lossy().contains("/deps/")).map(|p| p.display().to_string()).unwrap_or_else(|| "sonata2-rs".into())
}

#[cfg(test)]
pub(crate) mod testenv;
