//! The session's plumbing, ported from `sonata2/__main__.py` and
//! `sonata2/logs.py` (docs/RUST_PORT.md, step 3): logs, and the supervisor
//! that keeps shell components running (`keep`, `keep-all`), and the
//! key bindings that need no window (`key`, `screenshot`).

pub mod keep;
pub mod keys;
pub mod logs;
