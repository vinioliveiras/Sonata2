"""Game controllers drive the desktop (like Steam's desktop mode), for any
controller Linux knows (Xbox, PlayStation, 8BitDo...). Runs in the menu
bar process; nothing runs while no controller is connected.

    Left stick     pointer (LT slower, RT faster)     A / cross     click (hold: drag)
    Right stick    scroll                             X / triangle  right click
    D-pad          arrow keys                         B / circle    Esc
    LB / RB        app switcher (A picks, B closes)   Y / square    Launchpad
    Start          Search                             View/Select   Overview
    Guide (Xbox / PS)  Game Bar (also in games; Settings can make it Overview again)
    LB + RB + L3 + R3 held together: desktop control on / off

While the Game Bar is open the controller drives it instead (Vini: like
the Xbox Game Bar), even with desktop control off and in a game: D-pad /
left stick move between its controls, A presses, B or Guide closes, LB /
RB jump between the bar and the widgets. The controllers are grabbed while
it's open, so the game behind doesn't also get the presses.

Off by default. Games keep the controller: it is never grabbed, and the desktop control
pauses by itself while a fullscreen window has the focus (gamemode.py)
or while Steam runs (its own desktop mode would double every press).
Actions are named (ACTIONS) so a future SteamOS-style theme can bind the
same buttons to its own navigation.

Settings: ~/.config/sonata2/gamepad.json {"enabled", "speed", "scroll",
"pause_steam", "guide_gamebar"} (Settings > Game Controllers)."""
import os
import sys
import time

from gi.repository import Gio, GLib

from .. import config, gamemode, logs, names
from ..i18n import N_, _
from . import evdev as E
from .vpointer import BTN_LEFT, BTN_RIGHT, VirtualPointer, key

DEFAULTS = {"enabled": False, "speed": 1.0, "scroll": 1.0, "pause_steam": True, "guide_gamebar": True}
DEAD = 0.18                 # stick dead zone
TICK_MS = 8                 # while a stick is pushed (~120 Hz)
MAX_PX_S = 1500.0           # pointer speed at full tilt (speed 1.0)
SCROLL_PX_S = 900.0
# held together: desktop control on / off (Vini: L1 + R1 + L3 + R3 -- no
# game asks for all four at once, and it works the same on every pad)
TOGGLE_CHORD = frozenset((E.BTN_TL, E.BTN_TR, E.BTN_THUMBL, E.BTN_THUMBR))
# the Game Bar with the left stick: a push past this is one step, held it repeats
NAV_PUSH = 0.6
NAV_FIRST_MS = 380
NAV_REPEAT_MS = 140

# button -> action (named for other front ends)
BUTTONS = {E.BTN_SOUTH: "primary", E.BTN_NORTH: "secondary", E.BTN_EAST: "back", E.BTN_WEST: "launchpad",
           E.BTN_TL: "switch_prev", E.BTN_TR: "switch_next", E.BTN_START: "spotlight",
           E.BTN_SELECT: "mission", E.BTN_MODE: "guide", E.KEY_HOMEPAGE: "guide",
           E.BTN_DPAD_UP: "up", E.BTN_DPAD_DOWN: "down", E.BTN_DPAD_LEFT: "left", E.BTN_DPAD_RIGHT: "right"}
ACTIONS = set(BUTTONS.values())
# what each button does, as Settings lists it (Xbox / PlayStation names; shown translated: _(text))
LEGEND = [(N_("Left stick"), N_("Pointer (LT slower, RT faster)")),
          (N_("Right stick"), N_("Scroll")), (N_("A / Cross"), N_("Click (hold to drag)")),
          (N_("X / Triangle"), N_("Right-click")), (N_("B / Circle"), N_("Back (Esc)")),
          (N_("Y / Square"), names.APPS), ("LB / RB", N_("Switch apps")), (N_("D-pad"), N_("Arrow keys")),
          (N_("Start / Options"), names.SEARCH), (N_("View / Share"), names.OVERVIEW),
          (N_("Xbox / PS button"), N_("Game Bar")),
          ("LB + RB + L3 + R3", N_("Desktop control on / off"))]
ARROWS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}


def log(*a, error=False) -> None:
    """Routine lines: detailed logs only; errors always."""
    if error or logs.verbose():
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
    def __init__(self, app=None, switcher=None, gamebar=None):
        self.app = app
        self.switcher = switcher              # () -> the app switcher window, or None
        self.gamebar = gamebar                # () -> the Game Bar window, or None
        self._grabbed = False
        self._nav_dir = None                  # the left stick's direction in the Game Bar
        self._nav_src = 0
        self.cfg = config.load("gamepad", DEFAULTS)
        self._cfg_mon = config.watch("gamepad", self._config_changed)
        self.pads = {}                        # path -> Gamepad
        self.axes = {}                        # (path, code) -> -1..1 / 0..1
        self.vp = None
        self._tick_src = 0
        self._acc = [0.0, 0.0]
        self._last = 0.0
        self._down = set()                    # buttons held now (the on / off chord)
        self._chord_done = False              # fired for this hold: once until let go
        self._steam = False
        self._steam_src = 0
        self._scan_src = 0
        self._held = set()                    # virtual mouse buttons we hold down (drag)
        self._dev_mon = Gio.File.new_for_path("/dev/input").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._dev_mon.connect("changed", lambda *_a: self._scan_soon())
        self._fs_mon = gamemode.watch(lambda _on: None)      # keeps gamemode.active() fresh
        self.scan()

    # -- devices --------------------------------------------------------------------------------
    def _scan_soon(self) -> None:
        """One plug creates several nodes (eventN, jsN, hidraw...): one scan once they settle."""
        if self._scan_src:
            GLib.source_remove(self._scan_src)
        self._scan_src = GLib.timeout_add(600, self._scan_due)

    def _scan_due(self) -> bool:
        self._scan_src = 0
        self.scan()
        return False

    def scan(self) -> None:
        # listened to even while off: the Guide burst turns it on
        for path in E.find_gamepads():
            if path not in self.pads:
                try:
                    self.pads[path] = E.Gamepad(path, self._event)
                    log(self.pads[path].name or path)
                    if self._grabbed:                     # plugged in while the Game Bar is open
                        self.pads[path].grab(True)
                except OSError as e:          # not readable: logind gives controllers to the user
                    log(f"can't read {path} ({e})", error=True)
        names = {pad.name for pad in self.pads.values() if pad.name}
        for path in E.find_guide_devices(names):          # the Xbox button over Bluetooth
            if path not in self.pads:
                try:
                    self.pads[path] = E.Gamepad(path, self._event)
                    log(f"{self.pads[path].name} (Xbox / home button)")
                except OSError as e:
                    log(f"can't read {path} ({e})", error=True)
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
            self._release_held()                          # unplugged mid-drag: never a stuck button
            return
        bar = self._bar_open()
        if bar is not None:                               # the Game Bar has the controller
            if typ == E.EV_KEY and value != 2:
                self._chord(code, value != 0)
            self._to_bar(bar, pad, typ, code, value)
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
        if value == 2:                                    # key repeat
            return
        pressed = value != 0
        self._chord(code, pressed)
        name = BUTTONS.get(code)
        if name is None:
            return
        if name == "guide":
            if pressed and self.cfg.get("guide_gamebar", True) and self.app is not None:
                # Vini: the Xbox / PS button opens the Game Bar -- in games too,
                # with desktop control off (that's when it's needed)
                self.app.activate_action("gamebar", None)
            elif pressed and not self.paused:
                self.action("mission", True)
            return
        if not self.paused:
            self.action(name, pressed)
        elif not pressed and name in ("primary", "secondary"):
            self._release_held()                          # paused mid-drag: still let go

    def _release_held(self) -> None:
        for btn in list(self._held):
            self._button(btn, False)

    def _button(self, btn: int, pressed: bool) -> None:
        if pressed:
            self._held.add(btn)
        elif btn in self._held:
            self._held.discard(btn)
        else:
            return                                        # never pressed by us: nothing to release
        self._pointer().button(btn, pressed)

    def _chord(self, code: int, pressed: bool) -> None:
        """LB + RB + L3 + R3 held together: desktop control on / off (works
        while off or paused); once per hold."""
        if pressed:
            self._down.add(code)
        else:
            self._down.discard(code)
        if not TOGGLE_CHORD <= self._down:
            self._chord_done = False
            return
        if self._chord_done:
            return
        self._chord_done = True
        sw = self.switcher() if self.switcher else None
        if sw is not None and sw.get_visible():
            sw._close()                                   # LB / RB opened it on the way
        self.set_enabled(not self.cfg["enabled"])

    def set_enabled(self, on: bool) -> None:
        log("desktop control", "on" if on else "off")
        self.cfg = {**self.cfg, "enabled": on}
        config.save("gamepad", self.cfg)
        if not on:
            self.axes.clear()
            self._release_held()
        self._notify(_("Controller: desktop control on") if on else _("Controller: desktop control off"))

    def _notify(self, text: str) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", ("Sonata", 0, "input-gaming", text, "", [], {}, 2500)),
                     None, Gio.DBusCallFlags.NONE, 1000, None, None, None)
        except GLib.Error:
            pass

    # -- the Game Bar -------------------------------------------------------------------------------
    def _bar_open(self):
        bar = self.gamebar() if self.gamebar else None
        return bar if bar is not None and bar.is_open() else None

    def grab(self, on: bool) -> None:
        """Every controller to us alone (the Game Bar is open) or back to all."""
        if on == self._grabbed:
            return
        self._grabbed = on
        for pad in list(self.pads.values()):
            if not pad.grab(on):
                log(f"can't {'grab' if on else 'release'} {pad.name or pad.path}")
        if not on:
            self._nav(None)
            self.axes.clear()                             # stale tilts never move the pointer after

    def _to_bar(self, bar, pad, typ, code, value) -> None:
        if typ == E.EV_ABS:
            if code in (E.ABS_HAT0X, E.ABS_HAT0Y):
                if value:
                    bar.pad({(E.ABS_HAT0X, -1): "left", (E.ABS_HAT0X, 1): "right", (E.ABS_HAT0Y, -1): "up",
                             (E.ABS_HAT0Y, 1): "down"}[(code, 1 if value > 0 else -1)])
            elif code in (E.ABS_X, E.ABS_Y):
                self.axes[(pad.path, code)] = pad.norm(code, value)
                self._nav(self._stick_dir())
            return
        if value != 1:                                    # presses only (no repeats, no releases)
            return
        name = BUTTONS.get(code)
        if name in ("primary", "back", "guide", "switch_prev", "switch_next") or name in ARROWS:
            bar.pad(name)

    def _stick_dir(self):
        x, y = self._axis(E.ABS_X), self._axis(E.ABS_Y)
        if max(abs(x), abs(y)) < NAV_PUSH:
            return None
        if abs(x) > abs(y):
            return "right" if x > 0 else "left"
        return "down" if y > 0 else "up"

    def _nav(self, direction) -> None:
        """One step per push of the left stick; held, it repeats."""
        if direction == self._nav_dir:
            return
        self._nav_dir = direction
        if self._nav_src:
            GLib.source_remove(self._nav_src)
            self._nav_src = 0
        if direction is None:
            return
        self._nav_step()
        self._nav_src = GLib.timeout_add(NAV_FIRST_MS, self._nav_held)

    def _nav_step(self) -> None:
        bar = self._bar_open()
        if bar is None or self._nav_dir is None:
            self._nav(None)
            return
        bar.pad(self._nav_dir)

    def _nav_held(self) -> bool:
        self._nav_src = 0                                 # (this one ends here)
        self._nav_step()
        if self._nav_dir is not None:
            self._nav_src = GLib.timeout_add(NAV_REPEAT_MS, self._nav_held)
        return False

    # -- actions ------------------------------------------------------------------------------------
    def action(self, name: str, pressed: bool) -> None:
        sw = self.switcher() if self.switcher else None
        switching = sw is not None and sw.get_visible() and not sw.panel.has_css_class("closing")
        if name == "primary":
            if switching:
                if pressed:
                    sw._switch()
                else:
                    self._button(BTN_LEFT, False)         # pressed before the switcher opened
                return
            self._button(BTN_LEFT, pressed)
        elif name == "secondary":
            self._button(BTN_RIGHT, pressed)
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
            from ..__main__ import self_argv
            try:
                GLib.spawn_async(self_argv() + [name], flags=GLib.SpawnFlags.SEARCH_PATH)
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
        self.grab(False)
        if self._scan_src:
            GLib.source_remove(self._scan_src)
            self._scan_src = 0
        self._release_held()
        for pad in list(self.pads.values()):
            pad.close()
        if self.vp is not None:
            self.vp.close()
