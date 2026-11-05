//! A temporary home for one test: XDG data and config folders of its own,
//! English, nothing of the real session (tests/__init__.py's rule). Tests
//! share the process environment: one at a time.

use std::sync::{Mutex, MutexGuard};

pub(crate) static ENV: Mutex<()> = Mutex::new(());

pub struct Env {
    _dir: tempfile::TempDir,
    _guard: MutexGuard<'static, ()>,
}

/// The variables env() changes, as the run had them (taken before the
/// first change): a test run in a process of its own (gtk_tests) gets
/// these, not the last test's temporary folders -- a Broadway display's
/// socket is in the run's own runtime folder.
pub fn original() -> &'static [(&'static str, Option<std::ffi::OsString>)] {
    static O: std::sync::OnceLock<Vec<(&'static str, Option<std::ffi::OsString>)>> = std::sync::OnceLock::new();
    O.get_or_init(|| ["XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"].into_iter().map(|k| (k, std::env::var_os(k))).collect())
}

pub fn env() -> Env {
    let guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
    original();
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("XDG_DATA_HOME", dir.path().join("data"));
    std::env::set_var("XDG_CONFIG_HOME", dir.path().join("config"));
    std::env::set_var("XDG_RUNTIME_DIR", dir.path().join("run"));
    std::env::remove_var("WAYFIRE_SOCKET");
    std::env::remove_var("DBUS_SESSION_BUS_ADDRESS");
    sonata_i18n::use_language("en");
    Env { _dir: dir, _guard: guard }
}
