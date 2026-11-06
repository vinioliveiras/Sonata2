//! Sonata's design system in Rust: tokens + themed CSS, the counterpart of
//! Python Sonata's `ui/` (docs/DESIGN.md). Every value comes from the
//! tokens (tokens.json); the CSS templates are css.json's.
//!
//! ```no_run
//! sonata_ui::setup();                          // once per process, after the app started
//! sonata_ui::register("calculator", "button { background: %(calc_key)s; }");
//! ```

pub mod apps;
pub mod controls;
pub mod css;
pub mod dialog;
pub mod fixed;
pub mod fmt;
pub mod frame;
pub mod gallery;
pub mod glass;
pub mod icons;
pub mod menu;
pub mod module_slider;
pub mod panel;
pub mod progress;
pub mod theme;
pub mod timer;
pub mod tokens;
pub mod transition;
pub mod window;

pub use timer::cancel;
pub use theme::{force_appearance, glass, is_dark, on_buttons_change, on_change, register, register_with, rgba, setup, values};

/// GTK for a test, false with no display. A failed gtk::init is
/// remembered: GTK marks itself initialised on that attempt, so a second
/// call answered Ok and the next widget brought the whole test binary down
/// (SIGSEGV, every later test lost) -- as sonata-shell's.
#[cfg(test)]
pub(crate) fn gtk_for_tests() -> bool {
    // (one attempt, under a lock: the tests run on several threads, and two
    // gtk::init at once crashed too)
    static TRIED: std::sync::Mutex<Option<bool>> = std::sync::Mutex::new(None);
    let mut tried = TRIED.lock().unwrap_or_else(|e| e.into_inner());
    match *tried {
        Some(false) => false,
        Some(true) => gtk::is_initialized_main_thread(), // (GTK is one thread's: elsewhere, skipped)
        None => {
            let ok = gtk::init().is_ok();
            *tried = Some(ok);
            ok
        }
    }
}
