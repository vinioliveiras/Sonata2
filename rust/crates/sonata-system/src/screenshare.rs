//! Screen Sharing (backend/screenshare.py; Vini): see and control this
//! Sonata desktop from another computer or a phone, with any VNC viewer.
//!
//! wayvnc (wlroots' VNC server) runs from the menu bar process while
//! Settings > Sharing > Screen Sharing is on. Always with a password: user
//! "sonata" + a generated password, encrypted with RSA-AES (wayvnc's own
//! key in the config folder's wayvnc/, files readable only by you).
//!
//! config "sharing": {"screen": bool, "output": "HDMI-A-1" or "", "rev"}.

use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::io::Read;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

pub const NAME: &str = "sharing";
pub const PORT: u16 = 5900;
pub const USER: &str = "sonata";
/// a server that keeps quitting waits longer each time
pub const RESTART_MS: [u64; 4] = [2000, 5000, 15000, 60000];

/// rev: bumped to restart it (a new password)
pub fn defaults() -> Value {
    json!({"screen": false, "output": "", "rev": 0})
}

/// wayvnc's files (this build's settings folder)
pub fn folder() -> PathBuf {
    sonata_config::write_dir().join("wayvnc")
}

pub fn installed() -> bool {
    sonata_backend::run::which("wayvnc").is_some()
}

fn private(path: &std::path::Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

fn make_folder() {
    use std::os::unix::fs::DirBuilderExt;
    let _ = std::fs::DirBuilder::new().recursive(true).mode(0o700).create(folder());
}

/// ("sonata", password): made once, kept in wayvnc's config.
pub fn credentials() -> (&'static str, String) {
    let pw = std::fs::read_to_string(folder().join("password")).map(|s| s.trim().to_string()).unwrap_or_default();
    if !pw.is_empty() {
        return (USER, pw);
    }
    (USER, new_password())
}

/// "4f1a-9c02-b7e3": easy to type on a phone.
pub fn new_password() -> String {
    make_folder();
    let mut b = [0u8; 6];
    let _ = std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut b));
    let pw = b.chunks(2).map(|c| format!("{:02x}{:02x}", c[0], c[1])).collect::<Vec<_>>().join("-");
    let path = folder().join("password");
    let _ = std::fs::write(&path, format!("{pw}\n"));
    private(&path);
    pw
}

/// wayvnc's RSA key (RSA-AES: the password and the screen go encrypted).
fn key() -> Option<PathBuf> {
    let path = folder().join("rsa_key.pem");
    if !path.exists() {
        let p = path.to_string_lossy().to_string();
        sonata_backend::run::run(&["ssh-keygen", "-q", "-m", "pem", "-t", "rsa", "-b", "3072", "-N", "", "-f", &p], 30);
        let _ = std::fs::remove_file(format!("{p}.pub"));
    }
    path.exists().then(|| {
        private(&path);
        path
    })
}

/// wayvnc's config file; returns its path.
pub fn write_config() -> PathBuf {
    make_folder();
    let (user, pw) = credentials();
    let mut lines = vec!["address=0.0.0.0".to_string(), format!("port={PORT}"), "enable_auth=true".into(), format!("username={user}"), format!("password={pw}")];
    if let Some(k) = key() {
        lines.push(format!("rsa_private_key_file={}", k.display()));
    }
    let path = folder().join("config");
    let _ = std::fs::write(&path, lines.join("\n") + "\n");
    private(&path);
    path
}

/// Where to connect: this computer's local addresses (Tailscale's first),
/// from `ip -o -4 addr show scope global`.
pub fn addresses_of(ip_out: &str) -> Vec<(String, String)> {
    let mut out: Vec<(u8, String, String)> = ip_out
        .lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split_whitespace().collect();
            (p.len() > 3).then(|| (if p[1].starts_with("tailscale") { 0 } else { 1 }, format!("{}:{PORT}", p[3].split('/').next().unwrap_or("")), p[1].to_string()))
        })
        .collect();
    out.sort();
    out.into_iter().map(|(_, a, i)| (a, i)).collect()
}

pub fn addresses() -> Vec<(String, String)> {
    addresses_of(&sonata_backend::run::run(&["ip", "-o", "-4", "addr", "show", "scope", "global"], 3).1)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Wanted {
    pub screen: bool,
    pub output: String,
    pub rev: i64,
}

struct Inner {
    /// the running wayvnc's pid
    pid: Cell<Option<i32>>,
    fails: Cell<usize>,
    retry: RefCell<Option<glib::SourceId>>,
    mon: RefCell<Vec<gio::FileMonitor>>,
    running_with: RefCell<Option<Wanted>>,
    program: String,
    installed: Box<dyn Fn() -> bool>,
    last_cmd: RefCell<Vec<String>>,
}

/// wayvnc while sharing is on; started again if it quits. Main thread.
#[derive(Clone)]
pub struct ScreenSharing(Rc<Inner>);

impl Default for ScreenSharing {
    fn default() -> Self {
        Self::new()
    }
}

impl ScreenSharing {
    pub fn new() -> Self {
        Self::with_program("wayvnc", installed)
    }

    /// The server run (tests run their own).
    pub fn with_program(program: &str, installed: impl Fn() -> bool + 'static) -> Self {
        Self(Rc::new(Inner { pid: Cell::new(None), fails: Cell::new(0), retry: RefCell::default(), mon: RefCell::default(), running_with: RefCell::default(), program: program.into(), installed: Box::new(installed), last_cmd: RefCell::default() }))
    }

    pub fn start(&self) {
        let w = Rc::downgrade(&self.0);
        *self.0.mon.borrow_mut() = sonata_config::watch(NAME, move || {
            if let Some(i) = w.upgrade() {
                ScreenSharing(i).apply();
            }
        });
        self.apply();
    }

    pub fn running(&self) -> Option<i32> {
        self.0.pid.get()
    }

    pub fn last_command(&self) -> Vec<String> {
        self.0.last_cmd.borrow().clone()
    }

    pub fn wanted(&self) -> Wanted {
        let cfg = sonata_config::load(NAME, &defaults());
        Wanted { screen: crate::py::truthy(cfg.get("screen")) && (self.0.installed)(), output: cfg.get("output").and_then(Value::as_str).unwrap_or("").into(), rev: cfg.get("rev").and_then(Value::as_i64).unwrap_or(0) }
    }

    pub fn apply(&self) {
        let want = self.wanted();
        if !want.screen {
            self.stop();
            return;
        }
        if self.0.pid.get().is_some() && self.0.running_with.borrow().as_ref() == Some(&want) {
            return;
        }
        self.stop();
        self.spawn(want);
    }

    fn spawn(&self, want: Wanted) {
        let mut cmd = vec![self.0.program.clone(), "-C".into(), write_config().to_string_lossy().into_owned()];
        if !want.output.is_empty() {
            cmd.extend(["-o".to_string(), want.output.clone()]);
        }
        *self.0.last_cmd.borrow_mut() = cmd.clone();
        let log = sonata_session::logs::log_dir().join("screen-sharing.log");
        if let Some(d) = log.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let out = std::fs::OpenOptions::new().create(true).append(true).open(&log).ok();
        let err = out.as_ref().and_then(|f| f.try_clone().ok());
        let mut c = std::process::Command::new(&cmd[0]);
        c.args(&cmd[1..]).stdin(std::process::Stdio::null());
        c.stdout(out.map(Into::into).unwrap_or(std::process::Stdio::null())).stderr(err.map(Into::into).unwrap_or(std::process::Stdio::null()));
        let child = match c.spawn() {
            Ok(ch) => ch,
            Err(e) => {
                eprintln!("sonata2: screen sharing: {e}");
                return;
            }
        };
        let pid = child.id() as i32;
        // GLib reaps it (std's Child is dropped without waiting)
        drop(child);
        self.0.pid.set(Some(pid));
        *self.0.running_with.borrow_mut() = Some(want);
        let w = Rc::downgrade(&self.0);
        glib::child_watch_add_local(glib::Pid(pid), move |p, _status| {
            if let Some(i) = w.upgrade() {
                ScreenSharing(i).ended(p.0);
            }
        });
    }

    fn ended(&self, pid: i32) {
        if self.0.pid.get() != Some(pid) {
            return; // stopped on purpose (stop / apply)
        }
        self.0.pid.set(None);
        if !self.wanted().screen {
            return;
        }
        let delay = RESTART_MS[self.0.fails.get().min(RESTART_MS.len() - 1)];
        self.0.fails.set(self.0.fails.get() + 1);
        let w = Rc::downgrade(&self.0);
        *self.0.retry.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(delay), move || {
            if let Some(i) = w.upgrade() {
                i.retry.borrow_mut().take();
                ScreenSharing(i).apply();
            }
        }));
    }

    pub fn stop(&self) {
        if let Some(s) = self.0.retry.borrow_mut().take() {
            s.remove();
        }
        *self.0.running_with.borrow_mut() = None;
        if let Some(pid) = self.0.pid.take() {
            unsafe { libc::kill(pid, libc::SIGTERM) };
            // still there 2 s later (the watch hasn't reaped it): killed
            let alive = move || std::fs::metadata(format!("/proc/{pid}")).is_ok() && !std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default().contains(") Z ");
            glib::timeout_add_local_once(Duration::from_secs(2), move || {
                if alive() {
                    unsafe { libc::kill(pid, libc::SIGKILL) };
                }
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn always_a_password_and_private_files() {
        let _l = crate::test_lock();
        let path = write_config();
        let text = std::fs::read_to_string(&path).unwrap();
        let (user, pw) = credentials();
        assert!(text.contains("enable_auth=true\n"));
        assert!(text.contains(&format!("username={user}\n")) && text.contains(&format!("password={pw}\n")));
        assert!(regex::Regex::new(r"^[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}$").unwrap().is_match(&pw));
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(credentials(), (user, pw.clone())); // kept
        assert_ne!(new_password(), pw);
    }

    #[test]
    fn addresses_tailscale_first() {
        let out = "2: wlan0    inet 192.168.1.20/24 brd x scope global\n5: tailscale0    inet 100.64.0.7/32 scope global\n";
        assert_eq!(addresses_of(out), [("100.64.0.7:5900".to_string(), "tailscale0".to_string()), ("192.168.1.20:5900".to_string(), "wlan0".to_string())]);
    }

    /// tests/test_screenshare.py: a server that quits is started again
    /// (after RESTART_MS[0]), as long as sharing is on.
    #[test]
    fn started_again_when_it_quits() {
        crate::on_main(move || {
            let dir = tempfile::tempdir().unwrap();
            let fake = dir.path().join("wayvnc");
            std::fs::write(&fake, "#!/bin/sh\nexit 1\n").unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
            let save = |v: Value| sonata_config::save(NAME, v.as_object().unwrap()).unwrap();
            save(json!({"screen": 1})); // (bool() of the setting, as the Python reads it)
            let s = ScreenSharing::with_program(fake.to_str().unwrap(), || true);
            s.apply();
            assert!(s.running().is_some());
            crate::spin(300);
            assert_eq!((s.running(), s.0.fails.get()), (None, 1)); // it quit: a retry waits
            crate::spin(RESTART_MS[0] + 500);
            assert_eq!(s.0.fails.get(), 2); // started again (and quit again)
            save(json!({"screen": false}));
            s.apply();
            assert_eq!(s.running(), None);
        });
    }

    /// On, off, the display and a new password (restarted); never without wayvnc.
    #[test]
    fn on_off_display_and_new_password() {
        crate::on_main(move || {
            let dir = tempfile::tempdir().unwrap();
            let fake = dir.path().join("wayvnc");
            std::fs::write(&fake, "#!/bin/sh\nexec sleep 30\n").unwrap();
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
            let save = |v: Value| sonata_config::save(NAME, v.as_object().unwrap()).unwrap();
            save(json!({"screen": false}));
            let s = ScreenSharing::with_program(fake.to_str().unwrap(), || true);
            s.apply();
            assert_eq!(s.running(), None); // off: nothing runs
            save(json!({"screen": true, "output": "HDMI-A-1"}));
            s.apply();
            let first = s.running().unwrap();
            assert_eq!(s.last_command()[3..], ["-o", "HDMI-A-1"]);
            s.apply();
            assert_eq!(s.running(), Some(first)); // same settings: left alone
            save(json!({"screen": true, "output": "HDMI-A-1", "rev": 1}));
            s.apply();
            let second = s.running().unwrap();
            assert_ne!(second, first); // a new password: started again
            save(json!({"screen": false}));
            s.apply();
            assert_eq!(s.running(), None);
            crate::spin(300);
            for pid in [first, second] {
                let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default();
                assert!(stat.is_empty() || stat.contains(") Z "), "{pid} still runs");
            }
            save(json!({"screen": true}));
            let never = ScreenSharing::with_program(fake.to_str().unwrap(), || false);
            never.apply();
            assert_eq!(never.running(), None);
            save(json!({"screen": false}));
        });
    }
}
