//! Game controllers drive the desktop (sonata2/gamepad/): menus, the app
//! switcher, the Game Bar, a virtual pointer.
//!
//! - [`evdev`]: controllers read straight from /dev/input (evdev.py)
//! - [`service`]: what each button and stick does ([`Gamepads`], service.py)
//! - [`vpointer`]: the virtual mouse (vpointer.py)
//!
//! The menu bar starts it (topbar.py), with the app switcher and the Game
//! Bar handed in as [`Targets`]:
//!
//! ```ignore
//! let pads = sonata_gamepad::Gamepads::start(&app, Rc::new(MyTargets { .. }));
//! app.connect_shutdown(move |_| pads.stop());
//! ```

pub mod evdev;
pub mod service;
pub mod vpointer;

pub use service::{Gamepads, Targets};

/// One test at a time on GLib's main loop: a source added by one test
/// thread and dispatched by another would panic (and the tests' timings
/// would mix).
#[cfg(test)]
pub(crate) fn main_loop_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// Run the main loop for `ms`.
#[cfg(test)]
pub(crate) fn settle(ms: u64) {
    let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
    let ctx = glib::MainContext::default();
    while std::time::Instant::now() < end {
        if !ctx.iteration(false) {
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
    }
}
