//! Preview (macOS Preview, images), ported from `sonata2/preview/`
//! (docs/RUST_PORT.md).

pub mod canvas;
pub mod edit;
pub mod imageload;
pub mod info;
pub mod markup;
pub mod marks;
pub mod sidebar;
pub mod window;

/// The app id: on trial its own (`sonata2rs`), so both Previews can be open.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.preview"
    } else {
        "io.github.vinioliveiras.sonata2.preview"
    }
}
