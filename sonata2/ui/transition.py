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

    side = ui.transition.SlidingSelection(listbox)          # in place of the list
    # (a sidebar: the selection slides from the old row to the clicked one)

Cheap: one texture or one offset per item; nothing runs when idle."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Graphene, Gsk, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
@keyframes sonata-open { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: none; } }
popover > contents { animation: sonata-open %(t_open)s %(ease_out)s; }
""", key="motion-open")

theme.register("""
/* a source list's selection sliding to the clicked row (SlidingSelection):
   the rows' own selected background is off while the pill moves, and never
   fades (it would show twice at the start, and flash at the end) */
list.sonata-sliding-list > row:selected, list.sonata-sliding-list.sliding > row { transition: none; }
list.sonata-sliding-list.sliding > row:selected, list.sonata-sliding-list.sliding > row:selected:hover,
list.sonata-sliding-list.sliding > row:selected:active { background: none; transition: none; }
.sonata-sel-pill { background: %(sidebar_selected)s; border-radius: %(r_menu)s; }
""", key="sliding-selection")

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


# -- sliding selection: a sidebar's highlight moves to the clicked row (macOS) -----------------
SLIDE_MS = 220


class _Under(Gtk.Widget):
    """Draws the pill under the list: no size of its own; the pill is placed
    by allocation only (a move per frame re-lays out nothing else)."""

    def __init__(self, pill):
        super().__init__(can_target=False)
        self.pill = pill
        self.rect = (0.0, 0.0, 0.0, 0.0)
        pill.set_parent(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        x, y, w, h = self.rect
        if self.pill.get_visible():
            t = Gsk.Transform().translate(Graphene.Point().init(x, y))
            self.pill.allocate(max(1, round(w)), max(1, round(h)), -1, t)

    def do_dispose(self):
        if self.pill.get_parent() is self:
            self.pill.unparent()


class SlidingSelection(Gtk.Overlay):
    """A Gtk.ListBox (a sidebar) whose selection slides from the old row to
    the new one instead of jumping: a pill (.sonata-sel-pill, styled like the
    selected row) drawn under the rows moves and resizes between them, and
    the row's own selected background takes over when it arrives. Clicking
    again mid-slide starts from where the pill is. Nothing runs when idle."""

    def __init__(self, listbox: Gtk.ListBox, ms: int = SLIDE_MS, **props):
        self.pill = Gtk.Box(css_classes=["sonata-sel-pill"], visible=False)
        self.under = _Under(self.pill)
        super().__init__(child=self.under, **props)
        self.list, self.ms = listbox, ms
        self.add_overlay(listbox)
        self.set_measure_overlay(listbox, True)
        listbox.add_css_class("sonata-sliding-list")
        self._prev = listbox.get_selected_row()
        self._anim = None
        listbox.connect("row-selected", self._selected)

    def _bounds(self, row):
        if row is None or not row.get_mapped() or row.get_parent() is not self.list:
            return None
        ok, r = row.compute_bounds(self.under)
        return (r.get_x(), r.get_y(), r.get_width(), r.get_height()) if ok else None

    def _selected(self, _lb, row) -> None:
        old, self._prev = self._prev, row
        if self._anim is not None:                       # mid-slide: from where the pill is
            start = self.under.rect
            self._anim.pause()
            self._anim = None
        else:
            start = self._bounds(old)
        end = self._bounds(row)
        if not self.get_mapped() or start is None or end is None or row is old or start[2] <= 0:
            self._finish()
            return
        self.pill.set_visible(True)
        self.list.add_css_class("sliding")

        def step(v, a=start, b=end):
            self.under.rect = tuple(a[i] + (b[i] - a[i]) * v for i in range(4))
            self.under.queue_allocate()
        step(0.0)
        anim = Adw.TimedAnimation.new(self, 0.0, 1.0, self.ms, Adw.CallbackAnimationTarget.new(step))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.connect("done", lambda a: a is self._anim and self._finish())
        self._anim = anim
        anim.play()

    def _finish(self) -> None:
        self._anim = None
        self.pill.set_visible(False)
        self.list.remove_css_class("sliding")


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
        from .. import logs
        if not logs.verbose():                        # frame timings: detailed logs only
            return
        print(f"sonata2-frames: {self.label}: {len(t)} frames, {base:.1f} ms "
              f"({1000 / base:.0f} Hz), {late} late, worst {gaps[-1]:.1f} ms", flush=True)


def tween(owner, key: str, start: float, end: float, ms: int, step, label: str,
          easing=Adw.Easing.EASE_IN_OUT_CUBIC):
    """Animate one value of `owner` (step(v) each frame) with its FrameStats.
    A new tween with the same key replaces the running one and stops its
    stats: a paused Adw animation never emits "done", so its stats would tick
    forever (the Dock's and the menu bar's auto-hide slides)."""
    running = getattr(owner, "_tweens", None)
    if running is None:
        running = owner._tweens = {}
    old = running.pop(key, None)
    if old is not None:
        old[0].pause()
        old[1].stop()
    anim = Adw.TimedAnimation.new(owner, start, end, ms, Adw.CallbackAnimationTarget.new(step))
    anim.set_easing(easing)
    stats = FrameStats(owner, label)

    def done(*_a):
        stats.stop()
        if running.get(key, (None,))[0] is anim:
            del running[key]
    anim.connect("done", done)
    running[key] = (anim, stats)
    anim.play()
    return anim
