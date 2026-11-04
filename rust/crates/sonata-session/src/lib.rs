//! The session's plumbing, ported from `sonata2/__main__.py` and
//! `sonata2/logs.py` (docs/RUST_PORT.md, step 3): logs, and the supervisor
//! that keeps shell components running (`keep`, `keep-all`).

pub mod keep;
pub mod logs;
