"""The menu bar's logo (where macOS has the Apple): Settings > Appearance >
"Menu bar logo" (appearance.json "menu_logo").

    "distro"          the distribution's logo (os-release LOGO=) as a
                      one-colour silhouette in the menu bar's text colour
    "distro-colour"   the same logo in its own colours
    "sonata"          Sonata's logo
    "shape:<name>"    a geometric shape (SHAPES)
    "icon:<name>"     a ready-made symbol (ICONS)

LogoGlyph draws any of them at the menu bar's icon size and follows the
setting live."""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

DEFAULT = "distro"
SHAPES = (("circle", "Circle"), ("square", "Square"), ("triangle", "Triangle"), ("diamond", "Diamond"),
          ("hexagon", "Hexagon"), ("star", "Star"), ("ring", "Ring"))
ICONS = (("starred-symbolic", "Star (outline)"), ("emblem-favorite-symbolic", "Heart"),
         ("weather-clear-symbolic", "Sun"), ("weather-clear-night-symbolic", "Moon"),
         ("audio-x-generic-symbolic", "Music"), ("applications-games-symbolic", "Game controller"),
         ("utilities-terminal-symbolic", "Terminal"), ("face-smile-symbolic", "Smile"))


def choices() -> list:
    """[(value, label)] for Settings."""
    out = [("distro", "Distribution logo"), ("distro-colour", "Distribution logo, in colour"),
           ("sonata", "Sonata")]
    out += [(f"shape:{k}", label) for k, label in SHAPES]
    out += [(f"icon:{k}", label) for k, label in ICONS]
    return out


def _shape_path(name, x, y, s):
    b = Gsk.PathBuilder.new()
    cx, cy, r = x + s / 2, y + s / 2, s / 2
    if name in ("circle", "ring"):
        b.add_circle(Graphene.Point().init(cx, cy), r if name == "circle" else r * 0.92)
    elif name == "square":
        rr = Gsk.RoundedRect()
        rr.init_from_rect(Graphene.Rect().init(x + s * 0.06, y + s * 0.06, s * 0.88, s * 0.88), s * 0.22)
        b.add_rounded_rect(rr)
    else:
        pts = {"triangle": [(90, 1.0), (210, 1.0), (330, 1.0)],
               "diamond": [(90, 1.0), (180, 0.8), (270, 1.0), (0, 0.8)],
               "hexagon": [(90 + 60 * i, 1.0) for i in range(6)],
               "star": [(90 + 36 * i, 1.0 if i % 2 == 0 else 0.45) for i in range(10)]}[name]
        for i, (deg, k) in enumerate(pts):
            a = math.radians(deg)
            px, py = cx + r * k * math.cos(a), cy - r * k * math.sin(a)
            (b.move_to if i == 0 else b.line_to)(px, py)
        b.close()
    return b.to_path()


class LogoGlyph(Gtk.Widget):
    def __init__(self, size: int = 16):
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.size = size
        self.kind = None
        self.texture = None
        self.set_kind(_current())
        from .. import config
        self._mon = config.watch("appearance", lambda *_: self.set_kind(_current()))

    def set_kind(self, kind: str) -> None:
        if kind == self.kind:
            return
        self.kind = kind
        self.texture = None
        if kind in ("distro", "distro-colour", "sonata") or kind.startswith("icon:"):
            self.texture = self._load(kind)
        self.queue_draw()

    def _load(self, kind):
        from .. import icons
        scale = max(1, self.get_scale_factor())
        px = self.size * scale * 2
        if kind.startswith("distro"):
            path = icons.distro_logo(px)
            if path:
                tex = icons._rsvg_texture(path, px) if path.endswith(".svg") else None
                if tex is None:
                    try:
                        tex = Gdk.Texture.new_from_filename(path)
                    except GLib.Error:
                        tex = None
                if tex is not None:
                    if kind == "distro":
                        tex = _glyph_of_tile(path, px) or tex
                    return tex
            kind = "sonata"                           # no distro logo: Sonata's
        name = "sonata-logo-symbolic" if kind == "sonata" else kind.split(":", 1)[1]
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        return theme.lookup_icon(name, None, self.size, scale, Gtk.TextDirection.NONE, 0)

    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snap):
        s = self.size
        x, y = (self.get_width() - s) / 2, (self.get_height() - s) / 2
        color = self.get_color()
        kind = self.kind or DEFAULT
        if kind.startswith("shape:"):
            name = kind.split(":", 1)[1]
            path = _shape_path(name, x, y, s)
            if name == "ring":
                snap.append_stroke(path, Gsk.Stroke.new(s * 0.16), color)
            else:
                snap.append_fill(path, Gsk.FillRule.WINDING, color)
            return
        if self.texture is None:
            return
        if isinstance(self.texture, Gtk.IconPaintable):          # symbolic icons take the text colour
            snap.save()
            snap.translate(Graphene.Point().init(x, y))
            self.texture.snapshot_symbolic(snap, s, s, [color])
            snap.restore()
            return
        tw, th = self.texture.get_width(), self.texture.get_height()
        k = min(s / tw, s / th)
        box = Graphene.Rect().init(x + (s - tw * k) / 2, y + (s - th * k) / 2, tw * k, th * k)
        if kind == "distro-colour":
            snap.append_texture(self.texture, box)
            return
        # one-colour silhouette: the logo's shape, filled with the text colour
        snap.push_mask(Gsk.MaskMode.ALPHA)
        snap.append_texture(self.texture, box)
        snap.pop()
        snap.append_color(color, box)
        snap.pop()


def _glyph_of_tile(path, px):
    """Logos drawn on a solid tile (Ubuntu's orange square): the silhouette
    must be the glyph, not the tile -- pixels of the tile's colour become
    transparent. None when the logo has no such tile."""
    from .. import icons
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = icons.pixbuf_at(path, px)
    if pb is None:
        return None
    tone = icons._edge_tone(pb)
    if not tone:
        return None
    t = Gdk.RGBA()
    t.parse(tone)
    tr, tg, tb = int(t.red * 255), int(t.green * 255), int(t.blue * 255)
    pb = pb.add_alpha(False, 0, 0, 0) if not pb.get_has_alpha() else pb
    w, h, n, stride = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    data = bytearray(pb.get_pixels())
    for yy in range(h):
        for xx in range(w):
            o = yy * stride + xx * n
            if abs(data[o] - tr) + abs(data[o + 1] - tg) + abs(data[o + 2] - tb) < 90:
                data[o + 3] = 0                              # the tile
    out = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB, True, 8,
                                          w, h, stride)
    return Gdk.Texture.new_for_pixbuf(out)


def _current() -> str:
    from .. import config
    from ..icons import APPEARANCE_DEFAULTS
    return config.load("appearance", APPEARANCE_DEFAULTS).get("menu_logo", DEFAULT) or DEFAULT
