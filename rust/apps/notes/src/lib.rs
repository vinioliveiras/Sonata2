//! Notes (macOS Ventura Notes, with Reminders in the same window), ported
//! from `sonata2/notes/` (docs/RUST_PORT.md).

#[cfg(feature = "gui")]
pub mod editor;
pub mod markup;
#[cfg(feature = "gui")]
pub mod notifier;
#[cfg(feature = "gui")]
pub mod reminders;
pub mod store;
#[cfg(feature = "gui")]
pub mod window;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.notes"
}
