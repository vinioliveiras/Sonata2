//! The system services Settings and the shell talk to (sonata2/backend/:
//! sound, Bluetooth, users, updates, locales, location, permissions...).
//!
//! One module per Python module, the same functions in snake_case, the same
//! files and commands. What blocks (pactl, D-Bus calls to system services,
//! package managers) says so: call it off the main loop, with run_async.

pub mod appimages;
pub mod appmanage;
pub mod audiofollow;
pub mod bluez;
pub mod compose;
pub mod devicevolume;
pub mod equalizer;
pub mod gtkstyle;
pub mod inuse;
pub mod linereader;
pub mod locales;
pub mod location;
pub mod memguard;
pub mod mixer;
pub mod pactl_watch;
pub mod permstore;
pub mod power;
pub mod py;
pub mod screenshare;
pub mod security;
pub mod selfupdate;
pub mod trace;
pub mod uninstall;
pub mod updates;
pub mod users;
pub mod xsettings;

/// system.run_async: `work` in a worker thread, `done(result)` back on the
/// main loop (the thread-default context).
pub fn run_async<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static, done: impl FnOnce(T) + 'static) {
    glib::spawn_future_local(async move {
        if let Ok(r) = gio::spawn_blocking(work).await {
            done(r);
        }
    });
}

/// (exit code, stdout) of `cmd` given `input` on its stdin, killed after
/// `timeout_s` (a stuck tool must not hang the caller). 127: not installed.
pub fn run_input(cmd: &[&str], input: &str, timeout_s: u64) -> (i32, String) {
    run_full(cmd, Some(input), &[], timeout_s)
}

/// (exit code, stdout whatever the exit code) of `cmd` in English
/// (LC_ALL=C: its output is parsed), killed after `timeout_s`.
pub fn run_c(cmd: &[&str], timeout_s: u64) -> (i32, String) {
    run_full(cmd, None, &[("LC_ALL", "C")], timeout_s)
}

/// run_input / run_c: stdin from `input` (else none), extra `env`; stderr
/// dropped. 127: not installed; 1 and "" when it can't start or times out.
pub fn run_full(cmd: &[&str], input: Option<&str>, env: &[(&str, &str)], timeout_s: u64) -> (i32, String) {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};
    if sonata_backend::run::which(cmd[0]).is_none() {
        return (127, String::new());
    }
    let mut c = Command::new(cmd[0]);
    c.args(&cmd[1..]).envs(env.iter().copied()).stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::piped()).stderr(Stdio::null());
    let Ok(mut child) = c.spawn() else { return (1, String::new()) };
    if let (Some(mut i), Some(data)) = (child.stdin.take(), input) {
        let _ = i.write_all(data.as_bytes());
    }
    let mut out = child.stdout.take();
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(o) = out.as_mut() {
            let _ = o.read_to_string(&mut s);
        }
        s
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) if std::time::Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
            Err(_) => break None,
        }
    };
    let out = reader.join().unwrap_or_default();
    match status {
        Some(st) => (st.code().unwrap_or(1), out),
        None => (1, String::new()),
    }
}

/// The tests share one process (cargo runs them on threads): the ones using
/// the main loop or writing settings take turns, and own GLib's default
/// context meanwhile (timeouts go there).
#[cfg(test)]
pub(crate) fn test_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// `f` on one thread kept for the tests' main loop, which owns GLib's
/// default context: timeouts a test leaves behind (a recheck, a retry) run
/// there later, never on another test's thread.
#[cfg(test)]
pub(crate) fn on_main<R: Send + 'static>(f: impl FnOnce() -> R + Send + 'static) -> R {
    type Job = Box<dyn FnOnce() + Send>;
    static MAIN: std::sync::OnceLock<std::sync::Mutex<std::sync::mpsc::Sender<Job>>> = std::sync::OnceLock::new();
    let tx = MAIN
        .get_or_init(|| {
            let (tx, rx) = std::sync::mpsc::channel::<Job>();
            std::thread::spawn(move || {
                let ctx = glib::MainContext::default();
                let _g = ctx.acquire().expect("main context");
                for job in rx {
                    job();
                }
            });
            std::sync::Mutex::new(tx)
        })
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    let _l = test_lock();
    let (rtx, rrx) = std::sync::mpsc::channel();
    tx.send(Box::new(move || {
        let _ = rtx.send(std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)));
    }))
    .unwrap();
    match rrx.recv().unwrap() {
        Ok(r) => r,
        Err(e) => std::panic::resume_unwind(e),
    }
}

/// Run the main loop for `ms` (inside on_main).
#[cfg(test)]
pub(crate) fn spin(ms: u64) {
    let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
    let ctx = glib::MainContext::default();
    while std::time::Instant::now() < end {
        if !ctx.iteration(false) {
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
    }
}
