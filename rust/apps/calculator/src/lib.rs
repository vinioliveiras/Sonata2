//! Calculator, the first Sonata component ported to Rust (docs/RUST_PORT.md).

pub mod decimal;
pub mod engine;
#[cfg(feature = "gui")]
pub mod window;

/// The app id: on trial next to the Python Sonata, its own
/// (`sonata2rs`), so both Calculators can be open at once.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.calculator"
    } else {
        "io.github.vinioliveiras.sonata2.calculator"
    }
}

