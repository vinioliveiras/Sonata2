//! The menu bar's status icons (sonata2/shell/tray.py): background apps'
//! icons, as macOS menu extras.
//!
//! Discord, Steam, KeePassXC, Telegram, Dropbox, nm-applet... publish a
//! StatusNotifierItem (KDE/freedesktop SNI spec; Electron, libappindicator
//! and Qt use it). This crate is, over plain Gio D-Bus:
//!
//! - [`Watcher`]: org.kde.StatusNotifierWatcher at /StatusNotifierWatcher,
//!   owned on the session bus when nobody else does (queued otherwise).
//!   Items register with it; items whose bus name vanishes are dropped.
//! - [`Host`]: org.kde.StatusNotifierHost-<pid>; follows whichever watcher
//!   runs and keeps one [`TrayItem`] per registered item (Id, Title,
//!   Status, icons, ToolTip, Menu, ItemIsMenu), refreshed on
//!   NewIcon/NewStatus/... signals.
//! - [`TrayBox`]: the icons of one menu bar, left of the other status items.
//!   Left-click: Activate (ItemIsMenu, or an item without Activate: its
//!   menu). Double-click: the app itself comes forward (its window, else
//!   opened). Right-click: the item's com.canonical.dbusmenu menu, shown as
//!   Sonata's glass menu. Middle-click: SecondaryActivate. Scroll: Scroll.
//!   Passive items are hidden; NeedsAttention shows the attention icon.
//!
//! Everything is event-driven and async: no polling, no sync calls (the
//! double-click's Wayfire IPC aside, as in Python).
//!
//! # For the menu bar
//!
//! topbar.py does three things with the tray, and so does the Rust bar:
//!
//! ```no_run
//! # use gtk::prelude::*;
//! # let right = gtk::Box::new(gtk::Orientation::Horizontal, 0);
//! # let show_tray = true;
//! // building the bar: the icons, left of the menu extras
//! let tray = sonata_tray::TrayBox::new(show_tray);  // cfg "show_tray", default true
//! right.append(tray.widget());
//! // menu bar settings changed
//! tray.set_shown(show_tray);
//! // the bar goes away (a display unplugged...): stop following the host
//! tray.stop();
//! ```
//!
//! Every menu bar (one per display) shares the process's one [`host()`];
//! `TrayBox::with_host` takes another (tests). The box hides itself when it
//! has no icon to show. Its buttons carry the classes `topbar-item icon
//! tray-item` (and `open` while their menu shows): the menu bar's CSS
//! styles them, tray.py registers none of its own.
//!
//! quitapps.py reads the watcher's items itself; it uses [`WATCHER`],
//! [`WATCHER_PATH`] and [`split_service`].
//!
//! [`host()`] exports org.kde.StatusNotifierWatcher as tray.py did; a
//! host that doesn't own the watcher (`Host::new(conn, false)`) registers
//! with the one that does (a host name of its own, by pid) and shows the
//! same items.

mod appmatch;
mod dbusmenu;
mod host;
mod icon;
mod item;
mod traybox;
mod watcher;

pub use appmatch::{app_of_pid, bring_forward, match_app_id, window_of};
pub use dbusmenu::{layout_sections, steam_link, DBusMenu, Entry, Node, STEAM_LINKS};
pub use host::{host, Host, ListenerId};
pub use icon::{argb_to_rgba, mono_mask, pick_pixmap, MonoIcon};
pub use item::{IconSource, Pixmap, TrayItem};
pub use traybox::{double_click_ms, TrayBox};
pub use watcher::{Watcher, WatcherState};

pub const WATCHER: &str = "org.kde.StatusNotifierWatcher";
pub const WATCHER_PATH: &str = "/StatusNotifierWatcher";
pub const ITEM_IFACE: &str = "org.kde.StatusNotifierItem";
pub const MENU_IFACE: &str = "com.canonical.dbusmenu";
pub(crate) const PROPS: &str = "org.freedesktop.DBus.Properties";
/// The icons' size in the menu bar, in logical pixels.
pub const ICON_PX: i32 = 16;
/// Every call to an app waits this long at most (ms): a hung app never
/// holds the menu bar.
pub(crate) const TIMEOUT: i32 = 2000;

/// "name/path", "name" or "/path" (then the sender's) -> (name, path).
pub fn split_service(service: &str, sender: &str) -> (String, String) {
    if service.starts_with('/') {
        return (sender.to_string(), service.to_string());
    }
    match service.split_once('/') {
        Some((name, rest)) => (name.to_string(), format!("/{rest}")),
        None => (service.to_string(), "/StatusNotifierItem".to_string()),
    }
}

/// "_File" -> "File", "Save__As" -> "Save_As" (dbusmenu labels carry
/// mnemonics; Sonata's menus show none).
pub(crate) fn strip_mnemonic(label: &str) -> String {
    label.replace("__", "\0").replace('_', "").replace('\0', "_")
}

#[cfg(test)]
mod tests;
