"""Battery / power events: plugging in, charging, level and Low Power
Mode changes are pushed (UPower and power-profiles-daemon D-Bus signals),
so every battery icon updates at once; readers still use system.battery(),
on_ac() and power_profile_fast().

    power.watch(callback)          # callback() after any change (debounced)
    power.icon_name(pct, status, ac, profile)   # the one battery icon rule
"""
from gi.repository import Gio, GLib

_PATHS = (("org.freedesktop.UPower", "/org/freedesktop/UPower"),
          ("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice"),
          ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
          ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"))
_subs = []


def watch(callback) -> bool:
    """False when the system bus isn't reachable (callers keep polling)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        return False
    state = {"src": 0}

    def fire():
        state["src"] = 0
        callback()
        return False

    def changed(*_a):
        if not state["src"]:
            state["src"] = GLib.timeout_add(150, fire)      # a burst of property changes -> one update
    for name, path in _PATHS:
        _subs.append(bus.signal_subscribe(name, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                          path, None, Gio.DBusSignalFlags.NONE, changed))
    return True


def icon_name(pct, status: str, ac: bool, profile=None) -> str:
    """Big Sur battery states: charging (bolt), on power but holding --
    full or a charge limit -- (plug), Low Power Mode (yellow), low (red in
    the icon); `missing` when there's a battery but no reading."""
    if pct is None:
        return "sonata-battery-missing-symbolic"
    level = min(100, max(0, (int(pct) + 5) // 10 * 10))
    if status == "Charging":
        state = "-charging"
    elif ac:
        state = "-plugged"
    elif profile == "power-saver":
        state = "-saver"
    else:
        state = ""
    return f"sonata-battery-{level}{state}-symbolic"
