"""The window buttons' colours (Vini: Settings > Appearance > Button Colours):
Colourful (macOS, the default), Graphite (all grey), Black & White (black on
light, white on dark) or Custom (a colour per button).

    trafficlights.style()              # "color" | "graphite" | "mono" | "custom"
    trafficlights.colors(dark)         # {"close": "#ff5f57", ...}
    trafficlights.folder(dark)         # the pictures for that look: sonata-tl-<name>[-hover].svg, <name>[-hover].png
    trafficlights.apply()              # pictures made, every place told (Sonata's windows, other GTK 4
                                       # apps, Wayfire's title bars, Steam)

The pictures are drawn here (Cairo) in both forms the buttons need: SVG
(GTK, Steam: sharp at any scale) and PNG (pixdecor draws them 1:1), into
~/.cache/sonata2/tl/<look>/<light|dark>/. The default look keeps the
bundled ones (data/icons, data/decor). A glyph on hover is dark on a light
dot and light on a dark one, so every look stays readable."""
import hashlib
import json
import math
import os

from . import config

STYLES = ("color", "graphite", "mono", "custom")
NAMES = ("close", "minimize", "maximize", "restore")
GRAPHITE = "#8e8e93"
MONO = {False: "#1d1d1f", True: "#f5f5f7"}               # black on light, white on dark
RIM = (0, 0, 0, 0.18)
SS = 8                                                   # PNGs: drawn 8x larger, then averaged down


def _settings() -> dict:
    from .icons import APPEARANCE_DEFAULTS
    return config.load("appearance", APPEARANCE_DEFAULTS)


def style() -> str:
    s = _settings().get("buttons_style", "color")
    return s if s in STYLES else "color"


def custom() -> dict:
    from .ui.tokens import TL_COLORS
    c = _settings().get("buttons_colors") or {}
    out = {n: c.get(n) or TL_COLORS[n] for n in ("close", "minimize", "maximize")}
    out["restore"] = out["maximize"]
    return out


def colors(dark: bool, look: str = None, chosen: dict = None) -> dict:
    from .ui.tokens import TL_COLORS
    look = look or style()
    if look == "graphite":
        return {n: GRAPHITE for n in NAMES}
    if look == "mono":
        return {n: MONO[bool(dark)] for n in NAMES}
    if look == "custom":
        return dict(chosen or custom())
    return dict(TL_COLORS)


def _rgb(hexc: str) -> tuple:
    h = hexc.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def glyph_rgba(hexc: str) -> tuple:
    """The hover glyph: dark on a light dot, light on a dark one."""
    r, g, b = _rgb(hexc)
    light = 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45
    return (0, 0, 0, 0.55) if light else (1, 1, 1, 0.85)


def dot(cr, size: float, color: str, glyph=None) -> None:
    """One button on a Cairo context (size x size): the dot, its hairline
    rim, and the glyph on hover (macOS': x, -, two small triangles)."""
    c = size / 2
    cr.arc(c, c, c - 0.2, 0, 2 * math.pi)
    cr.set_source_rgb(*_rgb(color))
    cr.fill()
    cr.arc(c, c, c - 0.45, 0, 2 * math.pi)
    cr.set_source_rgba(*RIM)
    cr.set_line_width(0.5)
    cr.stroke()
    if not glyph:
        return
    cr.set_source_rgba(*glyph_rgba(color))
    cr.set_line_width(1.1)
    cr.set_line_cap(1)                                  # ROUND
    a = size * 0.2
    if glyph == "close":
        cr.move_to(c - a, c - a)
        cr.line_to(c + a, c + a)
        cr.move_to(c - a, c + a)
        cr.line_to(c + a, c - a)
        cr.stroke()
    elif glyph == "minimize":
        cr.move_to(c - a * 1.25, c)
        cr.line_to(c + a * 1.25, c)
        cr.stroke()
    else:                                               # maximize / restore: macOS zoom
        b = size * 0.2
        for sx in (1, -1):
            cr.move_to(c - sx * (b + 0.3), c - sx * (b + 0.3))
            cr.line_to(c + sx * b * 0.55, c - sx * (b + 0.3))
            cr.line_to(c - sx * (b + 0.3), c + sx * b * 0.55)
            cr.close_path()
            cr.fill()


def _png(path: str, size: int, color: str, glyph) -> None:
    import cairo
    big = cairo.ImageSurface(cairo.FORMAT_ARGB32, size * SS, size * SS)
    cr = cairo.Context(big)
    cr.scale(SS, SS)
    dot(cr, size, color, glyph)
    big.flush()
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)    # a box filter, premultiplied
    data, stride = big.get_data(), big.get_stride()
    od, ost = out.get_data(), out.get_stride()
    n = SS * SS
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for yy in range(y * SS, y * SS + SS):
                row = yy * stride
                for xx in range(x * SS, x * SS + SS):
                    o = row + xx * 4
                    for k in range(4):
                        acc[k] += data[o + k]
            o = y * ost + x * 4
            for k in range(4):
                od[o + k] = (acc[k] + n // 2) // n
    out.mark_dirty()
    out.write_to_png(path)


def _svg(path: str, size: int, color: str, glyph) -> None:
    import cairo
    s = cairo.SVGSurface(path, size, size)
    dot(cairo.Context(s), size, color, glyph)
    s.finish()


def _bundled(dark: bool) -> tuple:
    """(svg folder, png folder) of the default look (shipped)."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "data", "icons", "Sonata", "apps", "scalable"), os.path.join(base, "data", "decor")


def _key(dark: bool) -> str:
    look = style()
    cols = colors(dark, look)
    return look + "-" + hashlib.sha1(json.dumps(cols, sort_keys=True).encode()).hexdigest()[:10]


def folder(dark: bool) -> str:
    """Where this look's pictures are (made if needed): SVGs named
    sonata-tl-<name>[-hover].svg, PNGs <name>[-hover].png."""
    if style() == "color":
        return _bundled(dark)[0]
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", "tl",
                     _key(dark), "dark" if dark else "light")
    if not os.path.isfile(os.path.join(d, "restore-hover.png")):
        make(d, colors(dark))
    return d


def png_folder(dark: bool) -> str:
    return _bundled(dark)[1] if style() == "color" else folder(dark)


def make(d: str, cols: dict) -> None:
    from .ui.tokens import FRAME
    size = FRAME["dot"]
    os.makedirs(d, exist_ok=True)
    for n in NAMES:
        for suffix, g in (("", None), ("-hover", n)):
            _svg(os.path.join(d, f"sonata-tl-{n}{suffix}.svg"), size, cols[n], g)
            _png(os.path.join(d, f"{n}{suffix}.png"), size, cols[n], g)


def css_rules(dark: bool) -> str:
    """Sonata's own windows (ui/window.py's .traffic buttons) in this look."""
    if style() == "color":
        return ""
    d = folder(dark)
    url = lambda n: f'url("file://{os.path.join(d, "sonata-tl-" + n + ".svg")}")'   # noqa: E731
    return "\n".join([
        f".traffic button.tl-close {{ background-image: {url('close')}; }}",
        f".traffic button.tl-close:hover {{ background-image: {url('close-hover')}; }}",
        f".traffic button.tl-min {{ background-image: {url('minimize')}; }}",
        f".traffic button.tl-min:hover {{ background-image: {url('minimize-hover')}; }}",
        f".traffic button.tl-zoom {{ background-image: {url('maximize')}; }}",
        f".traffic button.tl-zoom:hover {{ background-image: {url('maximize-hover')}; }}"])


def wayfire_options(dark: bool) -> list:
    """pixdecor's button pictures (Wayfire's title bars) for this look."""
    d = png_folder(dark)
    out = []
    for n in NAMES:
        out.append(("pixdecor", f"button_{n}_image", os.path.join(d, f"{n}.png")))
        out.append(("pixdecor", f"button_{n}_hover_image", os.path.join(d, f"{n}-hover.png")))
    return out


def apply_wayfire(dark: bool) -> None:
    from .backend import system
    for section, key, value in wayfire_options(dark):
        system.wayfire_set(section, key, value)


def apply() -> None:
    """After a change in Settings (in a thread): the pictures, then each place."""
    from .ui import theme
    dark = theme.is_dark() if hasattr(theme, "is_dark") else False
    for d in (False, True):
        folder(d)
    apply_wayfire(dark)
    try:                                                  # other GTK 4 apps (adwstyle)
        from . import adwstyle, titlebars
        adwstyle.write(titlebars.enabled())
    except Exception as e:
        print(f"sonata2: button colours (GTK apps): {e}")
    try:                                                  # Steam, next time it opens
        from . import steamtheme
        if steamtheme.enabled():
            steamtheme.apply()
    except Exception as e:
        print(f"sonata2: button colours (Steam): {e}")
