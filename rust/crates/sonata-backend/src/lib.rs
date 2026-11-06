//! The shell's Linux services (sonata2/backend/ and the helpers it uses):
//! running the system's tools, Wayfire's config and IPC, sound effects,
//! per-device volumes, and the system state Settings and the menu bar show.

pub mod bluez;
pub mod devicevolume;
pub mod procfs;
pub mod run;
pub mod sounds;
pub mod stats;
pub mod system;
pub mod usbprotect;
pub mod userdirs;
pub mod vault;
pub mod wfconfig;
pub mod wfipc;
