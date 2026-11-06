//! A Windows disk left "dirty" (files/rodisk.py): Windows didn't shut down
//! cleanly (Fast Startup, hibernation, a crash), so Linux refuses to mount
//! it for writing -- nothing gets damaged. Files then opens it read-only
//! (Vini: DATA and WIN couldn't be opened at all), like macOS opens every
//! NTFS disk: its files can be read and copied; to write to it again,
//! Windows has to be started and shut down (or chkdsk run).
//!
//! The read-only mount goes straight to UDisks (Filesystem.Mount with
//! options=ro): GIO's volume mount has no way to ask for it.

use gio::prelude::*;
use sonata_i18n::n_;

const UDISKS: &str = "org.freedesktop.UDisks2";
const DIRTY_WORDS: [&str; 6] = ["dirty", "unclean", "hibernat", "fast restart", "fast startup", "not cleanly unmounted"];

/// Why the disk opened read-only (English: translate where shown).
pub const NOTE: &str = n_!("Windows didn't shut it down cleanly, so it opened read-only: you can read and copy its files. To change them, start Windows and shut it down (not restart), or run chkdsk there.");

pub fn is_dirty_error(message: &str) -> bool {
    let m = message.to_lowercase();
    DIRTY_WORDS.iter().any(|w| m.contains(w))
}

/// /dev/nvme1n1p1 -> UDisks' object path for it.
pub fn block_path(device: &str) -> String {
    let name = device.rsplit('/').next().unwrap_or(device);
    let safe: String = name.chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).collect();
    format!("/org/freedesktop/UDisks2/block_devices/{safe}")
}

/// UDisks' IdType of the device ("ntfs", "vfat"...; "" if unknown).
pub fn fs_type(device: &str) -> String {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return String::new() };
    let args = ("org.freedesktop.UDisks2.Block", "IdType").to_variant();
    bus.call_sync(Some(UDISKS), &block_path(device), "org.freedesktop.DBus.Properties", "Get", Some(&args), Some(glib::VariantTy::new("(v)").unwrap()), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE)
        .ok()
        .and_then(|r| r.child_value(0).as_variant())
        .and_then(|v| v.str().map(str::to_string))
        .unwrap_or_default()
}

/// Whether a failed mount is worth retrying read-only: the error says the
/// disk is dirty, or it's NTFS (the kernel's ntfs3 only says "wrong fs
/// type, bad option..." for a dirty volume; the real reason is in the
/// kernel log).
pub fn try_readonly(device: &str, message: &str) -> bool {
    worth_readonly(message, || fs_type(device))
}

/// try_readonly with the disk's type asked only when the message alone
/// doesn't tell (asking costs a system bus call).
fn worth_readonly(message: &str, fs: impl FnOnce() -> String) -> bool {
    is_dirty_error(message) || fs() == "ntfs"
}

/// Filesystem.Mount's argument: read-only, and a password may be asked.
fn mount_args() -> glib::Variant {
    let opts = glib::VariantDict::new(None);
    opts.insert_value("options", &"ro".to_variant());
    opts.insert_value("auth.no_user_interaction", &false.to_variant());
    glib::Variant::tuple_from_iter([opts.end()])
}

/// The answer to Mount: the mount point; "" when it was mounted already.
fn mounted(res: Result<glib::Variant, glib::Error>) -> Result<String, String> {
    match res {
        Ok(v) => Ok(v.child_value(0).str().unwrap_or_default().to_string()),
        Err(e) => {
            let m = e.message().to_string();
            if m.contains("AlreadyMounted") || m.to_lowercase().contains("already mounted") {
                Ok(String::new())
            } else {
                Err(m)
            }
        }
    }
}

/// Mount `device` read-only; done(mount point, or the error), on the main loop.
pub fn mount_readonly<F: FnOnce(Result<String, String>) + 'static>(device: &str, done: F) {
    let bus = match gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) {
        Ok(b) => b,
        Err(e) => return done(Err(e.message().to_string())),
    };
    bus.call(Some(UDISKS), &block_path(device), "org.freedesktop.UDisks2.Filesystem", "Mount", Some(&mount_args()), Some(glib::VariantTy::new("(s)").unwrap()), gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION, 60000, gio::Cancellable::NONE, move |res| done(mounted(res)));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dirty_words() {
        assert!(is_dirty_error("The disk contains an unclean file system (0, 0)."));
        assert!(is_dirty_error("volume is dirty and \"force\" flag is not set"));
        assert!(is_dirty_error("Windows is hibernated, refused to mount."));
        assert!(!is_dirty_error("Not authorized to perform operation"));
    }

    /// ntfs3 only says "wrong fs type, bad option..." for a dirty volume.
    #[test]
    fn ntfs_generic_error() {
        let msg = "Error mounting /dev/nvme1n1p1: wrong fs type, bad option, bad superblock on /dev/nvme1n1p1";
        assert!(worth_readonly(msg, || "ntfs".into()));
        assert!(!worth_readonly(msg, || "ext4".into()));
        // a dirty message doesn't need to ask the disk's type
        assert!(worth_readonly("volume is dirty", || unreachable!()));
    }

    #[test]
    fn block_paths() {
        assert_eq!(block_path("/dev/nvme1n1p1"), "/org/freedesktop/UDisks2/block_devices/nvme1n1p1");
        assert_eq!(block_path("/dev/mapper/x-y"), "/org/freedesktop/UDisks2/block_devices/x_y");
    }

    #[test]
    fn readonly_option() {
        let opts = glib::VariantDict::new(Some(&mount_args().child_value(0)));
        assert_eq!(opts.lookup_value("options", None).and_then(|v| v.str().map(str::to_string)).as_deref(), Some("ro"));
        assert_eq!(opts.lookup_value("auth.no_user_interaction", None).and_then(|v| v.get::<bool>()), Some(false));
    }

    #[test]
    fn already_mounted_is_fine() {
        let e = |m: &str| Err(glib::Error::new(gio::IOErrorEnum::Failed, m));
        assert_eq!(mounted(e("GDBus.Error:org.freedesktop.UDisks2.Error.AlreadyMounted: x")), Ok(String::new()));
        assert_eq!(mounted(e("Not authorized")), Err("Not authorized".into()));
        assert_eq!(mounted(Ok(("/run/media/me/WIN",).to_variant())), Ok("/run/media/me/WIN".into()));
    }

    #[test]
    fn wired() {
        let (auto, side) = (include_str!("automount.rs"), include_str!("sidebar.rs"));
        assert!(auto.contains("rodisk::try_readonly"));
        assert!(side.contains("rodisk::try_readonly"));
        assert!(side.contains("open_readonly"));
    }
}
