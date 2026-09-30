"""Bluetooth through BlueZ's D-Bus API (org.bluez), not bluetoothctl.

bluetoothctl in one-shot mode exits 0 even when a connection fails ("Failed
to connect: ..."), so Sonata said "Done" for connections that never
happened, and it only listed devices BlueZ already knew. Here every call
reports what really happened:

    devices()                    -> [BtDevice] (known + nearby, while discovering)
    connect(mac)                 -> (ok, message): pairs and trusts first if needed,
                                    then checks the device really is connected
    disconnect(mac)              -> (ok, message)
    discovery(on)                   look for nearby devices (Settings > Bluetooth open)

Calls block: run them off the main loop (system.run_async)."""
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from gi.repository import Gio, GLib

BUS_NAME = "org.bluez"
DEVICE = "org.bluez.Device1"
ADAPTER = "org.bluez.Adapter1"


@dataclass
class BtDevice:
    mac: str
    name: str
    paired: bool
    connected: bool
    icon: str = ""
    trusted: bool = False
    path: str = ""


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _objects(bus) -> dict:
    res = bus.call_sync(BUS_NAME, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                        GLib.VariantType.new("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 5000, None)
    return res.unpack()[0]


def available() -> bool:
    try:
        return any(ADAPTER in ifs for ifs in _objects(_bus()).values())
    except GLib.Error:
        return False


def adapter_path(bus=None) -> Optional[str]:
    try:
        objs = _objects(bus or _bus())
    except GLib.Error:
        return None
    return next((p for p, ifs in sorted(objs.items()) if ADAPTER in ifs), None)


def devices(named_only: bool = True) -> List[BtDevice]:
    """Known and nearby devices; nameless ones (BLE beacons) are left out."""
    try:
        objs = _objects(_bus())
    except GLib.Error:
        return []
    out = []
    for path, ifs in objs.items():
        d = ifs.get(DEVICE)
        if not d:
            continue
        name = d.get("Alias") or d.get("Name") or ""
        if named_only and (not d.get("Name") and not d.get("Paired")):
            continue
        out.append(BtDevice(d.get("Address", ""), name or d.get("Address", ""), bool(d.get("Paired")),
                            bool(d.get("Connected")), d.get("Icon", ""), bool(d.get("Trusted")), path))
    return sorted(out, key=lambda x: (not x.connected, not x.paired, x.name.lower()))


def _device_path(bus, mac: str) -> Optional[str]:
    for path, ifs in _objects(bus).items():
        if DEVICE in ifs and ifs[DEVICE].get("Address", "").upper() == mac.upper():
            return path
    return None


def _prop(bus, path, name):
    return bus.call_sync(BUS_NAME, path, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (DEVICE, name)), GLib.VariantType.new("(v)"),
                         Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]


def _message(e: GLib.Error) -> str:
    Gio.DBusError.strip_remote_error(e)
    text = e.message or ""
    known = {"AuthenticationFailed": "Pairing failed", "AuthenticationCanceled": "Pairing was cancelled",
             "ConnectionAttemptFailed": "The device didn't answer (is it on and in range?)",
             "page-timeout": "The device didn't answer (is it on and in range?)",
             "br-connection-profile-unavailable": "No audio/input service on this device",
             "InProgress": "Already connecting", "AlreadyConnected": "Already connected"}
    for k, v in known.items():
        if k in text:
            return v
    return text or "That didn't work"


def connect(mac: str) -> Tuple[bool, str]:
    try:
        bus = _bus()
        path = _device_path(bus, mac)
        if path is None:
            return False, "Device not found"
        if not _prop(bus, path, "Paired"):
            bus.call_sync(BUS_NAME, path, DEVICE, "Pair", None, None, Gio.DBusCallFlags.NONE, 30000, None)
        if not _prop(bus, path, "Trusted"):      # reconnects by itself next time
            bus.call_sync(BUS_NAME, path, "org.freedesktop.DBus.Properties", "Set",
                          GLib.Variant("(ssv)", (DEVICE, "Trusted", GLib.Variant("b", True))), None,
                          Gio.DBusCallFlags.NONE, 3000, None)
        bus.call_sync(BUS_NAME, path, DEVICE, "Connect", None, None, Gio.DBusCallFlags.NONE, 25000, None)
        return _settled(bus, path)
    except GLib.Error as e:
        return False, _message(e)


STAY_S = 4.0            # a real connection is still there this long after Connect


def _settled(bus, path) -> Tuple[bool, str]:
    """Connect can succeed and the link drop a second later (Xbox controllers
    do that when the driver doesn't suit them): it only counts when the
    device is connected with its services, and still is STAY_S later."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:          # connected, services resolved
        if _prop(bus, path, "Connected") and _prop(bus, path, "ServicesResolved"):
            break
        time.sleep(0.25)
    else:
        return False, "The device didn't stay connected"
    until = time.monotonic() + STAY_S
    while time.monotonic() < until:
        if not _prop(bus, path, "Connected"):
            try:
                icon = _prop(bus, path, "Icon")
            except GLib.Error:
                icon = ""
            if icon == "input-gaming":
                return False, ("It connected, then dropped. Xbox controllers usually need the xpadneo "
                               "driver (or Bluetooth ERTM turned off) on Linux.")
            return False, "It connected, then dropped"
        time.sleep(0.25)
    return True, "Connected"


def disconnect(mac: str) -> Tuple[bool, str]:
    try:
        bus = _bus()
        path = _device_path(bus, mac)
        if path is None:
            return False, "Device not found"
        bus.call_sync(BUS_NAME, path, DEVICE, "Disconnect", None, None, Gio.DBusCallFlags.NONE, 10000, None)
        return (not _prop(bus, path, "Connected")), "Disconnected"
    except GLib.Error as e:
        return False, _message(e)


def forget(mac: str) -> Tuple[bool, str]:
    """Remove a paired device (macOS: "Forget This Device")."""
    try:
        bus = _bus()
        path, adapter = _device_path(bus, mac), adapter_path(bus)
        if path is None or adapter is None:
            return False, "Device not found"
        bus.call_sync(BUS_NAME, adapter, ADAPTER, "RemoveDevice", GLib.Variant("(o)", (path,)), None,
                      Gio.DBusCallFlags.NONE, 5000, None)
        return True, "Removed"
    except GLib.Error as e:
        return False, _message(e)


_discovery_bus = None


def discovery(on: bool) -> None:
    """Look for nearby devices (BlueZ stops when this process's bus
    connection goes, so it's kept here)."""
    global _discovery_bus
    try:
        bus = _discovery_bus or _bus()
        _discovery_bus = bus
        adapter = adapter_path(bus)
        if adapter:
            bus.call_sync(BUS_NAME, adapter, ADAPTER, "StartDiscovery" if on else "StopDiscovery", None, None,
                          Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error:
        pass
