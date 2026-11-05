//! Switch User (sonata2/userswitch.py; Vini: with another user added, the
//! lock screen had no way to switch to them). macOS's fast user switching:
//! this session stays as it is, locked, while another user logs in or goes
//! back to theirs.
//!
//! - That user already has a session on this seat: the login screen asks
//!   the password once, then logind unlocks it and brings it to the front
//!   (UnlockSession, ActivateSession) -- no second prompt on its lock screen.
//! - Otherwise the login screen comes up on a free VT:
//!   - Sonata's (greetd): a second greetd, sonata-greeter-switch.service,
//!     set up by tools/greeter-setup.sh with a polkit rule that lets the
//!     local user start and stop it; its greeter (SONATA_GREETER_SWITCH=1)
//!     stops it again once you pick a user who is already logged in;
//!   - GDM: gdmflexiserver; LightDM: dm-tool switch-to-greeter.
//!
//! ```ignore
//! available("vini")       // other users exist and there's a way to reach them
//! switch("vini")          // the lock screen's button: true when something is on its way
//! resume(session_id)      // the login screen: back to a running session
//! ```

use gio::prelude::*;
use std::path::Path;

pub const SWITCH_UNIT: &str = "sonata-greeter-switch.service";
/// set for the second greetd's login screen (tools/greeter-setup.sh)
pub const SWITCH_ENV: &str = "SONATA_GREETER_SWITCH";
pub const SWITCH_UNIT_FILE: &str = "/etc/systemd/system/sonata-greeter-switch.service";
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

/// How this system shows a login screen beside a running session.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Method {
    /// the switch greetd
    Sonata,
    Gdm,
    Lightdm,
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

/// The login screen already waiting on `seat` (switched away from it earlier).
pub fn greeter_session<'a>(listed: &'a [LoginSession], seat: &str) -> Option<&'a LoginSession> {
    listed.iter().find(|s| s.class == "greeter" && s.seat == seat && s.state != "closing")
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

/// That session in front.
pub fn activate(session_id: &str) -> bool {
    manager("ActivateSession", session_id)
}

/// Back to a running session, its password just checked: unlocked (its
/// lock screen goes -- Vini: the password was asked twice), then in front.
pub fn resume(session_id: &str) -> bool {
    manager("UnlockSession", session_id);
    activate(session_id)
}

/// How this system shows a login screen beside a running session, None for no way.
pub fn login_method_with(exists: &dyn Fn(&str) -> bool, which: &dyn Fn(&str) -> bool) -> Option<Method> {
    if exists(SWITCH_UNIT_FILE) {
        Some(Method::Sonata)
    } else if which("gdmflexiserver") {
        Some(Method::Gdm)
    } else if which("dm-tool") {
        Some(Method::Lightdm)
    } else {
        None
    }
}

pub fn login_method() -> Option<Method> {
    login_method_with(&|p| Path::new(p).exists(), &|c| sonata_backend::run::which(c).is_some())
}

/// Start or stop the switch greetd through systemd (the polkit rule lets
/// the local user and the greeter do it without a password).
fn unit(verb: &str) -> bool {
    let Some(bus) = bus() else { return false };
    let method = if verb == "start" { "StartUnit" } else { "StopUnit" };
    let r = bus.call_sync(Some("org.freedesktop.systemd1"), "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", method, Some(&(SWITCH_UNIT, "replace").to_variant()), glib::VariantTy::new("(o)").ok(), gio::DBusCallFlags::NONE, 10000, gio::Cancellable::NONE);
    match r {
        Ok(_) => true,
        Err(e) => {
            eprintln!("sonata2: switch user: {verb} {SWITCH_UNIT}: {}", e.message());
            false
        }
    }
}

/// What switching does on the system (a fake one in tests).
pub trait System {
    fn sessions(&self) -> Vec<LoginSession>;
    fn activate(&self, session_id: &str) -> bool;
    /// "start" / "stop" the switch greetd
    fn unit(&self, verb: &str) -> bool;
    /// a display manager's own command, on its own
    fn spawn(&self, argv: &[&str]) -> bool;
}

/// logind, systemd and the display managers' commands.
pub struct Real;

impl System for Real {
    fn sessions(&self) -> Vec<LoginSession> {
        sessions()
    }
    fn activate(&self, session_id: &str) -> bool {
        activate(session_id)
    }
    fn unit(&self, verb: &str) -> bool {
        unit(verb)
    }
    fn spawn(&self, argv: &[&str]) -> bool {
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};
        let Some((cmd, args)) = argv.split_first() else { return false };
        Command::new(cmd).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn().is_ok()
    }
}

/// The login screen on a free VT, this session left locked behind it.
pub fn show_login_screen(method: Method, sys: &dyn System) -> bool {
    match method {
        Method::Sonata => {
            if let Some(waiting) = greeter_session(&sys.sessions(), "seat0") {
                return sys.activate(&waiting.id); // already up (switched away from it): back to it
            }
            sys.unit("start") // (greetd switches to its VT itself)
        }
        Method::Gdm => sys.spawn(&["gdmflexiserver"]),
        Method::Lightdm => sys.spawn(&["dm-tool", "switch-to-greeter"]),
    }
}

/// The switch login screen's job is done (its user went back to a running
/// session): stop it, so no second login screen keeps running unseen.
pub fn leave_switch_greeter_with(sys: &dyn System) {
    if std::env::var_os(SWITCH_ENV).is_some_and(|v| !v.is_empty()) {
        sys.unit("stop");
    }
}

pub fn leave_switch_greeter() {
    leave_switch_greeter_with(&Real);
}

fn others<'a>(me: &str, everyone: &'a [String]) -> impl Iterator<Item = &'a String> {
    let me = me.to_string();
    everyone.iter().filter(move |u| **u != me)
}

/// Show Switch User: another user exists, and either a login screen can
/// be shown or that user is already logged in (switching to them works).
/// `listed`: asked only when needed.
pub fn available_with(me: &str, everyone: &[String], method: Option<Method>, listed: &dyn Fn() -> Vec<LoginSession>) -> bool {
    if others(me, everyone).next().is_none() {
        return false;
    }
    if method.is_some() {
        return true;
    }
    let listed = listed();
    others(me, everyone).any(|u| graphical_session(u, &listed, "seat0").is_some())
}

/// The users of this machine (AccountsService, else /etc/passwd).
fn everyone() -> Vec<String> {
    crate::logins::users().into_iter().map(|u| u.name).collect()
}

pub fn available(me: &str) -> bool {
    available_with(me, &everyone(), login_method(), &sessions)
}

/// Switch User from the lock screen: the login screen; without one, the
/// other user's running session (the first one found).
pub fn switch_with(me: &str, everyone: &[String], method: Option<Method>, sys: &dyn System) -> bool {
    if let Some(m) = method {
        return show_login_screen(m, sys);
    }
    let listed = sys.sessions();
    for u in others(me, everyone) {
        if let Some(s) = graphical_session(u, &listed, "seat0") {
            return sys.activate(&s.id);
        }
    }
    false
}

/// Blocking (D-Bus, systemd): off the UI thread.
pub fn switch(me: &str) -> bool {
    switch_with(me, &everyone(), login_method(), &Real)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    fn sess(id: &str, user: &str) -> LoginSession {
        LoginSession { id: id.into(), user: user.into(), seat: "seat0".into(), kind: "wayland".into(), class: "user".into(), state: "active".into(), remote: false }
    }

    fn names(n: &[&str]) -> Vec<String> {
        n.iter().map(|s| s.to_string()).collect()
    }

    #[derive(Default)]
    struct Fake {
        listed: Vec<LoginSession>,
        calls: RefCell<Vec<String>>,
    }

    impl System for Fake {
        fn sessions(&self) -> Vec<LoginSession> {
            self.listed.clone()
        }
        fn activate(&self, id: &str) -> bool {
            self.calls.borrow_mut().push(format!("activate {id}"));
            true
        }
        fn unit(&self, verb: &str) -> bool {
            self.calls.borrow_mut().push(format!("unit {verb}"));
            true
        }
        fn spawn(&self, argv: &[&str]) -> bool {
            self.calls.borrow_mut().push(argv.join(" "));
            true
        }
    }

    #[test]
    fn test_graphical_session_of_a_user() {
        let listed = vec![
            LoginSession { class: "greeter".into(), ..sess("c1", "greeter") },
            LoginSession { kind: "tty".into(), ..sess("3", "ana") },
            LoginSession { remote: true, ..sess("4", "ana") },
            LoginSession { seat: "seat1".into(), ..sess("5", "ana") },
            LoginSession { state: "closing".into(), ..sess("6", "ana") },
            LoginSession { state: "online".into(), ..sess("7", "ana") },
        ];
        // in the background: still hers
        assert_eq!(graphical_session("ana", &listed, "seat0").map(|s| s.id.as_str()), Some("7"));
        assert!(graphical_session("vini", &listed, "seat0").is_none());
        assert_eq!(greeter_session(&listed, "seat0").map(|s| s.id.as_str()), Some("c1"));
        assert!(greeter_session(&[sess("2", "vini")], "seat0").is_none());
    }

    #[test]
    fn only_a_local_desktop_session_is_resumed() {
        let mut listed = vec![sess("2", "vini")];
        assert_eq!(graphical_session("vini", &listed, "seat0").map(|s| s.id.as_str()), Some("2"));
        assert!(graphical_session("ana", &listed, "seat0").is_none()); // not logged in: logs in
        listed[0] = LoginSession { class: "greeter".into(), ..sess("2", "vini") };
        assert!(graphical_session("vini", &listed, "seat0").is_none());
    }

    #[test]
    fn test_login_method() {
        let no = |_: &str| false;
        assert_eq!(login_method_with(&|p| p == SWITCH_UNIT_FILE, &no), Some(Method::Sonata));
        assert_eq!(login_method_with(&no, &|c| c == "gdmflexiserver"), Some(Method::Gdm));
        assert_eq!(login_method_with(&no, &|c| c == "dm-tool"), Some(Method::Lightdm));
        assert_eq!(login_method_with(&no, &no), None);
        assert_eq!(SWITCH_UNIT_FILE, "/etc/systemd/system/sonata-greeter-switch.service");
    }

    #[test]
    fn test_alone_never() {
        assert!(!available_with("vini", &names(&["vini"]), Some(Method::Sonata), &Vec::new));
    }

    #[test]
    fn test_another_user_and_a_login_screen() {
        assert!(available_with("vini", &names(&["vini", "ana"]), Some(Method::Sonata), &|| panic!("not asked")));
    }

    #[test]
    fn test_without_a_login_screen_only_to_a_running_session() {
        let everyone = names(&["vini", "ana"]);
        assert!(!available_with("vini", &everyone, None, &|| vec![sess("2", "vini")]));
        assert!(available_with("vini", &everyone, None, &|| vec![sess("2", "vini"), sess("3", "ana")]));
    }

    #[test]
    fn test_sonata_starts_the_switch_greeter() {
        let sys = Fake { listed: vec![sess("2", "vini")], ..Default::default() };
        assert!(switch_with("vini", &names(&["vini", "ana"]), Some(Method::Sonata), &sys));
        assert_eq!(*sys.calls.borrow(), ["unit start"]);
    }

    /// Switched away from the login screen earlier: back to it, not a third greetd.
    #[test]
    fn test_sonata_switch_greeter_already_up() {
        let sys = Fake { listed: vec![sess("2", "vini"), LoginSession { class: "greeter".into(), ..sess("c3", "greeter") }], ..Default::default() };
        assert!(show_login_screen(Method::Sonata, &sys));
        assert_eq!(*sys.calls.borrow(), ["activate c3"]);
    }

    #[test]
    fn test_gdm_and_lightdm() {
        let sys = Fake::default();
        assert!(show_login_screen(Method::Gdm, &sys));
        assert!(show_login_screen(Method::Lightdm, &sys));
        assert_eq!(*sys.calls.borrow(), ["gdmflexiserver", "dm-tool switch-to-greeter"]);
    }

    #[test]
    fn test_no_login_screen_goes_to_the_other_session() {
        let sys = Fake { listed: vec![sess("2", "vini"), sess("3", "ana")], ..Default::default() };
        assert!(switch_with("vini", &names(&["vini", "ana"]), None, &sys));
        assert_eq!(*sys.calls.borrow(), ["activate 3"]);
        let sys = Fake { listed: vec![sess("2", "vini")], ..Default::default() };
        assert!(!switch_with("vini", &names(&["vini", "ana"]), None, &sys)); // nowhere to go
        assert!(sys.calls.borrow().is_empty());
    }

    #[test]
    fn test_leave_only_from_the_switch_greeter() {
        // (the only test here that sets SWITCH_ENV)
        let sys = Fake::default();
        std::env::set_var(SWITCH_ENV, "");
        leave_switch_greeter_with(&sys);
        assert!(sys.calls.borrow().is_empty());
        std::env::set_var(SWITCH_ENV, "1");
        leave_switch_greeter_with(&sys);
        std::env::remove_var(SWITCH_ENV);
        assert_eq!(*sys.calls.borrow(), ["unit stop"]);
    }
}
