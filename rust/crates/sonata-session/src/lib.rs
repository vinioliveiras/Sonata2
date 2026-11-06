//! The session's plumbing, ported from `sonata2/__main__.py`,
//! `sonata2/logs.py`, `sonata2/doctor.py`, `sonata2/stylecheck.py` and
//! `sonata2/autostart.py` (docs/RUST_PORT.md, step 3): logs, the
//! supervisor that keeps shell components running (`keep`, `keep-all`) and
//! `restart`, the key bindings that need no window (`key`, `screenshot`),
//! `doctor` and `autostart`; and services the menu bar starts: the style
//! check (`stylecheck::Watch`), the env report (`envreport`) and, in every
//! process, the main loop stall watch (`stallwatch`).

pub mod autostart;
pub mod doctor;
pub mod envreport;
pub mod home;
pub mod keep;
pub mod keys;
pub mod logs;
pub mod procs;
pub mod restart;
pub mod stallwatch;
pub mod stylecheck;

pub use autostart::autostart;
pub use doctor::doctor;
pub use restart::restart;

#[cfg(test)]
pub(crate) mod tests {
    /// Tests that change the process' environment take turns.
    pub static ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());
}
