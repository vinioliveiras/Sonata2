"""Switch User (Vini: with another user added, the lock screen had no way to
switch to them). macOS's fast user switching: this session stays as it is,
locked, while another user logs in or goes back to theirs.

- That user already has a session on this seat: the login screen asks the
  password once, then logind unlocks it and brings it to the front
  (UnlockSession, ActivateSession) -- no second prompt on its lock screen.
- Otherwise the login screen comes up on a free VT:
  - Sonata's (greetd): a second greetd, sonata-greeter-switch.service,
    set up by tools/greeter-setup.sh with a polkit rule that lets the
    local user start and stop it; its greeter (SONATA_GREETER_SWITCH=1)
    stops it again once you pick a user who is already logged in;
  - GDM: gdmflexiserver; LightDM: dm-tool switch-to-greeter.

    available("vini")       -> other users exist and there's a way to reach them
    show_login_screen()     -> True when a login screen is on its way
    activate(session_id)    -> True when logind switched to it
"""
import os
import shutil
import subprocess

from gi.repository import Gio, GLib

SWITCH_UNIT = "sonata-greeter-switch.service"
SWITCH_ENV = "SONATA_GREETER_SWITCH"
SWITCH_UNIT_FILE = "/etc/systemd/system/" + SWITCH_UNIT
LOGIN1 = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")
GRAPHICAL = {"wayland", "x11", "mir"}


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _call(bus, dest, path, iface, method, args=None, reply=None, timeout=3000):
    return bus.call_sync(dest, path, iface, method, args, GLib.VariantType(reply) if reply else None,
                         Gio.DBusCallFlags.NONE, timeout, None)


def sessions(bus=None) -> list:
    """This seat's sessions: [{"id", "user", "uid", "type", "class", "active", "path"}]."""
    out = []
    try:
        bus = bus or _bus()
        listed = _call(bus, *LOGIN1, "ListSessions", None, "(a(susso))").unpack()[0]
    except GLib.Error:
        return out
    for sid, uid, user, seat, path in listed:
        try:
            props = _call(bus, "org.freedesktop.login1", path, "org.freedesktop.DBus.Properties", "GetAll",
                          GLib.Variant("(s)", ("org.freedesktop.login1.Session",)), "(a{sv})").unpack()[0]
        except GLib.Error:
            continue
        out.append({"id": sid, "user": user, "uid": uid, "seat": seat or (props.get("Seat") or ("",))[0],
                    "type": props.get("Type", ""), "class": props.get("Class", ""),
                    "active": bool(props.get("Active")), "state": props.get("State", ""),
                    "remote": bool(props.get("Remote")), "path": path})
    return out


def graphical_session(user: str, listed: list, seat: str = "seat0"):
    """`user`'s desktop session on this seat (the one to bring back), or None."""
    for s in listed:
        if s["user"] == user and s["class"] == "user" and s["type"] in GRAPHICAL and not s["remote"] \
                and s["seat"] == seat and s["state"] != "closing":
            return s
    return None


def greeter_session(listed: list, seat: str = "seat0"):
    for s in listed:
        if s["class"] == "greeter" and s["seat"] == seat and s["state"] != "closing":
            return s
    return None


def activate(session_id: str, bus=None) -> bool:
    try:
        _call(bus or _bus(), *LOGIN1, "ActivateSession", GLib.Variant("(s)", (session_id,)))
        return True
    except GLib.Error as e:
        print(f"sonata2: switch user: can't activate session {session_id}: {e.message}", flush=True)
        return False


def unlock(session_id: str, bus=None) -> bool:
    """logind's Unlock for that session: its lock screen goes away (the
    login screen checked the password already -- Vini: asked twice)."""
    try:
        _call(bus or _bus(), *LOGIN1, "UnlockSession", GLib.Variant("(s)", (session_id,)))
        return True
    except GLib.Error as e:
        print(f"sonata2: switch user: can't unlock session {session_id}: {e.message}", flush=True)
        return False


def resume(session_id: str, bus=None) -> bool:
    """Back to a running session, its password just checked: unlocked, then
    in front -- the password asked once."""
    unlock(session_id, bus)                      # (each asks for the bus itself: an error is caught there)
    return activate(session_id, bus)


def login_method(exists=os.path.exists, which=shutil.which):
    """How this system shows a login screen beside a running session:
    "sonata" (the switch greetd), "gdm", "lightdm" or None."""
    if exists(SWITCH_UNIT_FILE):
        return "sonata"
    if which("gdmflexiserver"):
        return "gdm"
    if which("dm-tool"):
        return "lightdm"
    return None


def _unit(verb: str, bus=None) -> bool:
    """Start or stop the switch greetd through systemd (the polkit rule lets
    the local user and the greeter do it without a password)."""
    try:
        _call(bus or _bus(), "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
              "org.freedesktop.systemd1.Manager", "StartUnit" if verb == "start" else "StopUnit",
              GLib.Variant("(ss)", (SWITCH_UNIT, "replace")), "(o)", 10000)
        return True
    except GLib.Error as e:
        print(f"sonata2: switch user: {verb} {SWITCH_UNIT}: {e.message}", flush=True)
        return False


def show_login_screen(method=None, bus=None) -> bool:
    """The login screen on a free VT, this session left locked behind it."""
    method = method or login_method()
    if method == "sonata":
        bus = bus or _bus()
        waiting = greeter_session(sessions(bus))
        if waiting is not None:                      # already up (switched away from it): back to it
            return activate(waiting["id"], bus)
        return _unit("start", bus)                   # (greetd switches to its VT itself)
    cmd = {"gdm": ["gdmflexiserver"], "lightdm": ["dm-tool", "switch-to-greeter"]}.get(method)
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def leave_switch_greeter(bus=None) -> None:
    """The switch greeter's job is done (its user went back to a running
    session): stop it, so no second login screen keeps running unseen."""
    if os.environ.get(SWITCH_ENV):
        _unit("stop", bus)


def others(me: str, everyone) -> list:
    return [u for u in everyone if getattr(u, "name", u) != me]


def available(me: str, everyone=None, method=None, listed=None) -> bool:
    """Show Switch User: another user exists, and either a login screen can
    be shown or that user is already logged in (switching to them works)."""
    if everyone is None:
        from .shell.greeter import users
        everyone = users()
    rest = others(me, everyone)
    if not rest:
        return False
    if (method if method is not None else login_method()):
        return True
    listed = sessions() if listed is None else listed
    return any(graphical_session(getattr(u, "name", u), listed) for u in rest)


def switch(me: str, everyone=None, method=None, bus=None) -> bool:
    """Switch User from the lock screen: the login screen; without one, the
    other user's running session (the first one found)."""
    method = method if method is not None else login_method()
    if method:
        return show_login_screen(method, bus)
    if everyone is None:
        from .shell.greeter import users
        everyone = users()
    listed = sessions(bus)
    for u in others(me, everyone):
        s = graphical_session(getattr(u, "name", u), listed)
        if s is not None:
            return activate(s["id"], bus)
    return False
