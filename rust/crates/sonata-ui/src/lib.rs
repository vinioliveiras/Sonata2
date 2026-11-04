//! Sonata's design system in Rust: tokens + themed CSS, the counterpart of
//! `sonata2/ui/` (docs/DESIGN.md). Every value comes from the tokens, which
//! come from `sonata2/ui/tokens.py` (tools/export_tokens.py).
//!
//! ```no_run
//! sonata_ui::setup();                          // once per process, after the app started
//! sonata_ui::register("calculator", "button { background: %(calc_key)s; }");
//! ```

pub mod controls;
pub mod css;
pub mod dialog;
pub mod fmt;
pub mod glass;
pub mod menu;
pub mod panel;
pub mod progress;
pub mod theme;
pub mod tokens;
pub mod window;

pub use theme::{force_appearance, glass, is_dark, on_change, register, register_with, rgba, setup, values};
