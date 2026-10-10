"""USB devices plugged in while the screen is locked are blocked (GNOME's
USB protection): with USBGuard running, the lock screen sets its
InsertedDevicePolicy to "block" when it locks and puts the previous one
back when it unlocks. Devices that were plugged in before stay as they are.

Never left blocked by mistake: the previous policy is kept in a file
while locked; the menu bar puts it back if the lock screen went away
without doing it (restore_if_stale). Without USBGuard nothing happens.

Vini: installed, yet it never worked. Installing USBGuard isn't enough:
its services aren't started, its default (ImplicitPolicyTarget=block,
no rules) blocks every device that isn't allowed -- the keyboard too --
and polkit lets only root change the policy. status() tells which step is
missing; Security & Privacy > USB runs setup_command() in Terminal:

    status() -> "missing" | "off" | "denied" | "ready"
    setup_command(family) -> the commands, or None (unknown distro)
"""
import os
import shlex
import shutil

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


def _bus_call(method: str, args, reply: str):
    bus = _bus()
    if bus is None:
        return None
    try:
        return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", method,
                             args, GLib.VariantType(reply), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None).unpack()[0]
    except GLib.Error:
        return None


def available() -> bool:
    """usbguard-dbus running, or started by the bus when asked."""
    if _bus_call("NameHasOwner", GLib.Variant("(s)", (BUS,)), "(b)"):
        return True
    return BUS in (_bus_call("ListActivatableNames", None, "(as)") or [])


def installed() -> bool:
    return any(shutil.which(p) or os.path.exists(os.path.join("/usr/bin", p)) for p in ("usbguard", "usbguard-daemon"))


DENIED = ("NotAuthorized", "AccessDenied", "PermissionDenied", "not authorized", "Not authorized")


def status() -> str:
    """missing: not installed; off: its services aren't running; denied:
    polkit won't let this user change the policy; ready."""
    if not available():
        return "off" if installed() else "missing"
    bus = _bus()
    try:
        cur = bus.call_sync(BUS, PATH, IFACE, "getParameter", GLib.Variant("(s)", (PARAM,)),
                            GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None).unpack()[0]
        # set to what it is (changes nothing): the lock screen's own call, allowed or not
        bus.call_sync(BUS, PATH, IFACE, "setParameter", GLib.Variant("(ss)", (PARAM, cur)),
                      GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
    except (GLib.Error, AttributeError) as e:
        msg = getattr(e, "message", str(e))
        return "denied" if any(d in msg for d in DENIED) else "off"
    return "ready"


POLKIT_RULE = "/etc/polkit-1/rules.d/70-sonata2-usbguard.rules"
RULE = """// Sonata: the lock screen blocks new USB devices (Security & Privacy > USB)
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.usbguard") == 0 && /Parameter$/.test(action.id) &&
        subject.active && subject.local && (subject.isInGroup("wheel") || subject.isInGroup("sudo"))) {
        return polkit.Result.YES;
    }
});"""
DAEMON_CONF = "/etc/usbguard/usbguard-daemon.conf"
# as root: no rules needed (every device allowed while unlocked), the
# policy changeable by the user's session, the services on now and at boot
SETUP = "\n".join((
    "set -e",
    "touch /etc/usbguard/rules.conf && chmod 600 /etc/usbguard/rules.conf",
    f"sed -i 's/^ImplicitPolicyTarget=.*/ImplicitPolicyTarget=allow/' {DAEMON_CONF}",
    "mkdir -p /etc/polkit-1/rules.d",
    f"printf '%s\\n' {shlex.quote(RULE)} > {POLKIT_RULE}",
    "systemctl enable usbguard.service usbguard-dbus.service",
    "systemctl restart usbguard.service usbguard-dbus.service",
    "echo 'USB protection is ready.'"))


def setup_command(family: str):
    from . import system
    install = system.install_command("usbguard", family)
    return f"{install} && sudo sh -c {shlex.quote(SETUP)}" if install else None


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
