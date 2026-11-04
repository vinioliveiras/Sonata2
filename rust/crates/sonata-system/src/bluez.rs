//! Bluetooth through BlueZ's D-Bus API (backend/bluez.py; org.bluez, not
//! bluetoothctl).
//!
//! bluetoothctl in one-shot mode exits 0 even when a connection fails, so
//! Sonata said "Done" for connections that never happened, and it only
//! listed devices BlueZ already knew. Here every call reports what really
//! happened. Calls block: run them off the main loop (run_async), apart
//! from register_agent().

use gio::prelude::*;
use glib::Variant;
use sonata_i18n::tr;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

pub const BUS_NAME: &str = "org.bluez";
pub const DEVICE: &str = "org.bluez.Device1";
pub const ADAPTER: &str = "org.bluez.Adapter1";
/// a real connection is still there this long after Connect
pub const STAY_S: f64 = 4.0;

#[derive(Clone, Debug, PartialEq, Default)]
pub struct BtDevice {
    pub mac: String,
    pub name: String,
    pub paired: bool,
    pub connected: bool,
    pub icon: String,
    pub trusted: bool,
    pub path: String,
}

impl BtDevice {
    /// system::BtDevice (what Settings and the menu bar list)
    pub fn basic(&self) -> sonata_backend::system::BtDevice {
        sonata_backend::system::BtDevice { mac: self.mac.clone(), name: self.name.clone(), paired: self.paired, connected: self.connected }
    }
}

type Props = BTreeMap<String, Variant>;
/// path -> interface -> properties
type Objects = BTreeMap<String, BTreeMap<String, Props>>;

fn bus() -> Result<gio::DBusConnection, glib::Error> {
    gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE)
}

/// An a{sv} (or a{s...}) dictionary.
fn dict(v: &Variant) -> BTreeMap<String, Variant> {
    (0..v.n_children())
        .filter_map(|i| {
            let e = v.child_value(i);
            let k = e.child_value(0).str()?.to_string();
            let val = e.child_value(1);
            Some((k, val.as_variant().unwrap_or(val)))
        })
        .collect()
}

fn objects(bus: &gio::DBusConnection) -> Result<Objects, glib::Error> {
    let res = bus.call_sync(Some(BUS_NAME), "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, Some(glib::VariantTy::new("(a{oa{sa{sv}}})").unwrap()), gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE)?;
    let all = res.child_value(0);
    Ok((0..all.n_children())
        .filter_map(|i| {
            let e = all.child_value(i);
            let path = e.child_value(0).str()?.to_string();
            let ifs = e.child_value(1);
            let ifs = (0..ifs.n_children())
                .filter_map(|j| {
                    let f = ifs.child_value(j);
                    Some((f.child_value(0).str()?.to_string(), dict(&f.child_value(1))))
                })
                .collect();
            Some((path, ifs))
        })
        .collect())
}

fn s(p: &Props, k: &str) -> String {
    p.get(k).and_then(|v| v.str().map(str::to_string)).unwrap_or_default()
}

fn b(p: &Props, k: &str) -> bool {
    p.get(k).and_then(|v| v.get::<bool>()).unwrap_or(false)
}

pub fn available() -> bool {
    bus().and_then(|b| objects(&b)).is_ok_and(|o| o.values().any(|ifs| ifs.contains_key(ADAPTER)))
}

pub fn adapter_path(bus: &gio::DBusConnection) -> Option<String> {
    objects(bus).ok()?.into_iter().find(|(_, ifs)| ifs.contains_key(ADAPTER)).map(|(p, _)| p)
}

/// The devices of GetManagedObjects; nameless ones (BLE beacons) left out
/// with `named_only`.
pub fn devices_of(objs: &Objects, named_only: bool) -> Vec<BtDevice> {
    let mut out: Vec<BtDevice> = objs
        .iter()
        .filter_map(|(path, ifs)| {
            let d = ifs.get(DEVICE)?;
            if named_only && s(d, "Name").is_empty() && !b(d, "Paired") {
                return None;
            }
            let mut name = s(d, "Alias");
            if name.is_empty() {
                name = s(d, "Name");
            }
            if name.is_empty() {
                name = s(d, "Address");
            }
            Some(BtDevice { mac: s(d, "Address"), name, paired: b(d, "Paired"), connected: b(d, "Connected"), icon: s(d, "Icon"), trusted: b(d, "Trusted"), path: path.clone() })
        })
        .collect();
    out.sort_by_key(|x| (!x.connected, !x.paired, x.name.to_lowercase()));
    out
}

/// Known and nearby devices.
pub fn devices(named_only: bool) -> Vec<BtDevice> {
    bus().and_then(|b| objects(&b)).map(|o| devices_of(&o, named_only)).unwrap_or_default()
}

/// What a BlueZ error says, in words people know.
pub fn message(e: &glib::Error) -> String {
    let mut e = e.clone();
    gio::DBusError::strip_remote_error(&mut e);
    let text = e.message().to_string();
    let known: [(&str, fn() -> String); 7] = [
        ("AuthenticationFailed", || tr!("Pairing failed")),
        ("AuthenticationCanceled", || tr!("Pairing was cancelled")),
        ("ConnectionAttemptFailed", || tr!("The device didn't answer (is it on and in range?)")),
        ("page-timeout", || tr!("The device didn't answer (is it on and in range?)")),
        ("br-connection-profile-unavailable", || tr!("No audio/input service on this device")),
        ("InProgress", || tr!("Already connecting")),
        ("AlreadyConnected", || tr!("Already connected")),
    ];
    if let Some((_, f)) = known.iter().find(|(k, _)| text.contains(k)) {
        return f();
    }
    if text.is_empty() { tr!("That didn't work") } else { text }
}

/// What connect() needs from BlueZ (tests pass their own).
pub trait Bus {
    fn device_path(&self, mac: &str) -> Result<Option<String>, glib::Error>;
    fn call(&self, path: &str, iface: &str, method: &str, args: Option<Variant>, timeout_ms: i32) -> Result<(), glib::Error>;
    fn prop(&self, path: &str, name: &str) -> Result<Variant, glib::Error>;
}

pub struct SystemBus(pub gio::DBusConnection);

impl Bus for SystemBus {
    fn device_path(&self, mac: &str) -> Result<Option<String>, glib::Error> {
        Ok(objects(&self.0)?.into_iter().find(|(_, ifs)| ifs.get(DEVICE).is_some_and(|d| s(d, "Address").eq_ignore_ascii_case(mac))).map(|(p, _)| p))
    }
    fn call(&self, path: &str, iface: &str, method: &str, args: Option<Variant>, timeout_ms: i32) -> Result<(), glib::Error> {
        self.0.call_sync(Some(BUS_NAME), path, iface, method, args.as_ref(), None, gio::DBusCallFlags::NONE, timeout_ms, gio::Cancellable::NONE).map(|_| ())
    }
    fn prop(&self, path: &str, name: &str) -> Result<Variant, glib::Error> {
        let r = self.0.call_sync(Some(BUS_NAME), path, "org.freedesktop.DBus.Properties", "Get", Some(&(DEVICE, name).to_variant()), Some(glib::VariantTy::new("(v)").unwrap()), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE)?;
        Ok(r.child_value(0).as_variant().unwrap_or(r))
    }
}

fn pbool(bus: &dyn Bus, path: &str, name: &str) -> Result<bool, glib::Error> {
    Ok(bus.prop(path, name)?.get::<bool>().unwrap_or(false))
}

/// Pair (if needed), trust, connect -- judged by the end result: Pair can
/// report an error for a device that then connects fine (the Xbox
/// controller: "Pairing failed", yet connected), so a pairing error only
/// counts when the device doesn't end up connected.
pub fn connect(mac: &str) -> (bool, String) {
    match bus() {
        Ok(b) => connect_on(&SystemBus(b), mac, STAY_S),
        Err(e) => (false, message(&e)),
    }
}

pub fn connect_on(bus: &dyn Bus, mac: &str, stay_s: f64) -> (bool, String) {
    let path = match bus.device_path(mac) {
        Ok(Some(p)) => p,
        Ok(None) => return (false, tr!("Device not found")),
        Err(e) => return (false, message(&e)),
    };
    let mut pair_error = None;
    match pbool(bus, &path, "Paired") {
        Ok(false) => {
            if let Err(e) = bus.call(&path, DEVICE, "Pair", None, 30000) {
                pair_error = Some(message(&e)); // AlreadyExists, or paired from the device's side...
            }
        }
        Ok(true) => {}
        Err(e) => pair_error = Some(message(&e)),
    }
    // reconnects by itself next time
    if let Ok(false) = pbool(bus, &path, "Trusted") {
        let _ = bus.call(&path, "org.freedesktop.DBus.Properties", "Set", Some((DEVICE, "Trusted", true.to_variant()).to_variant()), 3000);
    }
    let mut connect_error = None;
    match pbool(bus, &path, "Connected") {
        Ok(false) => {
            if let Err(e) = bus.call(&path, DEVICE, "Connect", None, 25000) {
                connect_error = Some(message(&e));
            }
        }
        Ok(true) => {}
        Err(e) => connect_error = Some(message(&e)),
    }
    // after an error the device may still come up by itself (the Xbox
    // controller reconnects a few seconds later): give it longer
    let wait = if pair_error.is_some() || connect_error.is_some() { 20.0 } else { 8.0 };
    let (ok, msg) = settled(&|n| bus.prop(&path, n), wait, stay_s).unwrap_or_else(|e| (false, message(&e)));
    if ok {
        return (true, tr!("Connected"));
    }
    (false, connect_error.or(pair_error).unwrap_or(msg))
}

/// Connect can succeed and the link drop a second later (Xbox controllers
/// do that when the driver doesn't suit them): it only counts when the
/// device is connected with its services, and still is `stay_s` later.
pub fn settled(prop: &dyn Fn(&str) -> Result<Variant, glib::Error>, wait: f64, stay_s: f64) -> Result<(bool, String), glib::Error> {
    let get = |n: &str| -> Result<bool, glib::Error> { Ok(prop(n)?.get::<bool>().unwrap_or(false)) };
    let deadline = Instant::now() + Duration::from_secs_f64(wait);
    loop {
        // connected, services resolved
        if get("Connected")? && get("ServicesResolved")? {
            break;
        }
        if Instant::now() >= deadline {
            return Ok((false, tr!("The device didn't stay connected")));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let until = Instant::now() + Duration::from_secs_f64(stay_s);
    while Instant::now() < until {
        if !get("Connected")? {
            let icon = prop("Icon").ok().and_then(|v| v.str().map(str::to_string)).unwrap_or_default();
            if icon == "input-gaming" {
                return Ok((false, tr!("It connected, then dropped. Xbox controllers usually need the xpadneo driver (or Bluetooth ERTM turned off) on Linux.")));
            }
            return Ok((false, tr!("It connected, then dropped")));
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok((true, tr!("Connected")))
}

pub fn disconnect(mac: &str) -> (bool, String) {
    let r = (|| -> Result<(bool, String), glib::Error> {
        let b = SystemBus(bus()?);
        let Some(path) = b.device_path(mac)? else { return Ok((false, tr!("Device not found"))) };
        b.call(&path, DEVICE, "Disconnect", None, 10000)?;
        Ok((!pbool(&b, &path, "Connected")?, tr!("Disconnected")))
    })();
    r.unwrap_or_else(|e| (false, message(&e)))
}

/// Remove a paired device (macOS: "Forget This Device").
pub fn forget(mac: &str) -> (bool, String) {
    let r = (|| -> Result<(bool, String), glib::Error> {
        let conn = bus()?;
        let b = SystemBus(conn.clone());
        let (Some(path), Some(adapter)) = (b.device_path(mac)?, adapter_path(&conn)) else { return Ok((false, tr!("Device not found"))) };
        let p = glib::variant::ObjectPath::try_from(path).map_err(|_| glib::Error::new(gio::IOErrorEnum::InvalidArgument, "path"))?;
        b.call(&adapter, ADAPTER, "RemoveDevice", Some((p,).to_variant()), 5000)?;
        Ok((true, tr!("Removed")))
    })();
    r.unwrap_or_else(|e| (false, message(&e)))
}

/// Look for nearby devices (BlueZ stops when this process's bus connection
/// goes: the system bus connection is the process's shared one, kept).
pub fn discovery(on: bool) {
    if let Ok(conn) = bus() {
        if let Some(adapter) = adapter_path(&conn) {
            let _ = SystemBus(conn).call(&adapter, ADAPTER, if on { "StartDiscovery" } else { "StopDiscovery" }, None, 5000);
        }
    }
}

// -- pairing agent ----------------------------------------------------------------------------
// Without an agent BlueZ can't answer a device's "confirm pairing?" and
// Pair fails ("Pairing failed") even for devices that need no code
// (controllers, headphones). Sonata's agent says yes to those; devices that
// want a PIN or passkey typed in aren't supported yet (they fail clearly).
pub const AGENT_PATH: &str = "/io/github/vinioliveiras/sonata2/btagent";
pub const AGENT_XML: &str = r#"<node><interface name="org.bluez.Agent1">
<method name="Release"/>
<method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
<method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
<method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
<method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
<method name="RequestAuthorization"><arg type="o" direction="in"/></method>
<method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
<method name="Cancel"/>
</interface></node>"#;

/// The agent's answer to a method: Err(the D-Bus error) for typed codes,
/// yes to confirmations and authorizations (the user clicked Connect).
pub fn agent_answer(method: &str) -> Result<(), (&'static str, &'static str)> {
    match method {
        "RequestPinCode" | "RequestPasskey" => Err(("org.bluez.Error.Rejected", "Typing a code isn't supported yet")),
        _ => Ok(()),
    }
}

thread_local! {
    static AGENT: std::cell::RefCell<Option<gio::RegistrationId>> = const { std::cell::RefCell::new(None) };
}

/// Register Sonata's agent (once per process; call from the GTK thread,
/// which answers BlueZ's calls). True when registered.
pub fn register_agent() -> bool {
    if AGENT.with(|a| a.borrow().is_some()) {
        return true;
    }
    let r = (|| -> Result<gio::RegistrationId, glib::Error> {
        let conn = bus()?;
        let info = gio::DBusNodeInfo::for_xml(AGENT_XML)?;
        let iface = info.lookup_interface("org.bluez.Agent1").ok_or_else(|| glib::Error::new(gio::IOErrorEnum::Failed, "agent"))?;
        let id = conn
            .register_object(AGENT_PATH, &iface)
            .method_call(|_c, _sender, _path, _iface, method, _params, inv| match agent_answer(method) {
                Err((name, msg)) => inv.return_dbus_error(name, msg),
                Ok(()) => inv.return_value(None),
            })
            .build()?;
        let path = glib::variant::ObjectPath::try_from(AGENT_PATH.to_string()).unwrap();
        let mgr = |method: &str, args: Variant| conn.call_sync(Some("org.bluez"), "/org/bluez", "org.bluez.AgentManager1", method, Some(&args), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE);
        if let Err(e) = mgr("RegisterAgent", (path.clone(), "DisplayYesNo").to_variant()) {
            let _ = conn.unregister_object(id);
            return Err(e);
        }
        // another default agent (a desktop's): ours still answers ours
        let _ = mgr("RequestDefaultAgent", (path,).to_variant());
        Ok(id)
    })();
    match r {
        Ok(id) => {
            AGENT.with(|a| *a.borrow_mut() = Some(id));
            true
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    fn objs() -> Objects {
        let dev = |addr: &str, name: Option<&str>, paired: bool, connected: bool| -> BTreeMap<String, Props> {
            let mut p = Props::new();
            p.insert("Address".into(), addr.to_variant());
            if let Some(n) = name {
                p.insert("Name".into(), n.to_variant());
                p.insert("Alias".into(), n.to_variant());
            }
            p.insert("Paired".into(), paired.to_variant());
            p.insert("Connected".into(), connected.to_variant());
            BTreeMap::from([(DEVICE.to_string(), p)])
        };
        BTreeMap::from([
            ("/a".into(), dev("AA", Some("zeta"), true, false)),
            ("/b".into(), dev("BB", Some("Alpha"), false, false)),
            ("/c".into(), dev("CC", None, false, false)), // a beacon
            ("/d".into(), dev("DD", Some("Headphones"), true, true)),
        ])
    }

    #[test]
    fn devices_connected_then_paired_and_named() {
        let got: Vec<String> = devices_of(&objs(), true).into_iter().map(|d| d.name).collect();
        assert_eq!(got, ["Headphones", "zeta", "Alpha"]);
        assert_eq!(devices_of(&objs(), false).len(), 4);
    }

    /// The saved Xbox controller "connected" and dropped a second later;
    /// Sonata reported it connected.
    #[test]
    fn connection_that_drops_is_not_success() {
        let t0 = Instant::now();
        let prop = |n: &str| -> Result<Variant, glib::Error> {
            Ok(match n {
                "Icon" => "input-gaming".to_variant(),
                _ => (t0.elapsed() < Duration::from_millis(500)).to_variant(), // dropped after half a second
            })
        };
        let (ok, msg) = settled(&prop, 8.0, 1.0).unwrap();
        assert!(!ok);
        assert!(msg.contains("xpadneo"), "{msg}");
    }

    struct Fake {
        props: HashMap<&'static str, bool>,
        calls: RefCell<Vec<String>>,
    }

    impl Bus for Fake {
        fn device_path(&self, _mac: &str) -> Result<Option<String>, glib::Error> {
            Ok(Some("/dev".into()))
        }
        fn call(&self, _p: &str, _i: &str, method: &str, _a: Option<Variant>, _t: i32) -> Result<(), glib::Error> {
            self.calls.borrow_mut().push(method.into());
            if method == "Pair" {
                return Err(glib::Error::new(gio::IOErrorEnum::Failed, "org.bluez.Error.AuthenticationFailed"));
            }
            Ok(())
        }
        fn prop(&self, _p: &str, name: &str) -> Result<Variant, glib::Error> {
            Ok(self.props[name].to_variant())
        }
    }

    /// "Pairing failed" was shown while the Xbox controller connected.
    #[test]
    fn pairing_error_but_connected_is_success() {
        let bus = Fake { props: HashMap::from([("Paired", false), ("Trusted", true), ("Connected", true), ("ServicesResolved", true)]), calls: RefCell::default() };
        let (ok, msg) = connect_on(&bus, "AA", 0.3);
        assert!(ok, "{msg}");
        assert_eq!(*bus.calls.borrow(), ["Pair"]);
        assert_eq!(message(&glib::Error::new(gio::IOErrorEnum::Failed, "org.bluez.Error.AuthenticationFailed")), tr!("Pairing failed"));
        assert_eq!(message(&glib::Error::new(gio::IOErrorEnum::Failed, "")), tr!("That didn't work"));
    }

    #[test]
    fn pairing_agent_answers_confirmations() {
        assert!(agent_answer("RequestConfirmation").is_ok());
        assert!(agent_answer("AuthorizeService").is_ok());
        assert_eq!(agent_answer("RequestPinCode").unwrap_err().0, "org.bluez.Error.Rejected");
        assert!(gio::DBusNodeInfo::for_xml(AGENT_XML).is_ok());
    }
}
