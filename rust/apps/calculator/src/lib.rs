//! Calculator, the first Sonata component ported to Rust (docs/RUST_PORT.md).

pub mod decimal;
pub mod engine;
#[cfg(feature = "gui")]
pub mod window;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.calculator"
}

