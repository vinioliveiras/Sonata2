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

/// The tests that move XDG_DATA_HOME take turns (one process, one environment).
#[cfg(test)]
pub(crate) static TEST_ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.textedit"
}
