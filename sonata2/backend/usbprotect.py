"""USB devices plugged in while the screen is locked are blocked (GNOME's
USB protection): with USBGuard running, the lock screen sets its
InsertedDevicePolicy to "block" when it locks and puts the previous one
back when it unlocks. Devices that were plugged in before stay as they are.

Never left blocked by mistake: the previous policy is kept in a file
while locked; the menu bar puts it back if the lock screen went away
without doing it (restore_if_stale). Without USBGuard nothing happens
(Security & Privacy says how to get it)."""
import os

from gi.repository import Gio, GLib

BUS, PATH, IFACE = "org.usbguard1", "/org/usbguard1", "org.usbguard1"
PARAM = "InsertedDevicePolicy"
TIMEOUT_MS = 2000


def marker() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-usb-blocked")


def _bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        return None


def available() -> bool:
    bus = _bus()
    if bus is None:
        return False
    try:
        r = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                          GLib.Variant("(s)", (BUS,)), GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS,
                          None)
        return bool(r.unpack()[0])
    except GLib.Error:
        return False


def _set(value: str):
    """Sets the policy; returns the previous one (None: failed)."""
    bus = _bus()
    if bus is None:
        return None
    try:
        r = bus.call_sync(BUS, PATH, IFACE, "setParameter", GLib.Variant("(ss)", (PARAM, value)),
                          GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return r.unpack()[0]
    except GLib.Error as e:
        print(f"sonata2: USB protection: {e.message}", flush=True)
        return None


def block() -> bool:
    """The screen locked: new devices blocked (the previous policy kept)."""
    if os.path.exists(marker()) or not available():
        return False
    prev = _set("block")
    if prev is None:
        return False
    try:
        with open(marker(), "w") as f:
            f.write(prev or "apply-policy")
    except OSError:
        _set(prev or "apply-policy")                 # can't remember it: never leave it blocked
        return False
    return True


def restore() -> bool:
    """Unlocked: the policy it had before."""
    try:
        with open(marker()) as f:
            prev = f.read().strip() or "apply-policy"
    except OSError:
        return False
    if prev == "block":
        prev = "apply-policy"                        # (never "restored" into blocking for good)
    if _set(prev) is None:
        return False
    try:
        os.unlink(marker())
    except OSError:
        pass
    return True


def restore_if_stale(is_locked) -> bool:
    """The menu bar's check: blocked, yet nothing holds the lock -> put it back."""
    if os.path.exists(marker()) and not is_locked():
        return restore()
    return False
