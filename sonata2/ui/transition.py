"""Standard motion. Every popover (menus, menu bar panels, Control
Center, Dock previews) opens with the same short fade + grow (CSS
animation, registered here).

Cross-fade for content that changes in place (a Files folder, a pane):
a picture of the old content, over the new, fading out.

    fade = ui.transition.CrossFade(child)    # a Gtk.Overlay around child
    fade.capture()                           # before the change
    ... change the content ...
    fade.play()                              # when the new content is there

    before = ui.transition.glide_record(tiles, container)   # before re-ordering
    ... re-order ...
    ui.transition.glide_play(before, container)             # they slide into place
    # container.do_snapshot draws with ui.transition.snapshot_children()

Cheap: one texture or one offset per item; nothing runs when idle."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
@keyframes sonata-open { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: none; } }
popover > contents { animation: sonata-open %(t_open)s %(ease_out)s; }
""", key="motion-open")

DURATION_MS = 200


class CrossFade(Gtk.Overlay):
    def __init__(self, child: Gtk.Widget, duration: int = DURATION_MS):
        super().__init__(child=child)
        self.duration = duration
        self._pic = None
        self._anim = None

    def capture(self) -> None:
        """Freeze how the content looks now (covers it until play())."""
        self._finish()
        child = self.get_child()
        native = self.get_native()
        w, h = child.get_width(), child.get_height()
        if not child.get_mapped() or w <= 0 or h <= 0 or native is None or native.get_renderer() is None:
            return
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(child).snapshot(snap, w, h)
        node = snap.to_node()
        if node is None:
            return
        tex = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
        self._pic = Gtk.Picture(paintable=tex, can_target=False, content_fit=Gtk.ContentFit.FILL)
        self.add_overlay(self._pic)

    def play(self) -> None:
        """Fade the frozen picture out, revealing the new content."""
        pic = self._pic
        if pic is None or self._anim is not None:
            return
        target = Adw.PropertyAnimationTarget.new(pic, "opacity")
        self._anim = Adw.TimedAnimation.new(pic, 1.0, 0.0, self.duration, target)
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.connect("done", lambda *_: self._finish())
        self._anim.play()

    def _finish(self) -> None:
        if self._anim is not None:
            anim, self._anim = self._anim, None
            anim.skip() if anim.get_state() == Adw.AnimationState.PLAYING else None
        if self._pic is not None:
            self.remove_overlay(self._pic)
            self._pic = None


# -- glide: items that change place slide there (Launchpad, Dock) -------------------------
GLIDE_MS = 260


def snapshot_children(container: Gtk.Widget, snap) -> None:
    """Draw `container`'s children, each shifted by its glide offset (call
    from the container's do_snapshot instead of the parent class's)."""
    child = container.get_first_child()
    while child is not None:
        dx, dy = getattr(child, "_glide", (0, 0))
        if dx or dy:
            snap.save()
            snap.translate(Graphene.Point().init(dx, dy))
            container.snapshot_child(child, snap)
            snap.restore()
        else:
            container.snapshot_child(child, snap)
        child = child.get_next_sibling()


def glide_record(widgets, ref: Gtk.Widget) -> dict:
    """Where each widget is drawn now (layout + running glide), in `ref`'s
    coordinates. Call before re-ordering."""
    out = {}
    for w in widgets:
        if w.get_mapped():
            ok, p = w.compute_point(ref, Graphene.Point().init(0, 0))
            if ok:
                dx, dy = getattr(w, "_glide", (0, 0))
                out[w] = (p.x + dx, p.y + dy, w.get_parent())
    return out


def glide_play(before: dict, ref: Gtk.Widget, ms: int = GLIDE_MS) -> None:
    """After re-ordering: every widget recorded in `before` that moved (and
    kept its parent) slides from its old place to the new one. Runs in the
    layout phase of the next frame, so the new place is never shown first."""
    clock = ref.get_frame_clock()
    if not before or clock is None:
        return
    state = {}

    def on_layout(_clock):
        clock.disconnect(state.pop("id"))
        for w, (ox, oy, parent) in before.items():
            if w.get_parent() is not parent or not w.get_mapped():
                continue
            ok, p = w.compute_point(ref, Graphene.Point().init(0, 0))
            if not ok:
                continue
            dx, dy = ox - p.x, oy - p.y
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                continue
            _start(w, dx, dy, ms)
    state["id"] = clock.connect("layout", on_layout)
    ref.queue_allocate()


def _start(w, dx, dy, ms) -> None:
    if getattr(w, "_glide_anim", None) is not None:
        w._glide_anim.pause()

    def step(v, w=w, dx=dx, dy=dy):
        w._glide = (dx * v, dy * v)
        w.queue_draw()
    w._glide = (dx, dy)
    anim = Adw.TimedAnimation.new(w, 1.0, 0.0, ms, Adw.CallbackAnimationTarget.new(step))
    anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    w._glide_anim = anim
    anim.play()


class FrameStats:
    """How smooth an animation really ran (logged when it ends): frames
    drawn, the display's frame time, and the frames that came late --
    "sonata2-frames: launchpad open: 31 frames, 6.9 ms (144 Hz), 2 late,
    worst 21.3 ms". Nothing is kept once it stops."""

    def __init__(self, widget: Gtk.Widget, label: str):
        self.widget, self.label = widget, label
        self.times = []
        self.tick = widget.add_tick_callback(self._tick)

    def _tick(self, _w, clock) -> bool:
        self.times.append(clock.get_frame_time())
        return True

    def stop(self) -> None:
        if self.tick is None:
            return
        self.widget.remove_tick_callback(self.tick)
        self.tick = None
        t = self.times
        if len(t) < 3:
            return
        gaps = sorted((b - a) / 1000 for a, b in zip(t, t[1:]))
        base = gaps[len(gaps) // 2]                   # the display's frame time
        late = sum(1 for g in gaps if g > base * 1.5)
        print(f"sonata2-frames: {self.label}: {len(t)} frames, {base:.1f} ms "
              f"({1000 / base:.0f} Hz), {late} late, worst {gaps[-1]:.1f} ms", flush=True)
