"""Settings > Displays > Arrange (macOS): the displays drawn where they sit;
drag one to move it (it snaps edge to edge with the others, gliding into
place), drag the white menu bar to another display to make that one the
main display (Dock, desktop icons, notifications).

    snap(rects, name, x, y) -> (x, y)     # pure: where a dragged display lands
    normalize(rects) -> rects             # the layout's top-left at 0,0
    Arrangement(displays, main, on_move, on_main)"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

HEIGHT = 220                  # the drawing's height in Settings
PAD = 24
MIN_OVERLAP = 64              # displays side by side share at least this much edge (layout px)
BAR = 0.07                    # the menu bar's share of a display's height in the drawing


def wallpaper_thumb(width: int = 320):
    """The desktop picture, small (a GdkPixbuf; read in a worker thread), or None."""
    try:
        from gi.repository import Adw, GdkPixbuf, Gio
        from .. import prefs
        dark = Adw.StyleManager.get_default().get_dark()
        uri = prefs.get(prefs.BG, "picture-uri-dark" if dark else "picture-uri") or prefs.get(prefs.BG, "picture-uri")
        path = Gio.File.new_for_uri(uri).get_path() if uri else None
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, -1, True) if path else None
    except Exception:
        return None


def display_title(d) -> str:
    """"Built-in Display", or the monitor's name without its codes:
    'LG Electronics LG ULTRAGEAR 0x00066A07 (HDMI-A-1)' -> 'LG Electronics LG ULTRAGEAR'."""
    from ..backend import system
    if system.is_builtin(d.name):
        return "Built-in Display"
    words = [w for w in (d.description or "").split("(")[0].split() if not w.lower().startswith("0x")]
    return " ".join(words) or d.name


def _overlap(a0, a1, b0, b1) -> int:
    return min(a1, b1) - max(a0, b0)


def _collides(r, rects, skip) -> bool:
    x, y, w, h = r
    return any(n != skip and _overlap(x, x + w, o[0], o[0] + o[2]) > 0 and _overlap(y, y + h, o[1], o[1] + o[3]) > 0
               for n, o in rects.items())


def snap(rects: dict, name: str, x: float, y: float) -> tuple:
    """Where display `name` (rects: {name: (x, y, w, h)}) lands when dropped at
    (x, y): touching another display's edge, sharing at least MIN_OVERLAP of
    it, never overlapping any -- the closest such place."""
    _x, _y, w, h = rects[name]
    others = {n: r for n, r in rects.items() if n != name}
    if not others:
        return 0, 0
    best, best_d = None, None
    for ox, oy, ow, oh in others.values():
        need_v = min(MIN_OVERLAP, h, oh)
        need_h = min(MIN_OVERLAP, w, ow)
        cy = max(oy - h + need_v, min(y, oy + oh - need_v))         # beside: slide along the edge
        cx = max(ox - w + need_h, min(x, ox + ow - need_h))         # above / below
        for px, py in ((ox + ow, cy), (ox - w, cy), (cx, oy + oh), (cx, oy - h)):
            if _collides((px, py, w, h), rects, name):
                continue
            d = (px - x) ** 2 + (py - y) ** 2
            if best_d is None or d < best_d:
                best, best_d = (round(px), round(py)), d
    return best if best is not None else (round(_x), round(_y))


def normalize(rects: dict) -> dict:
    """The same layout moved so its top-left corner is 0,0 (Wayfire's origin)."""
    if not rects:
        return {}
    mx = min(r[0] for r in rects.values())
    my = min(r[1] for r in rects.values())
    return {n: (r[0] - mx, r[1] - my, r[2], r[3]) for n, r in rects.items()}


class Arrangement(Gtk.Widget):
    """The drawing. displays: [system.Display]; on_move({name: (x, y)});
    on_main(name)."""

    def __init__(self, displays, main: str, on_move, on_main, wallpaper=None):
        super().__init__(height_request=HEIGHT, hexpand=True, css_classes=["display-arrange"])
        self.rects = normalize({d.name: (d.x, d.y, *d.size) for d in displays if d.enabled and d.size[0]})
        self.titles = {d.name: display_title(d) for d in displays}
        self.main = main if main in self.rects else next(iter(self.rects), "")
        self.on_move, self.on_main = on_move, on_main
        self.wallpaper = wallpaper            # a small Gdk.Texture drawn in every display, or None
        self.drag = None                      # {"what": "display"|"bar", "name", "dx", "dy"}
        self.shown = {}                       # name -> drawn (x, y): gliding positions
        self.set_cursor(Gdk.Cursor.new_from_name("default"))
        g = Gtk.GestureDrag()
        g.connect("drag-begin", self._begin)
        g.connect("drag-update", self._update)
        g.connect("drag-end", self._end)
        self.add_controller(g)
        ui.on_change(self.queue_draw)

    # -- geometry ------------------------------------------------------------------------
    def _fit(self):
        """(scale, ox, oy): layout px -> widget px, the layout centred."""
        rs = self.rects.values()
        if not rs:
            return 1.0, 0, 0
        bw = max(r[0] + r[2] for r in rs) - min(r[0] for r in rs)
        bh = max(r[1] + r[3] for r in rs) - min(r[1] for r in rs)
        W, H = max(1, self.get_width()), max(1, self.get_height())
        s = min((W - 2 * PAD) / max(1, bw), (H - 2 * PAD) / max(1, bh))
        mx = min(r[0] for r in rs)
        my = min(r[1] for r in rs)
        return s, (W - bw * s) / 2 - mx * s, (H - bh * s) / 2 - my * s

    def _drawn(self, name):
        x, y, w, h = self.rects[name]
        x, y = self.shown.get(name, (x, y))
        if self.drag and self.drag["what"] == "display" and self.drag["name"] == name:
            x, y = self.drag["x"], self.drag["y"]
        return x, y, w, h

    def _widget_rect(self, name, fit=None):
        s, ox, oy = fit or self._fit()
        x, y, w, h = self._drawn(name)
        return ox + x * s, oy + y * s, w * s, h * s

    def hit(self, px, py):
        """("bar"|"display", name) under the pointer, or None."""
        fit = self._fit()
        for name in reversed(list(self.rects)):
            x, y, w, h = self._widget_rect(name, fit)
            if x <= px <= x + w and y <= py <= y + h:
                if name == self.main and py <= y + max(6, h * BAR) + 2:
                    return "bar", name
                return "display", name
        return None

    # -- dragging ------------------------------------------------------------------------
    def _begin(self, _g, px, py):
        what = self.hit(px, py)
        if what is None:
            self.drag = None
            return
        kind, name = what
        x, y, _w, _h = self.rects[name]
        self.drag = {"what": kind, "name": name, "x": x, "y": y, "x0": x, "y0": y, "fit": self._fit(),
                     "px": px, "py": py}

    def _update(self, _g, dx, dy):
        d = self.drag
        if d is None:
            return
        s = d["fit"][0]
        if d["what"] == "display":
            d["x"], d["y"] = d["x0"] + dx / s, d["y0"] + dy / s
        else:
            d["bx"], d["by"] = d["px"] + dx, d["py"] + dy
        self.queue_draw()

    def _end(self, _g, _dx, _dy):
        d, self.drag = self.drag, None
        if d is None:
            return
        if d["what"] == "bar":
            target = self.hit(d.get("bx", d["px"]), d.get("by", d["py"]))
            if target and target[1] != self.main:
                self.main = target[1]
                self.on_main(self.main)
            self.queue_draw()
            return
        name = d["name"]
        tx, ty = snap(self.rects, name, d["x"], d["y"])
        _x, _y, w, h = self.rects[name]
        old = dict(self.rects)
        self.rects[name] = (tx, ty, w, h)
        new = normalize(self.rects)
        moved = new != normalize(old)
        # glide from where it was dropped (the others keep their drawn place: the
        # whole layout may shift when normalized, so they glide too)
        start = {n: (d["x"], d["y"]) if n == name else old[n][:2] for n in self.rects}
        self.rects = new
        shift = (new[name][0] - tx, new[name][1] - ty)
        start = {n: (sx + shift[0], sy + shift[1]) for n, (sx, sy) in start.items()}
        self.shown = dict(start)                  # drawn where it was dropped until the glide runs
        self._glide(start)
        if moved:
            self.on_move({n: (r[0], r[1]) for n, r in new.items()})

    def _glide(self, start: dict) -> None:
        def step(t):
            self.shown = {n: (sx + (self.rects[n][0] - sx) * t, sy + (self.rects[n][1] - sy) * t)
                          for n, (sx, sy) in start.items() if n in self.rects}
            if t >= 1:
                self.shown = {}
            self.queue_draw()
        ui.transition.tween(self, "glide", 0.0, 1.0, 220, step, "displays arrange")

    # -- drawing -------------------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        fit = self._fit()
        accent = ui.rgba("accent")
        for name in self.rects:
            x, y, w, h = self._widget_rect(name, fit)
            rect = Graphene.Rect().init(x, y, w, h)
            rr = Gsk.RoundedRect()
            rr.init_from_rect(rect, 4)
            snap.push_rounded_clip(rr)
            if self.wallpaper is not None:                     # cover-fit, like the desktop
                tw, th = self.wallpaper.get_width(), self.wallpaper.get_height()
                k = max(w / max(1, tw), h / max(1, th))
                cover = Graphene.Rect().init(x + (w - tw * k) / 2, y + (h - th * k) / 2, tw * k, th * k)
                snap.append_scaled_texture(self.wallpaper, Gsk.ScalingFilter.LINEAR, cover)
            else:
                snap.append_color(ui.rgba("module_bg"), rect)
            if name == self.main:                                # its menu bar
                bar = self.drag is not None and self.drag["what"] == "bar"
                white = Gdk.RGBA()
                white.parse("rgba(255,255,255,%s)" % ("0.45" if bar else "0.9"))
                snap.append_color(white, Graphene.Rect().init(x, y, w, max(6, h * BAR)))
            snap.pop()
            dragging = self.drag is not None and self.drag.get("name") == name and self.drag["what"] == "display"
            edge = accent if dragging else ui.rgba("hairline")
            snap.append_border(rr, [2.0 if dragging else 1.0] * 4, [edge] * 4)
            self._label(snap, self.titles.get(name, name), x, y, w, h)
        d = self.drag
        if d is not None and d["what"] == "bar" and "bx" in d:          # the bar follows the pointer
            x, y, w, h = self._widget_rect(d["name"], fit)
            white = Gdk.RGBA()
            white.parse("rgba(255,255,255,0.95)")
            snap.append_color(white, Graphene.Rect().init(d["bx"] - w / 2, d["by"] - 3, w, max(6, h * BAR)))

    def _label(self, snap, text, x, y, w, h) -> None:
        layout = self.create_pango_layout(text)
        layout.set_width(int(max(10, w - 12) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.CENTER)
        _ink, logical = layout.get_pixel_extents()
        snap.save()
        snap.translate(Graphene.Point().init(x + 6, y + (h - logical.height) / 2))
        fg = Gdk.RGBA()
        fg.parse("white")
        shade = Gdk.RGBA()
        shade.parse("rgba(0,0,0,0.55)")
        snap.save()
        snap.translate(Graphene.Point().init(0, 1))          # a soft dark line under the name: readable
        snap.append_layout(layout, shade)                    # on any wallpaper
        snap.restore()
        snap.append_layout(layout, fg)
        snap.restore()
