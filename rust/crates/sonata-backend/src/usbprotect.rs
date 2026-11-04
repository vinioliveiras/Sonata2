//! USB devices plugged in while the screen is locked are blocked
//! (backend/usbprotect.py, its lock screen half): with USBGuard running,
//! the lock screen sets its InsertedDevicePolicy to "block" when it locks
//! and puts the previous one back when it unlocks. Never left blocked by
//! mistake: the previous policy is kept in a file while locked (the Python
//! menu bar puts it back if the lock screen went away without doing it).

use gio::prelude::*;
use std::path::PathBuf;

const BUS: &str = "org.usbguard1";
const PATH: &str = "/org/usbguard1";
const PARAM: &str = "InsertedDevicePolicy";
const TIMEOUT_MS: i32 = 2000;

pub fn marker() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp")).join("sonata2-usb-blocked")
}

fn bus() -> Option<gio::DBusConnection> {
    gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE).ok()
}

fn dbus<T: glib::variant::FromVariant>(method: &str, args: Option<&glib::Variant>, reply: &str) -> Option<T> {
    bus()?
        .call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", method, args, Some(glib::VariantTy::new(reply).ok()?), gio::DBusCallFlags::NONE, TIMEOUT_MS, gio::Cancellable::NONE)
        .ok()?
        .child_value(0)
        .get()
}

/// usbguard-dbus running, or started by the bus when asked.
pub fn available() -> bool {
    dbus::<bool>("NameHasOwner", Some(&(BUS,).to_variant()), "(b)").unwrap_or(false) || dbus::<Vec<String>>("ListActivatableNames", None, "(as)").is_some_and(|n| n.iter().any(|x| x == BUS))
}

/// Sets the policy; returns the previous one (None: failed).
fn set(value: &str) -> Option<String> {
    match bus()?.call_sync(Some(BUS), PATH, BUS, "setParameter", Some(&(PARAM, value).to_variant()), Some(glib::VariantTy::new("(s)").ok()?), gio::DBusCallFlags::NONE, TIMEOUT_MS, gio::Cancellable::NONE) {
        Ok(r) => r.child_value(0).get(),
        Err(e) => {
            eprintln!("sonata2-rs: USB protection: {}", e.message());
            None
        }
    }
}

/// The screen locked: new devices blocked (the previous policy kept).
pub fn block() -> bool {
    if marker().exists() || !available() {
        return false;
    }
    let Some(prev) = set("block") else { return false };
    let prev = if prev.is_empty() { "apply-policy".to_string() } else { prev };
    if std::fs::write(marker(), &prev).is_err() {
        set(&prev); // can't remember it: never leave it blocked
        return false;
    }
    true
}

/// What to put back: the saved policy, never "block" for good.
pub fn to_restore(saved: &str) -> &str {
    match saved.trim() {
        "" | "block" => "apply-policy",
        s => s,
    }
}

/// Unlocked: the policy it had before.
pub fn restore() -> bool {
    let Ok(saved) = std::fs::read_to_string(marker()) else { return false };
    if set(to_restore(&saved)).is_none() {
        return false;
    }
    let _ = std::fs::remove_file(marker());
    true
}

#[cfg(test)]
mod tests {
    #[test]
    fn never_restored_into_blocking() {
        assert_eq!(super::to_restore("block"), "apply-policy");
        assert_eq!(super::to_restore(""), "apply-policy");
        assert_eq!(super::to_restore("allow\n"), "allow");
    }
}
