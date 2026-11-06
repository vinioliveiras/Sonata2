//! org.kde.StatusNotifierWatcher, when this process owns the name.

use crate::{split_service, WATCHER, WATCHER_PATH};
use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::HashSet;
use std::rc::Rc;

const WATCHER_XML: &str = r#"<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
  <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
  <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
  <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
  <property name="ProtocolVersion" type="i" access="read"/>
  <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
  <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
  <signal name="StatusNotifierHostRegistered"/>
  <signal name="StatusNotifierHostUnregistered"/>
</interface></node>"#;
// The spec's signals are StatusNotifierItemRegistered/-Unregistered (what
// every host and item listens for).

/// What the watcher knows, without the bus: the items ("busname/path", in
/// registration order) and the hosts' bus names.
#[derive(Default, Debug)]
pub struct WatcherState {
    pub items: Vec<String>,
    pub hosts: HashSet<String>,
}

impl WatcherState {
    /// RegisterStatusNotifierItem(service) from `sender`: the new key, None
    /// when known already (or nameless).
    pub fn register_item(&mut self, service: &str, sender: &str) -> Option<String> {
        let (name, path) = split_service(service, sender);
        let key = format!("{name}{path}");
        if name.is_empty() || self.items.contains(&key) {
            return None;
        }
        self.items.push(key.clone());
        Some(key)
    }

    /// RegisterStatusNotifierHost: true for a new host.
    pub fn register_host(&mut self, sender: &str) -> bool {
        self.hosts.insert(sender.to_string())
    }

    /// `name` left the bus: whether it was a host, and its items' keys (removed).
    pub fn name_gone(&mut self, name: &str) -> (bool, Vec<String>) {
        let host = self.hosts.remove(name);
        let gone: Vec<String> = self.items.iter().filter(|k| split_service(k, "").0 == name).cloned().collect();
        self.items.retain(|k| !gone.contains(k));
        (host, gone)
    }
}

pub struct Watcher {
    conn: gio::DBusConnection,
    state: RefCell<WatcherState>,
    owned: Cell<bool>,
}

impl Watcher {
    /// Export the watcher on `conn` and ask for its name -- queued: when
    /// another watcher (KDE, waybar...) quits, this one takes over.
    pub fn new(conn: &gio::DBusConnection) -> Option<Rc<Watcher>> {
        let info = gio::DBusNodeInfo::for_xml(WATCHER_XML).ok()?.lookup_interface(WATCHER)?;
        let w = Rc::new(Watcher { conn: conn.clone(), state: RefCell::default(), owned: Cell::new(false) });
        let (a, b) = (Rc::downgrade(&w), Rc::downgrade(&w));
        // lives as long as the process: never unregistered
        let reg = conn
            .register_object(WATCHER_PATH, &info)
            .method_call(move |_, sender, _, _, method, params, inv| match a.upgrade() {
                Some(w) => w.call(sender.unwrap_or(""), method, &params, inv),
                None => inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", "gone"),
            })
            .property(move |_, _, _, _, prop| match (b.upgrade(), prop) {
                (Some(w), "RegisteredStatusNotifierItems") => w.state.borrow().items.to_variant(),
                (_, "IsStatusNotifierHostRegistered") => true.to_variant(),
                (_, "ProtocolVersion") => 0i32.to_variant(),
                _ => Vec::<String>::new().to_variant(),
            })
            .build()
            .ok()?;
        let _ = reg; // (kept for the process's life: dropping the id keeps it)
        let c = Rc::downgrade(&w);
        let sub = conn.signal_subscribe(
            Some("org.freedesktop.DBus"),
            Some("org.freedesktop.DBus"),
            Some("NameOwnerChanged"),
            Some("/org/freedesktop/DBus"),
            None,
            gio::DBusSignalFlags::NONE,
            move |_, _, _, _, _, params| {
                if let (Some(w), Some((name, _old, new))) = (c.upgrade(), params.get::<(String, String, String)>()) {
                    if new.is_empty() {
                        w.name_gone(&name);
                    }
                }
            },
        );
        let _ = sub; // (kept for the process's life: dropping the id keeps it)
        let (on, off) = (Rc::downgrade(&w), Rc::downgrade(&w));
        let own = gio::bus_own_name_on_connection(
            conn,
            WATCHER,
            gio::BusNameOwnerFlags::NONE,
            move |_, _| {
                if let Some(w) = on.upgrade() {
                    w.owned.set(true);
                }
            },
            move |_, _| {
                if let Some(w) = off.upgrade() {
                    w.owned.set(false);
                }
            },
        );
        let _ = own; // (kept for the process's life: dropping the id keeps it)
        Some(w)
    }

    /// True while this process is the session's watcher.
    pub fn owned(&self) -> bool {
        self.owned.get()
    }

    /// The registered items' keys ("busname/path"), in registration order.
    pub fn items(&self) -> Vec<String> {
        self.state.borrow().items.clone()
    }

    fn emit(&self, signal: &str, args: Option<glib::Variant>) {
        let _ = self.conn.emit_signal(None, WATCHER_PATH, WATCHER, signal, args.as_ref());
    }

    fn call(&self, sender: &str, method: &str, params: &glib::Variant, inv: gio::DBusMethodInvocation) {
        match method {
            "RegisterStatusNotifierItem" => {
                let service = params.child_value(0).str().unwrap_or("").to_string();
                let key = self.state.borrow_mut().register_item(&service, sender);
                if let Some(key) = key {
                    self.emit("StatusNotifierItemRegistered", Some((key,).to_variant()));
                }
                inv.return_value(None);
            }
            "RegisterStatusNotifierHost" => {
                let new = self.state.borrow_mut().register_host(sender);
                if new {
                    self.emit("StatusNotifierHostRegistered", None);
                }
                inv.return_value(None);
            }
            _ => inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method),
        }
    }

    fn name_gone(&self, name: &str) {
        let (host, gone) = self.state.borrow_mut().name_gone(name);
        if host {
            self.emit("StatusNotifierHostUnregistered", None);
        }
        for key in gone {
            self.emit("StatusNotifierItemUnregistered", Some((key,).to_variant()));
        }
    }
}
