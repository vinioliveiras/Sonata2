//! A temporary home for one test: XDG data and config folders of its own,
//! English, nothing of the real session (tests/__init__.py's rule). Tests
//! share the process environment: one at a time.

use std::sync::{Mutex, MutexGuard};

static ENV: Mutex<()> = Mutex::new(());

pub struct Env {
    _dir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

pub fn env() -> Env {
    let guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("XDG_DATA_HOME", dir.path().join("data"));
    std::env::set_var("XDG_CONFIG_HOME", dir.path().join("config"));
    std::env::set_var("XDG_RUNTIME_DIR", dir.path().join("run"));
    std::env::remove_var("WAYFIRE_SOCKET");
    std::env::remove_var("DBUS_SESSION_BUS_ADDRESS");
    sonata_i18n::use_language("en");
    Env { _dir: dir, _guard: guard }
}
