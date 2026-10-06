"""Videos' Trim bar (QuickTime Player: Edit > Trim): it takes the control
bar's place -- a filmstrip of the movie with a yellow frame around the part
kept; drag its ends (the movie shows the frame there), Cancel or Trim.
Trim saves the part as a new file beside the original (edit.py).

    bar = TrimBar(duration, on_seek, on_cancel, on_trim)
    bar.set_thumbnail(i, texture)     # the filmstrip fills in
    bar.set_position(seconds)         # the playhead
    bar.exporting(0.4)                # the buttons give way to the progress
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import edit as E  # noqa: E402

STRIP_H = 48               # the filmstrip's height
HANDLE_W = 12              # the yellow frame's ends, where they are dragged
FRAME_W = 3                # its top and bottom
GRAB = 14                  # px around an end that still picks it up

ui.register("""
.vd-trim { padding: 10px 12px; border-radius: %(r_dialog)s; background: %(solid_tint)s; color: %(label)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s; font-family: %(font)s; }
.vd-trim .vd-trim-time { font-size: %(text_small)s; color: %(label_secondary)s; font-feature-settings: "tnum"; }
.vd-trim levelbar trough { min-height: 6px; border-radius: 3px; }
""", key="videos-trim")


def _rgba(r, g, b, a) -> Gdk.RGBA:
    """A colour of its own (ui.rgba's are shared: never change those)."""
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


class Filmstrip(Gtk.Widget):
    """The movie's frames side by side, the part outside start..end dimmed,
    a yellow frame around the part kept, a white line at the playhead."""

    def __init__(self, duration: float, on_change, on_seek, count: int = 12):
        super().__init__(hexpand=True, height_request=STRIP_H, cursor=None)
        self.duration = max(duration, E.MIN_LENGTH)
        self.start, self.end, self.position = 0.0, self.duration, 0.0
        self.thumbs = [None] * count
        self.on_change, self.on_seek = on_change, on_seek
        self._grab = None
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_a: setattr(self, "_grab", None))
        self.add_controller(drag)

    # -- geometry ------------------------------------------------------------------------------
    def _inner(self) -> tuple:
        """The filmstrip between the handles' room: x0, width."""
        return HANDLE_W, max(1, self.get_width() - 2 * HANDLE_W)

    def x_of(self, t: float) -> float:
        x0, w = self._inner()
        return x0 + w * t / self.duration

    def time_at(self, x: float) -> float:
        x0, w = self._inner()
        return max(0.0, min(self.duration, (x - x0) / w * self.duration))

    def pick(self, x: float) -> str:
        """What a press at x takes: an end of the frame, or the playhead."""
        ds, de = abs(x - (self.x_of(self.start) - HANDLE_W / 2)), abs(x - (self.x_of(self.end) + HANDLE_W / 2))
        if min(ds, de) <= GRAB:
            return "start" if ds <= de else "end"
        return "playhead"

    # -- dragging ---------------------------------------------------------------------------------
    def _begin(self, _g, x, _y) -> None:
        self._x0 = x
        self._grab = self.pick(x)
        self._move(x)

    def _update(self, _g, dx, _dy) -> None:
        if self._grab:
            self._move(self._x0 + dx)

    def _move(self, x: float) -> None:
        t = self.time_at(x)
        if self._grab == "start":
            self.set_range(min(t, self.end - E.MIN_LENGTH), self.end)
            self.on_seek(self.start)
        elif self._grab == "end":
            self.set_range(self.start, max(t, self.start + E.MIN_LENGTH))
            self.on_seek(self.end)
        else:
            self.on_seek(max(self.start, min(t, self.end)))

    def set_range(self, start: float, end: float) -> None:
        self.start, self.end = E.clamp_range(start, end, self.duration)
        self.on_change(self.start, self.end)
        self.queue_draw()

    def set_position(self, t: float) -> None:
        self.position = t
        self.queue_draw()

    # -- drawing ----------------------------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        x0, sw = self._inner()
        n = len(self.thumbs)
        cell = sw / n
        dark = ui.rgba("sys_black")
        # the frames (each fills its cell, cropped to it), on black while they load
        clip = Gsk.RoundedRect()
        clip.init_from_rect(Graphene.Rect().init(x0, 0, sw, h), 4)
        snap.push_rounded_clip(clip)
        snap.append_color(dark, Graphene.Rect().init(x0, 0, sw, h))
        for i, tex in enumerate(self.thumbs):
            if tex is None:
                continue
            tw, th = tex.get_width(), tex.get_height()
            scale = max(cell / tw, h / th)
            dw, dh = tw * scale, th * scale
            snap.push_clip(Graphene.Rect().init(x0 + i * cell, 0, cell + 0.5, h))
            snap.append_texture(tex, Graphene.Rect().init(x0 + i * cell + (cell - dw) / 2, (h - dh) / 2, dw, dh))
            snap.pop()
        # outside the part kept: dimmed
        dim = _rgba(0, 0, 0, 0.6)
        xs, xe = self.x_of(self.start), self.x_of(self.end)
        snap.append_color(dim, Graphene.Rect().init(x0, 0, max(0, xs - x0), h))
        snap.append_color(dim, Graphene.Rect().init(xe, 0, max(0, x0 + sw - xe), h))
        snap.pop()
        # the yellow frame: its ends wide (handles), top and bottom thin
        yellow = ui.rgba("sys_yellow")
        frame = Gsk.RoundedRect()
        frame.init_from_rect(Graphene.Rect().init(xs - HANDLE_W, 0, xe - xs + 2 * HANDLE_W, h), 5)
        snap.push_rounded_clip(frame)
        for r in ((xs - HANDLE_W, 0, HANDLE_W, h), (xe, 0, HANDLE_W, h),
                  (xs, 0, xe - xs, FRAME_W), (xs, h - FRAME_W, xe - xs, FRAME_W)):
            snap.append_color(yellow, Graphene.Rect().init(*r))
        snap.pop()
        # a grip on each handle (QuickTime's chevrons, as short bars)
        grip = _rgba(0, 0, 0, 0.55)
        for cx in (xs - HANDLE_W / 2, xe + HANDLE_W / 2):
            snap.append_color(grip, Graphene.Rect().init(cx - 1, h / 2 - 7, 2, 14))
        # the playhead
        if self.start <= self.position <= self.end:
            white = _rgba(1, 1, 1, 1)
            px = self.x_of(self.position)
            snap.append_color(white, Graphene.Rect().init(px - 1, -1, 2, h + 2))


class TrimBar(Gtk.Box):
    def __init__(self, duration: float, on_seek, on_cancel, on_trim, count: int = 12):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["vd-trim"],
                         halign=Gtk.Align.FILL, valign=Gtk.Align.END, margin_bottom=22, margin_start=22,
                         margin_end=22)
        self.on_trim = on_trim
        self.strip = Filmstrip(duration, self._changed, on_seek, count)
        self.append(self.strip)
        row = Gtk.Box(spacing=8)
        self.times = Gtk.Label(xalign=0, hexpand=True, css_classes=["vd-trim-time"])
        row.append(self.times)
        self.progress = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, visible=False,
                                     valign=Gtk.Align.CENTER)
        row.append(self.progress)
        self.cancel_btn = ui.controls.push_button("Cancel", on_cancel)
        self.trim_btn = ui.controls.push_button("Trim", lambda: on_trim(self.strip.start, self.strip.end),
                                                style="default")
        row.append(self.cancel_btn)
        row.append(self.trim_btn)
        self.append(row)
        self._changed(0.0, self.strip.duration)

    def _changed(self, start: float, end: float) -> None:
        from .window import fmt_time
        self.times.set_label(f"{fmt_time(start)} – {fmt_time(end)}   ({fmt_time(end - start)})")

    def set_thumbnail(self, i: int, texture) -> None:
        if 0 <= i < len(self.strip.thumbs):
            self.strip.thumbs[i] = texture
            self.strip.queue_draw()

    def set_position(self, t: float) -> None:
        self.strip.set_position(t)

    def exporting(self, fraction) -> None:
        """None: back to choosing; 0..1: saving, the progress instead of the times."""
        busy = fraction is not None
        self.times.set_visible(not busy)
        self.progress.set_visible(busy)
        self.progress.set_value(fraction or 0.0)
        self.trim_btn.set_sensitive(not busy)
        self.strip.set_sensitive(not busy)
