"""Drag icon that hangs from the pointer like a pendulum: the grab point is
the pivot, moving the pointer swings the icon (pendulum driven by the
pointer's acceleration), and the swing dies out slowly and naturally
(Vini's request; not a macOS effect). Wayland only reports the pointer over
our own surfaces, so callers feed() positions from a drop controller;
elsewhere the icon keeps its last swing until it settles.

    icon = ui.drag.hang(drag, paintable, size)      # in "drag-begin"
    icon.feed(x, surface_key)                        # DropControllerMotion "motion"
"""
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

PERIOD = 0.95           # s, one full swing
DAMPING = 1.6           # 1/s: low, so it keeps swinging a while
MAX_ANGLE = math.radians(55)
MAX_ACCEL = 20000.0     # px/s^2 (a jerk must not spin it)
AIR = 3.0               # 1/s: air drag on the icon
PIVOT = 0.18            # grab point, from the top of the icon (fraction of its size)


class HangingIcon(Gtk.Widget):
    def __init__(self, paintable, size: int):
        super().__init__(can_target=False)
        self.paintable, self.size = paintable, size
        self.length = size * (1 - PIVOT)                 # pivot to the far edge
        self.box = int(2 * self.length + size * 0.2)
        self.set_size_request(self.box, self.box)
        self.theta = self.omega = 0.0                    # radians; + = bottom swung right
        self.x = None                                    # latest pointer x
        self.key = None
        self.px = self.pv = None                         # pointer x / velocity last frame
        self.quiet = 0.0
        self._tick = 0
        self._t = None

    @property
    def hotspot(self) -> int:
        return self.box // 2

    def feed(self, x: float, key=None) -> None:
        if key is not self.key:                          # another surface: new coordinates
            self.key, self.px, self.pv = key, None, None
        self.x = x
        self.quiet = 0.0
        if not self._tick:
            self._t = None
            self._tick = self.add_tick_callback(self._step)

    def _step(self, _w, clock) -> bool:
        t = clock.get_frame_time() / 1e6
        dt = min(0.04, t - self._t) if self._t else 1 / 60
        self._t = t
        accel, vel = 0.0, 0.0
        if self.x is not None:
            if self.px is not None:
                v = (self.x - self.px) / dt
                if self.pv is not None:
                    accel = max(-MAX_ACCEL, min(MAX_ACCEL, (v - self.pv) / dt))
                self.pv = 0.5 * v + 0.5 * (self.pv if self.pv is not None else v)
                vel = self.pv
            self.px = self.x
        w0 = 2 * math.pi / PERIOD
        # pendulum on a moving pivot: gravity, the pivot's acceleration, damping
        # (+ air drag: at a steady speed the icon trails a little behind)
        push = accel + AIR * vel
        alpha = -w0 * w0 * math.sin(self.theta) - push / (self.length * 4) * math.cos(self.theta) \
            - DAMPING * self.omega
        self.omega += alpha * dt
        self.theta = max(-MAX_ANGLE, min(MAX_ANGLE, self.theta + self.omega * dt))
        self.queue_draw()
        self.quiet += dt
        if self.quiet > 0.4 and abs(self.theta) < 0.002 and abs(self.omega) < 0.01:
            self.theta = self.omega = 0.0
            self._tick = 0
            return False
        return True

    def do_snapshot(self, snap) -> None:
        c = self.box / 2
        snap.save()
        snap.translate(Graphene.Point().init(c, c))       # pivot: the grab point
        snap.rotate(-math.degrees(self.theta))           # GTK rotates clockwise
        ar = self.paintable.get_intrinsic_aspect_ratio() or 1.0     # photos keep their shape
        w, h = (self.size, self.size / ar) if ar > 1 else (self.size * ar, self.size)
        snap.translate(Graphene.Point().init(-w / 2, -h * PIVOT))
        self.paintable.snapshot(snap, w, h)
        snap.restore()


def hang(drag, paintable, size: int) -> HangingIcon:
    icon = HangingIcon(paintable, size)
    Gtk.DragIcon.get_for_drag(drag).set_child(icon)
    drag.set_hotspot(icon.hotspot, icon.hotspot)
    return icon


def follow(widget, get_icon) -> Gtk.DropControllerMotion:
    """Feed the hanging icon from every drag motion over `widget` (its whole
    area, drop targets included). get_icon() returns the current icon or None."""
    ctl = Gtk.DropControllerMotion()

    def motion(_c, x, _y):
        icon = get_icon()
        if icon is not None:
            icon.feed(x, widget)
    ctl.connect("motion", motion)
    ctl.connect("enter", motion)
    widget.add_controller(ctl)
    return ctl
