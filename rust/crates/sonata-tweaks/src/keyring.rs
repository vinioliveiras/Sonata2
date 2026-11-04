//! Sonata's keyring (sonata2/keyring.py): where apps keep saved passwords
//! (Chrome, VS Code, NetworkManager's Wi-Fi secrets...) through the
//! freedesktop Secret Service.
//!
//! "gnome" (the default): GNOME's keyring, unlocked by the login itself --
//! the login screen's PAM hands it your password (pam_gnome_keyring in
//! /etc/pam.d/greetd), so nothing asks for a password after logging in.
//! "keepassxc": KeePassXC's Secret Service (Sonata's keyring before): its
//! database has a password of its own, asked at every login.
//!
//! Settings > Security & Privacy > "Use Login Password…" moves from
//! KeePassXC to GNOME's keyring once: the apps' secrets are copied over,
//! the login screen learns to unlock the keyring, and KeePassXC stays
//! installed as a password manager.
//!
//! `main(["backend" | "pam" | "unpam"])` is `sonata2 keyring ...`.

use glib::variant::ToVariant;
use serde_json::json;
use sonata_i18n::tr;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub const CONFIG: &str = "~/.config/keepassxc/keepassxc.ini";
pub const PAM_FILE: &str = "/etc/pam.d/greetd";
/// the lines pam_gnome_keyring needs: unlock with the login password,
/// start the daemon, and follow password changes
pub const PAM_LINES: [(&str, &str); 3] = [
    ("auth", "auth       optional     pam_gnome_keyring.so"),
    ("session", "session    optional     pam_gnome_keyring.so auto_start"),
    ("password", "password   optional     pam_gnome_keyring.so"),
];
pub const SECRETS: &str = "org.freedesktop.secrets";

// -- KeePassXC's settings (an ini, written as Python's configparser does) ----------------------
#[derive(Default, Debug, PartialEq)]
pub struct Ini(pub Vec<(String, Vec<(String, String)>)>);

impl Ini {
    pub fn parse(text: &str) -> Ini {
        let mut ini = Ini::default();
        let mut cur: Option<usize> = None;
        for line in text.lines() {
            let s = line.trim();
            if s.is_empty() || s.starts_with('#') || s.starts_with(';') {
                continue;
            }
            if s.starts_with('[') && s.ends_with(']') {
                let name = &s[1..s.len() - 1];
                cur = Some(ini.0.iter().position(|(n, _)| n == name).unwrap_or_else(|| {
                    ini.0.push((name.to_string(), Vec::new()));
                    ini.0.len() - 1
                }));
                continue;
            }
            if let (Some((k, v)), Some(c)) = (s.split_once(['=', ':']), cur) {
                let (k, v) = (k.trim().to_string(), v.trim().to_string());
                let sec = &mut ini.0[c].1;
                match sec.iter_mut().find(|(x, _)| *x == k) {
                    Some(slot) => slot.1 = v,
                    None => sec.push((k, v)),
                }
            }
        }
        ini
    }

    pub fn read(path: &Path) -> Ini {
        Ini::parse(&std::fs::read_to_string(path).unwrap_or_default())
    }

    pub fn get(&self, section: &str, key: &str) -> Option<&str> {
        self.0.iter().find(|(n, _)| n == section)?.1.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_str())
    }

    pub fn set(&mut self, section: &str, key: &str, value: &str) {
        if !self.0.iter().any(|(n, _)| n == section) {
            self.0.push((section.to_string(), Vec::new()));
        }
        let sec = &mut self.0.iter_mut().find(|(n, _)| n == section).unwrap().1;
        match sec.iter_mut().find(|(k, _)| k == key) {
            Some(slot) => slot.1 = value.to_string(),
            None => sec.push((key.to_string(), value.to_string())),
        }
    }

    /// configparser's write(space_around_delimiters=False).
    pub fn text(&self) -> String {
        self.0.iter().map(|(n, kv)| format!("[{n}]\n{}\n", kv.iter().map(|(k, v)| format!("{k}={v}\n")).collect::<String>())).collect()
    }
}

/// KeePassXC was the Secret Service with a database in use (Sonata's
/// keyring before): its secrets live there until moved.
pub fn keepassxc_in_use(path: &Path) -> bool {
    let ini = Ini::read(path);
    let on = ini.get("FdoSecrets", "Enabled").unwrap_or("").eq_ignore_ascii_case("true");
    let dbs = ini.get("General", "LastOpenedDatabases").filter(|s| !s.is_empty()).or_else(|| ini.get("General", "LastActiveDatabase")).unwrap_or("");
    on && !dbs.trim().is_empty()
}

fn config_path() -> PathBuf {
    crate::util::expand(CONFIG)
}

fn has(tool: &str) -> bool {
    sonata_backend::run::which(tool).is_some()
}

/// "gnome" or "keepassxc" (your choice, else what is already in use).
pub fn backend_with(has: &dyn Fn(&str) -> bool, ini: &Path) -> String {
    let chosen = crate::util::load("keyring", json!({"backend": ""}))["backend"].as_str().unwrap_or("").to_string();
    if chosen == "gnome" || chosen == "keepassxc" {
        return chosen;
    }
    if has("keepassxc") && keepassxc_in_use(ini) {
        return "keepassxc".into();
    }
    if has("gnome-keyring-daemon") || !has("keepassxc") { "gnome".into() } else { "keepassxc".into() }
}

pub fn backend() -> String {
    backend_with(&has, &config_path())
}

pub fn set_backend(name: &str) {
    let _ = sonata_config::update("keyring", &json!({"backend": name}));
}

/// [FdoSecrets] Enabled=on unless the user chose otherwise (force: set it
/// anyway). true: written.
pub fn enable_secret_service(path: &Path, on: bool, force: bool) -> bool {
    let mut ini = Ini::read(path);
    if ini.get("FdoSecrets", "Enabled").is_some() && !force {
        return false; // your choice stands
    }
    ini.set("FdoSecrets", "Enabled", if on { "true" } else { "false" });
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    std::fs::write(path, ini.text()).is_ok()
}

fn running() -> bool {
    sonata_backend::run::ok(&["pgrep", "-x", "keepassxc"], 5)
}

/// At login: KeePassXC with its Secret Service, minimized -- only while
/// it is the keyring (GNOME's is unlocked by the login itself).
pub fn start() -> bool {
    if !has("keepassxc") || backend() != "keepassxc" {
        return false;
    }
    if !running() {
        // (a running KeePassXC writes its settings back on quit)
        enable_secret_service(&config_path(), true, false);
        sonata_backend::run::spawn(&["keepassxc", "--minimized"]);
    }
    true
}

fn quit_keepassxc() {
    let _ = sonata_backend::run::run(&["pkill", "-x", "keepassxc"], 5);
    let end = std::time::Instant::now() + std::time::Duration::from_secs(8);
    while running() && std::time::Instant::now() < end {
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
}

fn start_keepassxc() {
    sonata_backend::run::spawn(&["keepassxc", "--minimized"]);
}

// -- the login screen (PAM) ----------------------------------------------------------------------
fn kind_of(line: &str) -> &str {
    line.split_whitespace().next().unwrap_or("")
}

/// greetd's PAM file with pam_gnome_keyring after each kind's last line
/// (unchanged when already there).
pub fn pam_text(text: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(String::from).collect();
    for (kind, line) in PAM_LINES {
        if lines.iter().any(|l| kind_of(l) == kind && l.contains("pam_gnome_keyring")) {
            continue;
        }
        let last = lines.iter().rposition(|l| kind_of(l) == kind).map(|i| i as isize).unwrap_or(lines.len() as isize - 1);
        lines.insert((last + 1) as usize, line.to_string());
    }
    lines.join("\n") + "\n"
}

/// pam_text undone: the exact lines Sonata adds removed (other lines, the
/// user's own pam_gnome_keyring ones included, stay).
pub fn pam_text_without(text: &str) -> String {
    text.split_inclusive('\n').filter(|l| !PAM_LINES.iter().any(|(_, ours)| l.trim_end_matches('\n') == *ours)).collect()
}

fn write_root_file(path: &Path, text: &str) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    sonata_config::atomic_write(path, text.as_bytes(), true)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o644))
}

fn bak(path: &Path) -> PathBuf {
    PathBuf::from(format!("{}.sonata-bak", path.display()))
}

/// As root (greeter-setup.sh revert): take Sonata's lines out again --
/// only when Sonata added them (its .sonata-bak copy exists).
pub fn unwrite_pam(path: &Path) -> i32 {
    if !bak(path).exists() {
        return 0;
    }
    let Ok(text) = std::fs::read_to_string(path) else { return 1 };
    let new = pam_text_without(&text);
    if new != text && write_root_file(path, &new).is_err() {
        return 1;
    }
    let _ = std::fs::remove_file(bak(path));
    0
}

pub fn pam_ready(path: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else { return false };
    let text = if text.ends_with('\n') { text } else { text + "\n" };
    pam_text(&text) == text
}

/// As root (`pkexec sonata2 keyring pam`): add the lines, keeping a copy.
pub fn write_pam(path: &Path) -> i32 {
    let Ok(text) = std::fs::read_to_string(path) else { return 1 };
    let new = pam_text(&text);
    if new == text {
        return 0;
    }
    if !bak(path).exists() && std::fs::copy(path, bak(path)).is_err() {
        return 1;
    }
    if write_root_file(path, &new).is_err() { 1 } else { 0 }
}

// -- moving the secrets (the Secret Service over D-Bus, a "plain" session) ----------------------
/// One saved secret: (label, attributes, value, content type).
pub type Secret = (String, HashMap<String, String>, Vec<u8>, String);

const SVC_PATH: &str = "/org/freedesktop/secrets";
const SVC: &str = "org.freedesktop.Secret.Service";

fn bus() -> Result<gio::DBusConnection, String> {
    gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).map_err(|e| e.to_string())
}

fn call(bus: &gio::DBusConnection, path: &str, iface: &str, method: &str, args: Option<&glib::Variant>) -> Result<glib::Variant, String> {
    bus.call_sync(Some(SECRETS), path, iface, method, args, None, gio::DBusCallFlags::NONE, 120_000, gio::Cancellable::NONE).map_err(|e| e.to_string())
}

fn prop(bus: &gio::DBusConnection, path: &str, iface: &str, name: &str) -> Result<glib::Variant, String> {
    let v = call(bus, path, "org.freedesktop.DBus.Properties", "Get", Some(&(iface, name).to_variant()))?;
    v.child_value(0).as_variant().ok_or_else(|| "no value".into())
}

/// Wait for a prompt (KeePassXC asks for its database password): true when done, not dismissed.
fn prompt(bus: &gio::DBusConnection, path: &str) -> bool {
    if path == "/" {
        return true;
    }
    let ctx = glib::MainContext::new();
    ctx.with_thread_default(|| {
        let done: std::rc::Rc<std::cell::Cell<Option<bool>>> = Default::default();
        let d = done.clone();
        #[allow(deprecated)]
        let sub = bus.signal_subscribe(Some(SECRETS), Some("org.freedesktop.Secret.Prompt"), Some("Completed"), Some(path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, params| {
            d.set(Some(!params.child_value(0).get::<bool>().unwrap_or(true)));
        });
        let _ = call(bus, path, "org.freedesktop.Secret.Prompt", "Prompt", Some(&("",).to_variant()));
        let end = std::time::Instant::now() + std::time::Duration::from_secs(300);
        while done.get().is_none() && std::time::Instant::now() < end {
            ctx.iteration(true);
        }
        #[allow(deprecated)]
        bus.signal_unsubscribe(sub);
        done.get().unwrap_or(false)
    })
    .unwrap_or(false)
}

fn open_session(bus: &gio::DBusConnection) -> Result<String, String> {
    let r = call(bus, SVC_PATH, SVC, "OpenSession", Some(&("plain", glib::Variant::from_variant(&"".to_variant())).to_variant()))?;
    r.child_value(1).str().map(String::from).ok_or_else(|| "no session".into())
}

fn paths(v: &glib::Variant) -> Vec<String> {
    v.iter().filter_map(|p| p.str().map(String::from)).collect()
}

fn unlock(bus: &gio::DBusConnection, path: &str) -> Result<(), String> {
    let objs = glib::Variant::array_from_iter_with_type(glib::VariantTy::OBJECT_PATH, [glib::variant::ObjectPath::try_from(path.to_string()).map_err(|e| e.to_string())?.to_variant()]);
    let r = call(bus, SVC_PATH, SVC, "Unlock", Some(&glib::Variant::tuple_from_iter([objs])))?;
    let p = r.child_value(1).str().unwrap_or("/").to_string();
    if prompt(bus, &p) { Ok(()) } else { Err("the keyring stayed locked".into()) }
}

/// Every item of the Secret Service now running (KeePassXC asks for its
/// database password to unlock).
pub fn read_secrets() -> Result<Vec<Secret>, String> {
    let bus = bus()?;
    let session = open_session(&bus)?;
    let mut out = Vec::new();
    for col in paths(&prop(&bus, SVC_PATH, SVC, "Collections")?) {
        if prop(&bus, &col, "org.freedesktop.Secret.Collection", "Locked").ok().and_then(|v| v.get::<bool>()).unwrap_or(false) {
            unlock(&bus, &col)?;
        }
        for item in paths(&prop(&bus, &col, "org.freedesktop.Secret.Collection", "Items")?) {
            let iface = "org.freedesktop.Secret.Item";
            let Ok(secret) = call(&bus, &item, iface, "GetSecret", Some(&(glib::variant::ObjectPath::try_from(session.clone()).map_err(|e| e.to_string())?,).to_variant())) else { continue };
            let s = secret.child_value(0);
            let value: Vec<u8> = s.child_value(2).get().unwrap_or_default();
            let ctype = s.child_value(3).str().filter(|c| !c.is_empty()).unwrap_or("text/plain").to_string();
            let label = prop(&bus, &item, iface, "Label").ok().and_then(|v| v.str().map(String::from)).unwrap_or_default();
            let attrs: HashMap<String, String> = prop(&bus, &item, iface, "Attributes").ok().and_then(|v| v.get()).unwrap_or_default();
            out.push((label, attrs, value, ctype));
        }
    }
    let _ = call(&bus, &session, "org.freedesktop.Secret.Session", "Close", None);
    Ok(out)
}

/// Into GNOME's login keyring (the default collection). Returns how many.
pub fn write_secrets(items: &[Secret]) -> Result<usize, String> {
    let bus = bus()?;
    let session = glib::variant::ObjectPath::try_from(open_session(&bus)?).map_err(|e| e.to_string())?;
    let mut col = call(&bus, SVC_PATH, SVC, "ReadAlias", Some(&("default",).to_variant()))?.child_value(0).str().unwrap_or("/").to_string();
    if col == "/" {
        col = "/org/freedesktop/secrets/collection/login".into();
        let p = glib::variant::ObjectPath::try_from(col.clone()).map_err(|e| e.to_string())?;
        call(&bus, SVC_PATH, SVC, "SetAlias", Some(&("default", p).to_variant()))?;
    }
    if prop(&bus, &col, "org.freedesktop.Secret.Collection", "Locked").ok().and_then(|v| v.get::<bool>()).unwrap_or(false) {
        unlock(&bus, &col)?;
    }
    for (label, attrs, data, ctype) in items {
        let props: HashMap<String, glib::Variant> = [("org.freedesktop.Secret.Item.Label".to_string(), label.to_variant()), ("org.freedesktop.Secret.Item.Attributes".to_string(), attrs.to_variant())].into_iter().collect();
        let secret = (session.clone(), Vec::<u8>::new(), data.clone(), ctype.clone()).to_variant();
        let r = call(&bus, &col, "org.freedesktop.Secret.Collection", "CreateItem", Some(&glib::Variant::tuple_from_iter([props.to_variant(), secret, true.to_variant()])))?;
        prompt(&bus, r.child_value(1).str().unwrap_or("/"));
    }
    Ok(items.len())
}

fn has_owner(bus: &gio::DBusConnection) -> bool {
    bus.call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", Some(&(SECRETS,).to_variant()), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE)
        .ok()
        .and_then(|v| v.child_value(0).get::<bool>())
        .unwrap_or(false)
}

fn bus_owner_ready(timeout: f64) -> bool {
    let Ok(bus) = bus() else { return false };
    let end = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
    while std::time::Instant::now() < end {
        if has_owner(&bus) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
    false
}

/// GNOME's keyring as the Secret Service, its login keyring unlocked (or
/// made) with the login password -- what the login screen does from now
/// on. Returns the environment it gives the session (SSH_AUTH_SOCK=...),
/// None when it didn't start.
pub fn start_gnome_keyring(password: &str) -> Option<Vec<(String, String)>> {
    use std::io::Write;
    let exe = sonata_backend::run::which("gnome-keyring-daemon")?;
    let mut child = std::process::Command::new(exe)
        .args(["--replace", "--unlock", "--daemonize", "--components=secrets,pkcs11"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;
    if let Some(mut i) = child.stdin.take() {
        let _ = i.write_all(password.as_bytes());
    }
    let out = child.wait_with_output().ok()?;
    let env = String::from_utf8_lossy(&out.stdout).lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.to_string(), v.to_string())).collect();
    (out.status.success() && bus_owner_ready(8.0)).then_some(env)
}

/// Undo start_gnome_keyring: stop the gnome-keyring-daemon that owns the
/// Secret Service name, so KeePassXC can take it back (it gives up when
/// the name is taken). Another owner is left alone.
pub fn stop_gnome_keyring() {
    let Ok(bus) = bus() else { return };
    let pid = bus
        .call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", Some(&(SECRETS,).to_variant()), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE)
        .ok()
        .and_then(|v| v.child_value(0).get::<u32>());
    let Some(pid) = pid else { return };
    if !std::fs::read_to_string(format!("/proc/{pid}/comm")).is_ok_and(|c| c.starts_with("gnome-keyring")) {
        return;
    }
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
    let end = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < end && has_owner(&bus) {
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
}

/// What switch_to_gnome does to the system (tests pass fakes).
pub struct Ops {
    pub auth: Box<dyn Fn(&str) -> bool>,
    pub pam_ready: Box<dyn Fn() -> bool>,
    pub write_pam: Box<dyn Fn() -> bool>,
    pub read: Box<dyn Fn() -> Result<Vec<Secret>, String>>,
    pub quit_kp: Box<dyn Fn()>,
    pub kp_service: Box<dyn Fn(bool)>,
    pub start_gnome: Box<dyn Fn(&str) -> bool>,
    pub stop_gnome: Box<dyn Fn()>,
    pub write: Box<dyn Fn(&[Secret]) -> Result<usize, String>>,
    pub start_kp: Box<dyn Fn()>,
    pub set_backend: Box<dyn Fn(&str)>,
}

/// The new keyring's environment (SSH_AUTH_SOCK=...): Settings hands it on.
pub static NEW_ENV: std::sync::Mutex<Vec<(String, String)>> = std::sync::Mutex::new(Vec::new());

impl Default for Ops {
    fn default() -> Self {
        Ops {
            auth: Box::new(|pw| sonata_shell::pam::authenticate(&glib::user_name().to_string_lossy(), pw)),
            pam_ready: Box::new(|| pam_ready(Path::new(PAM_FILE))),
            write_pam: Box::new(pkexec_pam),
            read: Box::new(read_secrets),
            quit_kp: Box::new(quit_keepassxc),
            kp_service: Box::new(|on| {
                enable_secret_service(&config_path(), on, true);
            }),
            start_gnome: Box::new(|pw| match start_gnome_keyring(pw) {
                Some(env) => {
                    *NEW_ENV.lock().unwrap_or_else(|e| e.into_inner()) = env;
                    true
                }
                None => false,
            }),
            stop_gnome: Box::new(stop_gnome_keyring),
            write: Box::new(write_secrets),
            start_kp: Box::new(start_keepassxc),
            set_backend: Box::new(set_backend),
        }
    }
}

/// Why the move stopped.
#[derive(Debug, PartialEq)]
pub enum SwitchError {
    /// That's not your login password.
    Permission(String),
    Failed(String),
}

/// KeePassXC -> GNOME's keyring, once. The login password must be right
/// (it becomes the keyring's). Nothing is changed until the secrets are
/// read; if a later step fails, KeePassXC is put back. Returns the number
/// of secrets moved. `step(text)` says what it is doing.
pub fn switch_to_gnome(password: &str, step: &dyn Fn(&str), o: &Ops) -> Result<usize, SwitchError> {
    step(&tr!("Checking your password…"));
    if !(o.auth)(password) {
        return Err(SwitchError::Permission("That's not your login password.".into()));
    }
    if !(o.pam_ready)() {
        step(&tr!("Letting the login screen unlock the keyring…"));
        if !(o.write_pam)() {
            return Err(SwitchError::Failed("The login screen's settings couldn't be changed.".into()));
        }
    }
    step(&tr!("Reading the saved passwords from KeePassXC…"));
    let items = (o.read)().map_err(SwitchError::Failed)?;
    step(&tr!("Moving them to the login keyring…"));
    (o.quit_kp)();
    (o.kp_service)(false);
    let moved = if (o.start_gnome)(password) { (o.write)(&items) } else { Err("GNOME's keyring didn't start.".into()) };
    match moved {
        Ok(n) => {
            (o.set_backend)("gnome");
            Ok(n)
        }
        Err(e) => {
            (o.stop_gnome)(); // free the Secret Service name first, or KeePassXC can't own it
            (o.kp_service)(true); // back as it was
            (o.start_kp)();
            Err(SwitchError::Failed(e))
        }
    }
}

/// `pkexec <this program> keyring pam`.
fn pkexec_pam() -> bool {
    let Ok(me) = std::env::current_exe() else { return false };
    let me = me.display().to_string();
    sonata_backend::run::ok(&["pkexec", &me, "keyring", "pam"], 120)
}

/// `sonata2 keyring backend|pam|unpam`.
pub fn main(args: &[String]) -> i32 {
    let root = unsafe { libc::geteuid() } == 0;
    let file = |i: usize| args.get(i).map(PathBuf::from).unwrap_or_else(|| PathBuf::from(PAM_FILE));
    match args.first().map(String::as_str) {
        Some("backend") => {
            println!("{}", backend());
            0
        }
        Some("pam") if !root => {
            println!("sonata2 keyring pam: run it as root (pkexec)");
            1
        }
        Some("pam") => write_pam(&file(1)),
        Some("unpam") if !root => {
            println!("sonata2 keyring unpam: run it as root");
            1
        }
        Some("unpam") => unwrite_pam(&file(1)),
        _ => {
            println!("usage: sonata2 keyring backend|pam|unpam");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    const GREETD: &str = "#%PAM-1.0\n\nauth       required     pam_securetty.so\nauth       requisite    pam_nologin.so\nauth       include      system-local-login\naccount    include      system-local-login\nsession    include      system-local-login\npassword   include      system-local-login\n";

    #[test]
    fn lines_after_each_kind() {
        let new = pam_text(GREETD);
        let lines: Vec<&str> = new.lines().collect();
        let at = |l: &str| lines.iter().position(|x| *x == l).unwrap();
        assert_eq!(lines[at("auth       include      system-local-login") + 1], "auth       optional     pam_gnome_keyring.so");
        assert_eq!(lines[at("session    include      system-local-login") + 1], "session    optional     pam_gnome_keyring.so auto_start");
        assert!(lines.contains(&"password   optional     pam_gnome_keyring.so"));
        assert_eq!(pam_text(&new), new); // once only
        assert_eq!(pam_text_without(&new), GREETD);
    }

    #[test]
    fn write_keeps_a_copy() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("greetd");
        std::fs::write(&p, GREETD).unwrap();
        assert!(!pam_ready(&p));
        assert_eq!(write_pam(&p), 0);
        assert!(pam_ready(&p));
        assert_eq!(std::fs::read_to_string(bak(&p)).unwrap(), GREETD);
        assert_eq!(unwrite_pam(&p), 0);
        assert_eq!(std::fs::read_to_string(&p).unwrap(), GREETD);
        assert!(!bak(&p).exists());
    }

    #[test]
    fn backends() {
        let _l = crate::util::test_lock();
        let d = tempfile::tempdir().unwrap();
        let ini = d.path().join("keepassxc.ini");
        let both = |n: &str| n == "keepassxc" || n == "gnome-keyring-daemon";
        set_backend("");
        // a new install uses the login keyring
        assert_eq!(backend_with(&both, &ini), "gnome");
        // KeePassXC in use stays until moved
        std::fs::write(&ini, "[General]\nLastOpenedDatabases=/home/v/p.kdbx\n\n[FdoSecrets]\nEnabled=true\n").unwrap();
        assert_eq!(backend_with(&both, &ini), "keepassxc");
        set_backend("gnome");
        assert_eq!(backend_with(&both, &ini), "gnome");
        set_backend("");
    }

    #[test]
    fn secret_service_off_forced() {
        let d = tempfile::tempdir().unwrap();
        let ini = d.path().join("keepassxc.ini");
        std::fs::write(&ini, "[General]\nMinimizeOnStartup=true\n\n[FdoSecrets]\nEnabled=true\n").unwrap();
        assert!(!enable_secret_service(&ini, false, false)); // your choice stands
        assert!(enable_secret_service(&ini, false, true));
        assert_eq!(std::fs::read_to_string(&ini).unwrap(), "[General]\nMinimizeOnStartup=true\n\n[FdoSecrets]\nEnabled=false\n\n");
    }

    fn ops(calls: &Rc<RefCell<Vec<String>>>, auth_ok: bool, write_ok: bool) -> Ops {
        let rec = |name: &'static str| {
            let c = calls.clone();
            move || c.borrow_mut().push(name.to_string())
        };
        let (a, b, c, d, e, f, g, h, i, j, k) = (rec("auth"), rec("pam_ready"), rec("write_pam"), rec("read"), rec("quit_kp"), calls.clone(), rec("start_gnome"), rec("stop_gnome"), rec("write"), rec("start_kp"), calls.clone());
        Ops {
            auth: Box::new(move |_| {
                if auth_ok {
                    a();
                }
                auth_ok
            }),
            pam_ready: Box::new(move || {
                b();
                false
            }),
            write_pam: Box::new(move || {
                c();
                true
            }),
            read: Box::new(move || {
                d();
                Ok(vec![("Chrome Safe Storage".into(), [("application".to_string(), "chrome".to_string())].into_iter().collect(), b"k".to_vec(), "text/plain".into())])
            }),
            quit_kp: Box::new(e),
            kp_service: Box::new(move |on| f.borrow_mut().push(format!("kp_service {on}"))),
            start_gnome: Box::new(move |_| {
                g();
                true
            }),
            stop_gnome: Box::new(h),
            write: Box::new(move |items| {
                i();
                if write_ok { Ok(items.len()) } else { Err("no".into()) }
            }),
            start_kp: Box::new(j),
            set_backend: Box::new(move |n| k.borrow_mut().push(format!("set_backend {n}"))),
        }
    }

    #[test]
    fn order_and_result() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        assert_eq!(switch_to_gnome("pw", &|_| {}, &ops(&calls, true, true)), Ok(1));
        assert_eq!(*calls.borrow(), ["auth", "pam_ready", "write_pam", "read", "quit_kp", "kp_service false", "start_gnome", "write", "set_backend gnome"]);
    }

    #[test]
    fn wrong_password_changes_nothing() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        assert!(matches!(switch_to_gnome("bad", &|_| {}, &ops(&calls, false, true)), Err(SwitchError::Permission(_))));
        assert!(calls.borrow().is_empty());
    }

    #[test]
    fn failure_puts_keepassxc_back() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        assert!(switch_to_gnome("pw", &|_| {}, &ops(&calls, true, false)).is_err());
        let c = calls.borrow();
        assert!(c.contains(&"kp_service true".to_string()) && c.contains(&"start_kp".to_string()) && c.contains(&"stop_gnome".to_string()));
        assert!(!c.iter().any(|x| x.starts_with("set_backend")));
    }
}
