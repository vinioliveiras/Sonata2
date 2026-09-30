"""Game controllers drive the desktop (like Steam's desktop mode), for any
controller Linux knows (Xbox, PlayStation, 8BitDo...). Runs in the menu
bar process; nothing runs while no controller is connected.

    Left stick     pointer (LT slower, RT faster)     A / cross     click (hold: drag)
    Right stick    scroll                             X / triangle  right click
    D-pad          arrow keys                         B / circle    Esc
    LB / RB        app switcher (A picks, B closes)   Y / square    Launchpad
    Start          Spotlight                          View/Select   Mission Control
    Guide (Xbox / PS) held 1 s: pause / resume

Games keep the controller: it is never grabbed, and the desktop control
pauses by itself while a fullscreen window has the focus (gamemode.py)
or while Steam runs (its own desktop mode would double every press).
Actions are named (ACTIONS) so a future SteamOS-style theme can bind the
same buttons to its own navigation.

Settings: ~/.config/sonata2/gamepad.json {"enabled", "speed"}."""
import os
import time

from gi.repository import Gio, GLib

from .. import config, gamemode
from . import evdev as E
from .vpointer import BTN_LEFT, BTN_RIGHT, VirtualPointer, key

DEFAULTS = {"enabled": True, "speed": 1.0}
DEAD = 0.18                 # stick dead zone
TICK_MS = 8                 # while a stick is pushed (~120 Hz)
MAX_PX_S = 1500.0           # pointer speed at full tilt (speed 1.0)
SCROLL_PX_S = 900.0
HOLD_S = 1.0                # Guide held this long: pause / resume

# button -> action (named for other front ends)
BUTTONS = {E.BTN_SOUTH: "primary", E.BTN_NORTH: "secondary", E.BTN_EAST: "back", E.BTN_WEST: "launchpad",
           E.BTN_TL: "switch_prev", E.BTN_TR: "switch_next", E.BTN_START: "spotlight",
           E.BTN_SELECT: "mission", E.BTN_MODE: "guide",
           E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}
ACTIONS = set(BUTTONS.values())
ARROWS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}


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
        self._paused_by_user = False
        self._guide_down = None
        self._steam = False
        self._steam_src = 0
        self._dev_mon = Gio.File.new_for_path("/dev/input").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._dev_mon.connect("changed", lambda *_a: GLib.timeout_add(600, lambda: (self.scan(), False)[1]))
        self._fs_mon = gamemode.watch(lambda _on: None)      # keeps gamemode.active() fresh
        self.scan()

    # -- devices --------------------------------------------------------------------------------
    def scan(self) -> None:
        if not self.cfg["enabled"]:
            for pad in list(self.pads.values()):
                pad.close()
            return
        for path in E.find_gamepads():
            if path not in self.pads:
                try:
                    self.pads[path] = E.Gamepad(path, self._event)
                    print(f"sonata2-gamepad: {self.pads[path].name or path}")
                except OSError as e:          # not readable: logind gives controllers to the user
                    print(f"sonata2-gamepad: can't read {path} ({e})")
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
        return self._paused_by_user or self._steam or gamemode.active()

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
        if name == "guide":                               # held: pause / resume (works while paused)
            if pressed:
                self._guide_down = time.monotonic()
                GLib.timeout_add(int(HOLD_S * 1000), self._guide_held)
            else:
                if self._guide_down and time.monotonic() - self._guide_down < HOLD_S and not self.paused:
                    self.action("mission", True)
                self._guide_down = None
            return
        if value == 2:                                    # key repeat
            return
        if not self.paused:
            self.action(name, pressed)

    def _guide_held(self) -> bool:
        if self._guide_down and time.monotonic() - self._guide_down >= HOLD_S - 0.05:
            self._guide_down = None
            self._paused_by_user = not self._paused_by_user
            self._notify("Controller: desktop control paused" if self._paused_by_user
                         else "Controller: desktop control on")
        return False

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
        sx = curve(self._axis(E.ABS_RX)) * SCROLL_PX_S * dt
        sy = curve(self._axis(E.ABS_RY)) * SCROLL_PX_S * dt
        if sx or sy:
            vp.scroll(sx, sy)
        return True

    def stop(self) -> None:
        for pad in list(self.pads.values()):
            pad.close()
        if self.vp is not None:
            self.vp.close()
