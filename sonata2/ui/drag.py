"""Drag icon that hangs from the pointer: while dragging, the icon swings
like a pendulum held at the grab point -- it leans against the direction of
movement and settles when the pointer stops (Vini's request; not a macOS
effect). Wayland only reports the pointer over our own surfaces, so callers
feed() positions from their drop targets; elsewhere the icon hangs still.

    icon = ui.drag.hang(drag, paintable, size)      # in "drag-begin"
    icon.feed(x, surface_key)                        # from DropTarget "motion"
"""
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

MAX_DEG = 28.0          # furthest lean
LEAN = 0.045            # degrees per px/s of pointer speed
STIFF = 180.0           # spring towards the lean angle
DAMP = 9.0              # damping (a little overshoot, then rest)
SPEED_DECAY = 8.0       # pointer speed fades when motion events stop
PAD = 0.35              # extra room around the icon (the swing needs it)


class HangingIcon(Gtk.Widget):
    def __init__(self, paintable, size: int):
        super().__init__(can_target=False)
        self.paintable, self.size = paintable, size
        self.box = int(size * (1 + 2 * PAD))
        self.set_size_request(self.box, self.box)
        self.angle = self.vel = self.speed = 0.0
        self._last = None               # (key, x, time)
        self._tick = 0
        self._t = None

    @property
    def hotspot(self) -> int:
        return self.box // 2

    def feed(self, x: float, key=None) -> None:
        now = GLib.get_monotonic_time() / 1e6
        if self._last and self._last[0] == key and now > self._last[2]:
            v = (x - self._last[1]) / (now - self._last[2])
            self.speed = 0.6 * self.speed + 0.4 * max(-3000.0, min(3000.0, v))
        self._last = (key, x, now)
        self._run()

    def _run(self) -> None:
        if not self._tick:
            self._t = None
            self._tick = self.add_tick_callback(self._step)

    def _step(self, _w, clock) -> bool:
        t = clock.get_frame_time() / 1e6
        dt = min(0.05, t - self._t) if self._t else 1 / 60
        self._t = t
        self.speed *= math.exp(-SPEED_DECAY * dt)
        target = max(-MAX_DEG, min(MAX_DEG, self.speed * LEAN))   # the bottom trails behind
        self.vel += (STIFF * (target - self.angle) - DAMP * self.vel) * dt
        self.angle += self.vel * dt
        self.queue_draw()
        if abs(self.angle) < 0.05 and abs(self.vel) < 0.05 and abs(self.speed) < 1:
            self.angle = self.vel = self.speed = 0.0
            self._tick = 0
            return False
        return True

    def do_snapshot(self, snap) -> None:
        c = self.box / 2
        snap.save()
        snap.translate(Graphene.Point().init(c, c))       # pivot: the grab point
        snap.rotate(self.angle)
        # the icon hangs below the pivot a little, like a held card
        snap.translate(Graphene.Point().init(-self.size / 2, -self.size * 0.35))
        self.paintable.snapshot(snap, self.size, self.size)
        snap.restore()


def hang(drag, paintable, size: int) -> HangingIcon:
    icon = HangingIcon(paintable, size)
    Gtk.DragIcon.get_for_drag(drag).set_child(icon)
    drag.set_hotspot(icon.hotspot, icon.hotspot)
    return icon
