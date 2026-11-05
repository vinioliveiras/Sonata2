//! Bluetooth through BlueZ's D-Bus API (backend/bluez.py): it lives in
//! sonata-backend now, so system.rs's bluetooth_* can use it (sonata-backend
//! can't depend on this crate). Kept here under its old path for the
//! callers that know it as sonata_system::bluez.

pub use sonata_backend::bluez::*;
