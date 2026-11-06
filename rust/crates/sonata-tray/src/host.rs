//! The host: follows the watcher's items.

use crate::item::TrayItem;
use crate::watcher::Watcher;
use crate::{PROPS, TIMEOUT, WATCHER, WATCHER_PATH};
use gio::prelude::*;
use std::cell::{Cell, OnceCell, RefCell};
use std::rc::{Rc, Weak};

type Listener = Rc<dyn Fn(Option<&Rc<TrayItem>>)>;

/// A listener's handle, for [`Host::disconnect`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ListenerId(u64);

/// Tracks the watcher's items; listeners get the changed item (or None:
/// many changed).
pub struct Host {
    conn: Option<gio::DBusConnection>,
    name: String,
    watcher: Option<Rc<Watcher>>,
    items: RefCell<Vec<Rc<TrayItem>>>, // registration order
    listeners: RefCell<Vec<(u64, Listener)>>,
    next: Cell<u64>,
    me: Weak<Host>,
}

thread_local! {
    static HOST: OnceCell<Rc<Host>> = const { OnceCell::new() };
}

/// The process's host, on the session bus; it also exports the watcher,
/// as tray.py did.
pub fn host() -> Rc<Host> {
    HOST.with(|h| {
        h.get_or_init(|| {
            let conn = gio::bus_get_sync(gio::BusType::Session, None::<&gio::Cancellable>).ok();
            Host::new(conn, true)
        })
        .clone()
    })
}

impl Host {
    /// A host on `conn` (None: no session bus, no tray). `own_watcher`:
    /// export org.kde.StatusNotifierWatcher too, queued for its name.
    pub fn new(conn: Option<gio::DBusConnection>, own_watcher: bool) -> Rc<Host> {
        let watcher = conn.as_ref().filter(|_| own_watcher).and_then(Watcher::new);
        let h = Rc::new_cyclic(|me| Host {
            conn: conn.clone(),
            name: format!("org.kde.StatusNotifierHost-{}", std::process::id()),
            watcher,
            items: RefCell::default(),
            listeners: RefCell::default(),
            next: Cell::new(1),
            me: me.clone(),
        });
        let Some(c) = conn else { return h };
        let _ = gio::bus_own_name_on_connection(&c, &h.name, gio::BusNameOwnerFlags::NONE, |_, _| {}, |_, _| {}); // (kept for the process's life: dropping the id keeps it)
        for sig in ["StatusNotifierItemRegistered", "StatusNotifierItemUnregistered"] {
            let w = h.me.clone();
            let id = c.signal_subscribe(Some(WATCHER), Some(WATCHER), Some(sig), Some(WATCHER_PATH), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, signal, params| {
                let (Some(h), Some(key)) = (w.upgrade(), params.child_value(0).str().map(String::from)) else { return };
                if signal.ends_with("Unregistered") {
                    h.remove(&key);
                } else {
                    h.add(&key);
                }
            });
            let _ = id; // (kept for the process's life: dropping the id keeps it)
        }
        let (a, v) = (h.me.clone(), h.me.clone());
        let watch = gio::bus_watch_name_on_connection(
            &c,
            WATCHER,
            gio::BusNameWatcherFlags::NONE,
            move |conn, _, _| {
                if let Some(h) = a.upgrade() {
                    h.appeared(&conn);
                }
            },
            move |_, _| {
                if let Some(h) = v.upgrade() {
                    h.vanished();
                }
            },
        );
        let _ = watch; // (kept for the process's life: dropping the id keeps it)
        h
    }

    /// The watcher this host exports, if it does.
    pub fn watcher(&self) -> Option<&Rc<Watcher>> {
        self.watcher.as_ref()
    }

    /// The host's bus name (org.kde.StatusNotifierHost-<pid>).
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Every item, in registration order (shown or not: see TrayItem::visible).
    pub fn items(&self) -> Vec<Rc<TrayItem>> {
        self.items.borrow().clone()
    }

    pub fn item(&self, key: &str) -> Option<Rc<TrayItem>> {
        self.items.borrow().iter().find(|i| i.key() == key).cloned()
    }

    /// Call `f` with the changed item (None: many changed) from now on.
    pub fn connect_changed(&self, f: impl Fn(Option<&Rc<TrayItem>>) + 'static) -> ListenerId {
        let id = self.next.get();
        self.next.set(id + 1);
        self.listeners.borrow_mut().push((id, Rc::new(f)));
        ListenerId(id)
    }

    pub fn disconnect(&self, id: ListenerId) {
        self.listeners.borrow_mut().retain(|(i, _)| *i != id.0);
    }

    pub(crate) fn changed(&self, item: Option<&Rc<TrayItem>>) {
        let all: Vec<Listener> = self.listeners.borrow().iter().map(|(_, l)| l.clone()).collect();
        for l in all {
            l(item);
        }
    }

    fn appeared(&self, conn: &gio::DBusConnection) {
        conn.call(Some(WATCHER), WATCHER_PATH, WATCHER, "RegisterStatusNotifierHost", Some(&(self.name.as_str(),).to_variant()), None, gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, |_| {});
        let w = self.me.clone();
        conn.call(Some(WATCHER), WATCHER_PATH, PROPS, "Get", Some(&(WATCHER, "RegisteredStatusNotifierItems").to_variant()), Some(glib::VariantTy::new("(v)").unwrap()), gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, move |r| {
            let (Some(h), Ok(r)) = (w.upgrade(), r) else { return };
            let keys = r.child_value(0).as_variant().and_then(|v| v.get::<Vec<String>>()).unwrap_or_default();
            for k in keys {
                h.add(&k);
            }
        });
    }

    fn vanished(&self) {
        let keys: Vec<String> = self.items.borrow().iter().map(|i| i.key().to_string()).collect();
        for k in keys {
            self.remove(&k);
        }
    }

    fn add(&self, key: &str) {
        let Some(conn) = &self.conn else { return };
        if key.is_empty() || self.item(key).is_some() {
            return;
        }
        // shown once its properties arrive
        let item = TrayItem::new(self.me.clone(), conn, key);
        self.items.borrow_mut().push(item);
    }

    fn remove(&self, key: &str) {
        let pos = self.items.borrow().iter().position(|i| i.key() == key);
        if let Some(p) = pos {
            let item = self.items.borrow_mut().remove(p);
            item.close();
            self.changed(Some(&item));
        }
    }
}
