"""Fullscreen first: while a fullscreen window (a game, a video) has the
focus, Sonata steps back -- its periodic work pauses (menu bar polling...)
and, when Feral GameMode (gamemoded) is installed, the fullscreen app is
registered with it (CPU governor, scheduling and I/O priority for the
game). Alt+Tab to a normal window and Sonata works as usual again; back
to the fullscreen window and it has the priority again.

The menu bar process watches Wayfire (focus and fullscreen events) and
publishes the state in $XDG_RUNTIME_DIR/sonata2-fullscreen ("1"/"0");
every Sonata process can follow it:

    gamemode.active()                 # True while fullscreen has the focus
    gamemode.watch(callback)          # callback(active), keep the returned monitor
    gamemode.Watcher()                # the menu bar: watches Wayfire, writes the state"""
import os

from gi.repository import Gio, GLib

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-fullscreen")
GAMEMODE = ("com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode")


def active() -> bool:
    try:
        with open(STATE, encoding="ascii") as f:
            return f.read(1) == "1"
    except OSError:
        return False


def watch(callback):
    """callback(active) whenever it changes. Returns the monitor (keep it)."""
    mon = Gio.File.new_for_path(STATE).monitor_file(Gio.FileMonitorFlags.NONE, None)
    last = {"v": active()}

    def changed(*_a):
        v = active()
        if v != last["v"]:
            last["v"] = v
            callback(v)
    mon.connect("changed", changed)
    return mon


class Watcher:
    """Lives in the menu bar process (it already talks to Wayfire)."""

    def __init__(self):
        from .wl.wfipc import WayfireIPC
        self.ipc = WayfireIPC()
        self.listeners = []
        self.active = False
        self.pid = None                  # the fullscreen app registered with GameMode
        self._src = 0
        self._write(False)
        if self.ipc.available:
            self.ipc.watch(["view-focused", "view-fullscreen", "view-unmapped", "view-mapped"],
                           lambda _ev: self._soon())
            self._soon()

    def _soon(self) -> None:
        if not self._src:                # a burst of events: one look
            self._src = GLib.timeout_add(150, self._check)

    def _check(self) -> bool:
        self._src = 0
        try:
            views = self.ipc.call("window-rules/list-views") or []
        except Exception:
            views = []
        front = next((v for v in views if isinstance(v, dict) and v.get("activated")), None)
        full = bool(front and front.get("fullscreen") and front.get("role", "toplevel") == "toplevel")
        pid = front.get("pid") if full else None
        if full != self.active or pid != self.pid:
            self._gamemode(self.pid, False)
            self.active, self.pid = full, pid
            self._gamemode(pid, True)
            self._write(full)
            for cb in list(self.listeners):
                cb(full)
        return False

    @staticmethod
    def _write(on: bool) -> None:
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="ascii") as f:
                f.write("1" if on else "0")
            os.replace(tmp, STATE)
        except OSError:
            pass

    @staticmethod
    def _gamemode(pid, register: bool) -> None:
        """GameMode's priority for the fullscreen app (nothing without gamemoded)."""
        if not pid or pid <= 0:
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(*GAMEMODE, "RegisterGame" if register else "UnregisterGame", GLib.Variant("(i)", (pid,)),
                     GLib.VariantType.new("(i)"), Gio.DBusCallFlags.NO_AUTO_START if not register
                     else Gio.DBusCallFlags.NONE, 1000, None, None)
        except GLib.Error:
            pass
