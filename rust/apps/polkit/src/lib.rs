//! Sonata's authentication agent (shell/polkit.py): the password prompt
//! for system actions -- installing updates, changing the time zone, other
//! users... macOS's "<App> wants to make changes" sheet (authsheet).
//!
//! The agent over polkit's D-Bus interface: polkitd calls
//! BeginAuthentication; the method returns once the sheet is done (an
//! error when cancelled), and CancelAuthentication closes it. The password
//! itself is checked by polkit's helper (polkitagent.rs).
//! `sonata2-rs polkit` registers it for the login session; without polkit's
//! libraries, or with another agent already there, it exits quietly.

use gio::prelude::*;
use gtk::prelude::WidgetExt;
use sonata_i18n::tr;
use sonata_shell::authsheet::{Options, PasswordSheet};
use sonata_shell::polkitagent::{self, Identity, Session};
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

pub const OBJECT_PATH: &str = "/io/github/vinioliveiras/sonata2/PolkitAgent";
const AUTHORITY: (&str, &str, &str) = ("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority", "org.freedesktop.PolicyKit1.Authority");
const AGENT_XML: &str = r#"<node><interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
    <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
    <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
    <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
</interface></node>"#;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.polkit"
    } else {
        "io.github.vinioliveiras.sonata2.polkit"
    }
}

/// A user's login name and real name (the gecos' first field), from the
/// password database.
fn passwd(uid: Option<u32>, name: Option<&str>) -> Option<(String, String)> {
    let mut pw: libc::passwd = unsafe { std::mem::zeroed() };
    let mut buf = vec![0 as libc::c_char; 16384];
    let mut out: *mut libc::passwd = std::ptr::null_mut();
    let rc = unsafe {
        match (uid, name) {
            (Some(u), _) => libc::getpwuid_r(u, &mut pw, buf.as_mut_ptr(), buf.len(), &mut out),
            (None, Some(n)) => {
                let c = std::ffi::CString::new(n).ok()?;
                libc::getpwnam_r(c.as_ptr(), &mut pw, buf.as_mut_ptr(), buf.len(), &mut out)
            }
            _ => return None,
        }
    };
    if rc != 0 || out.is_null() {
        return None;
    }
    let s = |p: *const libc::c_char| if p.is_null() { String::new() } else { unsafe { std::ffi::CStr::from_ptr(p) }.to_string_lossy().into_owned() };
    Some((s(pw.pw_name), s(pw.pw_gecos).split(',').next().unwrap_or("").to_string()))
}

/// A group's name (getgrgid), None when there's no such group.
fn group_name(gid: u32) -> Option<String> {
    let mut gr: libc::group = unsafe { std::mem::zeroed() };
    let mut buf = vec![0 as libc::c_char; 16384];
    let mut out: *mut libc::group = std::ptr::null_mut();
    let rc = unsafe { libc::getgrgid_r(gid, &mut gr, buf.as_mut_ptr(), buf.len(), &mut out) };
    if rc != 0 || out.is_null() || gr.gr_name.is_null() {
        return None;
    }
    Some(unsafe { std::ffi::CStr::from_ptr(gr.gr_name) }.to_string_lossy().into_owned())
}

/// What the sheet calls an identity: a user's login name; a group as
/// polkit's own to_string() writes it ("unix-group:wheel", the number
/// only when the group has no name), as polkit.py shows it.
fn user_name(i: &Identity) -> String {
    match i {
        Identity::User(uid) => passwd(Some(*uid), None).map(|p| p.0).unwrap_or_else(|| uid.to_string()),
        Identity::Group(gid) => format!("unix-group:{}", group_name(*gid).unwrap_or_else(|| gid.to_string())),
    }
}

fn real_name(name: &str) -> String {
    passwd(None, Some(name)).map(|p| p.1).filter(|r| !r.is_empty()).unwrap_or_else(|| name.to_string())
}

/// The account picked first: this user when they may answer, else the first.
pub fn first_index(names: &[String], me: &str) -> usize {
    names.iter().position(|n| n == me).unwrap_or(0)
}

/// One request: shows the sheet, tries passwords, calls done(ok) once.
struct AuthDialog {
    sheet: RefCell<Option<Rc<PasswordSheet>>>,
    identities: Vec<Identity>,
    cookie: String,
    session: RefCell<Option<Session>>,
    finished: std::cell::Cell<bool>,
    done: RefCell<Option<Box<dyn FnOnce(bool)>>>,
}

impl AuthDialog {
    fn new(app: &gtk::Application, message: &str, identities: Vec<Identity>, cookie: &str, done: impl FnOnce(bool) + 'static) -> Rc<Self> {
        let me = glib::user_name().to_string_lossy().into_owned();
        let names: Vec<String> = identities.iter().map(user_name).collect();
        let index = first_index(&names, &me);
        let dlg = Rc::new(Self { sheet: RefCell::default(), identities, cookie: cookie.into(), session: RefCell::default(), finished: Default::default(), done: RefCell::new(Some(Box::new(done))) });
        let mut o = Options::new(&tr!("Authentication Required"), if message.is_empty() { &"" } else { &message });
        if message.is_empty() {
            o.text = tr!("An application wants to make changes.");
        }
        o.detail = Some(tr!("Enter the password to allow this."));
        o.real_names = names.iter().map(|n| real_name(n)).collect();
        o.names = names;
        o.index = index;
        let (w1, w2, w3) = (Rc::downgrade(&dlg), Rc::downgrade(&dlg), Rc::downgrade(&dlg));
        let sheet = PasswordSheet::new(
            Some(app),
            None,
            o,
            move |pw| {
                if let Some(d) = w1.upgrade() {
                    d.try_password(pw);
                }
            },
            move || {
                if let Some(d) = w2.upgrade() {
                    d.finish(false);
                }
            },
            move |_| {
                if let Some(d) = w3.upgrade() {
                    d.cancel_session(); // another account: a new try
                }
            },
        );
        dlg.sheet.replace(Some(sheet));
        dlg
    }

    fn sheet(&self) -> Option<Rc<PasswordSheet>> {
        self.sheet.borrow().clone()
    }

    fn try_password(self: &Rc<Self>, pw: String) {
        self.cancel_session();
        let Some(sheet) = self.sheet() else { return };
        let identity = &self.identities[sheet.index.get().min(self.identities.len() - 1)];
        let say = {
            let s = Rc::downgrade(&sheet);
            move |text: &str| {
                if let Some(s) = s.upgrade() {
                    s.say(text);
                }
            }
        };
        let weak = Rc::downgrade(self);
        let completed = move |gained: bool| {
            let Some(d) = weak.upgrade() else { return };
            // a finished session is only let go, after the signal returns
            // (cancelling it again is what crashed the Python agent)
            if let Some(s) = d.session.take() {
                glib::idle_add_local_once(move || drop(s));
            }
            if gained {
                d.finish(true);
            } else if let Some(sheet) = d.sheet() {
                let again = tr!("Sorry, try again.");
                sheet.wrong(if sheet.error.get_visible() { None } else { Some(&again) });
            }
        };
        match Session::start(identity, &self.cookie, pw, say, completed) {
            Some(s) => {
                self.session.replace(Some(s));
            }
            None => sheet.wrong(Some(&tr!("Sorry, try again."))),
        }
    }

    fn cancel_session(&self) {
        if let Some(s) = self.session.take() {
            s.cancel();
        }
    }

    fn finish(&self, ok: bool) {
        if self.finished.replace(true) {
            return;
        }
        self.cancel_session();
        if let Some(sheet) = self.sheet.take() {
            sheet.close();
        }
        if let Some(done) = self.done.take() {
            done(ok);
        }
    }
}

/// The identities polkit sent that a session can take.
pub fn identities_of(list: &glib::Variant) -> Vec<Identity> {
    (0..list.n_children())
        .filter_map(|i| {
            let item = list.child_value(i);
            let kind: String = item.child_value(0).get()?;
            let details = glib::VariantDict::new(Some(&item.child_value(1)));
            polkitagent::identity_of(&kind, &details)
        })
        .collect()
}

struct Agent {
    app: gtk::Application,
    bus: gio::DBusConnection,
    polkitd: RefCell<Option<String>>,
    dialogs: RefCell<HashMap<String, Rc<AuthDialog>>>,
    _reg: RefCell<Option<gio::RegistrationId>>,
}

impl Agent {
    fn new(app: &gtk::Application) -> Result<Rc<Self>, glib::Error> {
        let bus = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE)?;
        // only polkitd may ask for passwords: its unique bus name, known
        // before any request (the watch reports it later)
        let owner = bus
            .call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", Some(&(AUTHORITY.0,).to_variant()), None, gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.child_value(0).get::<String>());
        let agent = Rc::new(Self { app: app.clone(), bus: bus.clone(), polkitd: RefCell::new(owner), dialogs: RefCell::default(), _reg: RefCell::default() });
        let (w1, w2) = (Rc::downgrade(&agent), Rc::downgrade(&agent));
        // (watched for the agent's whole life: its id isn't kept)
        let _ = gio::bus_watch_name_on_connection(
            &bus,
            AUTHORITY.0,
            gio::BusNameWatcherFlags::NONE,
            move |_, _, owner| {
                if let Some(a) = w1.upgrade() {
                    a.polkitd.replace(Some(owner.to_string()));
                }
            },
            move |_, _| {
                if let Some(a) = w2.upgrade() {
                    a.polkitd.replace(None);
                }
            },
        );
        let node = gio::DBusNodeInfo::for_xml(AGENT_XML)?;
        let iface = node.lookup_interface("org.freedesktop.PolicyKit1.AuthenticationAgent").expect("the agent's interface");
        let weak = Rc::downgrade(&agent);
        let reg = bus
            .register_object(OBJECT_PATH, &iface)
            .method_call(move |_conn, sender, _path, _iface, method, params, invocation| {
                if let Some(a) = weak.upgrade() {
                    a.call(sender, method, &params, invocation);
                }
            })
            .build()?;
        agent._reg.replace(Some(reg));
        Ok(agent)
    }

    fn call(self: &Rc<Self>, sender: Option<&str>, method: &str, params: &glib::Variant, invocation: gio::DBusMethodInvocation) {
        // anyone else on the bus could fake a prompt
        if sender.is_none() || sender.map(String::from) != *self.polkitd.borrow() {
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.NotAuthorized", "Only polkitd may call the authentication agent");
            return;
        }
        if method == "CancelAuthentication" {
            let cookie: String = params.child_value(0).get().unwrap_or_default();
            let dlg = self.dialogs.borrow().get(&cookie).cloned();
            invocation.return_value(None);
            if let Some(d) = dlg {
                d.finish(false);
            }
            return;
        }
        let message: String = params.child_value(1).get().unwrap_or_default();
        let cookie: String = params.child_value(4).get().unwrap_or_default();
        let ids = identities_of(&params.child_value(5));
        if ids.is_empty() {
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Failed", "No identity to authenticate");
            return;
        }
        let weak = Rc::downgrade(self);
        let key = cookie.clone();
        let dlg = AuthDialog::new(&self.app, &message, ids, &cookie, move |ok| {
            if let Some(a) = weak.upgrade() {
                a.dialogs.borrow_mut().remove(&key);
            }
            if ok {
                invocation.return_value(None);
            } else {
                invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled", "Authentication was cancelled");
            }
        });
        self.dialogs.borrow_mut().insert(cookie, dlg);
    }
}

/// This process's login session: logind's, else $XDG_SESSION_ID.
fn session_id(bus: &gio::DBusConnection) -> Option<String> {
    let login = "org.freedesktop.login1";
    let path = bus
        .call_sync(Some(login), "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "GetSessionByPID", Some(&(std::process::id(),).to_variant()), None, gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE)
        .ok()
        .and_then(|v| v.child_value(0).str().map(String::from));
    let from_logind = path.and_then(|p| {
        bus.call_sync(Some(login), &p, "org.freedesktop.DBus.Properties", "Get", Some(&("org.freedesktop.login1.Session", "Id").to_variant()), None, gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.child_value(0).as_variant())
            .and_then(|v| v.get::<String>())
    });
    from_logind.or_else(|| std::env::var("XDG_SESSION_ID").ok()).filter(|s| !s.is_empty())
}

/// Register for this login session. The agent (keep it), or None.
fn register(app: &gtk::Application) -> Option<Rc<Agent>> {
    if !polkitagent::available() {
        eprintln!("sonata2-rs polkit: polkit's agent library isn't installed");
        return None;
    }
    let agent = match Agent::new(app) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("sonata2-rs polkit: no system bus: {}", e.message());
            return None;
        }
    };
    let Some(sid) = session_id(&agent.bus) else {
        eprintln!("sonata2-rs polkit: no login session found");
        return None;
    };
    let subject = session_subject(&sid);
    let locale = ["LC_ALL", "LC_MESSAGES", "LANG"].iter().find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty())).unwrap_or_else(|| "C".into());
    let args = glib::Variant::tuple_from_iter([subject, locale.to_variant(), OBJECT_PATH.to_variant()]);
    if let Err(e) = agent.bus.call_sync(Some(AUTHORITY.0), AUTHORITY.1, AUTHORITY.2, "RegisterAuthenticationAgent", Some(&args), None, gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE) {
        eprintln!("sonata2-rs polkit: couldn't register: {}", e.message());
        return None;
    }
    if sonata_session::logs::verbose() {
        println!("sonata2-rs polkit: registered for session {sid}");
    }
    Some(agent)
}

/// polkit's Subject for a login session: (sa{sv}). The details as a
/// tuple's second member went out boxed, (sv), and polkit refused the
/// registration -- the Rust session had no password prompt at all.
pub fn session_subject(sid: &str) -> glib::Variant {
    let details = glib::VariantDict::new(None);
    details.insert_value("session-id", &sid.to_variant());
    glib::Variant::tuple_from_iter(["unix-session".to_variant(), details.end()])
}

/// `sonata2-rs polkit`: Sonata's password prompt for system actions.
pub fn run(_args: &[String]) -> glib::ExitCode {
    sonata_shell::layer::ensure_preload();
    let app = gtk::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Agent>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        // another agent has the session: exit quietly
        if let Some(agent) = register(app) {
            hold.replace(Some(app.hold()));
            state.replace(Some(agent));
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {

    /// Vini, in the Rust session: paru's pkexec said "Error creating
    /// textual authentication agent" -- Sonata's agent had been refused by
    /// polkit ("((sv)ss)" for "((sa{sv})ss)") and was gone.
    #[test]
    fn test_password_prompt_refused_by_polkit() {
        let subject = super::session_subject("2");
        assert_eq!(subject.type_().as_str(), "(sa{sv})");
        let args = glib::Variant::tuple_from_iter([subject, "C".to_variant(), "/x".to_variant()]);
        assert_eq!(args.type_().as_str(), "((sa{sv})ss)");
    }

    use super::*;

    #[test]
    fn this_user_answers_first() {
        let names = vec!["root".to_string(), "vini".to_string()];
        assert_eq!(first_index(&names, "vini"), 1);
        assert_eq!(first_index(&names, "ana"), 0);
    }

    #[test]
    fn identities_from_polkits_list() {
        let user = glib::VariantDict::new(None);
        user.insert_value("uid", &1000u32.to_variant());
        let group = glib::VariantDict::new(None);
        group.insert_value("gid", &10u32.to_variant());
        let none = glib::VariantDict::new(None);
        // a(sa{sv}), as polkitd sends it
        let item = |kind: &str, d: glib::VariantDict| glib::Variant::tuple_from_iter([kind.to_variant(), d.end()]);
        let ty = glib::VariantTy::new("(sa{sv})").unwrap();
        let list = glib::Variant::array_from_iter_with_type(ty, [item("unix-user", user), item("unix-group", group), item("unix-user", none)]);
        assert_eq!(identities_of(&list), [Identity::User(1000), Identity::Group(10)]);
    }

    /// A prompt any member of a group could answer listed it as
    /// "unix-group:10" where the Python agent says "unix-group:wheel".
    #[test]
    fn test_group_shown_by_number() {
        assert_eq!(user_name(&Identity::Group(0)), "unix-group:root");
        assert_eq!(user_name(&Identity::Group(4_000_000_000)), "unix-group:4000000000"); // no such group
    }

    #[test]
    fn the_root_account_is_named() {
        assert_eq!(user_name(&Identity::User(0)), "root");
        assert_eq!(real_name("no-such-user-here"), "no-such-user-here");
    }
}
