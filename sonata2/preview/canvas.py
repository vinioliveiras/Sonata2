"""Preview's picture view: draws the picture with a scale and a centre
instead of resizing a Gtk.Picture in a scrolled window, so zooming and
panning only redraw -- nothing is ever laid out again (no size-request
feedback with the window, nothing moves while an alert is up).

- zoom None fits the window (never above 100 %); a number is a scale.
- Ctrl+wheel and pinch zoom around the pointer; wheel steps animate.
- Two-finger scroll / wheel / drag pan a picture larger than the view,
  with inertia after a trackpad flick.
- Double-click: actual size around the click, again: fit.
- set_texture(tex, fade=True) cross-fades (the slideshow)."""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from ..ui import tokens  # noqa: E402

MIN_ZOOM, MAX_ZOOM = 0.1, 8.0          # like the toolbar steps (fit may go below MIN_ZOOM)
WHEEL_STEP = 1.25                 # a wheel notch with Ctrl
FRICTION = 5.0                    # inertia: velocity decays by e^-FRICTION per second


class Canvas(Gtk.Widget):
    __gtype_name__ = "SonataPreviewCanvas"

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, overflow=Gtk.Overflow.HIDDEN, css_classes=["pv-canvas"])
        self.texture = None
        self.zoom = None                  # None: fit
        self.cx = self.cy = 0.0           # the picture point at the centre of the view
        self.on_zoom = None               # callback() after the scale changed
        self._anim = None                 # zoom animation
        self._view = None                 # while animating: (scale, cx, cy) shown
        self._target = None               # the scale an animation heads to (wheel notches add up)
        self._fade = None                 # (old texture, old rect, animation, progress holder)
        self._pointer = None
        self._inertia = 0                 # tick callback id
        self._input()

    # -- geometry ------------------------------------------------------------------------------
    def fit_scale(self) -> float:
        if self.texture is None:
            return 1.0
        w, h = self.get_width(), self.get_height()
        if w <= 1 or h <= 1:
            return 1.0
        return min(1.0, w / self.texture.get_width(), h / self.texture.get_height())

    def scale(self) -> float:
        if self._view:
            return self._view[0]
        return self.fit_scale() if self.zoom is None else self.zoom

    def _centre(self):
        if self._view:
            return self._view[1], self._view[2]
        if self.zoom is None and self.texture is not None:
            return self.texture.get_width() / 2, self.texture.get_height() / 2
        return self.cx, self.cy

    def _clamped(self, s, cx, cy):
        """A centre that keeps the picture covering the view (centred when smaller)."""
        tw, th = self.texture.get_width(), self.texture.get_height()
        w, h = self.get_width(), self.get_height()
        half_w, half_h = w / 2 / s, h / 2 / s
        cx = tw / 2 if tw <= 2 * half_w else min(max(cx, half_w), tw - half_w)
        cy = th / 2 if th <= 2 * half_h else min(max(cy, half_h), th - half_h)
        return cx, cy

    def image_rect(self):
        """Where the picture is drawn (x, y, w, h) in the canvas, or None."""
        if self.texture is None:
            return None
        s = self.scale()
        cx, cy = self._centre()
        return (self.get_width() / 2 - cx * s, self.get_height() / 2 - cy * s,
                self.texture.get_width() * s, self.texture.get_height() * s)

    def pannable(self) -> bool:
        r = self.image_rect()
        return bool(r) and (r[2] > self.get_width() + 0.5 or r[3] > self.get_height() + 0.5)

    def to_image(self, x, y):
        s = self.scale()
        cx, cy = self._centre()
        return cx + (x - self.get_width() / 2) / s, cy + (y - self.get_height() / 2) / s

    # -- state ------------------------------------------------------------------------------------
    def set_texture(self, tex, fade: bool = False, keep_view: bool = False) -> None:
        old, old_rect = self.texture, self.image_rect()
        same_size = (old is not None and tex is not None and old.get_width() == tex.get_width()
                     and old.get_height() == tex.get_height())
        self._stop()
        self.texture = tex
        if not (keep_view and same_size):
            self.zoom = None
        if tex is not None and self.zoom is not None:
            self.cx, self.cy = self._clamped(self.zoom, self.cx, self.cy)
        self._fade = None
        if fade and old is not None and tex is not None:
            holder = {"p": 0.0}
            anim = Adw.TimedAnimation.new(self, 0.0, 1.0, tokens.ms(700),
                                          Adw.CallbackAnimationTarget.new(lambda v: self._fade_step(holder, v)))
            anim.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
            anim.connect("done", lambda _a: self._end_fade())
            self._fade = (old, old_rect, anim, holder)
            anim.play()
        self._changed()

    def _fade_step(self, holder, v) -> None:
        holder["p"] = v
        self.queue_draw()

    def _end_fade(self) -> None:
        self._fade = None
        self.queue_draw()

    def set_zoom(self, z, anchor=None, animate: bool = False) -> None:
        """z: a scale, or None to fit. anchor (x, y): the point of the view
        that stays put (default: the middle)."""
        if self.texture is None:
            self.zoom = z
            return
        w, h = self.get_width(), self.get_height()
        s0 = self.scale()
        c0 = self._centre()
        if z is not None:
            z = max(min(MIN_ZOOM, self.fit_scale()), min(MAX_ZOOM, z))
        s1 = self.fit_scale() if z is None else z
        ax, ay = anchor if anchor else (w / 2, h / 2)
        px, py = self.to_image(ax, ay)            # the picture point under the anchor

        def centre_at(s):
            return px - (ax - w / 2) / s, py - (ay - h / 2) / s
        if z is None:
            end = (self.texture.get_width() / 2, self.texture.get_height() / 2)
        else:
            end = self._clamped(s1, *centre_at(s1))
        self._stop()
        if not animate or not self.get_mapped() or abs(s1 - s0) < 1e-4:
            self.zoom = z
            self.cx, self.cy = end
            self._changed()
            return
        self._target = s1

        def step(t):
            s = math.exp(math.log(s0) + (math.log(s1) - math.log(s0)) * t)    # even steps to the eye
            if z is None:
                c = (c0[0] + (end[0] - c0[0]) * t, c0[1] + (end[1] - c0[1]) * t)
            else:
                c = self._clamped(s, *centre_at(s))
            self._view = (s, c[0], c[1])
            self.queue_draw()
        anim = Adw.TimedAnimation.new(self, 0.0, 1.0, tokens.ms(250), Adw.CallbackAnimationTarget.new(step))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        def done(_a):
            if self._anim is anim:
                self._anim, self._view, self._target = None, None, None
                self.zoom = z
                self.cx, self.cy = end
                self._changed()
        anim.connect("done", done)
        self._anim = anim
        self.zoom = z
        anim.play()

    def _stop(self) -> None:
        """Ends an animation or a flick where it is."""
        if self._anim is not None:
            view, anim = self._view, self._anim
            self._anim = None
            anim.skip()
            if view:                          # stay where the animation was
                self.zoom = view[0]
                self.cx, self.cy = view[1], view[2]
            self._view, self._target = None, None
        if self._inertia:
            self.remove_tick_callback(self._inertia)
            self._inertia = 0

    def pan_by(self, dx, dy) -> bool:
        """Moves the picture by dx, dy view pixels; False when it can't move."""
        if self.texture is None or not self.pannable():
            return False
        if self.zoom is None:                  # (only when fit was above the view: never)
            return False
        s = self.zoom
        self.cx, self.cy = self._clamped(s, self.cx - dx / s, self.cy - dy / s)
        self.queue_draw()
        return True

    def _changed(self) -> None:
        self.set_cursor(Gdk.Cursor.new_from_name("grab") if self.pannable() else None)
        self.queue_draw()
        if self.on_zoom:
            self.on_zoom()

    # -- GTK --------------------------------------------------------------------------------------
    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1                   # never asks the window for a size

    def do_size_allocate(self, w, h, baseline):
        if self.texture is not None and self.zoom is not None and not self._view:
            self.cx, self.cy = self._clamped(self.zoom, self.cx, self.cy)
        self.set_cursor(Gdk.Cursor.new_from_name("grab") if self.pannable() else None)

    def _draw(self, snap, tex, rect) -> None:
        x, y, w, h = rect
        s = w / max(1, tex.get_width())
        filt = Gsk.ScalingFilter.TRILINEAR if s < 0.99 else Gsk.ScalingFilter.LINEAR
        snap.append_scaled_texture(tex, filt, Graphene.Rect().init(round(x), round(y), round(w), round(h)))

    def do_snapshot(self, snap) -> None:
        if self.texture is None:
            return
        rect = self.image_rect()
        if self._fade:
            old, old_rect, _anim, holder = self._fade
            snap.push_cross_fade(holder["p"])
            if old_rect:
                self._draw(snap, old, old_rect)
            snap.pop()
            self._draw(snap, self.texture, rect)
            snap.pop()
            return
        self._draw(snap, self.texture, rect)

    # -- input ------------------------------------------------------------------------------------
    def _input(self) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        motion.connect("leave", lambda _c: setattr(self, "_pointer", None))
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES
                                           | Gtk.EventControllerScrollFlags.KINETIC)
        scroll.connect("scroll-begin", lambda _c: self._stop())
        scroll.connect("scroll", self._scroll)
        scroll.connect("decelerate", self._decelerate)
        self.add_controller(scroll)

        pinch = Gtk.GestureZoom()
        state = {}

        def begin(g, _seq):
            self._stop()
            ok, x, y = g.get_bounding_box_center()
            state.update(s=self.scale(), anchor=self.to_image(x, y) if ok else None)

        def changed(g, factor):
            ok, x, y = g.get_bounding_box_center()
            if not ok or self.texture is None or state.get("anchor") is None:
                return
            w, h = self.get_width(), self.get_height()
            s = max(min(MIN_ZOOM, self.fit_scale()), min(MAX_ZOOM, state["s"] * factor))
            px, py = state["anchor"]
            self.zoom = s                                     # fingers: follow them, no animation
            self.cx, self.cy = self._clamped(s, px - (x - w / 2) / s, py - (y - h / 2) / s)
            self._changed()

        def end(_g, _seq):
            if self.zoom is not None and self.zoom <= self.fit_scale() + 1e-3:
                self.set_zoom(None, animate=True)            # pinched in below fit: back to fit
        pinch.connect("begin", begin)
        pinch.connect("scale-changed", changed)
        pinch.connect("end", end)
        self.add_controller(pinch)

        drag = Gtk.GestureDrag()
        last = {}

        def drag_begin(_g, _x, _y):
            self._stop()
            last.update(dx=0.0, dy=0.0)
            if self.pannable():
                self.set_cursor(Gdk.Cursor.new_from_name("grabbing"))

        def drag_update(_g, dx, dy):
            self.pan_by(dx - last["dx"], dy - last["dy"])
            last.update(dx=dx, dy=dy)

        drag.connect("drag-begin", drag_begin)
        drag.connect("drag-update", drag_update)
        drag.connect("drag-end", lambda *_: self._changed())
        self.add_controller(drag)

        click = Gtk.GestureClick()

        def pressed(_g, n, x, y):
            if n == 2 and self.texture is not None:
                actual = self.zoom is not None and abs(self.zoom - 1.0) < 1e-3
                self.set_zoom(None if actual else 1.0, anchor=(x, y), animate=True)
        click.connect("pressed", pressed)
        self.add_controller(click)

    def _scroll(self, c, dx, dy) -> bool:
        if self.texture is None:
            return False
        wheel = c.get_unit() == Gdk.ScrollUnit.WHEEL
        if c.get_current_event_state() & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK):
            anchor = self._pointer
            if wheel:                          # notches: animated steps that add up
                base = self._target or self.scale()
                self.set_zoom(base * WHEEL_STEP ** -dy, anchor=anchor, animate=True)
            else:                              # smooth (trackpad): follow the fingers
                self.set_zoom(self.scale() * math.exp(-dy * 0.01), anchor=anchor)
            return True
        if not self.pannable():
            return False
        step = 48 if wheel else 1
        if c.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK and wheel:
            dx, dy = dy, dx
        self._stop()
        self.pan_by(-dx * step, -dy * step)
        return True

    def _decelerate(self, _c, vx, vy) -> None:
        """A trackpad flick: the picture glides on and slows down."""
        if not self.pannable() or math.hypot(vx, vy) < 50:
            return
        state = {"vx": -vx, "vy": -vy, "t": None}

        def tick(_w, clock):
            now = clock.get_frame_time() / 1e6
            if state["t"] is None:
                state["t"] = now
                return GLib.SOURCE_CONTINUE
            dt, state["t"] = now - state["t"], now
            decay = math.exp(-FRICTION * dt)
            moved = self.pan_by(state["vx"] * dt, state["vy"] * dt)
            state["vx"] *= decay
            state["vy"] *= decay
            if not moved or math.hypot(state["vx"], state["vy"]) < 20:
                self._inertia = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self._stop()
        self._inertia = self.add_tick_callback(tick)
