//! Password check through PAM (sonata2/pam.py), for the lock screen: one
//! conversation that answers the password prompt. libpam is opened at run
//! time, as the Python one does: without it the lock screen refuses to
//! lock (never lock without a way to unlock). Run it off the GTK thread (a
//! wrong password makes PAM wait ~2 s on purpose).

use std::ffi::{c_char, c_int, c_void, CString};
#[cfg(test)]
use std::ffi::CStr;
use std::sync::OnceLock;

const PAM_PROMPT_ECHO_OFF: c_int = 1;
const PAM_PROMPT_ECHO_ON: c_int = 2;
const PAM_BUF_ERR: c_int = 5;
/// password expired: still the right person (a lock screen can't change it)
const PAM_NEW_AUTHTOK_REQD: c_int = 12;
const PAM_REINITIALIZE_CRED: c_int = 0x8;
pub const SERVICES: [&str; 5] = ["sonata-lock", "system-local-login", "login", "system-auth", "other"];

#[repr(C)]
struct PamMessage {
    msg_style: c_int,
    msg: *const c_char,
}

#[repr(C)]
struct PamResponse {
    resp: *mut c_char,
    resp_retcode: c_int,
}

type ConvFn = extern "C" fn(c_int, *mut *const PamMessage, *mut *mut PamResponse, *mut c_void) -> c_int;

#[repr(C)]
struct PamConv {
    conv: ConvFn,
    appdata_ptr: *mut c_void,
}

struct Lib {
    start: unsafe extern "C" fn(*const c_char, *const c_char, *const PamConv, *mut *mut c_void) -> c_int,
    authenticate: unsafe extern "C" fn(*mut c_void, c_int) -> c_int,
    acct_mgmt: unsafe extern "C" fn(*mut c_void, c_int) -> c_int,
    setcred: unsafe extern "C" fn(*mut c_void, c_int) -> c_int,
    end: unsafe extern "C" fn(*mut c_void, c_int) -> c_int,
}

fn lib() -> Option<&'static Lib> {
    static LIB: OnceLock<Option<Lib>> = OnceLock::new();
    LIB.get_or_init(|| unsafe {
        let h = [c"libpam.so.0", c"libpam.so"].iter().map(|n| libc::dlopen(n.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL)).find(|h| !h.is_null())?;
        let sym = |name: &str| {
            let p = libc::dlsym(h, CString::new(name).ok()?.as_ptr());
            (!p.is_null()).then_some(p)
        };
        // (the library stays open for the process: its functions are kept)
        Some(Lib {
            start: std::mem::transmute::<*mut c_void, _>(sym("pam_start")?),
            authenticate: std::mem::transmute::<*mut c_void, _>(sym("pam_authenticate")?),
            acct_mgmt: std::mem::transmute::<*mut c_void, _>(sym("pam_acct_mgmt")?),
            setcred: std::mem::transmute::<*mut c_void, _>(sym("pam_setcred")?),
            end: std::mem::transmute::<*mut c_void, _>(sym("pam_end")?),
        })
    })
    .as_ref()
}

pub fn available() -> bool {
    lib().is_some()
}

/// The first PAM service of SERVICES this system has.
pub fn service_in(dirs: &[&str]) -> String {
    SERVICES.iter().find(|s| dirs.iter().any(|d| std::path::Path::new(d).join(s).exists())).unwrap_or(&"login").to_string()
}

fn service() -> String {
    service_in(&["/etc/pam.d", "/usr/lib/pam.d"])
}

/// Answers every prompt with the password (appdata: a NUL-ended copy).
/// PAM frees the responses: they're allocated with libc.
extern "C" fn conversation(n: c_int, msgs: *mut *const PamMessage, resp: *mut *mut PamResponse, data: *mut c_void) -> c_int {
    if n <= 0 || msgs.is_null() || resp.is_null() || data.is_null() {
        return PAM_BUF_ERR;
    }
    unsafe {
        let arr = libc::calloc(n as usize, std::mem::size_of::<PamResponse>()) as *mut PamResponse;
        if arr.is_null() {
            return PAM_BUF_ERR;
        }
        for i in 0..n as usize {
            let m = *msgs.add(i);
            if !m.is_null() && ((*m).msg_style == PAM_PROMPT_ECHO_OFF || (*m).msg_style == PAM_PROMPT_ECHO_ON) {
                (*arr.add(i)).resp = libc::strdup(data as *const c_char);
                (*arr.add(i)).resp_retcode = 0;
            }
        }
        *resp = arr;
    }
    0
}

/// True when `password` is `user`'s, and the account may log in.
pub fn authenticate(user: &str, password: &str) -> bool {
    let Some(lib) = lib() else { return false };
    let (Ok(user), Ok(service)) = (CString::new(user), CString::new(service())) else { return false };
    let mut secret = password.as_bytes().to_vec();
    if secret.contains(&0) {
        wipe(&mut secret);
        return false;
    }
    secret.push(0);
    let conv = PamConv { conv: conversation, appdata_ptr: secret.as_mut_ptr() as *mut c_void };
    let mut handle: *mut c_void = std::ptr::null_mut();
    let ok = unsafe {
        if (lib.start)(service.as_ptr(), user.as_ptr(), &conv, &mut handle) != 0 {
            false
        } else {
            let mut rc = (lib.authenticate)(handle, 0);
            if rc == 0 {
                // the account itself must still be valid: an expired or locked
                // account with the right password must not open the lock screen
                rc = (lib.acct_mgmt)(handle, 0);
                if rc == PAM_NEW_AUTHTOK_REQD {
                    rc = 0;
                }
            }
            if rc == 0 {
                (lib.setcred)(handle, PAM_REINITIALIZE_CRED); // (refresh Kerberos etc.)
            }
            (lib.end)(handle, rc);
            rc == 0
        }
    };
    wipe(&mut secret); // the password doesn't stay in this process's memory
    ok
}

fn wipe(bytes: &mut [u8]) {
    for b in bytes.iter_mut() {
        unsafe { std::ptr::write_volatile(b, 0) };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn c_text(p: *const c_char) -> String {
        if p.is_null() { String::new() } else { unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned() }
    }

    #[test]
    fn the_first_service_found() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("login"), "").unwrap();
        std::fs::write(d.path().join("other"), "").unwrap();
        assert_eq!(service_in(&[d.path().to_str().unwrap()]), "login");
        std::fs::write(d.path().join("sonata-lock"), "").unwrap();
        assert_eq!(service_in(&[d.path().to_str().unwrap()]), "sonata-lock");
        assert_eq!(service_in(&["/nonexistent"]), "login");
    }

    #[test]
    fn the_conversation_answers_password_prompts_only() {
        let secret = CString::new("hunter2").unwrap();
        let prompt = CString::new("Password: ").unwrap();
        let msgs = [PamMessage { msg_style: PAM_PROMPT_ECHO_OFF, msg: prompt.as_ptr() }, PamMessage { msg_style: 4, msg: prompt.as_ptr() }];
        let mut ptrs: Vec<*const PamMessage> = msgs.iter().map(|m| m as *const PamMessage).collect();
        let mut resp: *mut PamResponse = std::ptr::null_mut();
        assert_eq!(conversation(2, ptrs.as_mut_ptr(), &mut resp, secret.as_ptr() as *mut c_void), 0);
        unsafe {
            assert_eq!(c_text((*resp).resp), "hunter2");
            assert!((*resp.add(1)).resp.is_null()); // an info message gets no answer
            libc::free((*resp).resp as *mut c_void);
            libc::free(resp as *mut c_void);
        }
        assert_eq!(conversation(0, ptrs.as_mut_ptr(), &mut resp, secret.as_ptr() as *mut c_void), PAM_BUF_ERR);
    }

    #[test]
    fn nul_in_a_password_is_refused() {
        assert!(!authenticate("nobody-here", "a\0b"));
    }
}
