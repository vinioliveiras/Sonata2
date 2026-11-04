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

/// The app id: on trial its own (`sonata2rs`), so both Notes can be open.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.notes"
    } else {
        "io.github.vinioliveiras.sonata2.notes"
    }
}
