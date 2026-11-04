//! The login screen's part of Switch User (sonata2/userswitch.py): a user
//! who already has a session on this seat gets it back -- the password
//! asked once here, then logind unlocks it and brings it to the front --
//! never a second session. The lock screen's side (showing this login
//! screen) isn't ported yet.

use gio::prelude::*;

pub const SWITCH_UNIT: &str = "sonata-greeter-switch.service";
/// set for the second greetd's login screen (tools/greeter-setup.sh)
pub const SWITCH_ENV: &str = "SONATA_GREETER_SWITCH";
const GRAPHICAL: [&str; 3] = ["wayland", "x11", "mir"];
const LOGIN1: (&str, &str, &str) = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager");

#[derive(Clone, Debug, Default, PartialEq)]
pub struct LoginSession {
    pub id: String,
    pub user: String,
    pub seat: String,
    pub kind: String,
    pub class: String,
    pub state: String,
    pub remote: bool,
}

fn bus() -> Option<gio::DBusConnection> {
    gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE).ok()
}

fn call(bus: &gio::DBusConnection, method: &str, args: glib::Variant) -> Result<glib::Variant, glib::Error> {
    bus.call_sync(Some(LOGIN1.0), LOGIN1.1, LOGIN1.2, method, Some(&args), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE)
}

/// This seat's sessions (logind).
pub fn sessions() -> Vec<LoginSession> {
    let Some(bus) = bus() else { return Vec::new() };
    let Ok(listed) = bus.call_sync(Some(LOGIN1.0), LOGIN1.1, LOGIN1.2, "ListSessions", None, glib::VariantTy::new("(a(susso))").ok(), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE) else {
        return Vec::new();
    };
    let Some(listed) = listed.child_value(0).get::<Vec<(String, u32, String, String, glib::variant::ObjectPath)>>() else { return Vec::new() };
    let mut out = Vec::new();
    for (id, _uid, user, seat, path) in listed {
        let Ok(props) = bus.call_sync(Some(LOGIN1.0), path.as_str(), "org.freedesktop.DBus.Properties", "GetAll", Some(&("org.freedesktop.login1.Session",).to_variant()), glib::VariantTy::new("(a{sv})").ok(), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE) else {
            continue;
        };
        let props = glib::VariantDict::new(Some(&props.child_value(0)));
        let s = |k: &str| props.lookup::<String>(k).ok().flatten().unwrap_or_default();
        // (Seat is "(so)": its name first)
        let seat = if seat.is_empty() { props.lookup_value("Seat", None).and_then(|v| v.try_child_value(0)).and_then(|v| v.get::<String>()).unwrap_or_default() } else { seat };
        out.push(LoginSession { id, user, seat, kind: s("Type"), class: s("Class"), state: s("State"), remote: props.lookup::<bool>("Remote").ok().flatten().unwrap_or(false) });
    }
    out
}

/// `user`'s desktop session on `seat` (the one to bring back), or None.
pub fn graphical_session<'a>(user: &str, listed: &'a [LoginSession], seat: &str) -> Option<&'a LoginSession> {
    listed.iter().find(|s| s.user == user && s.class == "user" && GRAPHICAL.contains(&s.kind.as_str()) && !s.remote && s.seat == seat && s.state != "closing")
}

fn manager(method: &str, session_id: &str) -> bool {
    let Some(bus) = bus() else { return false };
    match call(&bus, method, (session_id,).to_variant()) {
        Ok(_) => true,
        Err(e) => {
            eprintln!("sonata2: switch user: can't {method} {session_id}: {}", e.message());
            false
        }
    }
}

/// Back to a running session, its password just checked: unlocked (its
/// lock screen goes -- Vini: the password was asked twice), then in front.
pub fn resume(session_id: &str) -> bool {
    manager("UnlockSession", session_id);
    manager("ActivateSession", session_id)
}

/// The switch login screen's job is done (its user went back to a running
/// session): stop it, so no second login screen keeps running unseen.
pub fn leave_switch_greeter() {
    if std::env::var_os(SWITCH_ENV).is_none_or(|v| v.is_empty()) {
        return;
    }
    let Some(bus) = bus() else { return };
    let r = bus.call_sync(Some("org.freedesktop.systemd1"), "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "StopUnit", Some(&(SWITCH_UNIT, "replace").to_variant()), glib::VariantTy::new("(o)").ok(), gio::DBusCallFlags::NONE, 10000, gio::Cancellable::NONE);
    if let Err(e) = r {
        eprintln!("sonata2: switch user: stop {SWITCH_UNIT}: {}", e.message());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sess(id: &str, user: &str) -> LoginSession {
        LoginSession { id: id.into(), user: user.into(), seat: "seat0".into(), kind: "wayland".into(), class: "user".into(), state: "active".into(), remote: false }
    }

    #[test]
    fn only_a_local_desktop_session_is_resumed() {
        let mut listed = vec![sess("2", "vini")];
        assert_eq!(graphical_session("vini", &listed, "seat0").map(|s| s.id.as_str()), Some("2"));
        assert!(graphical_session("ana", &listed, "seat0").is_none()); // not logged in: logs in
        listed[0].kind = "tty".into();
        assert!(graphical_session("vini", &listed, "seat0").is_none());
        listed[0] = LoginSession { remote: true, ..sess("2", "vini") };
        assert!(graphical_session("vini", &listed, "seat0").is_none());
        listed[0] = LoginSession { state: "closing".into(), ..sess("2", "vini") };
        assert!(graphical_session("vini", &listed, "seat0").is_none());
        listed[0] = LoginSession { class: "greeter".into(), ..sess("2", "vini") };
        assert!(graphical_session("vini", &listed, "seat0").is_none());
    }
}
