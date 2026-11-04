//! What sandboxed apps (Flatpak) were allowed through the portals
//! (backend/permstore.py; macOS: Security & Privacy > Privacy): the camera,
//! the location, screenshots, notifications, running in the background.
//! Read from and changed in the portals' own permission store
//! (org.freedesktop.impl.portal.PermissionStore), the one GNOME Settings and
//! KDE's Flatpak Permissions use. An app turned off here is refused next
//! time it asks; a reset one is asked again. Blocking (short D-Bus calls).

use glib::prelude::*;
use glib::Variant;
use sonata_i18n::n_;
use std::collections::BTreeMap;

pub const BUS: &str = "org.freedesktop.impl.portal.PermissionStore";
pub const PATH: &str = "/org/freedesktop/impl/portal/PermissionStore";
pub const IFACE: &str = "org.freedesktop.impl.portal.PermissionStore";
pub const TIMEOUT_MS: i32 = 2000;

/// (key, title, table, id, "allowed" value: None for location's accuracy level)
pub const KINDS: [(&str, &str, &str, &str, Option<&str>); 5] = [
    ("camera", n_!("Camera"), "devices", "camera", Some("yes")),
    ("location", n_!("Location"), "location", "location", None),
    ("screenshot", n_!("Screenshots"), "screenshot", "screenshot", Some("yes")),
    ("notifications", n_!("Notifications"), "notifications", "notification", Some("yes")),
    ("background", n_!("Running in the Background"), "background", "background", Some("yes")),
];

/// How the store is called (tests pass their own): (method, args) -> reply.
pub type Call<'a> = &'a dyn Fn(&str, Variant) -> Result<Option<Variant>, glib::Error>;

fn session_call(method: &str, args: Variant) -> Result<Option<Variant>, glib::Error> {
    let bus = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE)?;
    let ty = (method == "Lookup").then(|| glib::VariantTy::new("(a{sas}v)").unwrap());
    let r = bus.call_sync(Some(BUS), PATH, IFACE, method, Some(&args), ty, gio::DBusCallFlags::NONE, TIMEOUT_MS, gio::Cancellable::NONE)?;
    Ok(Some(r))
}

/// Whether a stored permission says yes (location keeps an accuracy level instead).
pub fn allowed(perms: &[String], yes: Option<&str>) -> bool {
    let Some(first) = perms.first() else { return false };
    match yes {
        None => !["NONE", "NO"].contains(&first.to_uppercase().as_str()), // ["EXACT", "<time>"] / ["NONE", ...]
        Some(y) => first == y,
    }
}

/// {app id: [permissions]} ({} when nothing is stored).
pub fn lookup(table: &str, ident: &str) -> BTreeMap<String, Vec<String>> {
    lookup_with(&session_call, table, ident)
}

pub fn lookup_with(call: Call, table: &str, ident: &str) -> BTreeMap<String, Vec<String>> {
    // no such table yet: nobody asked
    let Ok(Some(r)) = call("Lookup", (table, ident).to_variant()) else { return BTreeMap::new() };
    let all = r.child_value(0);
    (0..all.n_children())
        .filter_map(|i| {
            let e = all.child_value(i);
            Some((e.child_value(0).str()?.to_string(), e.child_value(1).get::<Vec<String>>().unwrap_or_default()))
        })
        .collect()
}

/// [(key, title, [(app id, allowed)])] for every kind.
pub fn all_permissions() -> Vec<(&'static str, &'static str, Vec<(String, bool)>)> {
    all_permissions_with(&session_call)
}

pub fn all_permissions_with(call: Call) -> Vec<(&'static str, &'static str, Vec<(String, bool)>)> {
    KINDS.iter().map(|(key, title, table, ident, yes)| (*key, *title, lookup_with(call, table, ident).into_iter().filter(|(a, _)| !a.is_empty()).map(|(a, p)| (a, allowed(&p, *yes))).collect())).collect()
}

pub fn set_allowed(key: &str, app: &str, on: bool) -> bool {
    set_allowed_with(&session_call, key, app, on)
}

pub fn set_allowed_with(call: Call, key: &str, app: &str, on: bool) -> bool {
    let Some((_, _, table, ident, yes)) = KINDS.iter().find(|k| k.0 == key) else { return false };
    let value: Vec<String> = match yes {
        // location: an accuracy level and when it was granted
        None => {
            let perms = lookup_with(call, table, ident).remove(app).filter(|p| !p.is_empty()).unwrap_or_else(|| vec!["EXACT".into(), "0".into()]);
            std::iter::once(if on { "EXACT" } else { "NONE" }.to_string()).chain(perms.into_iter().skip(1)).collect()
        }
        Some(y) => vec![if on { y } else { "no" }.to_string()],
    };
    match call("SetPermission", (*table, false, *ident, app, value).to_variant()) {
        Ok(_) => true,
        Err(e) => {
            eprintln!("sonata2: permission {key} {app}: {}", e.message());
            false
        }
    }
}

/// Forget the answer: the app is asked again next time.
pub fn reset(key: &str, app: &str) -> bool {
    reset_with(&session_call, key, app)
}

pub fn reset_with(call: Call, key: &str, app: &str) -> bool {
    let Some((_, _, table, ident, _)) = KINDS.iter().find(|k| k.0 == key) else { return false };
    call("DeletePermission", (*table, *ident, app).to_variant()).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn table(rows: &[(&str, &[&str])]) -> Variant {
        let m: std::collections::HashMap<String, Vec<String>> = rows.iter().map(|(a, p)| (a.to_string(), p.iter().map(|s| s.to_string()).collect())).collect();
        (m, "".to_variant()).to_variant()
    }

    fn fake(calls: &RefCell<Vec<(String, String)>>) -> impl Fn(&str, Variant) -> Result<Option<Variant>, glib::Error> + '_ {
        move |method, args| {
            calls.borrow_mut().push((method.into(), args.print(false).to_string()));
            if method != "Lookup" {
                return Ok(None);
            }
            match (args.child_value(0).str(), args.child_value(1).str()) {
                (Some("devices"), Some("camera")) => Ok(Some(table(&[("com.discordapp.Discord", &["yes"]), ("org.zoom.Zoom", &["no"])]))),
                (Some("location"), Some("location")) => Ok(Some(table(&[("org.gnome.Maps", &["EXACT", "99"])]))),
                _ => Err(glib::Error::new(gio::IOErrorEnum::NotFound, "no table")),
            }
        }
    }

    #[test]
    fn allowed_values() {
        let v = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(allowed(&v(&["yes"]), Some("yes")));
        assert!(!allowed(&v(&["no"]), Some("yes")));
        assert!(!allowed(&[], Some("yes")));
        assert!(allowed(&v(&["EXACT", "123"]), None));
        assert!(!allowed(&v(&["NONE", "123"]), None));
    }

    #[test]
    fn all_set_and_reset() {
        let calls = RefCell::default();
        let call = fake(&calls);
        let got: BTreeMap<&str, Vec<(String, bool)>> = all_permissions_with(&call).into_iter().map(|(k, _, a)| (k, a)).collect();
        assert_eq!(got["camera"], [("com.discordapp.Discord".to_string(), true), ("org.zoom.Zoom".to_string(), false)]);
        assert!(got["screenshot"].is_empty()); // nobody asked: no table
        calls.borrow_mut().clear();
        assert!(set_allowed_with(&call, "camera", "com.discordapp.Discord", false));
        assert!(set_allowed_with(&call, "location", "org.gnome.Maps", false));
        assert!(reset_with(&call, "camera", "com.discordapp.Discord"));
        let c = calls.borrow();
        let sets: Vec<&String> = c.iter().filter(|(m, _)| m == "SetPermission").map(|(_, a)| a).collect();
        assert_eq!(sets[0], "('devices', false, 'camera', 'com.discordapp.Discord', ['no'])");
        assert_eq!(sets[1], "('location', false, 'location', 'org.gnome.Maps', ['NONE', '99'])");
        assert!(c.contains(&("DeletePermission".into(), "('devices', 'camera', 'com.discordapp.Discord')".into())));
    }
}
