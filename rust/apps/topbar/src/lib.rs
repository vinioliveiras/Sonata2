//! The menu bar (sonata2/shell/topbar.py) and its parts: stats, media, sound mixer, OSD
//!
//! Ported so far: the leaf parts the menu bar and Control Center build
//! on -- statsui (performance figures), mpris (Now Playing), mixer_ui (the
//! per-app volumes), osd (the volume / brightness HUD), topbar_prefs and
//! menubar_size, plus controls (Control Center's slider and round button).

pub mod controls;
pub mod menubar_size;
pub mod mixer_ui;
pub mod mpris;
pub mod osd;
pub mod statsui;
pub mod topbar_prefs;

#[cfg(test)]
mod testenv;
