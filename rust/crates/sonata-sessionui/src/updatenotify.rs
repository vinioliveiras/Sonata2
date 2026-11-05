//! Tells the user when a new Sonata is out (menu bar process;
//! shell/updatenotify.py): checks the GitHub releases a while after login
//! and then every few hours, and posts a notification once per release;
//! clicking it opens Software Update. A check is a single small HTTPS
//! request, off the main loop.
//!
//! The check itself is backend/selfupdate.py's (`available()`), ported
//! with the backend: the menu bar hands it in.

use serde_json::json;
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::Arc;

/// after login: let the session settle
pub const FIRST_S: u32 = 120;
pub const EVERY_S: u32 = 6 * 3600;
/// selfupdate.json: {"notified": tag, "last_check": unix time}
pub const NAME: &str = "selfupdate";

/// A newer release (selfupdate.available()'s answer).
#[derive(Clone, Debug, PartialEq)]
pub struct Release {
    pub tag: String,
}

pub type Check = Arc<dyn Fn() -> Option<Release> + Send + Sync>;

/// The notification's title and text for a release.
pub fn texts(rel: &Release) -> (String, String) {
    let version = rel.tag.trim_start_matches(['v', 'V']);
    (fmt(&tr!("Sonata {version} is available"), &[("version", version)]), tr!("Click to see what's new and update."))
}

/// A check came back: remember when, and whether this release was told
/// already. True when it should be notified now.
pub fn found(rel: Option<&Release>, now: i64) -> bool {
    let _ = sonata_config::update(NAME, &json!({"last_check": now}));
    let Some(rel) = rel else { return false };
    let data = sonata_config::load(NAME, &json!({"notified": "", "last_check": 0}));
    if data.get("notified").and_then(|v| v.as_str()) == Some(rel.tag.as_str()) {
        return false;
    }
    let _ = sonata_config::update(NAME, &json!({"notified": rel.tag}));
    true
}

pub struct UpdateNotifier {
    check: Check,
    id: Rc<Cell<u32>>,
    sub: RefCell<Option<(gio::DBusConnection, gio::SignalSubscriptionId)>>,
    timer: RefCell<Option<glib::SourceId>>,
    me: std::rc::Weak<UpdateNotifier>,
}

impl UpdateNotifier {
    /// None on trial: the Python menu bar checks and notifies; the trial's
    /// own selfupdate.json would tell every release a second time.
    pub fn start(check: Check) -> Option<Rc<Self>> {
        if sonata_config::side_by_side() {
            return None;
        }
        let me = Rc::new_cyclic(|w| UpdateNotifier { check, id: Rc::default(), sub: RefCell::default(), timer: RefCell::default(), me: w.clone() });
        me.later(FIRST_S);
        Some(me)
    }

    fn later(&self, secs: u32) {
        let w = self.me.clone();
        let id = glib::timeout_add_seconds_local_once(secs, move || {
            if let Some(me) = w.upgrade() {
                me.tick();
            }
        });
        *self.timer.borrow_mut() = Some(id);
    }

    fn tick(&self) {
        let check = self.check.clone();
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let rel = gio::spawn_blocking(move || check()).await.ok().flatten();
            if let Some(me) = w.upgrade() {
                let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
                if found(rel.as_ref(), now) {
                    if let Some(r) = rel {
                        me.notify(&r);
                    }
                }
            }
        });
        self.later(EVERY_S);
    }

    pub fn notify(&self, rel: &Release) {
        let (title, body) = texts(rel);
        let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
        if self.sub.borrow().is_none() {
            let id = self.id.clone();
            #[allow(deprecated)]
            let sub = bus.signal_subscribe(None, Some("org.freedesktop.Notifications"), Some("ActionInvoked"), Some("/org/freedesktop/Notifications"), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, params| {
                if let Some((nid, _key)) = params.get::<(u32, String)>() {
                    if nid != 0 && nid == id.get() {
                        open_updates();
                    }
                }
            });
            *self.sub.borrow_mut() = Some((bus.clone(), sub));
        }
        let hints = glib::VariantDict::new(None);
        hints.insert("urgency", 1u8);
        let actions = vec!["default".to_string(), tr!("Update"), "update".into(), tr!("Update…")];
        let args = ("Software Update", 0u32, "software-update-available", title.as_str(), body.as_str(), actions, hints.end(), -1i32).to_variant();
        let id = self.id.clone();
        bus.call(Some("org.freedesktop.Notifications"), "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify", Some(&args), None, gio::DBusCallFlags::NONE, -1, gio::Cancellable::NONE, move |res| match res {
            Ok(v) => id.set(v.child_value(0).get::<u32>().unwrap_or(0)),
            Err(e) => eprintln!("sonata2 updates: notification failed: {}", e.message()),
        });
    }

    pub fn stop(&self) {
        if let Some(id) = self.timer.borrow_mut().take() {
            id.remove();
        }
    }
}

impl Drop for UpdateNotifier {
    fn drop(&mut self) {
        if let Some((bus, sub)) = self.sub.borrow_mut().take() {
            #[allow(deprecated)]
            bus.signal_unsubscribe(sub);
        }
    }
}

use glib::prelude::ToVariant;

/// Settings > About (Software Update), in this same Sonata (self_argv in
/// the Python): the Rust session's notifier opens the Rust Settings.
pub fn open_updates() {
    sonata_backend::run::spawn(&[crate::self_exe().as_str(), "settings", "--page", "sonataupdate"]);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn notified_once_per_release() {
        let _e = crate::testenv::env();
        let r = Release { tag: "v2.4.0".into() };
        assert!(!found(None, 10)); // up to date: only the time noted
        assert_eq!(sonata_config::load(NAME, &json!({"last_check": 0}))["last_check"], 10);
        assert!(found(Some(&r), 11));
        assert!(!found(Some(&r), 12)); // told already
        assert!(found(Some(&Release { tag: "v2.5.0".into() }), 13));
        assert_eq!(texts(&r), ("Sonata 2.4.0 is available".to_string(), "Click to see what's new and update.".to_string()));
    }
}
