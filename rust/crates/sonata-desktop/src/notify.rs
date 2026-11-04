//! A notification from a Sonata process, without waiting for the answer
//! (sonata2/notify.py; only what launchwatch needs -- no shared Rust copy
//! of notify.py exists yet).
//!
//! Sonata's notification server lives in the menu bar process: a call
//! waiting for its own reply there blocked the menu bar for 2 s, timed out
//! and looked failed, so the notice was sent again and again (Vini: four
//! copies of the same one at login). So: is a server there (one quick
//! question), then the notice is sent and not waited on.

use glib::variant::ToVariant;
use std::collections::HashMap;

/// False when no notification server is there (yet).
pub fn send(summary: &str, body: &str, app: &str, icon: &str, desktop: &str, urgent: bool) -> bool {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return false };
    let owned = bus
        .call_sync(
            Some("org.freedesktop.DBus"),
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            Some(&("org.freedesktop.Notifications",).to_variant()),
            Some(glib::VariantTy::new("(b)").unwrap()),
            gio::DBusCallFlags::NONE,
            1000,
            gio::Cancellable::NONE,
        )
        .ok()
        .and_then(|v| v.get::<(bool,)>())
        .is_some_and(|(b,)| b);
    if !owned {
        return false;
    }
    let mut hints: HashMap<String, glib::Variant> = HashMap::new();
    hints.insert("urgency".into(), (if urgent { 2u8 } else { 1u8 }).to_variant());
    if !desktop.is_empty() {
        hints.insert("desktop-entry".into(), desktop.strip_suffix(".desktop").unwrap_or(desktop).to_variant());
    }
    let params = (app, 0u32, icon, summary, body, Vec::<String>::new(), hints, -1i32).to_variant();
    bus.call(
        Some("org.freedesktop.Notifications"),
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
        Some(&params),
        None,
        gio::DBusCallFlags::NONE,
        -1,
        gio::Cancellable::NONE,
        |_| {},
    );
    true
}
