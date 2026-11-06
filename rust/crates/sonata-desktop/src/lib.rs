//! What the shell and apps share about apps and windows (sonata2/badges.py, applock.py, windowapps.py, launchwatch.py, open_apps.py, sandbox.py, appscope.py, folder_link.py, names.py, steamgames.py, launchpad_model.py)
//!
//! One module per Python module, the same functions in snake_case, reading
//! and writing the same files, so the Dock, Launchpad, notifications and
//! the menu bar -- Python or Rust -- see one another's data.

pub mod applock;
pub mod appmatch;
pub mod appscope;
pub mod badges;
pub mod folder_link;
pub mod launchpad_model;
pub mod launchwatch;
pub mod names;
pub mod notify;
pub mod open_apps;
pub mod sandbox;
pub mod steamgames;
pub mod windowapps;

/// The tests share one temporary HOME and XDG folders (cargo runs them on
/// threads of one process): the ones writing settings or caches take turns.
#[cfg(test)]
pub(crate) fn test_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}
