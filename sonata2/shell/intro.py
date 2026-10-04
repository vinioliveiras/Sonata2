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
ARRIVE_MS = 120           # after a restart: from all ready to sliding in (first frames drawn)
RESTART_TIMEOUT_S = 6     # never wait longer than this for the other one
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


def ready_file(name: str) -> str:
    """Left by a restarted component once it's ready (restart waits for both)."""
    return f"{RESTART_MARK}.{name}"


def wait(callback, name: str = None) -> None:
    """callback() once the intro is over (right away without one). After a
    restart: once every restarted component is ready -- the Dock and the
    menu bar slide in together (Vini); `name` tells restart this one is."""
    if not pending():
        if not restarting():
            GLib.idle_add(lambda: (callback(), False)[1])
            return
        if name:
            try:
                open(ready_file(name), "w").close()
            except OSError:
                pass
        _when_gone(RESTART_MARK, restarting, lambda: GLib.timeout_add(ARRIVE_MS, lambda: (callback(), False)[1]),
                   RESTART_TIMEOUT_S)
        return
    _when_gone(MARK, pending, callback, TIMEOUT_S)


def _when_gone(path, still, callback, timeout_s) -> None:
    state = {"done": False}

    def fire(*_a):
        if not state["done"]:
            state["done"] = True
            mon.cancel()
            callback()
        return False
    mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", lambda _m, _f, _o, ev: ev == Gio.FileMonitorEvent.DELETED and fire())
    state["mon"] = mon                         # keep it alive
    GLib.timeout_add_seconds(timeout_s, fire)
    if not still():                            # removed before the monitor existed: no event will come
        GLib.idle_add(fire)
