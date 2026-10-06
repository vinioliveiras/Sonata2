"""Preview's Markup (macOS Markup; Vini: draw on a screenshot): pen,
highlighter, shapes, arrows, text, emoji, numbered steps and pixelate over
the picture, every mark still editable (select it to move it, resize it,
recolour it or delete it) until Done bakes them into the picture as one
edit (⌘Z takes it back).

    items = [{"t": "pen", "c": "#ff3b30", "w": 0.004, "p": [[fx, fy], ...]}, ...]
    draw(cr, items, W, H, source)      # on a Cairo context where the picture is W x H
    apply(pil_image, items)            # the marks drawn into the full-size picture

Positions are fractions of the picture (fx 0..1 across, fy 0..1 down), sizes
fractions of its longer side -- the same marks at any zoom and on the
full-size picture. One renderer (Cairo + Pango) draws them on screen and
into the saved file, so what is saved is what was seen."""
import copy
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .. import ui  # noqa: E402

COLORS = ("#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#af52de", "#1c1c1e", "#ffffff")
WIDTHS = (0.0025, 0.005, 0.009)                 # thin / medium / thick, of the picture's longer side
SHAPES = ("rect", "round", "ellipse", "line", "arrow", "star", "bubble", "check", "cross", "question")
BOXED = set(SHAPES) | {"pixelate"}               # drawn from a drag: two corners
EMOJI = ("😀", "😂", "😍", "🤔", "😮", "😢", "😡", "👍", "👎", "👏", "🙏", "💪", "👀", "🔥", "✨", "🎉",
         "❤️", "💯", "✅", "❌", "⚠️", "❓", "❗", "💡", "📌", "⭐", "🚀", "🐛", "👉", "👆", "👇", "👈")
TEXT_SIZE, EMOJI_SIZE, STEP_SIZE = 0.03, 0.06, 0.035
DOUBLE_CLICK_MS = 300                      # a pen click waits this long: half of a double click leaves no dot
PIXEL = 0.012                                    # pixelate: a block, of the picture's longer side

ui.register("""
.pv-markup { padding: 4px 8px; border-bottom: 1px solid %(separator)s; }
.pv-markup button.mk { min-width: 30px; min-height: 26px; padding: 0; border-radius: 7px; background: none;
  border: none; box-shadow: none; color: %(label)s; }
.pv-markup button.mk:hover { background: %(tool_hover)s; }
.pv-markup button.mk.on { background: alpha(%(label)s, 0.14); }
.pv-markup button.mk:disabled { opacity: 0.35; }
.pv-markup .mk-sep { min-width: 1px; background: %(separator)s; margin: 5px 6px; }
.pv-markup button.mk-color { min-width: 22px; min-height: 22px; padding: 0; border-radius: 99px; background: none;
  border: none; box-shadow: none; }
.pv-markup button.mk-done { min-height: 26px; padding: 0 12px; }
.mk-pop button { min-width: 32px; min-height: 32px; padding: 0; border-radius: 7px; background: none; border: none;
  box-shadow: none; color: %(label)s; font-size: 18px; }
.mk-pop button:hover { background: %(tool_hover)s; }
.mk-pop button.on { background: alpha(%(label)s, 0.14); }
""", key="preview-markup")


# -- the marks ----------------------------------------------------------------------------------
def _rgba(hexc: str, alpha: float = 1.0):
    c = Gdk.RGBA()
    c.parse(hexc)
    return c.red, c.green, c.blue, alpha


def _box(item, W, H):
    (ax, ay), (bx, by) = item["p"][0], item["p"][-1]
    x0, x1 = sorted((ax * W, bx * W))
    y0, y1 = sorted((ay * H, by * H))
    return x0, y0, x1, y1


def _round_rect(cr, x0, y0, x1, y1, r):
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    cr.new_sub_path()
    cr.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
    cr.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
    cr.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
    cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _smooth(cr, pts):
    """A freehand stroke through the points, smoothed (midpoint quadratic curves)."""
    cr.move_to(*pts[0])
    if len(pts) == 1:
        cr.line_to(pts[0][0] + 0.01, pts[0][1])
        return
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        cx, cy = cr.get_current_point()
        # quadratic (control x0,y0) as a cubic
        cr.curve_to(cx + 2 / 3 * (x0 - cx), cy + 2 / 3 * (y0 - cy), mx + 2 / 3 * (x0 - mx), my + 2 / 3 * (y0 - my),
                    mx, my)
    cr.line_to(*pts[-1])


def _layout(cr, text, px, bold=True):
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string("Inter Variable, Inter, sans-serif")
    fd.set_absolute_size(max(1, px) * Pango.SCALE)
    if bold:
        fd.set_weight(Pango.Weight.SEMIBOLD)
    lay.set_font_description(fd)
    lay.set_text(text, -1)
    return lay


def text_size(item, W, H, cr=None):
    """(w, h) of a text / emoji mark in picture units."""
    import cairo
    own = cr is None
    if own:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surf)
    S = max(W, H)
    lay = _layout(cr, item.get("text", "") or " ", item.get("s", TEXT_SIZE) * S, item["t"] == "text")
    _ink, log = lay.get_pixel_extents()
    return log.width, log.height


def bounds(item, W, H):
    """The mark's box in picture units (x0, y0, x1, y1), for selecting it."""
    t = item["t"]
    S = max(W, H)
    if t in BOXED:
        return _box(item, W, H)
    if t in ("pen", "hl"):
        xs = [p[0] * W for p in item["p"]]
        ys = [p[1] * H for p in item["p"]]
        pad = item.get("w", WIDTHS[1]) * S * (3 if t == "hl" else 1) / 2
        return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    x, y = item["p"][0][0] * W, item["p"][0][1] * H
    if t == "step":
        r = item.get("s", STEP_SIZE) * S / 2
        return x - r, y - r, x + r, y + r
    w, h = text_size(item, W, H)
    return x, y, x + w, y + h


def _draw_item(cr, it, W, H, source=None):
    t = it["t"]
    S = max(W, H)
    lw = it.get("w", WIDTHS[1]) * S
    r, g, b, _a = _rgba(it.get("c", COLORS[0]))
    cr.save()
    cr.new_path()                                # nothing joins the last mark's end (a text's pen position)
    cr.set_line_join(1)                          # ROUND
    cr.set_line_cap(1)
    if t == "pixelate":
        x0, y0, x1, y1 = _box(it, W, H)
        if source is not None and x1 - x0 > 1 and y1 - y0 > 1:
            import cairo
            block = max(2.0, PIXEL * S)
            sw, sh = max(1, int((x1 - x0) / block)), max(1, int((y1 - y0) / block))
            small = cairo.ImageSurface(cairo.FORMAT_ARGB32, sw, sh)
            sc = cairo.Context(small)
            sc.scale(sw / (x1 - x0), sh / (y1 - y0))
            sc.translate(-x0, -y0)
            sc.set_source_surface(source, 0, 0)
            sc.get_source().set_filter(cairo.FILTER_GOOD)
            sc.paint()
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.clip()
            cr.translate(x0, y0)
            cr.scale((x1 - x0) / sw, (y1 - y0) / sh)
            cr.set_source_surface(small, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_NEAREST)
            cr.paint()
        else:                                    # (no picture to sample: a grey stand-in)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.85)
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.fill()
    elif t in ("pen", "hl"):
        pts = [(p[0] * W, p[1] * H) for p in it["p"]]
        if t == "hl":
            cr.set_source_rgba(r, g, b, 0.38)
            cr.set_line_width(lw * 3)
            cr.set_line_cap(2)                   # SQUARE: a marker's flat tip
        else:
            cr.set_source_rgba(r, g, b, 1)
            cr.set_line_width(lw)
        _smooth(cr, pts)
        cr.stroke()
    elif t in SHAPES:
        x0, y0, x1, y1 = _box(it, W, H)
        (ax, ay), (bx, by) = (it["p"][0][0] * W, it["p"][0][1] * H), (it["p"][-1][0] * W, it["p"][-1][1] * H)
        cr.set_source_rgba(r, g, b, 1)
        cr.set_line_width(lw)
        if t == "rect":
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
        elif t == "round":
            _round_rect(cr, x0, y0, x1, y1, min(x1 - x0, y1 - y0) * 0.18)
        elif t == "ellipse":
            cr.save()
            cr.translate((x0 + x1) / 2, (y0 + y1) / 2)
            cr.scale(max(0.5, (x1 - x0) / 2), max(0.5, (y1 - y0) / 2))
            cr.arc(0, 0, 1, 0, 2 * math.pi)
            cr.restore()
        elif t in ("line", "arrow"):
            ang = math.atan2(by - ay, bx - ax)
            head = max(lw * 3.2, 8) if t == "arrow" else 0
            ex, ey = bx - math.cos(ang) * head * 0.6, by - math.sin(ang) * head * 0.6
            cr.move_to(ax, ay)
            cr.line_to(ex, ey)
            cr.stroke()
            if t == "arrow":
                cr.move_to(bx, by)
                for side in (1, -1):
                    a2 = ang + math.pi - side * 0.45
                    cr.line_to(bx + math.cos(a2) * head, by + math.sin(a2) * head)
                cr.close_path()
                cr.fill()
            cr.restore()
            return
        elif t == "star":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
            for i in range(10):
                k = 1 if i % 2 == 0 else 0.42
                a = -math.pi / 2 + i * math.pi / 5
                (cr.move_to if i == 0 else cr.line_to)(cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k)
            cr.close_path()
        elif t == "bubble":
            h = y1 - y0
            body = y0 + h * 0.78
            _round_rect(cr, x0, y0, x1, body, min(x1 - x0, body - y0) * 0.25)
            cr.new_sub_path()
            tx = x0 + (x1 - x0) * 0.22
            cr.move_to(tx, body - lw / 2)
            cr.line_to(tx - (x1 - x0) * 0.05, y1)
            cr.line_to(tx + (x1 - x0) * 0.12, body - lw / 2)
        elif t == "check":
            cr.move_to(x0, y0 + (y1 - y0) * 0.55)
            cr.line_to(x0 + (x1 - x0) * 0.38, y1)
            cr.line_to(x1, y0)
        elif t == "cross":
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.move_to(x1, y0)
            cr.line_to(x0, y1)
        elif t == "question":
            lay = _layout(cr, "?", (y1 - y0) * 1.15)
            _i, log = lay.get_pixel_extents()
            cr.move_to((x0 + x1) / 2 - log.width / 2, (y0 + y1) / 2 - log.height / 2)
            PangoCairo.show_layout(cr, lay)
            cr.restore()
            return
        cr.stroke()
    elif t in ("text", "emoji"):
        x, y = it["p"][0][0] * W, it["p"][0][1] * H
        lay = _layout(cr, it.get("text", ""), it.get("s", TEXT_SIZE if t == "text" else EMOJI_SIZE) * S,
                      t == "text")
        if t == "text" and it.get("c", "").lower() in ("#ffffff", "#ffcc00"):
            cr.move_to(x, y)                     # light text: a soft dark edge to read on light pictures
            PangoCairo.layout_path(cr, lay)
            cr.set_source_rgba(0, 0, 0, 0.35)
            cr.set_line_width(max(1.0, it.get("s", TEXT_SIZE) * S * 0.08))
            cr.stroke()
        cr.move_to(x, y)
        cr.set_source_rgba(r, g, b, 1)
        PangoCairo.show_layout(cr, lay)
    elif t == "step":
        x, y = it["p"][0][0] * W, it["p"][0][1] * H
        rad = it.get("s", STEP_SIZE) * S / 2
        cr.arc(x, y, rad, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 1)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.9)
        cr.set_line_width(max(1.0, rad * 0.12))
        cr.stroke()
        light = it.get("c", "").lower() in ("#ffffff", "#ffcc00")
        lay = _layout(cr, str(it.get("n", 1)), rad * 1.15)
        _i, log = lay.get_pixel_extents()
        cr.move_to(x - log.width / 2, y - log.height / 2)
        cr.set_source_rgba(*((0.1, 0.1, 0.1, 1) if light else (1, 1, 1, 1)))
        PangoCairo.show_layout(cr, lay)
    cr.restore()


def draw(cr, items, W, H, source=None) -> None:
    """Every mark, in order, on a context where the picture spans 0..W x 0..H.
    source: a Cairo surface of the picture at that size (pixelate samples it)."""
    for it in items:
        o = it.get("o", 1.0)                     # fading (live drawing's strokes that go away)
        if o >= 0.999:
            _draw_item(cr, it, W, H, source)
        elif o > 0.001:
            cr.push_group()
            _draw_item(cr, it, W, H, source)
            cr.pop_group_to_source()
            cr.paint_with_alpha(o)


def _pil_to_surface(im):
    import cairo
    im = im.convert("RGBA")
    w, h = im.size
    data = bytearray(im.tobytes("raw", "BGRa"))
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, w * 4), data


def apply(image, items):
    """The marks drawn into a PIL image (full size); returns the new image."""
    if not items:
        return image
    from PIL import Image
    mode = image.mode
    surf, _keep = _pil_to_surface(image)
    import cairo
    cr = cairo.Context(surf)
    W, H = image.size
    # pixelate samples the picture as it was (as on screen), not the marks
    # drawn into it so far (a stroke under a pixelated box showed through)
    clean, _keep2 = (_pil_to_surface(image) if any(it.get("t") == "pixelate" for it in items)
                     else (surf, None))
    draw(cr, items, W, H, source=clean)
    surf.flush()
    out = Image.frombuffer("RGBA", (W, H), bytes(surf.get_data()), "raw", "BGRa", 0, 1)
    return out if mode == "RGBA" else out.convert(mode)


def texture_surface(texture):
    """A Cairo surface of a Gdk.Texture (pixelate samples it on screen)."""
    import cairo
    d = Gdk.TextureDownloader.new(texture)
    d.set_format(Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED)
    data, stride = d.download_bytes()
    buf = bytearray(data.get_data())
    return cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_ARGB32, texture.get_width(),
                                              texture.get_height(), stride), buf


# -- the editor: marks over Preview's canvas ------------------------------------------------------
class MarkupLayer(Gtk.DrawingArea):
    """Over the canvas while Markup is on. rect(): where the picture is
    (x, y, w, h); texture(): the picture shown (for pixelate)."""

    def __init__(self, rect, texture, on_change=None):
        super().__init__(hexpand=True, vexpand=True, can_focus=True, focusable=True)
        self.rect, self.texture, self.on_change = rect, texture, on_change
        self.items = []
        self.tool = "pen"
        self.shape = "rect"
        self.emoji = EMOJI[0]
        self.color = COLORS[0]
        self.width = WIDTHS[1]
        self.sel = None                          # the selected mark's index
        self.cur = None                          # the mark being drawn
        self._undo, self._redo = [], []
        self._src = None                         # (texture, surface, buffer) for pixelate
        self.follow = None                       # fn(layer) before each mark: its tool from elsewhere (live drawing)
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda _g, n, x, y: n == 2 and self._double_click(x, y))
        self.add_controller(dbl)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._cursor()

    # -- coordinates ------------------------------------------------------------------------------
    def _pic(self):
        r = self.rect()
        return r if r and r[2] > 0 and r[3] > 0 else None

    def to_frac(self, x, y):
        px, py, pw, ph = self._pic()
        return [(x - px) / pw, (y - py) / ph]

    # -- history ----------------------------------------------------------------------------------
    def _snapshot(self) -> None:
        self._undo.append(copy.deepcopy(self.items))
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.items))
        self.items = self._undo.pop()
        self.sel = None
        self._changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.items))
        self.items = self._redo.pop()
        self.sel = None
        self._changed()
        return True

    def _changed(self) -> None:
        self.queue_draw()
        if self.on_change:
            self.on_change()

    # -- tools ------------------------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self.tool = tool
        if tool != "select":
            self.sel = None
        self._cursor()
        self.queue_draw()

    def _cursor(self) -> None:
        self.set_cursor(Gdk.Cursor.new_from_name({"select": "default", "text": "text"}.get(self.tool, "crosshair")))

    def set_color(self, c: str) -> None:
        self.color = c
        if self.sel is not None:
            self._snapshot()
            self.items[self.sel]["c"] = c
            self._changed()

    def set_width(self, w: float) -> None:
        self.width = w
        if self.sel is not None and "w" in self.items[self.sel]:
            self._snapshot()
            self.items[self.sel]["w"] = w
            self._changed()

    def delete_selected(self) -> None:
        if self.sel is not None:
            self._snapshot()
            del self.items[self.sel]
            self.sel = None
            self._changed()

    def _next_step(self) -> int:
        return 1 + max((it.get("n", 0) for it in self.items if it["t"] == "step"), default=0)

    def hit(self, x, y):
        """The topmost mark under (x, y), or None."""
        pic = self._pic()
        if not pic:
            return None
        px, py, pw, ph = pic
        for i in range(len(self.items) - 1, -1, -1):
            x0, y0, x1, y1 = bounds(self.items[i], pw, ph)
            if px + x0 - 6 <= x <= px + x1 + 6 and py + y0 - 6 <= y <= py + y1 + 6:
                return i
        return None

    def _handle(self):
        """The selected mark's resize handle (bottom right), in widget pixels."""
        pic = self._pic()
        if self.sel is None or not pic:
            return None
        px, py, pw, ph = pic
        x0, y0, x1, y1 = bounds(self.items[self.sel], pw, ph)
        return px + x1, py + y1

    # -- input ------------------------------------------------------------------------------------
    def _begin(self, _g, x, y) -> None:
        if self.follow:
            self.follow(self)
        self.grab_focus()
        if not self._pic():
            return
        f = self.to_frac(x, y)
        t = self.tool
        self._drag = {"x": x, "y": y}
        if t == "select":
            # the undo step is taken on the first real move: a click that only
            # selects is no edit (it pushed one, and cleared Redo)
            h = self._handle()
            if h and abs(x - h[0]) < 10 and abs(y - h[1]) < 10:
                self._drag.update(mode="resize", orig=copy.deepcopy(self.items[self.sel]), moved=False)
                return
            self.sel = self.hit(x, y)
            if self.sel is not None:
                self._drag.update(mode="move", orig=copy.deepcopy(self.items[self.sel]), moved=False)
            self.queue_draw()
            return
        if t == "text":
            if getattr(self, "_text_pop", None) is not None:
                return                                # its field is open (a double click's second press)
            i = self.hit(x, y)
            if i is not None and self.items[i]["t"] == "text":
                self._edit_text_at(x, y)              # a click on a text edits it
            else:
                self._ask_text(x, y)
            return
        if t in ("emoji", "step"):
            self._snapshot()
            it = {"t": t, "c": self.color, "p": [f]}
            if t == "emoji":
                it.update(text=self.emoji, s=EMOJI_SIZE)
                pic = self._pic()
                w, h = text_size(it, pic[2], pic[3])
                f[0] -= w / 2 / pic[2]
                f[1] -= h / 2 / pic[3]                # centred where clicked
            else:
                it.update(n=self._next_step(), s=STEP_SIZE)
            self.items.append(it)
            self._changed()
            return
        kind = self.shape if t == "shape" else t
        self.cur = {"t": kind, "c": self.color, "w": self.width, "p": [f, list(f)] if kind in BOXED else [f]}

    def _update(self, _g, dx, dy) -> None:
        d = getattr(self, "_drag", None)
        pic = self._pic()
        if not d or not pic:
            return
        x, y = d["x"] + dx, d["y"] + dy
        if d.get("mode") and not d.get("moved"):
            if abs(dx) + abs(dy) < 1:
                return
            self._snapshot()                          # before the first change
            d["moved"] = True
        if d.get("mode") == "move" and self.sel is not None:
            fx, fy = dx / pic[2], dy / pic[3]
            self.items[self.sel]["p"] = [[p[0] + fx, p[1] + fy] for p in d["orig"]["p"]]
            self.queue_draw()
            return
        if d.get("mode") == "resize" and self.sel is not None:
            self._resize(d["orig"], dx, dy, pic)
            return
        if self.cur is None:
            return
        f = self.to_frac(x, y)
        if self.cur["t"] in BOXED:
            self.cur["p"][-1] = f
        else:
            last = self.cur["p"][-1]
            if abs((f[0] - last[0]) * pic[2]) + abs((f[1] - last[1]) * pic[3]) >= 2:
                self.cur["p"].append(f)
        self.queue_draw()

    def _resize(self, orig, dx, dy, pic) -> None:
        it = self.items[self.sel]
        t = it["t"]
        if t in BOXED:
            p = copy.deepcopy(orig["p"])
            i = 0 if p[0][0] > p[-1][0] else -1       # the right/bottom corner follows
            p[i][0] += dx / pic[2]
            j = 0 if p[0][1] > p[-1][1] else -1
            p[j][1] += dy / pic[3]
            it["p"] = p
        elif "s" in orig:                             # text, emoji, steps: their size
            x0, y0, x1, y1 = bounds(orig, pic[2], pic[3])
            k = max(0.2, (x1 - x0 + dx) / max(1, x1 - x0))
            it["s"] = max(0.008, orig["s"] * k)
        else:                                         # a stroke: scaled from its top left
            x0, y0, x1, y1 = bounds(orig, pic[2], pic[3])
            kx = max(0.05, (x1 - x0 + dx) / max(1, x1 - x0))
            ky = max(0.05, (y1 - y0 + dy) / max(1, y1 - y0))
            ox, oy = x0 / pic[2], y0 / pic[3]
            it["p"] = [[ox + (p[0] - ox) * kx, oy + (p[1] - oy) * ky] for p in orig["p"]]
        self.queue_draw()

    def _end(self, _g, _dx, _dy) -> None:
        d, self._drag = getattr(self, "_drag", None), None
        if d and d.get("mode"):
            if d.get("moved"):
                self._changed()
            return
        cur, self.cur = self.cur, None
        if cur is None:
            return
        pic = self._pic()
        if not pic:
            self.queue_draw()
            return
        if cur["t"] not in BOXED and len(cur["p"]) == 1:
            # a click with the pen: a dot -- unless it's half of a double
            # click on a text (to edit it), which must leave no dots behind
            self._dots = getattr(self, "_dots", [])
            src = GLib.timeout_add(DOUBLE_CLICK_MS, lambda: (self._commit_dot(cur), False)[1])
            self._dots.append((src, cur))
            return
        if cur["t"] in BOXED:
            x0, y0, x1, y1 = _box(cur, pic[2], pic[3])
            if x1 - x0 < 3 and y1 - y0 < 3:
                self.queue_draw()
                return                                # a click, not a shape
        self._snapshot()
        self.items.append(cur)
        self._changed()

    def _commit_dot(self, cur) -> None:
        self._dots = [(s, c) for s, c in getattr(self, "_dots", []) if c is not cur]
        self._snapshot()
        self.items.append(cur)
        self._changed()

    def _drop_dots(self) -> None:
        for src, _c in getattr(self, "_dots", []):
            GLib.source_remove(src)
        self._dots = []

    def _double_click(self, x, y) -> None:
        i = self.hit(x, y)
        if i is not None and self.items[i]["t"] == "text":
            self._drop_dots()                         # the clicks were to edit it, not to draw
            if getattr(self, "_text_pop", None) is None:
                self._edit_text_at(x, y)

    def _key(self, _c, keyval, _code, state) -> bool:
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.sel is not None:
            self.delete_selected()
            return True
        if keyval == Gdk.KEY_Escape and self.sel is not None:
            self.sel = None
            self.queue_draw()
            return True
        return False

    # -- text -------------------------------------------------------------------------------------
    def _ask_text(self, x, y, index=None) -> None:
        entry = ui.controls.text_field(self.items[index]["text"] if index is not None else "", "Text")
        entry.set_size_request(220, -1)
        pop = Gtk.Popover(child=entry, has_arrow=False, position=Gtk.PositionType.BOTTOM)
        pop.set_parent(self)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        f = self.to_frac(x, y)
        self._text_pop = pop
        state = {"done": False}

        def done(*_a):
            # Return, or the field closed by a click elsewhere (macOS keeps
            # the text then too -- it was thrown away)
            if state["done"]:
                return
            state["done"] = True
            text = entry.get_text().strip()
            pop.popdown()
            if index is not None and (index >= len(self.items) or self.items[index].get("t") != "text"):
                return                                # (the mark went meanwhile)
            if index is not None:
                self._snapshot()
                if text:
                    self.items[index]["text"] = text
                else:
                    del self.items[index]
                    self.sel = None
                self._changed()
            elif text:
                self._snapshot()
                self.items.append({"t": "text", "c": self.color, "p": [f], "text": text, "s": TEXT_SIZE})
                self._changed()
        entry.connect("activate", done)

        def closed(*_a):
            done()
            if getattr(self, "_text_pop", None) is pop:
                self._text_pop = None
            GLib.idle_add(lambda: (pop.unparent(), False)[1])
        pop.connect("closed", closed)
        pop.popup()
        entry.grab_focus()

    def _edit_text_at(self, x, y) -> None:
        i = self.hit(x, y)
        if i is not None and self.items[i]["t"] == "text":
            pic = self._pic()
            it = self.items[i]
            self._ask_text(pic[0] + it["p"][0][0] * pic[2], pic[1] + it["p"][0][1] * pic[3], index=i)

    # -- drawing ----------------------------------------------------------------------------------
    def _source(self, pw, ph):
        tex = self.texture()
        if tex is None:
            return None
        if self._src is None or self._src[0] is not tex:
            try:
                surf, buf = texture_surface(tex)
            except Exception:
                return None
            self._src = (tex, surf, buf)
        return self._src[1]

    def _draw(self, _area, cr, _w, _h) -> None:
        pic = self._pic()
        if not pic:
            return
        px, py, pw, ph = pic
        cr.save()
        cr.translate(px, py)
        src = None
        if any(it["t"] == "pixelate" for it in self.items + ([self.cur] if self.cur else [])):
            surf = self._source(pw, ph)
            if surf is not None:
                import cairo
                src = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(pw)), max(1, int(ph)))
                sc = cairo.Context(src)
                sc.scale(pw / surf.get_width(), ph / surf.get_height())
                sc.set_source_surface(surf, 0, 0)
                sc.paint()
        draw(cr, self.items + ([self.cur] if self.cur else []), pw, ph, src)
        cr.restore()
        if self.sel is not None and self.sel < len(self.items):
            x0, y0, x1, y1 = bounds(self.items[self.sel], pw, ph)
            cr.set_source_rgba(0, 0.48, 1, 0.9)
            cr.set_line_width(1)
            cr.set_dash([4, 3])
            cr.rectangle(px + x0 - 3.5, py + y0 - 3.5, x1 - x0 + 7, y1 - y0 + 7)
            cr.stroke()
            cr.set_dash([])
            hx, hy = px + x1, py + y1                  # the resize handle
            cr.arc(hx, hy, 5, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0, 0.48, 1, 1)
            cr.set_line_width(1.5)
            cr.stroke()


# -- the Markup toolbar ---------------------------------------------------------------------------
def _glyph(name: str, size: int = 16) -> Gtk.DrawingArea:
    """A tool's icon, drawn in the label colour (crisp at any scale, light and dark)."""
    area = Gtk.DrawingArea(content_width=size, content_height=size, can_target=False,
                           halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

    def paint(a, cr, w, h):
        c = a.get_color()
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.scale(w / 16, h / 16)
        cr.set_line_width(1.5)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        GLYPHS[name](cr)
    area.set_draw_func(paint)
    return area


def _g_select(cr):
    cr.move_to(4, 2); cr.line_to(12.5, 8.3); cr.line_to(8.4, 9); cr.line_to(6.6, 13.2); cr.close_path(); cr.stroke()  # noqa: E702


def _g_pen(cr):
    cr.move_to(3, 13); cr.line_to(4, 10); cr.line_to(11, 3); cr.line_to(13, 5); cr.line_to(6, 12)  # noqa: E702
    cr.close_path(); cr.stroke()  # noqa: E702


def _g_hl(cr):
    cr.move_to(5, 11); cr.line_to(11, 3); cr.line_to(14, 6); cr.line_to(8, 12); cr.close_path(); cr.stroke()  # noqa: E702
    cr.move_to(5, 11); cr.line_to(3, 14); cr.line_to(7, 14); cr.line_to(8, 12); cr.stroke()  # noqa: E702


def _g_shape(cr):
    cr.rectangle(2, 2, 7, 7); cr.stroke(); cr.arc(10.5, 10.5, 3.6, 0, 2 * math.pi); cr.stroke()  # noqa: E702


def _g_text(cr):
    cr.move_to(3, 3); cr.line_to(13, 3); cr.move_to(8, 3); cr.line_to(8, 13); cr.stroke()  # noqa: E702


def _g_step(cr):
    cr.arc(8, 8, 6.3, 0, 2 * math.pi); cr.fill()  # noqa: E702
    lay = _layout(cr, "1", 8.5)
    _i, log = lay.get_pixel_extents()
    cr.set_operator(0)                            # CLEAR: the digit cut out
    cr.move_to(8 - log.width / 2, 8 - log.height / 2)
    PangoCairo.show_layout(cr, lay)


def _g_pixel(cr):
    for (x, y, a) in ((2, 2, 1), (10, 2, .4), (6, 6, .75), (2, 10, .4), (10, 10, 1), (6, 2, .2), (2, 6, .6),
                      (10, 6, .3), (6, 10, .55)):
        cr.save(); cr.rectangle(x, y, 4, 4); cr.clip(); cr.paint_with_alpha(a); cr.restore()  # noqa: E702


def _g_undo(cr):
    cr.move_to(5, 3.5); cr.line_to(2.5, 6.5); cr.line_to(5, 9.5); cr.stroke()  # noqa: E702
    cr.move_to(2.5, 6.5); cr.line_to(9.5, 6.5); cr.arc(9.5, 10, 3.5, -math.pi / 2, math.pi / 2)  # noqa: E702
    cr.line_to(7, 13.5); cr.stroke()  # noqa: E702


def _g_redo(cr):
    cr.translate(16, 0); cr.scale(-1, 1); _g_undo(cr)  # noqa: E702


def _g_width(cr):
    for y, w in ((4, 1), (8, 2), (12.5, 3.4)):
        cr.set_line_width(w); cr.move_to(2, y); cr.line_to(14, y); cr.stroke()  # noqa: E702


def _g_trash(cr):
    cr.move_to(3, 4); cr.line_to(13, 4); cr.move_to(6, 4); cr.line_to(6, 2.5); cr.line_to(10, 2.5)  # noqa: E702
    cr.line_to(10, 4); cr.move_to(4.5, 4); cr.line_to(5.2, 13.5); cr.line_to(10.8, 13.5); cr.line_to(11.5, 4)  # noqa: E702
    cr.stroke()


def _g_shape_of(kind):
    def g(cr):
        it = {"t": kind, "c": "#000", "w": 1.4 / 16, "p": [[0.15, 0.15], [0.85, 0.85]]}
        if kind in ("line", "arrow"):
            it["p"] = [[0.15, 0.85], [0.85, 0.15]]
        src = cr.get_source()
        cr.save()
        # drawn with the glyph's colour: the mark's colour is replaced by the source
        r, g_, b, a = src.get_rgba()
        it["c"] = "#%02x%02x%02x" % (int(r * 255), int(g_ * 255), int(b * 255))
        it["w"] = 1.5 / 16
        _draw_item(cr, it, 16, 16)
        cr.restore()
    return g


def _g_fade(cr):
    cr.arc(8, 9, 5.3, 0, 2 * math.pi); cr.stroke()  # noqa: E702
    cr.move_to(8, 6); cr.line_to(8, 9); cr.line_to(10.2, 10.3); cr.move_to(6, 1.8); cr.line_to(10, 1.8)  # noqa: E702
    cr.stroke()


def _g_close(cr):
    cr.move_to(4, 4); cr.line_to(12, 12); cr.move_to(12, 4); cr.line_to(4, 12); cr.stroke()  # noqa: E702


GLYPHS = {"fade": _g_fade, "close": _g_close, "select": _g_select, "pen": _g_pen, "hl": _g_hl, "shape": _g_shape, "text": _g_text, "step": _g_step,
          "pixelate": _g_pixel, "undo": _g_undo, "redo": _g_redo, "width": _g_width, "trash": _g_trash}
for _k in SHAPES:
    GLYPHS["shape-" + _k] = _g_shape_of(_k)


class MarkupBar(Gtk.Box):
    """Tools | colours | width | undo redo delete | Cancel Done, for a MarkupLayer."""
    TOOLS = (("select", "Select (move, resize, recolour; Delete removes)"), ("pen", "Pen"),
             ("hl", "Highlighter"), ("shape", "Shapes"), ("text", "Text"), ("emoji", "Emoji"),
             ("step", "Numbered steps"), ("pixelate", "Pixelate (hide something)"))

    def __init__(self, layer: MarkupLayer, on_done=None, on_cancel=None, tools=None, colors=COLORS, actions=None):
        """tools: [(name, tooltip)] (TOOLS); actions: [(glyph, tooltip, fn)] after
        the width (undo, redo, delete); no on_done: no Cancel / Done (live drawing)."""
        super().__init__(spacing=2, css_classes=["pv-markup"], halign=Gtk.Align.FILL)
        self.layer = layer
        self.tools = {}
        box = Gtk.Box(spacing=2, hexpand=True, halign=Gtk.Align.CENTER)
        for name, tip in tools or self.TOOLS:
            b = Gtk.Button(css_classes=["mk"], tooltip_text=tip, can_focus=False)
            b.set_child(Gtk.Label(label=layer.emoji) if name == "emoji" else _glyph(name))
            b.connect("clicked", lambda _b, n=name: self._tool(n))
            box.append(b)
            self.tools[name] = b
        box.append(Gtk.Box(css_classes=["mk-sep"]))
        self.swatches = {}
        for c in colors:
            b = Gtk.Button(css_classes=["mk-color"], can_focus=False, tooltip_text=c)
            sw = Gtk.DrawingArea(content_width=16, content_height=16, can_target=False)
            sw.set_draw_func(lambda _a, cr, w, h, c=c: self._swatch(cr, w, h, c))
            b.set_child(sw)
            b.connect("clicked", lambda _b, c=c: self._color(c))
            box.append(b)
            self.swatches[c] = sw
        box.append(Gtk.Box(css_classes=["mk-sep"]))
        wb = Gtk.Button(css_classes=["mk"], tooltip_text="Line width", can_focus=False)
        wb.set_child(_glyph("width"))
        wb.connect("clicked", lambda b: self._widths(b))
        box.append(wb)
        for name, tip, fn in actions if actions is not None else (
                ("undo", "Undo", layer.undo), ("redo", "Redo", layer.redo),
                ("trash", "Delete the selected mark", layer.delete_selected)):
            b = Gtk.Button(css_classes=["mk"], tooltip_text=tip, can_focus=False)
            b.set_child(_glyph(name))
            b.connect("clicked", lambda _b, f=fn: f())
            box.append(b)
        self.append(box)
        self.box = box
        if on_done is not None:
            cancel = ui.controls.push_button("Cancel", on_cancel, valign=Gtk.Align.CENTER)
            cancel.add_css_class("mk-done")
            done = ui.controls.push_button("Done", on_done, style="default", valign=Gtk.Align.CENTER)
            done.add_css_class("mk-done")
            self.append(cancel)
            self.append(done)
        self._tool(layer.tool)

    def _swatch(self, cr, w, h, c) -> None:
        r, g, b, _a = _rgba(c)
        on = c == self.layer.color
        cr.arc(w / 2, h / 2, w / 2 - (2.5 if on else 1), 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 1)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(0.75)
        cr.stroke()
        if on:                                     # the chosen one: a ring in the accent
            cr.arc(w / 2, h / 2, w / 2 - 0.75, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0.48, 1, 1)
            cr.set_line_width(1.5)
            cr.stroke()

    def _tool(self, name: str) -> None:
        if name == "shape":
            self._pick(self.tools["shape"], [("shape-" + s, s) for s in SHAPES], "shape")
        elif name == "emoji":
            self._pick(self.tools["emoji"], [(e, e) for e in EMOJI], "emoji")
        self.layer.set_tool(name)
        for n, b in self.tools.items():
            (b.add_css_class if n == name else b.remove_css_class)("on")

    def _pick(self, anchor, choices, what) -> None:
        """Shapes / emoji: a grid under the tool's button."""
        grid = Gtk.FlowBox(max_children_per_line=5 if what == "shape" else 8, selection_mode=Gtk.SelectionMode.NONE,
                           css_classes=["mk-pop"], row_spacing=2, column_spacing=2)
        pop = Gtk.Popover(child=grid, has_arrow=False)
        pop.set_parent(anchor)
        for key, value in choices:
            b = Gtk.Button(can_focus=False)
            b.set_child(_glyph(key, 18) if what == "shape" else Gtk.Label(label=key))
            if value == (self.layer.shape if what == "shape" else self.layer.emoji):
                b.add_css_class("on")

            def chose(_b, v=value):
                if what == "shape":
                    self.layer.shape = v
                    anchor.set_child(_glyph("shape-" + v))
                else:
                    self.layer.emoji = v
                    anchor.set_child(Gtk.Label(label=v))
                pop.popdown()
            b.connect("clicked", chose)
            grid.append(b)
        pop.connect("closed", lambda *_: GLib.idle_add(lambda: (pop.unparent(), False)[1]))
        pop.popup()

    def _widths(self, anchor) -> None:
        box = Gtk.Box(spacing=2, css_classes=["mk-pop"])
        pop = Gtk.Popover(child=box, has_arrow=False)
        pop.set_parent(anchor)
        for i, w in enumerate(WIDTHS):
            b = Gtk.Button(can_focus=False, tooltip_text=("Thin", "Medium", "Thick")[i])
            area = Gtk.DrawingArea(content_width=22, content_height=18, can_target=False)

            def paint(a, cr, ww, hh, i=i):
                c = a.get_color()
                cr.set_source_rgba(c.red, c.green, c.blue, 1)
                cr.set_line_width((1.2, 2.4, 4.2)[i])
                cr.set_line_cap(1)
                cr.move_to(3, hh / 2)
                cr.line_to(ww - 3, hh / 2)
                cr.stroke()
            area.set_draw_func(paint)
            b.set_child(area)
            if abs(w - self.layer.width) < 1e-6:
                b.add_css_class("on")
            b.connect("clicked", lambda _b, w=w: (self.layer.set_width(w), pop.popdown()))
            box.append(b)
        pop.connect("closed", lambda *_: GLib.idle_add(lambda: (pop.unparent(), False)[1]))
        pop.popup()

    def _color(self, c: str) -> None:
        self.layer.set_color(c)
        for sw in self.swatches.values():
            sw.queue_draw()
