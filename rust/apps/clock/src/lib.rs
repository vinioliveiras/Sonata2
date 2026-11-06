//! Clock (macOS Ventura Clock): Alarms, Stopwatch, Timers, ported from
//! `sonata2/clock/` (docs/RUST_PORT.md). Its alarms and timers are the
//! Python Clock's own files: the menu bar rings them.

pub mod alarms;
pub mod sound;
pub mod timers;
#[cfg(feature = "gui")]
pub mod pages;
#[cfg(feature = "gui")]
pub mod window;
#[cfg(test)]
mod testenv;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.clock"
}
