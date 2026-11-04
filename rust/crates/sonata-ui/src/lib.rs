//! Sonata's design system in Rust: tokens + themed CSS, the counterpart of
//! `sonata2/ui/` (docs/DESIGN.md). Every value comes from the tokens, which
//! come from `sonata2/ui/tokens.py` (tools/export_tokens.py).
//!
//! ```no_run
//! sonata_ui::setup();                          // once per process, after the app started
//! sonata_ui::register("calculator", "button { background: %(calc_key)s; }");
//! ```

pub mod theme;
pub mod tokens;
pub mod window;

pub use theme::{force_appearance, is_dark, on_change, register, register_with, setup, values};
