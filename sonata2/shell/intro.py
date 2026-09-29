"""The login intro: from the login screen to the desktop without a jump.

sonata-session leaves a marker file before Wayfire starts. While it is
there, the Dock and the menu bar start out of sight, and the welcome
screen (welcome.py: blurred wallpaper, the user's picture, a progress
bar -- the login screen's look) covers the display until the shell is
ready. Then it fades, and the Dock and menu bar slide in from their edges.
A restart of Sonata has no marker: everything shows at once."""
import os

from gi.repository import Gio, GLib

MARK = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "sonata2-intro")
TIMEOUT_S = 12            # never keep the Dock hidden longer than this


def pending() -> bool:
    return os.path.exists(MARK)


def finish() -> None:
    try:
        os.unlink(MARK)
    except OSError:
        pass


def wait(callback) -> None:
    """callback() once the intro is over (right away without one)."""
    if not pending():
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


def slide_layer_margin(win, edge, distance: int, ms: int = 420) -> None:
    """Slide a layer surface in from beyond `edge`: its margin on that edge
    goes from -distance to 0 (ease-out)."""
    from . import layer
    LS = layer.layer_shell()
    if not LS:
        return
    import time
    start = time.monotonic()

    # a timer, not the frame clock: a surface that is entirely off-screen
    # gets no frame callbacks, so a tick callback would never run
    def step():
        t = min(1.0, (time.monotonic() - start) * 1000 / ms)
        eased = 1 - (1 - t) ** 3
        LS.set_margin(win, edge, int(round(-distance * (1 - eased))))
        return t < 1
    GLib.timeout_add(16, step)
