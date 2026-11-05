//! A temporary home for one test: XDG folders of its own, English,
//! nothing of the real session (tests/__init__.py's rule). Tests share the
//! process environment: one at a time.

use std::sync::{Mutex, MutexGuard};

static ENV: Mutex<()> = Mutex::new(());

pub struct Env {
    pub dir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

/// The runtime folder the tests started with (a private Broadway
/// display's socket is there), while `f` runs.
pub fn with_runtime_dir_of_the_session<R>(f: impl FnOnce() -> R) -> R {
    let ours = std::env::var_os("XDG_RUNTIME_DIR");
    if let Some(d) = START_RUNTIME.get_or_init(|| None) {
        std::env::set_var("XDG_RUNTIME_DIR", d);
    }
    let r = f();
    if let Some(o) = ours {
        std::env::set_var("XDG_RUNTIME_DIR", o);
    }
    r
}

static START_RUNTIME: std::sync::OnceLock<Option<std::ffi::OsString>> = std::sync::OnceLock::new();

pub fn env() -> Env {
    START_RUNTIME.get_or_init(|| std::env::var_os("XDG_RUNTIME_DIR"));
    let guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
    let dir = tempfile::tempdir().unwrap();
    for (k, sub) in [("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "run")] {
        let p = dir.path().join(sub);
        std::fs::create_dir_all(&p).unwrap();
        std::env::set_var(k, p);
    }
    std::env::remove_var("WAYFIRE_SOCKET");
    std::env::remove_var("DBUS_SESSION_BUS_ADDRESS");
    sonata_i18n::use_language("en");
    Env { dir, _guard: guard }
}
