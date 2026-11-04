//! UDisks2 over D-Bus with plain Gio (diskutil/udisks.py): the object
//! tree, live changes (InterfacesAdded/Removed, PropertiesChanged: mounts,
//! labels) and async method calls. Every call allows interactive
//! authorization, so UDisks' polkit rules (and Sonata's polkit agent)
//! decide what needs a password.

use crate::model::{tree_of, Tree};
use gio::prelude::*;
use glib::Variant;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;

pub const NAME: &str = "org.freedesktop.UDisks2";
pub const ROOT: &str = "/org/freedesktop/UDisks2";
pub const MANAGER: &str = "/org/freedesktop/UDisks2/Manager";
/// a plug-in sends a burst of signals: one reload
const DEBOUNCE_MS: u64 = 250;
/// Format / Check / Repair may take long
pub const LONG_MS: i32 = 60 * 60 * 1000;

/// An option of a UDisks call: a{sv}'s value.
pub enum Opt {
    B(bool),
    S(String),
}

/// The a{sv} options of every UDisks call (user interaction allowed).
pub fn options(extra: &[(&str, Opt)]) -> Variant {
    let mut d: HashMap<String, Variant> = HashMap::new();
    d.insert("auth.no_user_interaction".into(), false.to_variant());
    for (k, v) in extra {
        d.insert(k.to_string(), match v {
            Opt::B(b) => b.to_variant(),
            Opt::S(s) => s.to_variant(),
        });
    }
    d.to_variant()
}

/// A D-Bus error as a sentence (its "GDBus.Error:name: " left off).
pub fn error_text(err: &glib::Error) -> String {
    let msg = err.message();
    if let Some(rest) = msg.strip_prefix("GDBus.Error:") {
        if let Some((name, text)) = rest.split_once(": ") {
            if !name.contains(' ') {
                return text.to_string();
            }
        }
    }
    msg.to_string()
}

/// The user cancelled the password prompt: nothing to report.
pub fn dismissed(err: &glib::Error) -> bool {
    gio::DBusError::remote_error(err).is_some_and(|n| n.ends_with("NotAuthorizedDismissed") || n.ends_with(".Cancelled"))
}

#[derive(Default)]
pub struct Client {
    conn: RefCell<Option<gio::DBusConnection>>,
    subs: RefCell<Vec<gio::SignalSubscriptionId>>,
    pending: RefCell<Option<glib::SourceId>>,
    on_objects: RefCell<Option<Rc<dyn Fn(Tree)>>>,
    on_error: RefCell<Option<Rc<dyn Fn(String)>>>,
}

impl Client {
    /// on_objects(tree) now and after every change; on_error(message)
    /// when UDisks can't be reached.
    pub fn start(self: &Rc<Self>, on_objects: impl Fn(Tree) + 'static, on_error: impl Fn(String) + 'static) {
        self.on_objects.replace(Some(Rc::new(on_objects)));
        self.on_error.replace(Some(Rc::new(on_error)));
        let me = self.clone();
        glib::spawn_future_local(async move {
            match gio::bus_get_future(gio::BusType::System).await {
                Ok(conn) => {
                    for (iface, member) in [("org.freedesktop.DBus.ObjectManager", "InterfacesAdded"), ("org.freedesktop.DBus.ObjectManager", "InterfacesRemoved"), ("org.freedesktop.DBus.Properties", "PropertiesChanged")] {
                        let weak = Rc::downgrade(&me);
                        #[allow(deprecated)]
                        let id = conn.signal_subscribe(Some(NAME), Some(iface), Some(member), None, None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| {
                            if let Some(c) = weak.upgrade() {
                                c.changed();
                            }
                        });
                        me.subs.borrow_mut().push(id);
                    }
                    me.conn.replace(Some(conn));
                    me.reload();
                }
                Err(e) => me.error(error_text(&e)),
            }
        });
    }

    fn error(&self, msg: String) {
        let cb = self.on_error.borrow().clone();
        if let Some(f) = cb {
            f(msg);
        }
    }

    pub fn stop(&self) {
        if let Some(conn) = self.conn.borrow().as_ref() {
            for s in self.subs.take() {
                #[allow(deprecated)]
                conn.signal_unsubscribe(s);
            }
        }
        if let Some(id) = self.pending.take() {
            id.remove();
        }
    }

    fn changed(self: &Rc<Self>) {
        if self.pending.borrow().is_some() {
            return;
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(DEBOUNCE_MS), move || {
            if let Some(c) = weak.upgrade() {
                c.pending.take();
                c.reload();
            }
        });
        self.pending.replace(Some(id));
    }

    pub fn reload(self: &Rc<Self>) {
        let me = self.clone();
        glib::spawn_future_local(async move {
            match me.fetch().await {
                Ok(tree) => {
                    let cb = me.on_objects.borrow().clone();
                    if let Some(f) = cb {
                        f(tree);
                    }
                }
                Err(e) => me.error(e),
            }
        });
    }

    /// A fresh tree straight from UDisks (the last signal may still be
    /// debounced): for the checks right before erasing.
    pub async fn fetch(&self) -> Result<Tree, String> {
        let conn = self.conn.borrow().clone().ok_or_else(|| "no system bus".to_string())?;
        let reply = conn
            .call_future(Some(NAME), ROOT, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, Some(glib::VariantTy::new("(a{oa{sa{sv}}})").expect("a type")), gio::DBusCallFlags::NONE, 10000)
            .await
            .map_err(|e| error_text(&e))?;
        Ok(tree_of(&reply.child_value(0)))
    }

    /// A method of org.freedesktop.UDisks2.<iface> at `path`.
    pub async fn call(&self, path: &str, iface: &str, method: &str, args: Variant, timeout: i32) -> Result<Variant, glib::Error> {
        let conn = self.conn.borrow().clone().ok_or_else(|| glib::Error::new(gio::IOErrorEnum::NotConnected, "no system bus"))?;
        conn.call_future(Some(NAME), path, &format!("org.freedesktop.UDisks2.{iface}"), method, Some(&args), None, gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION, timeout).await
    }

    fn opts(extra: &[(&str, Opt)]) -> Variant {
        Variant::tuple_from_iter([options(extra)])
    }

    pub async fn mount(&self, fs_path: &str) -> Result<Variant, glib::Error> {
        self.call(fs_path, "Filesystem", "Mount", Self::opts(&[]), 30000).await
    }

    pub async fn unmount(&self, fs_path: &str) -> Result<Variant, glib::Error> {
        self.call(fs_path, "Filesystem", "Unmount", Self::opts(&[]), 30000).await
    }

    pub async fn set_label(&self, fs_path: &str, label: &str) -> Result<Variant, glib::Error> {
        self.call(fs_path, "Filesystem", "SetLabel", Variant::tuple_from_iter([label.to_variant(), options(&[])]), 30000).await
    }

    /// (consistent / repaired)
    pub async fn check(&self, fs_path: &str) -> Result<bool, glib::Error> {
        let r = self.call(fs_path, "Filesystem", "Check", Self::opts(&[]), LONG_MS).await?;
        Ok(r.child_value(0).get::<bool>().unwrap_or(false))
    }

    pub async fn repair(&self, fs_path: &str) -> Result<bool, glib::Error> {
        let r = self.call(fs_path, "Filesystem", "Repair", Self::opts(&[]), LONG_MS).await?;
        Ok(r.child_value(0).get::<bool>().unwrap_or(false))
    }

    /// The unlocked block's path.
    pub async fn unlock(&self, path: &str, passphrase: &str) -> Result<String, glib::Error> {
        let r = self.call(path, "Encrypted", "Unlock", Variant::tuple_from_iter([passphrase.to_variant(), options(&[])]), 30000).await?;
        Ok(r.child_value(0).str().unwrap_or("").to_string())
    }

    pub async fn lock(&self, path: &str) -> Result<Variant, glib::Error> {
        self.call(path, "Encrypted", "Lock", Self::opts(&[]), 30000).await
    }

    pub async fn eject(&self, drive_path: &str) -> Result<Variant, glib::Error> {
        self.call(drive_path, "Drive", "Eject", Self::opts(&[]), 30000).await
    }

    pub async fn power_off(&self, drive_path: &str) -> Result<Variant, glib::Error> {
        self.call(drive_path, "Drive", "PowerOff", Self::opts(&[]), 30000).await
    }

    pub async fn delete_loop(&self, block_path: &str) -> Result<Variant, glib::Error> {
        self.call(block_path, "Loop", "Delete", Self::opts(&[]), 30000).await
    }

    /// Block.Format (tear-down unmounts / locks what's on it first).
    pub async fn format(&self, block_path: &str, fs_type: &str, label: &str, update_partition_type: bool) -> Result<Variant, glib::Error> {
        let mut extra = vec![("tear-down", Opt::B(true))];
        if update_partition_type {
            extra.push(("update-partition-type", Opt::B(true)));
        }
        if !label.is_empty() {
            extra.push(("label", Opt::S(label.into())));
        }
        self.call(block_path, "Block", "Format", Variant::tuple_from_iter([fs_type.to_variant(), options(&extra)]), LONG_MS).await
    }

    /// One partition over the whole (empty) table, formatted.
    pub async fn create_partition(&self, table_path: &str, fs_type: &str, label: &str) -> Result<Variant, glib::Error> {
        let fmt = if label.is_empty() { options(&[]) } else { options(&[("label", Opt::S(label.into()))]) };
        let args = Variant::tuple_from_iter([0u64.to_variant(), 0u64.to_variant(), "".to_variant(), "".to_variant(), options(&[]), fs_type.to_variant(), fmt]);
        self.call(table_path, "PartitionTable", "CreatePartitionAndFormat", args, LONG_MS).await
    }

    /// Manager.CanFormat / CanCheck / CanRepair -> (available, missing utility).
    pub async fn can(&self, what: &str, fs_type: &str) -> (bool, String) {
        match self.call(MANAGER, "Manager", &format!("Can{what}"), (fs_type,).to_variant(), 5000).await {
            Ok(r) => {
                let inner = r.child_value(0);
                (inner.child_value(0).get::<bool>().unwrap_or(false), inner.child_value(1).str().unwrap_or("").to_string())
            }
            Err(_) => (false, String::new()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_always_allow_asking() {
        let o = options(&[("tear-down", Opt::B(true)), ("label", Opt::S("X".into()))]);
        let d = glib::VariantDict::new(Some(&o));
        assert_eq!(d.lookup::<bool>("auth.no_user_interaction").unwrap(), Some(false));
        assert_eq!(d.lookup::<bool>("tear-down").unwrap(), Some(true));
        assert_eq!(d.lookup::<String>("label").unwrap().as_deref(), Some("X"));
    }

    #[test]
    fn dbus_errors_read_as_sentences() {
        let e = glib::Error::new(gio::IOErrorEnum::Failed, "GDBus.Error:org.freedesktop.UDisks2.Error.DeviceBusy: Error unmounting: target is busy");
        assert_eq!(error_text(&e), "Error unmounting: target is busy");
        let plain = glib::Error::new(gio::IOErrorEnum::Failed, "Something: else happened");
        assert_eq!(error_text(&plain), "Something: else happened");
    }
}
