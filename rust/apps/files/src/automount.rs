//! Every disk mounted and shown (files/automount.py, Vini's call): internal
//! partitions (the Windows one of a dual boot, a data disk...) and anything
//! plugged in are mounted as soon as Files runs (it's resident from login)
//! and when they appear. System partitions (EFI, swap, recovery...) never
//! show up here: the volume monitor (udisks) already leaves them out.
//!
//! Mounting an internal disk is a system action: install.sh adds a polkit
//! rule so the logged-in administrator isn't asked for a password each
//! time. A Windows disk left dirty (Fast Startup, hibernation) is mounted
//! read-only (rodisk.rs); one that can't be mounted at all is left alone
//! and logged.

use crate::{mountop, rodisk};
use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::HashSet;

thread_local! {
    static STARTED: Cell<bool> = const { Cell::new(false) };
    /// disks that failed once this session: not tried again
    static TRIED: RefCell<HashSet<String>> = RefCell::default();
}

/// The bits of a volume automount looks at (a fake one in tests).
pub trait Vol {
    fn mounted(&self) -> bool;
    fn can_mount(&self) -> bool;
    /// what tells this disk apart from the others (device, UUID or name)
    fn key(&self) -> String;
    fn name(&self) -> String;
    fn device(&self) -> Option<String>;
    /// done(Err(GIO's message)) when it couldn't be mounted
    fn mount(&self, done: Box<dyn FnOnce(Result<(), String>)>);
}

impl Vol for gio::Volume {
    fn mounted(&self) -> bool {
        self.get_mount().is_some()
    }
    fn can_mount(&self) -> bool {
        VolumeExt::can_mount(self)
    }
    fn key(&self) -> String {
        self.identifier("unix-device").or_else(|| self.uuid()).map(|s| s.to_string()).unwrap_or_else(|| VolumeExt::name(self).to_string())
    }
    fn name(&self) -> String {
        VolumeExt::name(self).to_string()
    }
    fn device(&self) -> Option<String> {
        self.identifier("unix-device").map(|s| s.to_string())
    }
    fn mount(&self, done: Box<dyn FnOnce(Result<(), String>)>) {
        // an encrypted disk plugged in: Sonata's password alert
        VolumeExt::mount(self, gio::MountMountFlags::NONE, Some(&mountop::new(None)), gio::Cancellable::NONE, move |r| done(r.map_err(|e| e.message().to_string())));
    }
}

pub fn mount_one(vol: &dyn Vol) {
    if vol.mounted() || !vol.can_mount() {
        return;
    }
    let key = vol.key();
    if !TRIED.with(|t| t.borrow_mut().insert(key.clone())) {
        return; // failed once this session: don't insist
    }
    let (name, device) = (vol.name(), vol.device());
    vol.mount(Box::new(move |res| match res {
        Ok(()) => {
            TRIED.with(|t| t.borrow_mut().remove(&key));
        }
        // hibernated NTFS, a cancelled password...
        Err(msg) => match device {
            // a Windows disk left dirty: shown, read-only
            Some(dev) if rodisk::try_readonly(&dev, &msg) => rodisk::mount_readonly(&dev, move |r| match r {
                Ok(_) => eprintln!("sonata2-files: {name} mounted read-only (dirty)"),
                Err(e) => eprintln!("sonata2-files: {name} couldn't be mounted read-only: {e}"),
            }),
            _ => eprintln!("sonata2-files: couldn't mount {name}: {msg}"),
        },
    }));
}

/// Mount every disk now and each one that appears (once per process).
pub fn start() {
    if STARTED.with(|s| s.replace(true)) {
        return;
    }
    let monitor = gio::VolumeMonitor::get();
    for vol in monitor.volumes() {
        mount_one(&vol);
    }
    monitor.connect_volume_added(|_, vol| mount_one(vol));
    // the monitor stops reporting once the last reference goes
    std::mem::forget(monitor);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::rc::Rc;

    struct Fake {
        fail: bool,
        mounted: bool,
        can: bool,
        calls: Rc<Cell<u32>>,
        key: String,
    }

    impl Fake {
        fn new(key: &str) -> Self {
            Fake { fail: false, mounted: false, can: true, calls: Rc::default(), key: key.into() }
        }
    }

    impl Vol for Fake {
        fn mounted(&self) -> bool {
            self.mounted
        }
        fn can_mount(&self) -> bool {
            self.can
        }
        fn key(&self) -> String {
            self.key.clone()
        }
        fn name(&self) -> String {
            "Disk".into()
        }
        fn device(&self) -> Option<String> {
            None // (no read-only retry: that would ask the real UDisks)
        }
        fn mount(&self, done: Box<dyn FnOnce(Result<(), String>)>) {
            self.calls.set(self.calls.get() + 1);
            done(if self.fail { Err("Not authorized".into()) } else { Ok(()) });
        }
    }

    /// Mounted / unmountable volumes are skipped; a failed one isn't
    /// retried in the same session; a successful one may be again later.
    #[test]
    fn mounts_once_and_never_insists() {
        let skip = Fake { mounted: true, ..Fake::new("/dev/a") };
        let nomount = Fake { can: false, ..Fake::new("/dev/b") };
        mount_one(&skip);
        mount_one(&nomount);
        assert_eq!((skip.calls.get(), nomount.calls.get()), (0, 0));
        let bad = Fake { fail: true, ..Fake::new("/dev/c") };
        mount_one(&bad);
        mount_one(&bad);
        assert_eq!(bad.calls.get(), 1);
        let ok = Fake::new("/dev/d");
        mount_one(&ok);
        mount_one(&ok);
        assert_eq!(ok.calls.get(), 2);
    }
}
