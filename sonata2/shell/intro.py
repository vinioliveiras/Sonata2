"""The login intro: from the login screen to the desktop without a jump.

sonata-session leaves a marker file before Wayfire starts. While it is
there, the Dock and the menu bar start out of sight, and the welcome
screen (welcome.py: blurred wallpaper, the user's picture, a progress
bar -- the login screen's look) covers the display until the shell is
ready. Then it fades, and the Dock and menu bar slide in from their edges.
A restart of Sonata (Restart Sonata, `sonata2 restart`) leaves its own
marker: the old Dock and menu bar slide away (leave_on_signal), the new
ones start out of sight and slide in as soon as each is ready, and the
wallpaper never goes away (the new one comes up under the old one first)."""
import os

from gi.repository import Gio, GLib

MARK = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "sonata2-intro")
RESTART_MARK = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "sonata2-restarting")
TIMEOUT_S = 12            # never keep the Dock hidden longer than this
ARRIVE_MS = 220           # after a restart: from ready to sliding in (its first frames drawn)
_leaving = []


def pending() -> bool:
    return os.path.exists(MARK)


def restarting() -> bool:
    return os.path.exists(RESTART_MARK)


def entering() -> bool:
    """Start out of sight and slide in (login intro, or a restart of Sonata)."""
    return pending() or restarting()


def leave_on_signal(callback) -> None:
    """callback() when `sonata2 restart` asks this component to go
    (SIGUSR1): it slides away before the new one comes."""
    import signal
    if not _leaving:
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, lambda: ([cb() for cb in list(_leaving)], True)[1])
    _leaving.append(callback)


def finish() -> None:
    try:
        os.unlink(MARK)
    except OSError:
        pass


def wait(callback) -> None:
    """callback() once the intro is over (right away without one; a
    moment after this component is ready after a restart)."""
    if not pending():
        if restarting():
            GLib.timeout_add(ARRIVE_MS, lambda: (callback(), False)[1])
        else:
            GLib.idle_add(lambda: (callback(), False)[1])
        return
    state = {"done": False}

    def fire(*_a):
        if not state["done"]:
            state["done"] = True
            mon.cancel()
            callback()
        return False
    mon = Gio.File.new_for_path(MARK).monitor_file(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", lambda _m, _f, _o, ev: ev == Gio.FileMonitorEvent.DELETED and fire())
    state["mon"] = mon                         # keep it alive
    GLib.timeout_add_seconds(TIMEOUT_S, fire)
    if not pending():                          # removed before the monitor existed: no event will come
        GLib.idle_add(fire)
