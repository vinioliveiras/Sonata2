"""Sonata's own icons, independent of the system icon theme.

The shell process looks icons up in the bundled `Sonata` theme (our
overrides -> Sonata-MacTahoe -> hicolor), chosen in Sonata's own settings
(`appearance.json`, key `icon_theme`), never in the Linux one. Only when an
app's icon exists in none of those does it fall back to the system theme.
setup() must run once, before any widget is created.

App icons (app_icon): the desktop entry's icon is looked up under several
names (Icon=, desktop id, StartupWMClass, executable...) in Sonata's own
themes, so e.g. an absolute Icon=/opt/spotify/spotify.png still gets the
MacTahoe artwork. Apps Sonata has no artwork for keep their own icon, drawn
on a Big Sur squircle plate (MacTahoe's shape) so every app icon has the
same shape; the plate takes the icon's colour when the icon is a solid
tile of its own."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import config  # noqa: E402

ICONS_DIR = os.path.join(os.path.dirname(__file__), "data", "icons")
# Sonata's appearance.json (config.load keeps only known keys: every
# reader passes these defaults)
APPEARANCE_DEFAULTS = {"flatpak_theme": True, "icon_theme": "Sonata", "theme": "mac", "accent": "blue", "reduce_transparency": False,
                       "renderer": "gl", "system_titlebars": True, "menu_logo": "distro",
                       "menu_text": ""}

_system = None     # Gtk.IconTheme with the system's theme, for fallbacks


def setup() -> None:
    global _system
    settings = Gtk.Settings.get_default()
    display = Gdk.Display.get_default()
    _system = Gtk.IconTheme(theme_name=settings.get_property("gtk-icon-theme-name"))
    Gtk.IconTheme.get_for_display(display).add_search_path(ICONS_DIR)
    # Process-local: changes only this shell's lookups, not the system setting.
    settings.set_property("gtk-icon-theme-name",
                          config.load("appearance", APPEARANCE_DEFAULTS)["icon_theme"])


# -- app icons -------------------------------------------------------------------------------
_own_names = None      # icon names Sonata's themes draw (app artwork)
_plated = set()        # gicon strings to draw on the white plate


def _own():
    global _own_names
    if _own_names is None:
        _own_names = set()
        for rel in ("Sonata/apps/scalable", "Sonata-MacTahoe/apps/scalable"):
            try:
                _own_names.update(os.path.splitext(n)[0] for n in os.listdir(os.path.join(ICONS_DIR, rel)))
            except OSError:
                pass
    return _own_names


def _candidates(info):
    icon = info.get_icon()
    if isinstance(icon, Gio.ThemedIcon):
        yield from icon.get_names()
    elif isinstance(icon, Gio.FileIcon):
        base = os.path.splitext(icon.get_file().get_basename() or "")[0]
        yield base
        yield base.split("-linux")[0]                   # spotify-linux-512 -> spotify
    did = (info.get_id() or "")[:-8] if (info.get_id() or "").endswith(".desktop") else (info.get_id() or "")
    wm = info.get_startup_wm_class() if hasattr(info, "get_startup_wm_class") else None
    exe = os.path.basename(info.get_executable() or "")
    cmd = (info.get_commandline() or "") if hasattr(info, "get_commandline") else ""
    if exe in LAUNCHERS or "://" in cmd:
        exe = ""        # a game's shortcut (steam steam://rungameid/...): not the launcher's icon
    for name in (did, wm, exe, (info.get_name() or "").replace(" ", "-")):
        if name:
            yield name
            yield name.lower()


LAUNCHERS = {"steam", "lutris", "heroic", "xdg-open", "gtk-launch", "gio", "env"}


def _steam_game_icon(info):
    """A Steam game's shortcut (Icon=steam_icon_<id>): its picture filling the
    squircle, like the Dock shows the running game; None otherwise."""
    icon = info.get_icon()
    names = icon.get_names() if isinstance(icon, Gio.ThemedIcon) else []
    for n in names:
        if n.startswith("steam_icon_") and n[11:].isdigit():
            from . import steamgames
            pic = steamgames.icon_path(n[11:])
            return picture_icon(pic) if pic else None
    return None


def app_icon(info) -> Gio.Icon:
    """The icon to show for an app (Gio.AppInfo): Sonata's artwork when any
    of its names match, else the app's own icon, marked for the plate."""
    game = _steam_game_icon(info)
    if game is not None:
        return game
    own = _own()
    for name in _candidates(info):
        if name in own:
            return Gio.ThemedIcon.new(name)
    icon = info.get_icon() or Gio.ThemedIcon.new("application-x-executable")
    made = generated(icon)
    if made is not None:
        return made
    _plated.add(icon.to_string())                 # drawn live (no display to render with)
    return icon


# -- generated app icons: plated once, saved to disk ---------------------------------------------
# App icons without Sonata artwork are rendered on their plate once and kept
# as PNGs; the same file then shows everywhere (Dock, Launchpad, Files...).
# A new version of the app's icon (another file or date) makes a new one.
# Settings > Appearance > "Regenerate" empties the folder.
GENERATED = os.path.join(GLib.get_user_cache_dir(), "sonata2", "app-icons")
GEN_SIZE = 256


def generated(gicon):
    """Gio.FileIcon of `gicon` on its plate, rendering it if needed; None
    when it can't be rendered here (no display, icon not found)."""
    import hashlib
    display = Gdk.Display.get_default()
    if display is None:
        return None
    src = _resolve(display, 1, gicon, int(GEN_SIZE * PLATE_ARTWORK))
    f = src.get_file() if src is not None and hasattr(src, "get_file") else None
    path = f.get_path() if f is not None else None
    if not path:
        return None
    try:
        stamp = f"{path}\n{int(os.path.getmtime(path))}\n{PLATE_VERSION}"
    except OSError:
        return None
    name = hashlib.sha1(gicon.to_string().encode()).hexdigest()[:20]
    png, meta = os.path.join(GENERATED, name + ".png"), os.path.join(GENERATED, name + ".src")
    try:
        with open(meta, encoding="utf-8") as fh:
            fresh = fh.read() == stamp and os.path.exists(png)
    except OSError:
        fresh = False
    if not fresh and not _render_plate(display, src, png, meta, stamp):
        return None
    return Gio.FileIcon.new(Gio.File.new_for_path(png))


PLATE_VERSION = 1       # bump when the plate's look changes: every icon is made again


def picture_icon(path: str):
    """Gio.FileIcon of a square picture (a Steam game's icon) filling the
    Big Sur squircle edge to edge, like an iOS app icon; None when it can't
    be made. Made once, kept with the generated app icons."""
    import hashlib
    display = Gdk.Display.get_default()
    if display is None or not path or not os.path.exists(path):
        return None
    try:
        stamp = f"picture\n{path}\n{int(os.path.getmtime(path))}\n{PLATE_VERSION}"
        tex = Gdk.Texture.new_from_filename(path)
    except (OSError, GLib.Error):
        return None
    name = "pic-" + hashlib.sha1(path.encode()).hexdigest()[:20]
    png, meta = os.path.join(GENERATED, name + ".png"), os.path.join(GENERATED, name + ".src")
    try:
        with open(meta, encoding="utf-8") as fh:
            fresh = fh.read() == stamp and os.path.exists(png)
    except OSError:
        fresh = False
    if not fresh and not _render_plate(display, tex, png, meta, stamp, full=True):
        return None
    return Gio.FileIcon.new(Gio.File.new_for_path(png))


def _render_plate(display, inner, png, meta, stamp, full: bool = False) -> bool:
    try:
        os.makedirs(GENERATED, exist_ok=True)
        snap = Gtk.Snapshot()
        _Plate(inner, GEN_SIZE, full).snapshot(snap, GEN_SIZE, GEN_SIZE)
        node = snap.to_node()
        renderer = Gsk.CairoRenderer.new()
        renderer.realize_for_display(display)
        tex = renderer.render_texture(node, Graphene.Rect().init(0, 0, GEN_SIZE, GEN_SIZE))
        renderer.unrealize()
        tmp = png + f".{os.getpid()}.tmp"                 # several shell processes may do it at once
        tex.save_to_png(tmp)
        os.replace(tmp, png)
        with open(meta + f".{os.getpid()}.tmp", "w", encoding="utf-8") as fh:
            fh.write(stamp)
        os.replace(meta + f".{os.getpid()}.tmp", meta)
        return True
    except (OSError, GLib.Error, TypeError, AttributeError):
        return False


def clear_generated() -> None:
    """Settings: forget every generated icon (made again when next shown)."""
    import shutil
    shutil.rmtree(GENERATED, ignore_errors=True)


# The plate's geometry, measured on MacTahoe's own artwork (so app icons we
# plate match the ones we have): a superellipse |x|^4 + |y|^4 = 1 filling
# 89 % of the tile, a soft shadow under it, the app's icon at 62 %.
PLATE_INSET = 0.055
PLATE_EXPONENT = 4.0
PLATE_ARTWORK = 0.62
PLATE_WHITE = "#ffffff"
# the sheen over the whole plate *and* the icon on it (drawn last, so an
# icon's own tile and the plate shade the same way): top, bottom
PLATE_SHEEN = ("rgba(255,255,255,0.10)", "rgba(0,0,0,0.075)")


def _squircle(x, y, w, h, n=PLATE_EXPONENT, steps=96):
    import math
    b = Gsk.PathBuilder.new()
    cx, cy, ax, ay = x + w / 2, y + h / 2, w / 2, h / 2
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s_ = math.cos(t), math.sin(t)
        px = cx + ax * math.copysign(abs(c) ** (2 / n), c)
        py = cy + ay * math.copysign(abs(s_) ** (2 / n), s_)
        (b.move_to if i == 0 else b.line_to)(px, py)
    b.close()
    return b.to_path()


class _Plate(GObject.Object, Gdk.Paintable):
    """An app's own icon on a Big Sur squircle -- white, or the icon's own
    colour when its edges are one solid colour (Claude's orange tile)."""

    def __init__(self, inner, size, full: bool = False):
        super().__init__()
        self.inner, self.size, self.full = inner, size, full   # full: a picture filling the squircle
        tone = None if full else _solid_edge(inner)
        self.color = tone or PLATE_WHITE               # flat: the icon's own tile blends in

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_snapshot(self, snap, w, h):
        inset = w * PLATE_INSET
        pw, ph = w - 2 * inset, h - 2 * inset
        rect = Graphene.Rect().init(inset, inset, pw, ph)
        shadow = Gsk.RoundedRect()
        shadow.init_from_rect(rect, pw * 0.3)
        snap.append_outset_shadow(shadow, _rgba("rgba(0,0,0,0.22)"), 0, w * 0.012, 0, w * 0.02)
        path = _squircle(inset, inset, pw, ph)
        snap.push_fill(path, Gsk.FillRule.WINDING)
        snap.append_color(_rgba(self.color), rect)
        if self.full:
            snap.append_scaled_texture(self.inner, Gsk.ScalingFilter.TRILINEAR, rect)
        else:
            a = w * PLATE_ARTWORK
            snap.save()
            snap.translate(Graphene.Point().init((w - a) / 2, (h - a) / 2))
            self.inner.snapshot(snap, a, a)
            snap.restore()
        snap.append_linear_gradient(rect, Graphene.Point().init(0, inset), Graphene.Point().init(0, h - inset),
                                    [_stop(0, PLATE_SHEEN[0]), _stop(1, PLATE_SHEEN[1])])
        snap.pop()
        snap.append_stroke(path, Gsk.Stroke.new(max(0.5, w / 256)), _rgba("rgba(0,0,0,0.10)"))


_tones = {}


def _solid_edge(inner):
    """The colour of an icon whose outer edge is one solid colour (a tile
    of its own, like Claude's), as "#rrggbb"; None otherwise (logos on
    transparency, gradients, white tiles). Cached per icon file."""
    path = None
    f = inner.get_file() if hasattr(inner, "get_file") else None
    if f is not None:
        path = f.get_path()
    if path is None:
        return None
    if path in _tones:
        return _tones[path]
    tone = None
    try:
        pb = pixbuf_at(path, 48)
        tone = _edge_tone(pb) if pb is not None else None
    except (GLib.Error, ValueError, ImportError):
        pass
    _tones[path] = tone
    return tone


def _edge_tone(pb):
    if not pb.get_has_alpha() and pb.get_n_channels() < 3:
        return None
    w, h, n, stride = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    px = pb.get_pixels()

    def at(x, y):
        o = y * stride + x * n
        return px[o], px[o + 1], px[o + 2], (px[o + 3] if n == 4 else 255)
    opaque = [(x, y) for y in range(h) for x in range(w) if at(x, y)[3] > 200]
    if len(opaque) < w * h * 0.5:
        return None                                  # a logo on transparency: white plate
    xs, ys = [p[0] for p in opaque], [p[1] for p in opaque]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    m = max(2, (x1 - x0) // 8)                       # a ring just inside the edge (past rounded corners)
    ring = [at(x, y) for x in range(x0 + m, x1 - m + 1) for y in (y0 + 2, y1 - 2)] + \
           [at(x, y) for y in range(y0 + m, y1 - m + 1) for x in (x0 + 2, x1 - 2)]
    ring = [c for c in ring if c[3] > 200]
    if len(ring) < 20:
        return None
    med = [sorted(c[i] for c in ring)[len(ring) // 2] for i in range(3)]
    close = sum(1 for c in ring if sum(abs(c[i] - med[i]) for i in range(3)) < 48)
    if close < len(ring) * 0.85 or min(med) > 225:
        return None                                  # not one colour, or white already
    return "#%02x%02x%02x" % tuple(med)


def _stop(offset, spec):
    st = Gsk.ColorStop()
    st.offset, st.color = offset, _rgba(spec)
    return st


def _rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


# Icons drawn in two versions, one per appearance (macOS: the Trash and
# Launchpad follow Light/Dark): name -> (Light name, Dark name)
APPEARANCE_VARIANTS = {"user-trash": ("user-trash", "user-trash-dark"),
                       "user-trash-full": ("user-trash-full", "user-trash-full-dark"),
                       "sonata-launchpad": ("sonata-launchpad-light", "sonata-launchpad")}


def _dark() -> bool:
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        return Adw.StyleManager.get_default().get_dark()
    except (ValueError, ImportError):
        return False


def for_appearance(gicon):
    """`gicon` in the current appearance's version (see APPEARANCE_VARIANTS)."""
    if isinstance(gicon, Gio.ThemedIcon):
        names = gicon.get_names()
        pair = APPEARANCE_VARIANTS.get(names[0]) if names else None
        if pair:
            return Gio.ThemedIcon.new(pair[1] if _dark() else pair[0])
    return gicon


def set_image(image: Gtk.Image, gicon) -> None:
    """Show `gicon` from Sonata's theme, or from the system theme if only
    that one has it (apps from app_icon() without artwork: on the plate)."""
    gicon = for_appearance(gicon)
    size = image.get_pixel_size() if image.get_pixel_size() > 0 else 48
    if gicon.to_string() in _plated:
        image.set_from_paintable(paintable(image, gicon, size))
        return
    found = _lookup(image, gicon, size)
    if isinstance(found, Gdk.Texture):            # drawn by librsvg (see below)
        image.set_from_paintable(found)
        return
    theme = Gtk.IconTheme.get_for_display(image.get_display())
    if _system is None or theme.has_gicon(gicon) or not _system.has_gicon(gicon):
        image.set_from_gicon(gicon)               # follows icon theme changes
        return
    image.set_from_paintable(found)


def distro_logo(size: int = 128):
    """File of the system's logo (os-release LOGO=, e.g. "cachyos"; then
    distributor-logo-<ID>, the ID_LIKE family...) from the icon themes
    (Sonata's bundled distributor logos included) or /usr/share/pixmaps.
    None when the distro has none."""
    osr = {}
    try:
        with open("/etc/os-release") as f:
            osr = {k: v.strip().strip('"') for k, v in (ln.split("=", 1) for ln in f if "=" in ln)}
    except OSError:
        pass
    ids = [osr.get("ID", "")] + osr.get("ID_LIKE", "").split()
    names = [osr.get("LOGO", "")]
    for i in ids:
        names += [f"distributor-logo-{i}", f"{i}-logo", i] if i else []
    names = [n for n in names if n]
    display = Gdk.Display.get_default()
    themes = [t for t in (Gtk.IconTheme.get_for_display(display) if display else None, _system) if t]
    for n in names:
        for t in themes:
            if t.has_icon(n):
                f = t.lookup_icon(n, None, size, 1, Gtk.TextDirection.NONE, 0).get_file()
                if f and f.get_path():
                    return f.get_path()
        for ext in ("svg", "png"):
            if os.path.exists(f"/usr/share/pixmaps/{n}.{ext}"):
                return f"/usr/share/pixmaps/{n}.{ext}"
    return None


def set_logo(image: Gtk.Image) -> None:
    """`image` shows the system's logo at its pixel size, in its own
    colours (librsvg: GTK's SVG renderer ignores <style> sheets); the
    Sonata logo when the distro has none."""
    px = image.get_pixel_size() if image.get_pixel_size() > 0 else 96
    path = distro_logo(px)
    tex = None
    if path and path.endswith(".svg"):
        tex = _rsvg_texture(path, px * 2)                     # sharp on HiDPI
    elif path:
        try:
            tex = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            tex = None
    if tex is not None:
        image.set_from_paintable(tex)
    else:
        image.set_from_icon_name("sonata-logo-symbolic")


# Full-colour SVG icons with filters (the soft shadows in MacTahoe's artwork)
# are drawn by librsvg through GdkPixbuf: newer GTK SVG renderers draw those
# filters as a stray translucent square at the top left.
_filtered = {}          # svg path -> bool
_rendered = {}          # (path, px) -> Gdk.Texture


def _has_filter(path: str) -> bool:
    v = _filtered.get(path)
    if v is None:
        try:
            with open(path, "rb") as f:
                v = b"<filter" in f.read()
        except OSError:
            v = False
        _filtered[path] = v
    return v


def _render_rsvg(path: str, px: int):
    """librsvg (Rsvg typelib) -> cairo -> Gdk.MemoryTexture; None if unavailable."""
    try:
        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg
        import cairo
        handle = Rsvg.Handle.new_from_file(path)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
        cr = cairo.Context(surf)
        vp = Rsvg.Rectangle()
        vp.x, vp.y, vp.width, vp.height = 0, 0, px, px
        handle.render_document(cr, vp)
        surf.flush()
        return Gdk.MemoryTexture.new(px, px, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                     GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())
    except (ValueError, ImportError, GLib.Error, AttributeError):
        pass
    try:                                           # GdkPixbuf's SVG loader is librsvg too
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, px, px, True)
        return Gdk.Texture.new_for_pixbuf(pb)
    except (GLib.Error, ImportError):
        return None


def pixbuf_at(path: str, px: int):
    """GdkPixbuf of an image file at px (SVG through librsvg, which a
    GdkPixbuf without its SVG loader can't read); None if unreadable."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    if path.endswith(".svg"):
        tex = _render_rsvg(path, px)
        if tex is None:
            return None
        dl = Gdk.TextureDownloader.new(tex)
        dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = dl.download_bytes()
        return GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, True, 8,
                                               tex.get_width(), tex.get_height(), stride)
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, px, px)
    except GLib.Error:
        return None


def _rsvg_texture(path: str, px: int):
    key = (path, px)
    tex = _rendered.get(key)
    if tex is None:
        tex = _render_rsvg(path, px)
        if tex is None:
            return None
        if len(_rendered) > 400:
            _rendered.clear()
        _rendered[key] = tex
    return tex


def paintable(widget: Gtk.Widget, gicon, size: int):
    """Icon paintable (drag icons etc.) with the same fallback as set_image."""
    if gicon.to_string() in _plated:
        return _Plate(_lookup(widget, gicon, int(size * 0.62) or 1), size)
    return _lookup(widget, gicon, size)


_hicolor_theme = None


def _hicolor():
    global _hicolor_theme
    if _hicolor_theme is None:
        _hicolor_theme = Gtk.IconTheme(theme_name="hicolor")
    return _hicolor_theme


def _is_symbolic(path: str) -> bool:
    base = os.path.basename(path)
    return "-symbolic." in base or ".symbolic." in base


def _asks_symbolic(gicon) -> bool:
    names = gicon.get_names() if isinstance(gicon, Gio.ThemedIcon) else []
    return bool(names) and all(n.endswith("-symbolic") for n in names)


def _lookup(widget, gicon, size):
    return _resolve(widget.get_display(), widget.get_scale_factor(), gicon, size)


def _resolve(display, scale, gicon, size):
    gicon = for_appearance(gicon)
    theme = Gtk.IconTheme.get_for_display(display)
    if _system is not None and not theme.has_gicon(gicon) and _system.has_gicon(gicon):
        theme = _system
    icon = theme.lookup_by_gicon(gicon, size, scale, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
    f = icon.get_file() if icon is not None else None
    path = f.get_path() if f is not None else None
    if path and _is_symbolic(path) and not _asks_symbolic(gicon):
        # GTK falls back to "<name>-symbolic" inside the first theme that has
        # it (Sonata-MacTahoe draws some app logos as grey symbolics) before
        # looking in the next theme, where the app's colour icon is
        for other in (_system, _hicolor()):
            if other is None or other is theme or not other.has_gicon(gicon):
                continue
            alt = other.lookup_by_gicon(gicon, size, scale, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
            af = alt.get_file() if alt is not None else None
            if af is not None and af.get_path() and not _is_symbolic(af.get_path()):
                icon, path = alt, af.get_path()
                break
    if path and path.endswith(".svg") and not path.endswith("-symbolic.svg") and _has_filter(path):
        tex = _rsvg_texture(path, size * scale)
        if tex is not None:
            return tex
    return icon
