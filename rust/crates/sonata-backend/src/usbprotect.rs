//! USB devices plugged in while the screen is locked are blocked
//! (backend/usbprotect.py, its lock screen and menu bar halves): with USBGuard running,
//! the lock screen sets its InsertedDevicePolicy to "block" when it locks
//! and puts the previous one back when it unlocks. Never left blocked by
//! mistake: the previous policy is kept in a file while locked; the menu
//! bar puts it back if the lock screen went away without doing it
//! (`restore_if_stale`, every `STALE_CHECK_S` through `StaleCheck`).

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
    restore_at(&marker(), set)
}

fn restore_at(marker: &std::path::Path, set: impl Fn(&str) -> Option<String>) -> bool {
    let Ok(saved) = std::fs::read_to_string(marker) else { return false };
    if set(to_restore(&saved)).is_none() {
        return false;
    }
    let _ = std::fs::remove_file(marker);
    true
}

/// The menu bar's check: blocked, yet nothing holds the lock -> put it back.
pub fn restore_if_stale(is_locked: impl Fn() -> bool) -> bool {
    restore_if_stale_at(&marker(), is_locked, set)
}

fn restore_if_stale_at(marker: &std::path::Path, is_locked: impl Fn() -> bool, set: impl Fn(&str) -> Option<String>) -> bool {
    if marker.exists() && !is_locked() {
        return restore_at(marker, set);
    }
    false
}

pub const STALE_CHECK_S: u32 = 30;

/// The menu bar's timer for `restore_if_stale` (shell/topbar.py's
/// usb_check), started once by the main display's menu bar and kept for
/// its life; dropping it (or `stop`) ends the checks. `paused` is
/// quiet.paused(): locked (nothing stale then) or a game in front -- that
/// look is skipped, the next one comes. `is_locked` is idlelock's.
/// The marker's D-Bus call runs in a thread: USBGuard can take its 2 s.
/// None on trial: the Python menu bar runs the same check on the same
/// marker, and two of them would race to restore one policy.
pub struct StaleCheck {
    src: std::cell::RefCell<Option<glib::SourceId>>,
}

impl StaleCheck {
    pub fn start(paused: impl Fn() -> bool + 'static, is_locked: impl Fn() -> bool + Send + Sync + Clone + 'static) -> Option<Self> {
        let busy = std::rc::Rc::new(std::cell::Cell::new(false));
        let src = glib::timeout_add_seconds_local(STALE_CHECK_S, move || {
            // (marker() first, on the main loop: a plain stat, and most
            // looks end here without a thread)
            if paused() || busy.get() || !marker().exists() {
                return glib::ControlFlow::Continue;
            }
            busy.set(true);
            let (busy, locked) = (busy.clone(), is_locked.clone());
            glib::MainContext::default().spawn_local(async move {
                let _ = gio::spawn_blocking(move || restore_if_stale(locked)).await;
                busy.set(false);
            });
            glib::ControlFlow::Continue
        });
        Some(StaleCheck { src: std::cell::RefCell::new(Some(src)) })
    }

    pub fn stop(&self) {
        // (only if still there: SourceId::remove panics on an ended source,
        // and this crate has no sonata_ui::cancel)
        if let Some(s) = self.src.borrow_mut().take().filter(|s| glib::MainContext::default().find_source_by_id(s).is_some()) {
            s.remove();
        }
    }
}

impl Drop for StaleCheck {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn never_restored_into_blocking() {
        assert_eq!(super::to_restore("block"), "apply-policy");
        assert_eq!(super::to_restore(""), "apply-policy");
        assert_eq!(super::to_restore("allow\n"), "allow");
    }

    /// A lock screen gone without unblocking USB: the menu bar puts the
    /// previous policy back -- never "block", even when that was saved.
    #[test]
    fn test_never_left_blocked() {
        let d = tempfile::tempdir().unwrap();
        let marker = d.path().join("sonata2-usb-blocked");
        std::fs::write(&marker, "block").unwrap(); // a broken previous value
        let calls = RefCell::new(Vec::new());
        let set = |v: &str| {
            calls.borrow_mut().push(v.to_string());
            Some("block".to_string())
        };
        assert!(restore_if_stale_at(&marker, || false, set));
        assert_eq!(*calls.borrow(), ["apply-policy"]);
        assert!(!restore_if_stale_at(&marker, || false, set)); // nothing to do
        assert!(!marker.exists());
    }

    #[test]
    fn test_still_locked_left_alone() {
        let d = tempfile::tempdir().unwrap();
        let marker = d.path().join("sonata2-usb-blocked");
        std::fs::write(&marker, "allow").unwrap();
        let called = RefCell::new(false);
        let set = |_: &str| {
            *called.borrow_mut() = true;
            None
        };
        assert!(!restore_if_stale_at(&marker, || true, set));
        assert!(!*called.borrow());
        assert!(marker.exists());
    }
}
