"""Videos' Crop: a frame over the movie to drag (inside: move it; its edges
and corners: resize it), the rest dimmed, with thirds lines like Photos; a
bar with the shape (Free, 16:9, 4:3, 1:1, 9:16), the size in pixels,
Cancel and Crop. Crop saves the frame's part as a new file beside the
original (edit.py), like Trim.

    frame = CropFrame(iw, ih)            # over the picture, in its overlay
    bar = CropBar(frame, on_cancel, on_crop)
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import edit as E  # noqa: E402
from .trimbar import _rgba  # noqa: E402

GRAB = 14                  # screen px around an edge / corner that pick it up
HANDLE = 18                # the corners' L marks
LINE = 3

CURSORS = {"move": "move", "l": "w-resize", "r": "e-resize", "t": "n-resize", "b": "s-resize",
           "tl": "nw-resize", "tr": "ne-resize", "bl": "sw-resize", "br": "se-resize"}


class CropFrame(Gtk.Widget):
    """The crop over the movie (shown letterboxed like the picture under it).
    rect: (x, y, w, h) in the movie's pixels."""

    def __init__(self, iw: int, ih: int, on_change=None):
        super().__init__(hexpand=True, vexpand=True)
        self.iw, self.ih = max(1, iw), max(1, ih)
        self.ratio = None
        self.rect = (0, 0, self.iw, self.ih)
        self.on_change = on_change or (lambda r: None)
        self._part, self._start = "", None
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", lambda *_a: setattr(self, "_part", ""))
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self._hover(x, y))
        self.add_controller(motion)

    # -- screen <-> movie ------------------------------------------------------------------------
    def _map(self) -> tuple:
        return E.video_rect(self.iw, self.ih, self.get_width(), self.get_height())

    def to_movie(self, x: float, y: float) -> tuple:
        ox, oy, s = self._map()
        return (x - ox) / s, (y - oy) / s

    def on_screen(self) -> tuple:
        ox, oy, s = self._map()
        x, y, w, h = self.rect
        return ox + x * s, oy + y * s, w * s, h * s

    def part_at(self, x: float, y: float) -> str:
        _ox, _oy, s = self._map()
        px, py = self.to_movie(x, y)
        return E.crop_part(self.rect, px, py, GRAB / max(s, 1e-6))

    # -- changing it -----------------------------------------------------------------------------
    def set_ratio(self, ratio) -> None:
        """A new shape: the biggest of it inside the picture, centred."""
        self.ratio = ratio
        self.set_rect(E.ratio_rect(self.iw, self.ih, ratio))

    def set_rect(self, rect) -> None:
        self.rect = tuple(int(v) for v in rect)
        self.on_change(self.rect)
        self.queue_draw()

    def _hover(self, x, y) -> None:
        if not self._part:
            part = self.part_at(x, y)
            self.set_cursor(Gdk.Cursor.new_from_name(CURSORS[part]) if part else None)

    def _begin(self, _g, x, y) -> None:
        self._part = self.part_at(x, y)
        self._start = self.rect

    def _update(self, _g, dx, dy) -> None:
        if not self._part or self._start is None:
            return
        _ox, _oy, s = self._map()
        self.set_rect(E.drag_crop(self._start, self._part, dx / s, dy / s, self.iw, self.ih, self.ratio))

    # -- drawing ---------------------------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        ox, oy, s = self._map()
        pw, ph = self.iw * s, self.ih * s
        x, y, w, h = self.on_screen()
        dim = _rgba(0, 0, 0, 0.55)
        for r in ((ox, oy, pw, y - oy), (ox, y + h, pw, oy + ph - y - h),
                  (ox, y, x - ox, h), (x + w, y, ox + pw - x - w, h)):
            if r[2] > 0 and r[3] > 0:
                snap.append_color(dim, Graphene.Rect().init(*r))
        white, thin = _rgba(1, 1, 1, 0.95), _rgba(1, 1, 1, 0.35)
        for i in (1, 2):                                       # thirds
            snap.append_color(thin, Graphene.Rect().init(x + w * i / 3, y, 1, h))
            snap.append_color(thin, Graphene.Rect().init(x, y + h * i / 3, w, 1))
        for r in ((x, y, w, 1), (x, y + h - 1, w, 1), (x, y, 1, h), (x + w - 1, y, 1, h)):
            snap.append_color(white, Graphene.Rect().init(*r))
        k = min(HANDLE, w / 3, h / 3)                         # thick L marks in the corners
        t = LINE
        for r in ((x, y, k, t), (x, y, t, k), (x + w - k, y, k, t), (x + w - t, y, t, k),
                  (x, y + h - t, k, t), (x, y + h - k, t, k), (x + w - k, y + h - t, k, t),
                  (x + w - t, y + h - k, t, k)):
            snap.append_color(white, Graphene.Rect().init(*r))


class CropBar(Gtk.Box):
    def __init__(self, frame: CropFrame, on_cancel, on_crop):
        super().__init__(spacing=10, css_classes=["vd-trim"], halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                         margin_bottom=22, margin_start=22, margin_end=22)
        self.frame = frame
        self.shapes = ui.controls.segmented([(k, label) for k, label, _r in E.RATIOS], current="free",
                                            on_pick=self.pick)
        self.shapes.set_valign(Gtk.Align.CENTER)
        self.append(self.shapes)
        self.size = Gtk.Label(css_classes=["vd-trim-time"], width_chars=11, xalign=0)
        self.append(self.size)
        self.progress = Gtk.LevelBar(min_value=0, max_value=1, visible=False, valign=Gtk.Align.CENTER)
        self.progress.set_size_request(120, -1)
        self.append(self.progress)
        self.cancel_btn = ui.controls.push_button("Cancel", on_cancel)
        self.crop_btn = ui.controls.push_button("Crop", lambda: on_crop(self.frame.rect), style="default")
        self.append(self.cancel_btn)
        self.append(self.crop_btn)
        frame.on_change = self._changed
        self._changed(frame.rect)

    def pick(self, key: str) -> None:
        self.shapes.select(key)
        self.frame.set_ratio(dict((k, r) for k, _l, r in E.RATIOS)[key])

    def _changed(self, rect) -> None:
        _x, _y, w, h = rect
        self.size.set_label(f"{w - w % 2} × {h - h % 2}")       # what the file gets (even sizes)

    def exporting(self, fraction) -> None:
        busy = fraction is not None
        self.size.set_visible(not busy)
        self.progress.set_visible(busy)
        self.progress.set_value(fraction or 0.0)
        self.crop_btn.set_sensitive(not busy)
        self.shapes.set_sensitive(not busy)
        self.frame.set_sensitive(not busy)
