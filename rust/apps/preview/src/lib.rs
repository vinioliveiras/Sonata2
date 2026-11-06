//! Preview (macOS Preview, images), ported from `sonata2/preview/`
//! (docs/RUST_PORT.md).

pub mod canvas;
pub mod edit;
pub mod imageload;
pub mod info;
pub mod markup;
pub mod marks;
pub mod panel;
pub mod sidebar;
pub mod window;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.preview"
}
