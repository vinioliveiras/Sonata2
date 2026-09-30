"""Game controllers drive the desktop (like Steam's desktop mode), for any
controller Linux knows (Xbox, PlayStation, 8BitDo...). Runs in the menu
bar process; nothing runs while no controller is connected.

    Left stick     pointer (LT slower, RT faster)     A / cross     click (hold: drag)
    Right stick    scroll                             X / triangle  right click
    D-pad          arrow keys                         B / circle    Esc
    LB / RB        app switcher (A picks, B closes)   Y / square    Launchpad
    Start          Spotlight                          View/Select   Mission Control
    Guide (Xbox / PS)  Mission Control; pressed 5 times quickly: on / off

Off by default. Games keep the controller: it is never grabbed, and the desktop control
pauses by itself while a fullscreen window has the focus (gamemode.py)
or while Steam runs (its own desktop mode would double every press).
Actions are named (ACTIONS) so a future SteamOS-style theme can bind the
same buttons to its own navigation.

Settings: ~/.config/sonata2/gamepad.json {"enabled", "speed", "scroll",
"pause_steam"} (Settings > Game Controllers)."""
import os
import sys
import time

from gi.repository import Gio, GLib

from .. import config, gamemode
from . import evdev as E
from .vpointer import BTN_LEFT, BTN_RIGHT, VirtualPointer, key

DEFAULTS = {"enabled": False, "speed": 1.0, "scroll": 1.0, "pause_steam": True}
DEAD = 0.18                 # stick dead zone
TICK_MS = 8                 # while a stick is pushed (~120 Hz)
MAX_PX_S = 1500.0           # pointer speed at full tilt (speed 1.0)
SCROLL_PX_S = 900.0
TOGGLE_TAPS = 5             # Guide pressed this many times quickly: on / off
TAP_GAP_S = 0.5             # max time between those presses

# button -> action (named for other front ends)
BUTTONS = {E.BTN_SOUTH: "primary", E.BTN_NORTH: "secondary", E.BTN_EAST: "back", E.BTN_WEST: "launchpad",
           E.BTN_TL: "switch_prev", E.BTN_TR: "switch_next", E.BTN_START: "spotlight",
           E.BTN_SELECT: "mission", E.BTN_MODE: "guide", E.KEY_HOMEPAGE: "guide",
           E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}
ACTIONS = set(BUTTONS.values())
# what each button does, as Settings lists it (Xbox / PlayStation names)
LEGEND = [("Left stick", "Pointer (LT slower, RT faster)"),
          ("Right stick", "Scroll"), ("A / Cross", "Click (hold to drag)"), ("X / Triangle", "Right-click"),
          ("B / Circle", "Back (Esc)"), ("Y / Square", "Launchpad"), ("LB / RB", "Switch apps"),
          ("D-pad", "Arrow keys"), ("Start / Options", "Spotlight"), ("View / Share", "Mission Control"),
          ("Xbox / PS button", "Mission Control · 5× quickly: on / off")]
ARROWS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}


def log(*a) -> None:
    print("sonata2-gamepad:", *a, file=sys.stderr, flush=True)


def steam_running() -> bool:
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
                    if f.read().strip() in ("steam", "steamwebhelper"):
                        return True
            except OSError:
                continue
    return False


def curve(v: float) -> float:
    """Dead zone, then a gentle curve (fine control near the centre)."""
    a = abs(v)
    if a < DEAD:
        return 0.0
    a = (a - DEAD) / (1 - DEAD)
    return (a ** 2.2) * (1 if v > 0 else -1)


class Gamepads:
    def __init__(self, app=None, switcher=None):
        self.app = app
        self.switcher = switcher              # () -> the app switcher window, or None
        self.cfg = config.load("gamepad", DEFAULTS)
        self._cfg_mon = config.watch("gamepad", self._config_changed)
        self.pads = {}                        # path -> Gamepad
        self.axes = {}                        # (path, code) -> -1..1 / 0..1
        self.vp = None
        self._tick_src = 0
        self._acc = [0.0, 0.0]
        self._last = 0.0
        self._taps = []                       # Guide presses of the current burst
        self._tap_src = 0
        self._steam = False
        self._steam_src = 0
        self._dev_mon = Gio.File.new_for_path("/dev/input").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._dev_mon.connect("changed", lambda *_a: GLib.timeout_add(600, lambda: (self.scan(), False)[1]))
        self._fs_mon = gamemode.watch(lambda _on: None)      # keeps gamemode.active() fresh
        self.scan()

    # -- devices --------------------------------------------------------------------------------
    def scan(self) -> None:
        # listened to even while off: the Guide burst turns it on
        for path in E.find_gamepads():
            if path not in self.pads:
                try:
                    self.pads[path] = E.Gamepad(path, self._event)
                    log(self.pads[path].name or path)
                except OSError as e:          # not readable: logind gives controllers to the user
                    log(f"can't read {path} ({e})")
        names = {pad.name for pad in self.pads.values() if pad.name}
        for path in E.find_guide_devices(names):          # the Xbox button over Bluetooth
            if path not in self.pads:
                try:
                    self.pads[path] = E.Gamepad(path, self._event)
                    log(f"{self.pads[path].name} (Xbox / home button)")
                except OSError as e:
                    log(f"can't read {path} ({e})")
        if self.pads and not self._steam_src:
            self._steam = steam_running()
            self._steam_src = GLib.timeout_add_seconds(5, self._check_steam)

    def _check_steam(self) -> bool:
        if not self.pads:
            self._steam_src = 0
            return False
        self._steam = steam_running()
        return True

    def _config_changed(self, *_a) -> None:
        self.cfg = config.load("gamepad", DEFAULTS)
        self.scan()

    # -- state ------------------------------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return (not self.cfg["enabled"] or (self._steam and self.cfg.get("pause_steam", True))
                or gamemode.active())

    def _pointer(self) -> VirtualPointer:
        if self.vp is None or not self.vp.ok:
            self.vp = VirtualPointer()
        return self.vp

    # -- events -------------------------------------------------------------------------------------
    def _event(self, pad, typ, code, value) -> None:
        if typ is None:                                   # unplugged
            self.pads.pop(pad.path, None)
            for k in [k for k in self.axes if k[0] == pad.path]:
                del self.axes[k]
            return
        if typ == E.EV_ABS:
            if code in (E.ABS_HAT0X, E.ABS_HAT0Y):        # d-pad as a hat: arrows
                if value and not self.paused:
                    self.action({(E.ABS_HAT0X, -1): "left", (E.ABS_HAT0X, 1): "right",
                                 (E.ABS_HAT0Y, -1): "up", (E.ABS_HAT0Y, 1): "down"}[(code, 1 if value > 0 else -1)],
                                True)
                return
            self.axes[(pad.path, code)] = pad.norm(code, value)
            self._tick_soon()
            return
        name = BUTTONS.get(code)
        if name is None:
            return
        pressed = value != 0
        if name == "guide":
            if value == 1:
                self._guide_tap()
            return
        if value == 2:                                    # key repeat
            return
        if not self.paused:
            self.action(name, pressed)

    def _guide_tap(self) -> None:
        """One press: Mission Control, once the burst is over. Five quick
        presses: desktop control on / off (works while off or paused)."""
        now = time.monotonic()
        if self._taps and now - self._taps[-1] > TAP_GAP_S:
            self._taps = []
        self._taps.append(now)
        if self._tap_src:
            GLib.source_remove(self._tap_src)
            self._tap_src = 0
        if len(self._taps) >= TOGGLE_TAPS:
            self._taps = []
            self.set_enabled(not self.cfg["enabled"])
            return
        self._tap_src = GLib.timeout_add(int(TAP_GAP_S * 1000), self._burst_over)

    def _burst_over(self) -> bool:
        self._tap_src = 0
        taps, self._taps = len(self._taps), []
        if taps == 1 and not self.paused:
            self.action("mission", True)
        return False

    def set_enabled(self, on: bool) -> None:
        log("desktop control", "on" if on else "off")
        self.cfg = {**self.cfg, "enabled": on}
        config.save("gamepad", self.cfg)
        if not on:
            self.axes.clear()
        self._notify("Controller: desktop control on" if on else "Controller: desktop control off")

    def _notify(self, text: str) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", ("Sonata", 0, "input-gaming", text, "", [], {}, 2500)),
                     None, Gio.DBusCallFlags.NONE, 1000, None, None, None)
        except GLib.Error:
            pass

    # -- actions ------------------------------------------------------------------------------------
    def action(self, name: str, pressed: bool) -> None:
        sw = self.switcher() if self.switcher else None
        switching = sw is not None and sw.get_visible() and not sw.panel.has_css_class("closing")
        if name == "primary":
            if switching:
                if pressed:
                    sw._switch()
                return
            self._pointer().button(BTN_LEFT, pressed)
        elif name == "secondary":
            self._pointer().button(BTN_RIGHT, pressed)
        elif not pressed:
            return
        elif name == "back":
            sw._close() if switching else key("Escape")
        elif name in ARROWS:
            if switching and name in ("left", "right"):
                sw.step(-1 if name == "left" else 1)
            else:
                key(ARROWS[name])
        elif name in ("switch_next", "switch_prev") and self.app is not None:
            self.app.activate_action("switcher", GLib.Variant("s", "next" if name == "switch_next" else "prev"))
        elif name in ("launchpad", "spotlight"):
            from ..__main__ import self_command
            try:
                GLib.spawn_async(self_command().split() + [name], flags=GLib.SpawnFlags.SEARCH_PATH)
            except GLib.Error:
                pass
        elif name == "mission":
            key("F3")                                     # Wayfire's scale (Mission Control) binding

    # -- sticks -------------------------------------------------------------------------------------
    def _tick_soon(self) -> None:
        if not self._tick_src and not self.paused and self._pushed():
            self._last = time.monotonic()
            self._tick_src = GLib.timeout_add(TICK_MS, self._tick)

    def _pushed(self) -> bool:
        return any(abs(v) >= DEAD for (_p, c), v in self.axes.items() if c not in E.TRIGGERS)

    def _axis(self, code) -> float:
        vals = [v for (_p, c), v in self.axes.items() if c == code]
        return max(vals, key=abs) if vals else 0.0

    def _tick(self) -> bool:
        now = time.monotonic()
        dt, self._last = min(0.05, now - self._last), now
        if self.paused or not self._pushed():
            self._tick_src = 0
            self._acc = [0.0, 0.0]
            return False
        speed = float(self.cfg.get("speed", 1.0))
        slow = max(self._axis(E.ABS_Z), self._axis(E.ABS_BRAKE))
        fast = max(self._axis(E.ABS_RZ), self._axis(E.ABS_GAS))
        mult = speed * (1 - 0.65 * slow) * (1 + 1.2 * fast)
        vp = self._pointer()
        dx = curve(self._axis(E.ABS_X)) * MAX_PX_S * mult * dt
        dy = curve(self._axis(E.ABS_Y)) * MAX_PX_S * mult * dt
        self._acc[0] += dx
        self._acc[1] += dy
        mx, my = int(self._acc[0]), int(self._acc[1])
        if mx or my:
            self._acc[0] -= mx
            self._acc[1] -= my
            vp.move(mx, my)
        scroll = SCROLL_PX_S * float(self.cfg.get("scroll", 1.0)) * dt
        sx = curve(self._axis(E.ABS_RX)) * scroll
        sy = curve(self._axis(E.ABS_RY)) * scroll
        if sx or sy:
            vp.scroll(sx, sy)
        return True

    def stop(self) -> None:
        if self._tap_src:
            GLib.source_remove(self._tap_src)
            self._tap_src = 0
        for pad in list(self.pads.values()):
            pad.close()
        if self.vp is not None:
            self.vp.close()
