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
    gamemode.Watcher()                # the menu bar: watches Wayfire, writes the state
    gamemode.boosted()                # Automatic is running at High Performance right now

Automatic energy mode (power-profiles-daemon's "balanced"): while any
window is full screen (a game, a video), the profile is raised to
"performance"; when the last one leaves full screen, is minimized or closes, back to
"balanced". A mode the user picked themselves is never touched."""
import os

from gi.repository import Gio, GLib

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-fullscreen")
# set while Automatic energy mode was raised to High Performance for a
# fullscreen window (kept across a menu bar restart)
BOOST = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-power-boost")
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


def boosted() -> bool:
    return os.path.exists(BOOST)


def set_boosted(on: bool) -> None:
    try:
        if on:
            open(BOOST, "w").close()
        elif os.path.exists(BOOST):
            os.remove(BOOST)
    except OSError:
        pass


class PowerBoost:
    """High Performance while anything is full screen, in Automatic mode.
    get/set: the profile reader and writer (backend.system), run(fn, cb):
    off the main loop (powerprofilesctl is slow)."""

    def __init__(self, get=None, set_=None, run=None):
        from .backend import system
        self.get = get or system.power_profile_fast
        self.set = set_ or system.set_power_profile
        self.run = run or (lambda fn, cb: system.run_async(fn, cb))
        self.full = False

    def update(self, any_full: bool) -> None:
        if any_full == self.full:
            return
        self.full = any_full
        if any_full and not boosted():
            def raise_(cur):
                if cur == "balanced" and self.full:          # Automatic only, still full screen
                    set_boosted(True)
                    self.run(lambda: self.set("performance"), None)
            self.run(self.get, raise_)
        elif not any_full and boosted():
            def lower(cur):
                set_boosted(False)
                if cur == "performance" and not self.full:  # the user didn't pick another mode
                    self.run(lambda: self.set("balanced"), None)
            self.run(self.get, lower)


# -- lighter effects while a game fills the graphics card ----------------------------------------
# Blur and window animations each need graphics memory of their own; with a
# game holding nearly all of it they only failed (skipped frames) and left
# other apps less. While a full-screen game plays and the card is >= 90 %
# full, they're turned off in the running session; back when the game leaves
# full screen. Only the session's own copy of the config is changed (made
# again at login), so a crash can't keep them off.
LIGHT_AT = 0.90
LIGHT = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-light-effects.json")
LIGHT_OPTIONS = (("blur", "blur_by_default", 'app_id is "sonata2-no-blur"'),
                 ("animate", "open_animation", "none"), ("animate", "close_animation", "none"),
                 ("animate", "minimize_animation", "none"))


def light_effects_wanted() -> bool:
    """Settings > Displays > Graphics (on by default)."""
    from . import config, gpu
    return bool(config.load(gpu.NAME, gpu.DEFAULTS).get("light_effects", True))


class LightEffects:
    """get/set: wfconfig.wayfire_get / runtime_set (tests pass fakes)."""

    def __init__(self, get=None, set_=None, wanted=None):
        from . import wfconfig
        self.get = get or wfconfig.wayfire_get
        self.set = set_ or wfconfig.runtime_set
        self.wanted = wanted or light_effects_wanted
        self.full = False
        self.ratio = 0.0

    def on(self) -> bool:
        return os.path.exists(LIGHT)

    def update(self, full=None, used=None, total=None) -> None:
        if full is not None:
            self.full = full
        if used is not None and total:
            self.ratio = used / total
        if not self.full:
            self._restore()                              # the game left full screen
        elif self.ratio >= LIGHT_AT and not self.on() and self.wanted():
            self._lighten()

    def _lighten(self) -> None:
        import json
        saved = {f"{s}/{k}": self.get(s, k, "") for s, k, _v in LIGHT_OPTIONS}
        try:
            with open(LIGHT, "w", encoding="utf-8") as f:
                json.dump(saved, f)
        except OSError:
            return
        for s, k, v in LIGHT_OPTIONS:
            self.set(s, k, v)

    def _restore(self) -> None:
        import json
        if not self.on():
            return
        try:
            with open(LIGHT, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        for s, k, _v in LIGHT_OPTIONS:
            if f"{s}/{k}" not in saved:
                continue                                 # unreadable save: leave the session copy
            # "" = the config never set it: drop our value so Wayfire's default is back
            self.set(s, k, saved[f"{s}/{k}"] or None)
        try:
            os.remove(LIGHT)
        except OSError:
            pass


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
        self.power = PowerBoost()
        self.power.full = boosted()          # restarted while raised: lowered once nothing is full screen
        self.light = LightEffects()          # (fed the card's memory by the menu bar's VramWatch)
        self.light.update(full=False)        # restarted while lightened: effects back until a game fills the card
        if self.ipc.available:
            self.ipc.watch(["view-focused", "view-fullscreen", "view-unmapped", "view-mapped", "view-minimized"],
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
        # a full-screen window that's minimized isn't being played: Balanced again
        # until it's back (Alt+Tab away keeps it: the game still runs on screen)
        self.power.update(any(isinstance(v, dict) and v.get("fullscreen") and v.get("mapped", True)
                              and not v.get("minimized") and v.get("role", "toplevel") == "toplevel"
                              for v in views))
        self.light.update(full=full)
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
