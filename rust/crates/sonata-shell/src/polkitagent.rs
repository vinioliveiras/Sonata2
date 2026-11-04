//! polkit's password exchange (PolkitAgent.Session, libpolkit-agent-1):
//! the session runs polkit-agent-helper-1, which checks the password
//! itself -- the agent only passes it on. The libraries are opened at run
//! time, as pam.rs does: without them `sonata2-rs polkit` doesn't register
//! and the session keeps another agent.

use glib::prelude::*;
use glib::translate::*;
use std::ffi::{c_char, c_int, c_void, CString};
use std::sync::OnceLock;

struct Lib {
    unix_user_new: unsafe extern "C" fn(c_int) -> *mut c_void,
    unix_group_new: unsafe extern "C" fn(c_int) -> *mut c_void,
    session_new: unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void,
    session_initiate: unsafe extern "C" fn(*mut c_void),
    session_response: unsafe extern "C" fn(*mut c_void, *const c_char),
    session_cancel: unsafe extern "C" fn(*mut c_void),
}

fn open(names: &[&std::ffi::CStr]) -> Option<*mut c_void> {
    names.iter().map(|n| unsafe { libc::dlopen(n.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) }).find(|h| !h.is_null())
}

fn lib() -> Option<&'static Lib> {
    static LIB: OnceLock<Option<Lib>> = OnceLock::new();
    LIB.get_or_init(|| unsafe {
        let gobj = open(&[c"libpolkit-gobject-1.so.0", c"libpolkit-gobject-1.so"])?;
        let agent = open(&[c"libpolkit-agent-1.so.0", c"libpolkit-agent-1.so"])?;
        let sym = |h: *mut c_void, name: &str| {
            let p = libc::dlsym(h, CString::new(name).ok()?.as_ptr());
            (!p.is_null()).then_some(p)
        };
        // (the libraries stay open for the process)
        Some(Lib {
            unix_user_new: std::mem::transmute::<*mut c_void, _>(sym(gobj, "polkit_unix_user_new")?),
            unix_group_new: std::mem::transmute::<*mut c_void, _>(sym(gobj, "polkit_unix_group_new")?),
            session_new: std::mem::transmute::<*mut c_void, _>(sym(agent, "polkit_agent_session_new")?),
            session_initiate: std::mem::transmute::<*mut c_void, _>(sym(agent, "polkit_agent_session_initiate")?),
            session_response: std::mem::transmute::<*mut c_void, _>(sym(agent, "polkit_agent_session_response")?),
            session_cancel: std::mem::transmute::<*mut c_void, _>(sym(agent, "polkit_agent_session_cancel")?),
        })
    })
    .as_ref()
}

pub fn available() -> bool {
    lib().is_some()
}

/// Who may answer: a user or a group (polkit's D-Bus identity).
#[derive(Clone, Debug, PartialEq)]
pub enum Identity {
    User(u32),
    Group(u32),
}

/// polkit's D-Bus identity -> one the session takes. No id given: skipped
/// -- never a default of 0, which would mean root.
pub fn identity_of(kind: &str, details: &glib::VariantDict) -> Option<Identity> {
    let id = |k: &str| details.lookup_value(k, None).and_then(|v| v.get::<u32>().or_else(|| v.get::<i32>().and_then(|i| u32::try_from(i).ok())));
    match kind {
        "unix-user" => id("uid").map(Identity::User),
        "unix-group" => id("gid").map(Identity::Group),
        _ => None,
    }
}

fn identity_object(lib: &Lib, i: &Identity) -> Option<glib::Object> {
    let p = unsafe {
        match i {
            Identity::User(uid) => (lib.unix_user_new)(*uid as c_int),
            Identity::Group(gid) => (lib.unix_group_new)(*gid as c_int),
        }
    };
    (!p.is_null()).then(|| unsafe { from_glib_full(p as *mut glib::gobject_ffi::GObject) })
}

/// The password, wiped from memory when the session lets go of it.
struct Secret(String);

impl Drop for Secret {
    fn drop(&mut self) {
        let bytes = unsafe { self.0.as_bytes_mut() };
        for b in bytes.iter_mut() {
            unsafe { std::ptr::write_volatile(b, 0) };
        }
    }
}

/// One try of the password, for one identity and one request.
pub struct Session {
    obj: glib::Object,
    _identity: glib::Object,
}

impl Session {
    /// The password goes in when the helper asks for it ("request");
    /// show_error(text) for what it says; completed(gained) at the end.
    pub fn start(identity: &Identity, cookie: &str, password: String, show_error: impl Fn(&str) + 'static, completed: impl Fn(bool) + 'static) -> Option<Self> {
        let lib = lib()?;
        let ident = identity_object(lib, identity)?;
        let cookie = CString::new(cookie).ok()?;
        let p = unsafe { (lib.session_new)(ident.as_ptr() as *mut c_void, cookie.as_ptr()) };
        if p.is_null() {
            return None;
        }
        let obj: glib::Object = unsafe { from_glib_full(p as *mut glib::gobject_ffi::GObject) };
        let secret = Secret(password);
        obj.connect_local("request", false, move |values| {
            let sess: glib::Object = values[0].get().ok()?;
            // every prompt the helper shows gets the password (as polkit.py
            // answers each with the field's text)
            if let Ok(c) = CString::new(secret.0.clone()) {
                unsafe { (lib.session_response)(sess.as_ptr() as *mut c_void, c.as_ptr()) };
                let mut bytes = c.into_bytes_with_nul();
                for b in bytes.iter_mut() {
                    unsafe { std::ptr::write_volatile(b, 0) }; // not left in memory
                }
            }
            None
        });
        obj.connect_local("show-error", false, move |values| {
            show_error(&values.get(1).and_then(|v| v.get::<String>().ok()).unwrap_or_default());
            None
        });
        obj.connect_local("completed", false, move |values| {
            completed(values.get(1).and_then(|v| v.get::<bool>().ok()).unwrap_or(false));
            None
        });
        unsafe { (lib.session_initiate)(obj.as_ptr() as *mut c_void) };
        Some(Self { obj, _identity: ident })
    }

    pub fn cancel(&self) {
        if let Some(lib) = lib() {
            unsafe { (lib.session_cancel)(self.obj.as_ptr() as *mut c_void) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identities_never_default_to_root() {
        let d = glib::VariantDict::new(None);
        assert_eq!(identity_of("unix-user", &d), None); // no uid: skipped, not 0
        d.insert_value("uid", &1000u32.to_variant());
        assert_eq!(identity_of("unix-user", &d), Some(Identity::User(1000)));
        assert_eq!(identity_of("unix-group", &d), None);
        let g = glib::VariantDict::new(None);
        g.insert_value("gid", &998u32.to_variant());
        assert_eq!(identity_of("unix-group", &g), Some(Identity::Group(998)));
        assert_eq!(identity_of("unix-netgroup", &g), None);
        let neg = glib::VariantDict::new(None);
        neg.insert_value("uid", &(-1i32).to_variant());
        assert_eq!(identity_of("unix-user", &neg), None);
    }
}
