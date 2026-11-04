//! The login intro (shell/intro.py): from the login screen to the desktop
//! without a jump. sonata-session leaves a marker file before Wayfire
//! starts; while it is there the Dock and the menu bar start out of sight
//! and the welcome screen covers the display until the shell is ready.
//! A restart of Sonata leaves its own marker.

use std::path::PathBuf;

fn runtime() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

/// (the Python shell's markers: one session, one intro)
pub fn mark() -> PathBuf {
    runtime().join("sonata2-intro")
}

pub fn restart_mark() -> PathBuf {
    runtime().join("sonata2-restarting")
}

pub fn pending() -> bool {
    mark().exists()
}

pub fn restarting() -> bool {
    restart_mark().exists()
}

/// Start out of sight and slide in (the login intro, or a restart of Sonata).
pub fn entering() -> bool {
    pending() || restarting()
}

/// The intro is over: the Dock and the menu bar slide in.
pub fn finish() {
    let _ = std::fs::remove_file(mark());
}

/// Left by a restarted component once it's ready (restart waits for both).
pub fn ready_file(name: &str) -> PathBuf {
    PathBuf::from(format!("{}.{name}", restart_mark().display()))
}
