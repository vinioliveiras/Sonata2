//! Desktop-wide choices Settings applies (sonata2/*.py: title bars, traffic
//! lights, cursors, GPU, game mode, keyring, backups, default apps...) and
//! the writing half of prefs.py.
//!
//! Each module is its Python namesake, with the same files, keys and
//! commands, so the Python and Rust builds share their state. Where the
//! Python takes a `Gio.AppInfo`, the Rust takes a `&gio::AppInfo` and has a
//! `*_key` twin taking the app's key (its desktop id without ".desktop"),
//! the form the tests use.

pub mod adwstyle;
pub mod appperms;
pub mod backup;
pub mod browsergpu;
pub mod cursors;
pub mod defaultapps;
pub mod displaysleep;
pub mod factory_reset;
pub mod flatpak_theme;
pub mod fpslimit;
pub mod frame;
pub mod gamemode;
pub mod gpu;
pub mod housekeeping;
pub mod keyring;
pub mod powerprofile;
pub mod prefs_write;
pub mod shortcuts;
pub mod steamtheme;
pub mod titlebars;
pub mod trafficlights;
pub mod videowall;
pub mod wallpapers;

mod util;
