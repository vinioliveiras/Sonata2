//! Linux system state for the shell (sonata2/backend/system.py): Wi-Fi,
//! network services, printers, Bluetooth, Airplane Mode, sound, brightness,
//! battery, power actions, About, displays, power profiles, keyboards, date
//! & time, sharing, default apps. These are *Linux* settings: Sonata reads
//! and writes the system services and stores nothing itself.
//!
//! Everything here may block (subprocesses, D-Bus): call it off the main
//! loop. Not ported yet: BlueZ over D-Bus (bluez.py; the bluetoothctl
//! fallback is here), watching NetworkManager and the sound server.

use crate::run::{ok, read, run, spawn, which};
use crate::wfconfig;
use gio::prelude::*;
use regex::Regex;
use sonata_i18n::{n_, tr};
use std::sync::{Mutex, OnceLock};

pub const POWER_SUPPLY: &str = "/sys/class/power_supply";
const JUNK_DMI: [&str; 6] = ["to be filled by o.e.m.", "default string", "system product name", "not applicable", "", "o.e.m."];

fn re(p: &'static str) -> Regex {
    Regex::new(p).expect("a fixed pattern")
}

macro_rules! regex {
    ($p:literal) => {{
        static R: OnceLock<Regex> = OnceLock::new();
        R.get_or_init(|| re($p))
    }};
}

/// Split nmcli -t output on ':' honouring its '\:' escapes.
pub fn split_nmcli(line: &str) -> Vec<String> {
    let (mut parts, mut cur, mut esc) = (Vec::new(), String::new(), false);
    for ch in line.chars() {
        if esc {
            cur.push(ch);
            esc = false;
        } else if ch == '\\' {
            esc = true;
        } else if ch == ':' {
            parts.push(std::mem::take(&mut cur));
        } else {
            cur.push(ch);
        }
    }
    parts.push(cur);
    parts
}

fn int(s: &str) -> i64 {
    s.trim().parse().unwrap_or(0)
}

// -- Wi-Fi (NetworkManager) --------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct WifiNetwork {
    pub ssid: String,
    pub signal: i64,
    pub secure: bool,
    pub connected: bool,
}

pub fn wifi_available() -> bool {
    let (rc, out) = run(&["nmcli", "-t", "-f", "TYPE", "device"], 10);
    rc == 0 && out.split_whitespace().any(|t| t == "wifi")
}

pub fn wifi_enabled() -> bool {
    let (rc, out) = run(&["nmcli", "radio", "wifi"], 10);
    rc == 0 && out.trim().lines().last() == Some("enabled")
}

pub fn set_wifi_enabled(on: bool) -> bool {
    ok(&["nmcli", "radio", "wifi", if on { "on" } else { "off" }], 10)
}

// `nmcli device wifi [list]` alone asks NetworkManager to rescan when its
// list is older than 30 s: the current network only needs the list NM has.
fn wifi_active() -> (String, i64) {
    let (rc, out) = run(&["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "--rescan", "no"], 10);
    if rc == 0 {
        for line in out.lines() {
            let p = split_nmcli(line);
            if p.len() >= 3 && p[0] == "yes" {
                return (p[1].clone(), int(&p[2]));
            }
        }
    }
    (String::new(), 0)
}

pub fn wired(out: &str) -> bool {
    out.lines().any(|l| l.starts_with("ethernet:connected"))
}

const DEVICES: [&str; 5] = ["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"];
/// connected, its strength not in NetworkManager's list yet
pub const UNKNOWN_SIGNAL: i64 = 70;

/// The Wi-Fi connection a device is on ("" for none).
pub fn joined(out: &str) -> String {
    for l in out.lines() {
        let p = split_nmcli(l);
        if p.len() >= 3 && p[0] == "wifi" && p[1] == "connected" {
            return p[2].clone();
        }
    }
    String::new()
}

/// (ssid, signal): the scan list can lack the network in use (after a
/// boot, before a scan); the device's own state counts too.
fn wifi_now(devices_out: &str) -> (String, i64) {
    let (ssid, sig) = wifi_active();
    if !ssid.is_empty() {
        return (ssid, sig);
    }
    let ssid = joined(devices_out);
    let sig = if ssid.is_empty() { 0 } else { UNKNOWN_SIGNAL };
    (ssid, sig)
}

/// (ssid, signal 0-100, wired connected).
pub fn wifi_current() -> (String, i64, bool) {
    let (rc, out) = run(&DEVICES, 10);
    let out = if rc == 0 { out } else { String::new() };
    let (ssid, sig) = wifi_now(&out);
    (ssid, sig, wired(&out))
}

/// (Wi-Fi hardware, radio on, (ssid, signal, wired)) for the menu bar's poll.
pub fn wifi_status() -> (bool, bool, (String, i64, bool)) {
    let (rc, out) = run(&DEVICES, 10);
    let types: Vec<&str> = if rc == 0 { out.lines().map(|l| l.split(':').next().unwrap_or("")).collect() } else { Vec::new() };
    let avail = types.contains(&"wifi");
    let is_wired = rc == 0 && wired(&out);
    let on = avail && wifi_enabled();
    let (ssid, sig) = if on { wifi_now(if rc == 0 { &out } else { "" }) } else { (String::new(), 0) };
    (avail, on, (ssid, sig, is_wired))
}

/// The networks around, one per name (the strongest), joined one first.
pub fn parse_wifi_list(out: &str) -> Vec<WifiNetwork> {
    let mut best: Vec<WifiNetwork> = Vec::new();
    for line in out.lines() {
        let p = split_nmcli(line);
        if p.len() < 4 || p[1].is_empty() {
            continue;
        }
        let (inuse, ssid, sig, sec) = (p[0] == "*", p[1].clone(), int(&p[2]), p[3].clone());
        match best.iter_mut().find(|n| n.ssid == ssid) {
            Some(cur) => {
                if sig > cur.signal || inuse {
                    cur.signal = sig.max(cur.signal);
                    cur.secure = sec != "" && sec != "--";
                    cur.connected = inuse || cur.connected;
                }
            }
            None => best.push(WifiNetwork { ssid, signal: sig, secure: !sec.is_empty() && sec != "--", connected: inuse }),
        }
    }
    best.sort_by(|a, b| (!a.connected, -a.signal, a.ssid.to_lowercase()).cmp(&(!b.connected, -b.signal, b.ssid.to_lowercase())));
    best
}

pub fn wifi_scan(rescan: bool) -> Vec<WifiNetwork> {
    if rescan {
        run(&["nmcli", "device", "wifi", "rescan"], 15);
    }
    let (rc, out) = run(&["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"], 20);
    if rc == 0 { parse_wifi_list(&out) } else { Vec::new() }
}

const NM: (&str, &str, &str) = ("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager");

fn system_bus() -> Option<gio::DBusConnection> {
    gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE).ok()
}

/// `name` is on the system bus now (NetworkManager, UPower...).
pub fn system_service(name: &str) -> bool {
    let Some(bus) = system_bus() else { return false };
    bus.call_sync(
        Some("org.freedesktop.DBus"),
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        Some(&(name,).to_variant()),
        Some(glib::VariantTy::new("(b)").expect("a fixed type")),
        gio::DBusCallFlags::NONE,
        1000,
        gio::Cancellable::NONE,
    )
    .ok()
    .and_then(|v| v.get::<(bool,)>())
    .is_some_and(|v| v.0)
}

/// Join a network. With a password it goes to NetworkManager over D-Bus
/// (never on a command line, where other users could read it in `ps`).
pub fn wifi_connect(ssid: &str, password: &str) -> (bool, String) {
    if password.is_empty() {
        let (rc, out) = run(&["nmcli", "device", "wifi", "connect", ssid], 45);
        return (rc == 0, out.trim().to_string());
    }
    match wifi_connect_dbus(ssid, password) {
        Ok(r) => r,
        Err(e) => (false, e.message().replace(password, "•••")),
    }
}

fn prop(bus: &gio::DBusConnection, path: &str, iface: &str, name: &str) -> Result<glib::Variant, glib::Error> {
    let v = bus.call_sync(Some(NM.0), path, "org.freedesktop.DBus.Properties", "Get", Some(&(iface, name).to_variant()), Some(glib::VariantTy::new("(v)").expect("a fixed type")), gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE)?;
    Ok(v.child_value(0).as_variant().unwrap_or(v))
}

fn wifi_connect_dbus(ssid: &str, password: &str) -> Result<(bool, String), glib::Error> {
    use std::collections::HashMap;
    let bus = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE)?;
    let devices = bus.call_sync(Some(NM.0), NM.1, NM.2, "GetDevices", None, Some(glib::VariantTy::new("(ao)").expect("a fixed type")), gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE)?;
    let paths: Vec<glib::variant::ObjectPath> = devices.child_value(0).get().unwrap_or_default();
    let mut wifi = None;
    for p in paths {
        if prop(&bus, p.as_str(), "org.freedesktop.NetworkManager.Device", "DeviceType")?.get::<u32>() == Some(2) {
            wifi = Some(p);
            break;
        }
    }
    let Some(wifi) = wifi else { return Ok((false, tr!("No Wi-Fi device"))) };
    let mut conn: HashMap<String, HashMap<String, glib::Variant>> = HashMap::new();
    conn.insert("connection".into(), HashMap::from([("id".to_string(), ssid.to_variant()), ("type".to_string(), "802-11-wireless".to_variant())]));
    conn.insert("802-11-wireless".into(), HashMap::from([("ssid".to_string(), ssid.as_bytes().to_variant())]));
    conn.insert("802-11-wireless-security".into(), HashMap::from([("key-mgmt".to_string(), "wpa-psk".to_variant()), ("psk".to_string(), password.to_variant())]));
    let root = glib::variant::ObjectPath::try_from("/".to_string()).expect("a valid path");
    let args = glib::Variant::tuple_from_iter([conn.to_variant(), wifi.to_variant(), root.to_variant()]);
    let res = bus.call_sync(Some(NM.0), NM.1, NM.2, "AddAndActivateConnection", Some(&args), Some(glib::VariantTy::new("(oo)").expect("a fixed type")), gio::DBusCallFlags::NONE, 45000, gio::Cancellable::NONE)?;
    let saved: glib::variant::ObjectPath = res.child_value(0).get().expect("(oo)");
    let active: glib::variant::ObjectPath = res.child_value(1).get().expect("(oo)");
    // like nmcli: wait until it's up (or failed: a wrong password)
    let mut state = 0u32;
    for _ in 0..90 {
        state = prop(&bus, active.as_str(), "org.freedesktop.NetworkManager.Connection.Active", "State").ok().and_then(|v| v.get::<u32>()).unwrap_or(4);
        if state == 2 || state == 4 {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    if state == 2 {
        return Ok((true, String::new()));
    }
    // don't keep a profile with a wrong password
    let _ = bus.call_sync(Some(NM.0), saved.as_str(), "org.freedesktop.NetworkManager.Settings.Connection", "Delete", None, None, gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE);
    Ok((false, tr!("Couldn't join the network (wrong password?)")))
}

pub fn wifi_disconnect() -> bool {
    let (_rc, out) = run(&["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], 10);
    match out.lines().find(|l| l.ends_with(":wifi")).map(|l| l.split(':').next().unwrap_or("").to_string()) {
        Some(dev) if !dev.is_empty() => ok(&["nmcli", "device", "disconnect", &dev], 10),
        _ => false,
    }
}

// -- Network services (Ethernet, VPN, others) ---------------------------------------
#[derive(Clone, Debug, Default, PartialEq)]
pub struct NetService {
    pub name: String,
    pub uuid: String,
    pub kind: String,
    pub device: String,
    pub active: bool,
    pub address: String,
    pub gateway: String,
    pub dns: String,
}

pub fn net_kind(t: &str) -> String {
    match t {
        "802-3-ethernet" => "ethernet",
        "vpn" | "wireguard" => "vpn",
        "802-11-wireless" => "wifi",
        "bridge" => "bridge",
        "bond" => "bond",
        "gsm" => "mobile",
        "bluetooth" => "bluetooth",
        other => other,
    }
    .to_string()
}

/// Saved connections except Wi-Fi networks and loopback, active ones first.
pub fn net_services() -> Vec<NetService> {
    let (rc, out) = run(&["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,ACTIVE", "connection", "show"], 10);
    if rc != 0 {
        return Vec::new();
    }
    let mut services = Vec::new();
    for l in out.lines() {
        let p = split_nmcli(l);
        if p.len() < 5 || p[2] == "loopback" || p[2] == "802-11-wireless" {
            continue;
        }
        let mut s = NetService { name: p[0].clone(), uuid: p[1].clone(), kind: net_kind(&p[2]), device: p[3].clone(), active: p[4] == "yes", ..Default::default() };
        if s.active && !s.device.is_empty() {
            let (_rc, info) = run(&["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", &s.device], 10);
            for il in info.lines() {
                let (k, v) = il.split_once(':').unwrap_or((il, ""));
                if k.starts_with("IP4.ADDRESS") && s.address.is_empty() {
                    s.address = v.split('/').next().unwrap_or("").to_string();
                } else if k == "IP4.GATEWAY" {
                    s.gateway = v.to_string();
                } else if k.starts_with("IP4.DNS") && s.dns.is_empty() {
                    s.dns = v.to_string();
                }
            }
        }
        services.push(s);
    }
    services.sort_by(|a, b| (!a.active, a.kind != "ethernet", a.name.to_lowercase()).cmp(&(!b.active, b.kind != "ethernet", b.name.to_lowercase())));
    services
}

pub fn net_service_set(uuid: &str, up: bool) -> (bool, String) {
    let (rc, out) = run(&["nmcli", "connection", if up { "up" } else { "down" }, "uuid", uuid], 45);
    (rc == 0, out.trim().to_string())
}

/// OpenVPN (.ovpn) or WireGuard (.conf) file -> a NetworkManager VPN.
pub fn vpn_import(path: &str) -> (bool, String) {
    let kind = if path.ends_with(".conf") { "wireguard" } else { "openvpn" };
    let (rc, out) = run(&["nmcli", "connection", "import", "type", kind, "file", path], 20);
    (rc == 0, out.trim().to_string())
}

pub fn net_service_delete(uuid: &str) -> bool {
    ok(&["nmcli", "connection", "delete", "uuid", uuid], 10)
}

// -- Printers (CUPS) ----------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Printer {
    pub name: String,
    /// "Idle", "Printing", "Disabled" (English: translate when shown)
    pub state: String,
    pub default: bool,
}

pub fn parse_printers(default_out: &str, list_out: &str) -> Vec<Printer> {
    let default = default_out.split_once(':').map(|x| x.1.trim().to_string()).unwrap_or_default();
    let mut out = Vec::new();
    for l in list_out.lines() {
        if let Some(c) = regex!(r"printer (\S+) (?:is )?(\w+)").captures(l) {
            let st = match &c[2] {
                "idle" => n_!("Idle").to_string(),
                "now" => n_!("Printing").to_string(),
                "disabled" => n_!("Disabled").to_string(),
                other => {
                    let mut t = other.to_string();
                    if let Some(f) = t.get_mut(..1) {
                        f.make_ascii_uppercase();
                    }
                    t
                }
            };
            out.push(Printer { name: c[1].to_string(), state: st, default: c[1] == *default });
        }
    }
    out
}

/// None when CUPS isn't installed.
pub fn printers() -> Option<Vec<Printer>> {
    which("lpstat")?;
    let (_rc, d) = run(&["lpstat", "-d"], 10);
    let (_rc, out) = run(&["lpstat", "-p"], 10);
    Some(parse_printers(&d, &out))
}

/// CUPS' scheduler is up (installed isn't enough).
pub fn cups_running() -> bool {
    which("lpstat").is_some() && run(&["lpstat", "-r"], 10).1.contains("is running")
}

pub const CUPS_ON: &str = "sudo systemctl enable --now cups.socket cups.service";

pub fn set_default_printer(name: &str) -> bool {
    ok(&["lpoptions", "-d", name], 10)
}

/// The distro's printer tool, else the CUPS web page.
pub fn add_printer() {
    for tool in ["system-config-printer", "kde-add-printer"] {
        if which(tool).is_some() {
            spawn(&[tool]);
            return;
        }
    }
    let _ = gio::AppInfo::launch_default_for_uri("http://localhost:631/admin", gio::AppLaunchContext::NONE);
}

// -- Software Update -----------------------------------------------------------------
pub const TERMINALS: [(&str, &[&str]); 10] = [
    ("kgx", &["--"]),
    ("gnome-terminal", &["--"]),
    ("konsole", &["-e"]),
    ("kitty", &[]),
    ("alacritty", &["-e"]),
    ("foot", &[]),
    ("ghostty", &["-e"]),
    ("wezterm", &["start", "--"]),
    ("xfce4-terminal", &["-x"]),
    ("xterm", &["-e"]),
];

/// This system's native package family: arch, debian, rpm (or "").
pub fn package_family_from(os_release: &str) -> Option<&'static str> {
    let mut ids = String::new();
    for l in os_release.lines() {
        if let Some((k, v)) = l.split_once('=') {
            if k == "ID" || k == "ID_LIKE" {
                ids.push(' ');
                ids.push_str(&v.trim().trim_matches('"').to_lowercase());
            }
        }
    }
    let ids: Vec<&str> = ids.split_whitespace().collect();
    let fams: [(&str, &[&str]); 3] = [
        ("arch", &["arch", "archlinux", "cachyos", "manjaro", "endeavouros"]),
        ("debian", &["debian", "ubuntu"]),
        ("rpm", &["fedora", "rhel", "centos", "suse", "opensuse", "opensuse-tumbleweed"]),
    ];
    fams.iter().find(|(_, names)| names.iter().any(|n| ids.contains(n))).map(|f| f.0)
}

pub fn package_family() -> &'static str {
    if let Some(f) = package_family_from(&read("/etc/os-release")) {
        return f;
    }
    for (fam, tool) in [("arch", "pacman"), ("debian", "apt"), ("rpm", "dnf"), ("rpm", "zypper")] {
        if which(tool).is_some() {
            return fam;
        }
    }
    ""
}

/// The command that installs `names` on this system (None when its package manager is unknown).
pub fn install_command(names: &str, family: Option<&str>) -> Option<String> {
    let fam = match family { Some(f) => f, None => package_family() };
    let pm = match fam {
        "arch" => "sudo pacman -S --needed",
        "debian" => "sudo apt install",
        "rpm" => "sudo dnf install",
        _ => return None,
    };
    (!names.is_empty()).then(|| format!("{pm} {names}"))
}

/// Run a shell command in a terminal window that stays open at the end:
/// Sonata's own Terminal (through the Python Sonata until it is ported), else one installed.
pub fn run_in_terminal(command: &str) -> bool {
    let launcher = std::env::var("SONATA2_LAUNCHER").unwrap_or_else(|_| "sonata2".into());
    if which(&launcher).is_some() && ok(&["sh", "-c", "python3 -c 'import gi; gi.require_version(\"Vte\", \"3.91\")' 2>/dev/null"], 5) {
        spawn(&[&launcher, "terminal", "--exec", command]);
        return true;
    }
    let prompt = tr!("Press Enter to close").replace('\'', "");
    let script = format!("{command}; echo; read -p '{prompt}' _");
    let mut terms: Vec<(String, Vec<&str>)> = Vec::new();
    if let Ok(t) = std::env::var("TERMINAL") {
        terms.push((t, vec!["-e"]));
    }
    terms.extend(TERMINALS.iter().map(|(t, f)| (t.to_string(), f.to_vec())));
    for (term, flags) in terms {
        if which(&term).is_some() {
            let mut cmd: Vec<&str> = vec![&term];
            cmd.extend(flags);
            cmd.extend(["sh", "-c", &script]);
            spawn(&cmd);
            return true;
        }
    }
    false
}

// -- Bluetooth -----------------------------------------------------------------------
/// Some(powered), None = no adapter.
pub fn bluetooth_state() -> Option<bool> {
    let (rc, out) = run(&["bluetoothctl", "show"], 5);
    if rc != 0 || !out.contains("Controller") {
        return None;
    }
    Some(out.contains("Powered: yes"))
}

pub fn set_bluetooth(on: bool) -> bool {
    if on {
        run(&["rfkill", "unblock", "bluetooth"], 10);
    }
    ok(&["bluetoothctl", "power", if on { "on" } else { "off" }], 8)
}

#[derive(Clone, Debug, PartialEq)]
pub struct BtDevice {
    pub mac: String,
    pub name: String,
    pub paired: bool,
    pub connected: bool,
}

/// The devices bluetoothctl knows (BlueZ over D-Bus: not ported yet).
pub fn bluetooth_devices() -> Vec<BtDevice> {
    let (rc, out) = run(&["bluetoothctl", "devices"], 8);
    let mut devs = Vec::new();
    if rc == 0 {
        for line in out.lines() {
            let p: Vec<&str> = line.splitn(3, ' ').collect();
            if p.len() == 3 && p[0] == "Device" {
                let (_rc, info) = run(&["bluetoothctl", "info", p[1]], 5);
                devs.push(BtDevice { mac: p[1].into(), name: p[2].into(), paired: info.contains("Paired: yes"), connected: info.contains("Connected: yes") });
            }
        }
    }
    devs.sort_by(|a, b| (!a.connected, !a.paired, a.name.to_lowercase()).cmp(&(!b.connected, !b.paired, b.name.to_lowercase())));
    devs
}

fn last_line_or(out: &str, fallback: String) -> String {
    out.trim().lines().last().map(String::from).unwrap_or(fallback)
}

/// (ok, message): connect or disconnect, and whether it really happened.
pub fn bluetooth_connect_result(mac: &str, on: bool) -> (bool, String) {
    let (rc, out) = run(&["bluetoothctl", if on { "connect" } else { "disconnect" }, mac], 20);
    let low = out.to_lowercase();
    let good = rc == 0 && low.contains("successful") && !low.contains("failed");
    let msg = if good && on {
        tr!("Connected")
    } else if good {
        tr!("Disconnected")
    } else {
        last_line_or(&out, tr!("That didn't work"))
    };
    (good, msg)
}

pub fn bluetooth_forget(mac: &str) -> (bool, String) {
    let (rc, out) = run(&["bluetoothctl", "remove", mac], 10);
    let good = rc == 0 && out.to_lowercase().contains("removed");
    (good, if good { tr!("Removed") } else { last_line_or(&out, tr!("That didn't work")) })
}

// -- Airplane Mode (rfkill) ------------------------------------------------------------
static RADIOS_BEFORE: Mutex<Option<(bool, bool)>> = Mutex::new(None);

/// True when every radio is blocked, None without any (or without rfkill).
pub fn airplane_mode() -> Option<bool> {
    let (rc, out) = run(&["rfkill", "list"], 10);
    if rc != 0 {
        return None;
    }
    let blocked: Vec<bool> = regex!(r"Soft blocked:\s*(yes|no)").captures_iter(&out).map(|c| &c[1] == "yes").collect();
    if blocked.is_empty() { None } else { Some(blocked.iter().all(|b| *b)) }
}

/// Every radio off, or back on: Wi-Fi and Bluetooth return as they were before (macOS).
pub fn set_airplane_mode(on: bool) -> bool {
    if on {
        *RADIOS_BEFORE.lock().unwrap_or_else(|e| e.into_inner()) = Some((wifi_enabled(), bluetooth_state().unwrap_or(false)));
        let mut good = ok(&["rfkill", "block", "all"], 10);
        if !good {
            // no access to /dev/rfkill: through the services
            good = ok(&["nmcli", "radio", "all", "off"], 10);
            run(&["bluetoothctl", "power", "off"], 8);
        }
        return good;
    }
    let mut good = ok(&["rfkill", "unblock", "all"], 10);
    let before = RADIOS_BEFORE.lock().unwrap_or_else(|e| e.into_inner()).take().unwrap_or((true, true));
    if before.0 {
        good = set_wifi_enabled(true) && good;
    }
    if before.1 {
        set_bluetooth(true);
    }
    good
}

// -- sound (PipeWire / WirePlumber) ---------------------------------------------------
pub fn parse_wpctl_volume(out: &str) -> Option<(i64, bool)> {
    let c = regex!(r"Volume:\s*([\d.]+)").captures(out)?;
    let v: f64 = c[1].parse().ok()?;
    Some(((v * 100.0).round_ties_even() as i64, out.contains("[MUTED]")))
}

/// (percent, muted) of the default output, None without wpctl.
pub fn volume() -> Option<(i64, bool)> {
    let (rc, out) = run(&["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], 5);
    if rc == 0 { parse_wpctl_volume(&out) } else { None }
}

pub fn set_volume_of(percent: Option<i64>, muted: Option<bool>, node: &str) -> bool {
    let mut good = true;
    if let Some(p) = percent {
        let p = p.clamp(0, 100);
        good = ok(&["wpctl", "set-volume", "-l", "1.0", node, &format!("{p}%")], 10);
        let kind = match node {
            "@DEFAULT_AUDIO_SINK@" => Some("sink"),
            "@DEFAULT_AUDIO_SOURCE@" => Some("source"),
            _ => None,
        };
        if let (true, Some(k)) = (good, kind) {
            crate::devicevolume::remember(k, p); // kept for this device
        }
    }
    if let Some(m) = muted {
        good = ok(&["wpctl", "set-mute", node, if m { "1" } else { "0" }], 10) && good;
    }
    good
}

pub fn set_volume(percent: Option<i64>, muted: Option<bool>) -> bool {
    set_volume_of(percent, muted, "@DEFAULT_AUDIO_SINK@")
}

/// (percent, muted) of the default microphone, None without one.
pub fn input_volume() -> Option<(i64, bool)> {
    let (rc, out) = run(&["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], 5);
    if rc == 0 { parse_wpctl_volume(&out) } else { None }
}

pub fn set_input_volume(percent: Option<i64>, muted: Option<bool>) -> bool {
    set_volume_of(percent, muted, "@DEFAULT_AUDIO_SOURCE@")
}

#[derive(Clone, Debug, PartialEq)]
pub struct AudioSink {
    pub id: i64,
    pub name: String,
    pub default: bool,
}

/// Devices of the Audio > `section` ("Sinks" / "Sources") part of `wpctl status`.
pub fn parse_wpctl_status(out: &str, section: &str) -> Vec<AudioSink> {
    let mut nodes = Vec::new();
    let (mut in_audio, mut current): (bool, Option<String>) = (false, None);
    for line in out.lines() {
        let s = line.trim_matches(|c| " │├└─".contains(c));
        let top = !line.starts_with([' ', '│', '├', '└']) && !s.is_empty();
        if top {
            in_audio = s.starts_with("Audio");
            current = None;
            continue;
        }
        if !in_audio {
            continue;
        }
        if let Some(name) = s.strip_suffix(':') {
            current = Some(name.to_string());
            continue;
        }
        if current.as_deref() != Some(section) || s.is_empty() {
            continue;
        }
        if let Some(c) = regex!(r"^(\*)?\s*(\d+)\.\s+(.+?)(\s+\[vol:.*\])?$").captures(s) {
            nodes.push(AudioSink { id: c[2].parse().unwrap_or(0), name: c[3].trim().to_string(), default: c.get(1).is_some() });
        }
    }
    nodes
}

fn wpctl_nodes(section: &str) -> Vec<AudioSink> {
    let (rc, out) = run(&["wpctl", "status"], 5);
    if rc == 0 { parse_wpctl_status(&out, section) } else { Vec::new() }
}

pub fn audio_sinks() -> Vec<AudioSink> {
    wpctl_nodes("Sinks")
}

pub fn audio_sources() -> Vec<AudioSink> {
    wpctl_nodes("Sources")
}

pub fn set_default_sink(id: i64) -> bool {
    ok(&["wpctl", "set-default", &id.to_string()], 10)
}

/// Outputs / inputs as people see them: one per available *port* of each
/// device ("Headphones", "Speakers", "HDMI").
#[derive(Clone, Debug, PartialEq)]
pub struct AudioDevice {
    /// "sink-name|port-name" (or a wpctl id)
    pub key: String,
    pub name: String,
    pub default: bool,
}

pub fn parse_pactl_devices(json_out: &str, default: &str, kind: &str) -> Option<Vec<AudioDevice>> {
    let nodes: Vec<serde_json::Value> = serde_json::from_str(json_out).ok()?;
    let s = |v: &serde_json::Value, k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let mut devs: Vec<AudioDevice> = Vec::new();
    for n in &nodes {
        let name = s(n, "name");
        if (kind == "sources" && name.ends_with(".monitor")) || name.starts_with("sonata-eq") {
            continue; // "Monitor of ..." isn't a microphone; the equalizer's filters
        }
        let desc = Some(s(n, "description")).filter(|d| !d.is_empty()).unwrap_or_else(|| name.clone());
        let all: Vec<&serde_json::Value> = n.get("ports").and_then(|p| p.as_array()).map(|a| a.iter().collect()).unwrap_or_default();
        let ports: Vec<&serde_json::Value> = all.iter().copied().filter(|p| s(p, "availability") != "not available").collect();
        if all.is_empty() {
            devs.push(AudioDevice { key: format!("{name}|"), name: desc, default: name == default });
            continue;
        }
        for p in &ports {
            let mut label = Some(s(p, "description")).filter(|d| !d.is_empty()).unwrap_or_else(|| s(p, "name"));
            if ["analog output", "analog input", ""].contains(&label.to_lowercase().as_str()) || (ports.len() == 1 && all.len() == 1) {
                label = desc.clone();
            }
            let active = s(n, "active_port");
            devs.push(AudioDevice { key: format!("{name}|{}", s(p, "name")), name: label, default: name == default && active == s(p, "name") });
        }
    }
    // the same label twice: say which device
    let names: Vec<String> = devs.iter().map(|d| d.name.clone()).collect();
    for d in devs.iter_mut() {
        if names.iter().filter(|n| **n == d.name).count() > 1 {
            let node = d.key.split('|').next().unwrap_or("").to_string();
            let ndesc = nodes.iter().find(|n| s(n, "name") == node).map(|n| s(n, "description")).unwrap_or(node);
            d.name = format!("{} ({ndesc})", d.name);
        }
    }
    Some(devs)
}

fn pactl_devices(kind: &str) -> Option<Vec<AudioDevice>> {
    let (rc, out) = run(&["pactl", "-f", "json", "list", kind], 5);
    if rc != 0 {
        return None;
    }
    let (_rc, default) = run(&["pactl", &format!("get-default-{}", &kind[..kind.len() - 1])], 3);
    parse_pactl_devices(&out, default.trim(), kind)
}

pub fn audio_outputs() -> Vec<AudioDevice> {
    pactl_devices("sinks").unwrap_or_else(|| audio_sinks().into_iter().map(|s| AudioDevice { key: s.id.to_string(), name: s.name, default: s.default }).collect())
}

pub fn audio_inputs() -> Vec<AudioDevice> {
    pactl_devices("sources").unwrap_or_else(|| audio_sources().into_iter().map(|s| AudioDevice { key: s.id.to_string(), name: s.name, default: s.default }).collect())
}

fn select(kind: &str, key: &str) -> bool {
    let Some((name, port)) = key.split_once('|') else { return set_default_sink(key.parse().unwrap_or(-1)) };
    let mut good = ok(&["pactl", &format!("set-default-{kind}"), name], 10);
    if !port.is_empty() {
        good = ok(&["pactl", &format!("set-{kind}-port"), name, port], 10) && good;
    }
    good
}

pub fn select_output(key: &str) -> bool {
    select("sink", key)
}

pub fn select_input(key: &str) -> bool {
    select("source", key)
}

// -- display brightness ----------------------------------------------------------------
const BACKLIGHT: &str = "/sys/class/backlight";
/// The slider runs 0..100 over the panel's usable range: never below this (fully black).
pub const MIN_BRIGHTNESS: f64 = 5.0;
const BUILTIN: [&str; 3] = ["eDP", "LVDS", "DSI"];

/// (device name, current, max) of the laptop panel (firmware/platform interfaces first).
fn backlight() -> Option<(String, i64, i64)> {
    let mut names: Vec<String> = std::fs::read_dir(BACKLIGHT).ok()?.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    names.sort();
    let rank = |n: &String| match read(&format!("{BACKLIGHT}/{n}/type")).as_str() {
        "firmware" => 0,
        "platform" => 1,
        "raw" => 2,
        _ => 3,
    };
    names.sort_by_key(rank);
    for n in names {
        let (cur, mx) = (read(&format!("{BACKLIGHT}/{n}/brightness")), read(&format!("{BACKLIGHT}/{n}/max_brightness")));
        if let (Ok(c), Ok(m)) = (cur.parse::<i64>(), mx.parse::<i64>()) {
            if m > 0 {
                return Some((n, c, m));
            }
        }
    }
    None
}

pub fn to_level(percent: f64) -> i64 {
    ((percent - MIN_BRIGHTNESS) * 100.0 / (100.0 - MIN_BRIGHTNESS)).round_ties_even().clamp(0.0, 100.0) as i64
}

pub fn to_percent(level: f64) -> f64 {
    (MIN_BRIGHTNESS + level * (100.0 - MIN_BRIGHTNESS) / 100.0).clamp(MIN_BRIGHTNESS, 100.0)
}

pub fn is_builtin(output: Option<&str>) -> bool {
    output.is_none_or(|o| o.is_empty() || BUILTIN.iter().any(|b| o.starts_with(b)))
}

/// Slider level 0..100 of a display (the panel, or an external monitor over DDC/CI).
pub fn brightness(output: Option<&str>) -> Option<i64> {
    if !is_builtin(output) {
        return ddc_brightness(output?).map(|p| to_level(p as f64));
    }
    if let Some((_, cur, mx)) = backlight() {
        return Some(to_level(cur as f64 * 100.0 / mx as f64));
    }
    let (rc, out) = run(&["brightnessctl", "-m", "-c", "backlight"], 5);
    if rc != 0 {
        return None;
    }
    regex!(r",(\d+)%,").captures(&out).and_then(|c| c[1].parse::<f64>().ok()).map(to_level)
}

/// Slider level 0..100. The panel: through logind (allowed for the active
/// session, no root), else brightnessctl; an external monitor: DDC/CI.
pub fn set_brightness(level: i64, output: Option<&str>) -> bool {
    let percent = to_percent(level as f64);
    if !is_builtin(output) {
        return output.is_some_and(|o| set_ddc_brightness(o, percent));
    }
    if let (Some((name, _cur, mx)), Some(bus)) = (backlight(), system_bus()) {
        let value = (mx as f64 * percent / 100.0).round_ties_even() as u32;
        let r = bus.call_sync(
            Some("org.freedesktop.login1"),
            "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session",
            "SetBrightness",
            Some(&("backlight", name.as_str(), value).to_variant()),
            None,
            gio::DBusCallFlags::NONE,
            2000,
            gio::Cancellable::NONE,
        );
        if r.is_ok() {
            return true;
        }
    }
    ok(&["brightnessctl", "-q", "-c", "backlight", "set", &format!("{}%", percent.round_ties_even())], 10)
}

const DRM: &str = "/sys/class/drm";

fn drm_connectors(output: &str) -> Vec<std::path::PathBuf> {
    let Ok(rd) = std::fs::read_dir(DRM) else { return Vec::new() };
    let suffix = format!("-{output}");
    rd.flatten().map(|e| e.path()).filter(|p| p.file_name().is_some_and(|n| { let n = n.to_string_lossy(); n.starts_with("card") && n.ends_with(&suffix) })).collect()
}

fn drm_ddc_bus(output: &str) -> Option<i64> {
    for conn in drm_connectors(output) {
        for link in [conn.join("ddc"), conn.clone()] {
            let target = std::fs::canonicalize(&link).unwrap_or(link);
            let t = target.display().to_string();
            if let Some(c) = regex!(r"i2c-(\d+)$").captures(&t) {
                return c[1].parse().ok();
            }
            if let Ok(rd) = std::fs::read_dir(&target) {
                let kid = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).find(|k| regex!(r"^i2c-\d+$").is_match(k));
                if let Some(k) = kid {
                    return k[4..].parse().ok();
                }
            }
        }
    }
    None
}

/// (manufacturer, model name, serial) of the monitor on `output`, from its EDID.
pub fn edid_id_of(e: &[u8]) -> Option<(String, String, String)> {
    if e.len() < 128 {
        return None;
    }
    let v = ((e[8] as u32) << 8) | e[9] as u32;
    let mfg: String = [10, 5, 0].iter().map(|s| char::from((((v >> s) & 0x1F) + 64) as u8)).collect();
    let (mut name, mut serial) = (String::new(), String::new());
    for off in [54, 72, 90, 108] {
        let d = &e[off..off + 18];
        if d[0..3] == [0, 0, 0] && (d[3] == 0xFC || d[3] == 0xFF) {
            let text = String::from_utf8_lossy(d[5..18].split(|b| *b == b'\n').next().unwrap_or(&[])).trim().to_string();
            if d[3] == 0xFC {
                name = text;
            } else {
                serial = text;
            }
        }
    }
    Some((mfg, name, serial))
}

fn edid_id(output: &str) -> Option<(String, String, String)> {
    drm_connectors(output).into_iter().find_map(|c| std::fs::read(c.join("edid")).ok().and_then(|e| edid_id_of(&e[..e.len().min(128)])))
}

#[derive(Clone, Debug, PartialEq)]
pub struct DdcDisplay {
    pub bus: i64,
    pub connector: String,
    pub mfg: String,
    pub model: String,
    pub serial: String,
}

pub fn parse_ddc_detect(text: &str) -> Vec<DdcDisplay> {
    let mut out = Vec::new();
    let starts: Vec<usize> = regex!(r"(?m)^Display \d+").find_iter(text).map(|m| m.start()).collect();
    for (i, s) in starts.iter().enumerate() {
        let block = &text[*s..starts.get(i + 1).copied().unwrap_or(text.len())];
        let Some(bus) = regex!(r"I2C bus:\s*/dev/i2c-(\d+)").captures(block) else { continue };
        let conn = regex!(r"DRM connector:\s*(\S+)").captures(block).map(|c| c[1].to_string()).unwrap_or_default();
        let mon: Vec<String> = regex!(r"Monitor:\s*(.*)").captures(block).map(|c| c[1].trim().split(':').map(|x| x.trim().to_string()).collect()).unwrap_or_default();
        let at = |i: usize| mon.get(i).cloned().unwrap_or_default();
        out.push(DdcDisplay { bus: bus[1].parse().unwrap_or(0), connector: conn, mfg: at(0), model: at(1), serial: at(2) });
    }
    out
}

static DETECTED: Mutex<Option<(std::time::Instant, Vec<DdcDisplay>)>> = Mutex::new(None);
static DDC_MAX: Mutex<Vec<(i64, i64)>> = Mutex::new(Vec::new());

/// `ddcutil detect --brief` (kept a few minutes: probing every bus takes seconds).
pub fn ddc_displays() -> Vec<DdcDisplay> {
    let mut d = DETECTED.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((at, list)) = d.as_ref() {
        if at.elapsed().as_secs() < 300 {
            return list.clone();
        }
    }
    let (rc, out) = run(&["ddcutil", "detect", "--brief"], 20);
    let list = if rc == 0 { parse_ddc_detect(&out) } else { Vec::new() };
    *d = Some((std::time::Instant::now(), list.clone()));
    list
}

fn detected_bus(output: &str) -> Option<i64> {
    which("ddcutil")?;
    let shown = ddc_displays();
    if let Some(d) = shown.iter().find(|d| !d.connector.is_empty() && d.connector.split_once('-').map(|x| x.1).unwrap_or(&d.connector) == output) {
        return Some(d.bus);
    }
    let (mfg, name, serial) = edid_id(output)?;
    shown.iter().find(|d| d.mfg == mfg && d.model == name && (serial.is_empty() || d.serial.is_empty() || d.serial == serial)).map(|d| d.bus)
}

/// The I2C bus of a monitor's DDC/CI: from its DRM connector, else from what ddcutil finds.
pub fn ddc_bus(output: &str) -> Option<i64> {
    drm_ddc_bus(output).or_else(|| detected_bus(output))
}

/// Percent of the monitor's maximum, None without DDC/CI.
pub fn ddc_brightness(output: &str) -> Option<i64> {
    let bus = ddc_bus(output)?;
    let (rc, out) = run(&["ddcutil", "--bus", &bus.to_string(), "--brief", "getvcp", "10"], 8);
    if rc != 0 {
        return None;
    }
    let c = regex!(r"VCP 10 C (\d+) (\d+)").captures(&out)?;
    let (cur, max): (i64, i64) = (c[1].parse().ok()?, c[2].parse().ok()?);
    if max <= 0 {
        return None;
    }
    let mut m = DDC_MAX.lock().unwrap_or_else(|e| e.into_inner());
    m.retain(|x| x.0 != bus);
    m.push((bus, max));
    Some((cur as f64 * 100.0 / max as f64).round_ties_even() as i64)
}

pub fn set_ddc_brightness(output: &str, percent: f64) -> bool {
    let Some(bus) = ddc_bus(output) else { return false };
    let max = DDC_MAX.lock().unwrap_or_else(|e| e.into_inner()).iter().find(|x| x.0 == bus).map(|x| x.1).unwrap_or(100);
    let value = (percent * max as f64 / 100.0).round_ties_even() as i64;
    ok(&["ddcutil", "--bus", &bus.to_string(), "--noverify", "setvcp", "10", &value.to_string()], 8)
}

// -- battery -------------------------------------------------------------------------------
/// (percent or None without a battery, status: Charging/Discharging/Full/...).
pub fn battery(power_supply: &str) -> (Option<i64>, String) {
    let mut bats: Vec<String> = std::fs::read_dir(power_supply).map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.starts_with("BAT")).collect()).unwrap_or_default();
    bats.sort();
    let Some(b) = bats.first() else { return (None, String::new()) };
    let cap = read(&format!("{power_supply}/{b}/capacity"));
    (cap.parse().ok(), read(&format!("{power_supply}/{b}/status")))
}

/// Plugged in: any mains / USB-C supply online.
pub fn on_ac(power_supply: &str) -> bool {
    let Ok(rd) = std::fs::read_dir(power_supply) else { return false };
    rd.flatten().any(|e| {
        let x = e.file_name().to_string_lossy().into_owned();
        let kind = read(&format!("{power_supply}/{x}/type"));
        (kind == "Mains" || kind == "USB" || x.starts_with("AC") || x.starts_with("ADP")) && read(&format!("{power_supply}/{x}/online")) == "1"
    })
}

// -- session / power -------------------------------------------------------------------------
/// sleep / restart / shutdown / lock / logout.
pub fn power_action(kind: &str) {
    let sid = std::env::var("XDG_SESSION_ID").ok().filter(|s| !s.is_empty());
    let user = std::env::var("USER").unwrap_or_default();
    match kind {
        "sleep" => spawn(&["systemctl", "suspend"]),
        "restart" => spawn(&["systemctl", "reboot"]),
        "shutdown" => spawn(&["systemctl", "poweroff"]),
        "lock" => spawn(&["loginctl", "lock-session"]),
        "logout" => match &sid {
            Some(s) => spawn(&["loginctl", "terminate-session", s]),
            None => spawn(&["loginctl", "terminate-user", &user]),
        },
        _ => {}
    }
}

/// Reload the shell (or only `names` of it) with the current code; open
/// apps stay. Detached: the Python Sonata's `restart` does it.
pub fn restart_sonata(names: &[&str]) {
    let launcher = std::env::var("SONATA2_LAUNCHER").ok().filter(|l| std::path::Path::new(l).exists()).unwrap_or_else(|| "sonata2".into());
    let mut cmd: Vec<&str> = vec![&launcher, "restart"];
    cmd.extend_from_slice(names);
    spawn(&cmd);
}

// -- About ---------------------------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct About {
    pub os_name: String,
    pub machine: String,
    pub cpu: String,
    pub memory_gb: i64,
    pub gpus: Vec<String>,
    pub kernel: String,
}

/// 'ASUSTeK COMPUTER INC.' + 'ASUS TUF Gaming A15 FA507NV_FA507NV' -> 'ASUS TUF Gaming A15 FA507NV'.
pub fn machine_name(vendor: &str, family: &str, product: &str) -> String {
    let (vendor, family) = (vendor.trim(), family.trim());
    let mut product = product.trim().to_string();
    if JUNK_DMI.contains(&product.to_lowercase().as_str()) {
        product.clear();
    }
    // "X_X" -> "X" (the regex crate has no back-references)
    product = product.split(' ').map(|w| match w.split_once('_') { Some((a, b)) if a == b && !a.is_empty() => a.to_string(), _ => w.to_string() }).collect::<Vec<_>>().join(" ");
    if !family.is_empty() && !JUNK_DMI.contains(&family.to_lowercase().as_str()) && !product.contains(family) {
        product = format!("{family} {product}").trim().to_string();
    }
    let first = vendor.split_whitespace().next().unwrap_or("").to_lowercase().trim_end_matches([',', '.']).to_string();
    let brand = match first.as_str() {
        "asustek" => "asus".to_string(),
        "hewlett-packard" => "hp".to_string(),
        "micro-star" => "msi".to_string(),
        f => f.to_string(),
    };
    if !product.is_empty() && !brand.is_empty() && product.to_lowercase().starts_with(&brand) {
        return product;
    }
    let v = if JUNK_DMI.contains(&vendor.to_lowercase().as_str()) { "" } else { vendor };
    let joined = [v, product.as_str()].iter().filter(|x| !x.is_empty()).copied().collect::<Vec<_>>().join(" ");
    if joined.is_empty() { tr!("Computer") } else { joined }
}

pub fn gpu_name(vendor: &str, device: &str) -> String {
    let v = vendor.to_lowercase();
    let brand = if v.contains("nvidia") {
        "NVIDIA".to_string()
    } else if v.contains("advanced micro") || regex!(r"\bati\b").is_match(&v) {
        "AMD".to_string()
    } else if v.contains("intel") {
        "Intel".to_string()
    } else {
        regex!(r"\s*(Corporation|Inc\.?|Co\.?,? Ltd\.?)\s*").replace_all(vendor, " ").trim().to_string()
    };
    let name = regex!(r"\[([^\]]+)\]").captures(device).map(|c| c[1].to_string()).unwrap_or_else(|| device.to_string());
    if name.to_lowercase().starts_with(&brand.to_lowercase()) { name } else { format!("{brand} {name}") }
}

pub fn about() -> About {
    let osr: Vec<(String, String)> = read("/etc/os-release").lines().filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.to_string()))).collect();
    let dmi = "/sys/class/dmi/id";
    let machine = machine_name(&read(&format!("{dmi}/sys_vendor")), &read(&format!("{dmi}/product_family")), &read(&format!("{dmi}/product_name")));
    let cpuinfo = read("/proc/cpuinfo");
    let cpu = regex!(r"(?m)^model name\s*:\s*(.+)$").captures(&cpuinfo).map(|c| regex!(r"\s+").replace_all(&c[1], " ").trim().to_string()).unwrap_or_else(|| tr!("Unknown"));
    let mem = regex!(r"(?m)^MemTotal:\s*(\d+) kB").captures(&read("/proc/meminfo")).and_then(|c| c[1].parse::<f64>().ok()).map(|kb| (kb / 1024.0 / 1024.0).round_ties_even() as i64).unwrap_or(0);
    let mut gpus = Vec::new();
    let (rc, out) = run(&["lspci", "-mm"], 10);
    if rc == 0 {
        for line in out.lines() {
            let f: Vec<&str> = regex!(r#""([^"]*)""#).captures_iter(line).map(|c| c.get(1).map_or("", |m| m.as_str())).collect();
            if f.len() >= 3 && (f[0].contains("VGA") || f[0].contains("3D") || f[0].contains("Display")) {
                gpus.push(gpu_name(f[1], f[2]));
            }
        }
    }
    let mut uts: libc::utsname = unsafe { std::mem::zeroed() };
    let kernel = if unsafe { libc::uname(&mut uts) } == 0 {
        unsafe { std::ffi::CStr::from_ptr(uts.release.as_ptr()) }.to_string_lossy().into_owned()
    } else {
        String::new()
    };
    let os_name = osr.iter().find(|(k, _)| k == "PRETTY_NAME").map(|(_, v)| v.trim_matches('"').to_string()).unwrap_or_else(|| "Linux".into());
    About { os_name, machine, cpu, memory_gb: mem, gpus, kernel }
}

// -- displays (wlroots compositors) -------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Display {
    pub name: String,
    pub description: String,
    /// "1920x1080@60.000"
    pub modes: Vec<String>,
    pub current: String,
    pub scale: f64,
    pub x: i64,
    pub y: i64,
    pub enabled: bool,
    pub transform: String,
}

impl Display {
    /// Its logical size in the layout: the mode over the scale, turned when rotated.
    pub fn size(&self) -> (i64, i64) {
        let res = self.current.split('@').next().unwrap_or("");
        let Some((w, h)) = res.split_once('x').and_then(|(w, h)| Some((w.parse::<f64>().ok()?, h.parse::<f64>().ok()?))) else { return (0, 0) };
        let (w, h) = if ["90", "270", "flipped-90", "flipped-270"].contains(&self.transform.as_str()) { (h, w) } else { (w, h) };
        let s = if self.scale == 0.0 { 1.0 } else { self.scale };
        ((w / s).round_ties_even() as i64, (h / s).round_ties_even() as i64)
    }
}

pub fn parse_wlr_randr(out: &str) -> Vec<Display> {
    let mut result: Vec<Display> = Vec::new();
    for line in out.lines() {
        if !line.is_empty() && !line.starts_with(' ') {
            let (name, desc) = line.split_once(' ').unwrap_or((line, ""));
            result.push(Display { name: name.into(), description: desc.trim_matches(|c| c == '"' || c == ' ').into(), modes: Vec::new(), current: String::new(), scale: 1.0, x: 0, y: 0, enabled: true, transform: "normal".into() });
            continue;
        }
        let Some(cur) = result.last_mut() else { continue };
        let s = line.trim();
        if let Some(c) = regex!(r"^(\d+x\d+) px, ([\d.]+) Hz(.*)").captures(s) {
            let mode = format!("{}@{}", &c[1], &c[2]);
            if !cur.modes.contains(&mode) {
                cur.modes.push(mode.clone());
            }
            if c[3].contains("current") {
                cur.current = mode;
            }
        } else if let Some(v) = s.strip_prefix("Scale:") {
            cur.scale = v.trim().parse().unwrap_or(1.0);
        } else if let Some(v) = s.strip_prefix("Position:") {
            if let Some((x, y)) = v.trim().split_once(',') {
                if let (Ok(x), Ok(y)) = (x.trim().parse(), y.trim().parse()) {
                    cur.x = x;
                    cur.y = y;
                }
            }
        } else if let Some(v) = s.strip_prefix("Enabled:") {
            cur.enabled = v.trim() == "yes";
        } else if let Some(v) = s.strip_prefix("Transform:") {
            cur.transform = v.trim().into();
        }
    }
    result
}

pub fn displays() -> Vec<Display> {
    let (rc, out) = run(&["wlr-randr"], 5);
    if rc == 0 { parse_wlr_randr(&out) } else { Vec::new() }
}

/// Where each display sits; saved in the session's Wayfire config.
pub fn set_display_positions(positions: &[(String, i64, i64)]) -> bool {
    positions.iter().fold(true, |good, (name, x, y)| wfconfig::set(&format!("output:{name}"), "position", &format!("{x},{y}")) && good)
}

/// The saved mode: "highrr" (the default), or "1920x1080@143.981".
pub fn display_mode_setting(name: &str) -> String {
    let v = wfconfig::get(&format!("output:{name}"), "mode", "highrr");
    let v = if v.is_empty() { "highrr".to_string() } else { v };
    if let Some(c) = regex!(r"^(\d+x\d+)@(\d+)$").captures(&v) {
        let hz: i64 = c[2].parse().unwrap_or(0);
        if hz > 1000 {
            return format!("{}@{:.3}", &c[1], hz as f64 / 1000.0); // Wayfire keeps mHz
        }
    }
    v
}

pub fn set_display_mode(name: &str, mode: &str) -> bool {
    let mode = if ["highrr", "highres", "auto"].contains(&mode) {
        mode.to_string()
    } else {
        let (res, hz) = mode.split_once('@').unwrap_or((mode, "60"));
        format!("{res}@{}", (hz.parse::<f64>().unwrap_or(60.0) * 1000.0).round_ties_even() as i64)
    };
    wfconfig::set(&format!("output:{name}"), "mode", &mode)
}

pub fn set_display_scale(name: &str, scale: f64) -> bool {
    wfconfig::set(&format!("output:{name}"), "scale", &format!("{scale:?}"))
}

// -- power profiles -------------------------------------------------------------------------------
pub const POWER_PROFILES: [(&str, &str); 3] = [("performance", n_!("High Performance")), ("balanced", n_!("Automatic")), ("power-saver", n_!("Low Power"))];

/// ActiveProfile over D-Bus (powerprofilesctl is a Python script: too heavy for a poll).
pub fn power_profile_fast() -> Option<String> {
    let bus = system_bus()?;
    for (name, path) in [("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"), ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")] {
        let r = bus.call_sync(Some(name), path, "org.freedesktop.DBus.Properties", "Get", Some(&(name, "ActiveProfile").to_variant()), None, gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE);
        if let Ok(v) = r {
            if let Some(s) = v.child_value(0).as_variant().and_then(|x| x.get::<String>()) {
                return Some(s);
            }
        }
    }
    None
}

pub fn power_profile() -> Option<String> {
    let (rc, out) = run(&["powerprofilesctl", "get"], 5);
    (rc == 0).then(|| out.trim().to_string())
}

pub fn set_power_profile(profile: &str) -> bool {
    ok(&["powerprofilesctl", "set", profile], 5)
}

// -- input devices (Wayfire [input]) -----------------------------------------------------------------
pub const XKB_LAYOUTS: [(&str, &str); 12] = [
    ("us", n_!("U.S.")),
    ("us(intl)", n_!("U.S. International")),
    ("br", n_!("Brazilian (ABNT2)")),
    ("gb", n_!("British")),
    ("de", n_!("German")),
    ("fr", n_!("French")),
    ("es", n_!("Spanish")),
    ("pt", n_!("Portuguese")),
    ("it", n_!("Italian")),
    ("latam", n_!("Latin American")),
    ("ru", n_!("Russian")),
    ("jp", n_!("Japanese")),
];

fn join_layouts(lays: &str, vars: &str) -> Vec<String> {
    let lays: Vec<&str> = lays.split(',').collect();
    let mut vars: Vec<&str> = vars.split(',').collect();
    vars.resize(lays.len().max(vars.len()), "");
    lays.iter().zip(vars).map(|(l, v)| if v.is_empty() { l.to_string() } else { format!("{l}({v})") }).collect()
}

fn split_layouts(layouts: &[String]) -> (String, String) {
    let lays: Vec<&str> = layouts.iter().map(|x| x.split('(').next().unwrap_or("")).collect();
    let vars: Vec<&str> = layouts.iter().map(|x| x.split_once('(').map(|v| v.1.trim_end_matches(')')).unwrap_or("")).collect();
    (lays.join(","), vars.join(","))
}

/// Input sources, the active (first) one first: ["br", "us(intl)"].
pub fn keyboard_layouts() -> Vec<String> {
    let lay = wfconfig::get("input", "xkb_layout", "us");
    join_layouts(if lay.is_empty() { "us" } else { &lay }, &wfconfig::get("input", "xkb_variant", ""))
}

pub fn set_keyboard_layouts(layouts: &[String]) -> bool {
    let (lays, vars) = split_layouts(layouts);
    wfconfig::set("input", "xkb_layout", &lays) && wfconfig::set("input", "xkb_variant", &vars)
}

const PER_KEYBOARD_KEYS: [&str; 5] = ["kb_repeat_rate", "kb_repeat_delay", "xkb_options", "xkb_model", "xkb_rules"];
const NOT_KEYBOARDS: [&str; 11] = ["video bus", "power button", "sleep button", "lid switch", "wmi hotkeys", "sonata", "virtual", "consumer control", "system control", "headset", "speaker"];

/// The keyboards connected now (Wayfire's input devices; else the kernel's
/// list), the system's buttons and switches left out.
pub fn keyboards() -> Vec<String> {
    let mut names: Vec<String> = crate::wfipc::call("input/list-devices", serde_json::json!({}))
        .and_then(|v| v.as_array().cloned())
        .unwrap_or_default()
        .iter()
        .filter(|d| d.get("type").and_then(|t| t.as_str()) == Some("keyboard"))
        .filter_map(|d| d.get("name").and_then(|n| n.as_str()).map(String::from))
        .collect();
    if names.is_empty() {
        for b in read("/proc/bus/input/devices").split("\n\n") {
            let name = regex!(r#"(?m)^N: Name="(.*)"$"#).captures(b).map(|c| c[1].to_string());
            let ev = regex!(r"(?m)^B: EV=(\w+)$").captures(b).and_then(|c| u64::from_str_radix(&c[1], 16).ok());
            if let (Some(n), Some(ev)) = (name, ev) {
                if ev & 0x120013 == 0x120013 {
                    names.push(n); // keys + LEDs + repeat
                }
            }
        }
    }
    let mut out: Vec<String> = Vec::new();
    for n in names {
        if !out.contains(&n) && !NOT_KEYBOARDS.iter().any(|x| n.to_lowercase().contains(x)) {
            out.push(n);
        }
    }
    out
}

/// This keyboard's own layout ("us(intl)"), "" when it follows Input Sources.
pub fn keyboard_device_layout(name: &str) -> String {
    let lay = wfconfig::get(&format!("input:{name}"), "xkb_layout", "");
    if lay.is_empty() {
        return String::new();
    }
    let var = wfconfig::get(&format!("input:{name}"), "xkb_variant", "");
    let (l, v) = (lay.split(',').next().unwrap_or(""), var.split(',').next().unwrap_or(""));
    if v.is_empty() { l.to_string() } else { format!("{l}({v})") }
}

/// layout "": the keyboard follows Input Sources again.
pub fn set_keyboard_device_layout(name: &str, layout: &str) -> bool {
    let sec = format!("input:{name}");
    if layout.is_empty() {
        return wfconfig::remove_section(&sec);
    }
    let (lay, var) = layout.split_once('(').unwrap_or((layout, ""));
    let mut good = wfconfig::set(&sec, "xkb_layout", lay) && wfconfig::set(&sec, "xkb_variant", var.trim_end_matches(')'));
    for k in PER_KEYBOARD_KEYS {
        let v = wfconfig::get("input", k, "");
        if !v.is_empty() {
            good = wfconfig::set(&sec, k, &v) && good;
        }
    }
    good
}

pub fn keyboard_layout() -> String {
    let lay = wfconfig::get("input", "xkb_layout", "us");
    let lay = if lay.is_empty() { "us".to_string() } else { lay };
    let var = wfconfig::get("input", "xkb_variant", "");
    if var.is_empty() { lay } else { format!("{lay}({var})") }
}

pub fn set_keyboard_layout(value: &str) -> bool {
    let (lay, var) = value.split_once('(').unwrap_or((value, ""));
    wfconfig::set("input", "xkb_layout", lay) && wfconfig::set("input", "xkb_variant", var.trim_end_matches(')'))
}

// -- date & time ----------------------------------------------------------------------------------
pub fn timezone() -> String {
    let (rc, out) = run(&["timedatectl", "show", "-p", "Timezone", "--value"], 5);
    if rc == 0 && !out.trim().is_empty() {
        return out.trim().to_string();
    }
    std::fs::canonicalize("/etc/localtime").ok().and_then(|p| p.display().to_string().split_once("zoneinfo/").map(|x| x.1.to_string())).unwrap_or_else(|| "UTC".into())
}

pub fn timezones() -> Vec<String> {
    let (rc, out) = run(&["timedatectl", "list-timezones"], 10);
    if rc == 0 { out.split_whitespace().map(String::from).collect() } else { Vec::new() }
}

pub fn set_timezone(zone: &str) -> bool {
    ok(&["timedatectl", "set-timezone", zone], 60) // polkit may ask
}

pub fn ntp() -> Option<bool> {
    let (rc, out) = run(&["timedatectl", "show", "-p", "NTP", "--value"], 5);
    (rc == 0).then(|| out.trim() == "yes")
}

pub fn set_ntp(on: bool) -> bool {
    ok(&["timedatectl", "set-ntp", if on { "true" } else { "false" }], 60)
}

// -- sharing ----------------------------------------------------------------------------------------
pub fn computer_name() -> String {
    let (rc, out) = run(&["hostnamectl", "--pretty"], 5);
    if rc == 0 && !out.trim().is_empty() { out.trim().to_string() } else { glib::host_name().to_string() }
}

/// The host name a pretty name gives ("Vini's Laptop" -> "vini-s-laptop").
pub fn host_name_of(name: &str) -> String {
    let h = regex!(r"[^a-zA-Z0-9-]+").replace_all(name, "-").trim_matches('-').to_lowercase();
    if h.is_empty() { "computer".into() } else { h }
}

/// Pretty name as typed, host name derived (like macOS "Local hostname").
pub fn set_computer_name(name: &str) -> bool {
    let good = ok(&["hostnamectl", "set-hostname", "--pretty", name], 60);
    ok(&["hostnamectl", "set-hostname", "--static", &host_name_of(name)], 60) && good
}

// -- default apps ------------------------------------------------------------------------------------
pub fn default_browser() -> String {
    let (rc, out) = run(&["xdg-settings", "get", "default-web-browser"], 5);
    if rc == 0 { out.trim().to_string() } else { String::new() }
}

pub fn set_default_browser(desktop_id: &str) -> bool {
    ok(&["xdg-settings", "set", "default-web-browser", desktop_id], 10)
}

#[cfg(test)]
mod tests;
