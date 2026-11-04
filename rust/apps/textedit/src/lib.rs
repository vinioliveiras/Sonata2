//! TextEdit (macOS TextEdit, plain text, with Sublime-style extras), ported
//! from `sonata2/textedit/` (docs/RUST_PORT.md).

pub mod document;
pub mod search;
pub mod session;
#[cfg(feature = "gui")]
pub mod source;
#[cfg(feature = "gui")]
pub mod tabs;
#[cfg(feature = "gui")]
pub mod window;

/// The app id: on trial its own (`sonata2rs`), so both TextEdits can be open.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.textedit"
    } else {
        "io.github.vinioliveiras.sonata2.textedit"
    }
}
