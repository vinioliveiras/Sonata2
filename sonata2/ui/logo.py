"""The menu bar's logo (where macOS has the Apple): Settings > Appearance >
"Menu bar logo" (appearance.json "menu_logo").

    "distro"          the distribution's logo (os-release LOGO=) as a
                      one-colour silhouette in the menu bar's text colour
    "distro-colour"   the same logo in its own colours
    "sonata"          Sonata's logo
    "shape:<name>"    a geometric shape (SHAPES)
    "icon:<name>"     a ready-made symbol (ICONS)
    "text:sonata"     the word Sonata
    "text:user"       your name (first name of the account's full name)
    "text:custom"     your own text (appearance.json "menu_text"); emoji
                      welcome, drawn in one colour like the apps' tray
                      icons (tray.mono_mask)

LogoGlyph draws any of them at the menu bar's icon size and follows the
setting live."""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

DEFAULT = "distro"
TEXT_HEIGHT = 1.25                  # a text logo's line box, in icon sizes (13 px type in a 16 px slot)
TEXT_MAX = 24                       # characters of custom text
TEXTS = (("text:sonata", "Text: Sonata"), ("text:user", "Text: your name"), ("text:custom", "Text: custom…"))
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
    out += list(TEXTS)
    return out


def text_for(kind: str, custom: str = "") -> str:
    """The words a text: logo shows ("" for other kinds)."""
    if kind == "text:sonata":
        return "Sonata"
    if kind == "text:user":
        return (GLib.get_real_name() or "").split(" ")[0] if GLib.get_real_name() not in ("", "Unknown") \
            else GLib.get_user_name()
    if kind == "text:custom":
        return " ".join((custom or "").split())[:TEXT_MAX] or "Sonata"
    return ""


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
        self.text = ""
        self.set_kind(*_current_with_text())
        from .. import config
        self._mon = config.watch("appearance", lambda *_: self.set_kind(*_current_with_text()))

    def set_kind(self, kind: str, custom: str = "") -> None:
        text = text_for(kind, custom)
        if kind == self.kind and text == self.text:
            return
        self.kind, self.text = kind, text
        self.texture = None
        if kind in ("distro", "distro-colour", "sonata") or kind.startswith("icon:"):
            self.texture = self._load(kind)
        elif text:
            self.texture = self._text_texture(text)
        self.queue_resize()
        self.queue_draw()

    def _text_texture(self, text):
        """The words in the menu bar's bold font, one colour (emoji too)."""
        gi.require_version("PangoCairo", "1.0")
        import cairo
        from gi.repository import Pango, PangoCairo
        scale = max(1, self.get_scale_factor()) * 2
        layout = self.create_pango_layout(text)
        from .tokens import SHARED
        desc = Pango.FontDescription.from_string(       # the menu bar's font (tokens: "font")
            ",".join(f.strip().strip('"') for f in SHARED["font"].split(",")) + " Bold")
        desc.set_absolute_size(13 * Pango.SCALE)
        layout.set_font_description(desc)
        _ink, logical = layout.get_pixel_extents()
        w, h = max(1, logical.width * scale), max(1, logical.height * scale)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        cr.scale(scale, scale)
        cr.set_source_rgb(0, 0, 0)
        PangoCairo.show_layout(cr, layout)
        surf.flush()
        stride, data = surf.get_stride(), surf.get_data()
        rgba = bytearray(len(data))
        for i in range(0, len(data), 4):              # premultiplied BGRA -> RGBA
            b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
            if a:
                r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
            rgba[i:i + 4] = bytes((r, g, b, a))
        bands = []                                    # each character's columns (an emoji: its own tones)
        it = layout.get_iter()
        while True:
            _i, rect = it.get_cluster_extents()
            x0 = int(rect.x / Pango.SCALE * scale)
            bands.append((max(0, x0), min(w, x0 + int(rect.width / Pango.SCALE * scale) + 1)))
            if not it.next_cluster():
                break
        self.text_size = (logical.width, logical.height)
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8,
                                     GLib.Bytes.new(one_colour(rgba, stride, w, h, bands)), stride)

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
        if self.text and self.texture is not None and orientation == Gtk.Orientation.HORIZONTAL:
            w = self.text_size[0] * self.size / max(1, self.text_size[1]) * TEXT_HEIGHT
            return int(w), int(w), -1, -1
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
        if self.text:                                            # words: one colour, the slot's height
            tw, th = self.text_size
            k = s * TEXT_HEIGHT / max(1, th)
            box = Graphene.Rect().init((self.get_width() - tw * k) / 2, (self.get_height() - th * k) / 2,
                                       tw * k, th * k)
            snap.push_mask(Gsk.MaskMode.ALPHA)
            snap.append_texture(self.texture, box)
            snap.pop()
            snap.append_color(color, box)
            snap.pop()
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


def _current_with_text() -> tuple:
    from .. import config
    from ..icons import APPEARANCE_DEFAULTS
    cfg = config.load("appearance", APPEARANCE_DEFAULTS)
    return (cfg.get("menu_logo", DEFAULT) or DEFAULT), (cfg.get("menu_text") or "")


def one_colour(px: bytearray, stride: int, w: int, h: int, bands) -> bytes:
    """A text logo in a single colour (white or black, the menu bar's text):
    each character on its own, its main tone solid and the parts in a
    contrasting tone cut out -- a smiley's face stays, its eyes and mouth
    are holes; a dark game controller stays, its light buttons are holes.
    Plain letters (one tone) are just solid. Only edges keep soft alpha."""
    out = bytearray(len(px))
    for x0, x1 in bands:
        lums = []
        for y in range(h):
            for x in range(x0, x1):
                i = y * stride + x * 4
                if px[i + 3] > 128:
                    lums.append((0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255)
        two_tone = False
        if len(lums) >= 8:
            lums.sort()
            lo, hi, mid = lums[len(lums) // 10], lums[len(lums) * 9 // 10], lums[len(lums) // 2]
            two_tone = hi - lo >= 0.3
        for y in range(h):
            for x in range(x0, x1):
                i = y * stride + x * 4
                a = px[i + 3]
                if two_tone and a:
                    t = abs((0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255 - mid) / (hi - lo)
                    a = int(a * min(1.0, max(0.0, (0.55 - t) / 0.2)))     # far from the main tone: a hole
                a = min(255, max(0, (a - 48) * 255 // 160))                  # no see-through greys
                out[i + 3] = max(out[i + 3], a)
    return bytes(out)
