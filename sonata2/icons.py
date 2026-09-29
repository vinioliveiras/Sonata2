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
on a white Big Sur plate so every app icon has the same shape."""
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
APPEARANCE_DEFAULTS = {"icon_theme": "Sonata", "theme": "mac", "accent": "blue", "reduce_transparency": False,
                       "renderer": "gl"}

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
    for name in (did, wm, exe, (info.get_name() or "").replace(" ", "-")):
        if name:
            yield name
            yield name.lower()


def app_icon(info) -> Gio.Icon:
    """The icon to show for an app (Gio.AppInfo): Sonata's artwork when any
    of its names match, else the app's own icon, marked for the plate."""
    own = _own()
    for name in _candidates(info):
        if name in own:
            return Gio.ThemedIcon.new(name)
    icon = info.get_icon() or Gio.ThemedIcon.new("application-x-executable")
    _plated.add(icon.to_string())
    return icon


class _Plate(GObject.Object, Gdk.Paintable):
    """An app's own icon on a white Big Sur squircle (plate 94 % of the
    tile like MacTahoe's artwork, corners 24 %, artwork 62 %)."""

    def __init__(self, inner, size):
        super().__init__()
        self.inner, self.size = inner, size

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_snapshot(self, snap, w, h):
        inset = w * 0.065
        rect = Graphene.Rect().init(inset, inset, w - 2 * inset, h - 2 * inset)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, (w - 2 * inset) * 0.225)
        snap.push_rounded_clip(rr)
        snap.append_linear_gradient(rect, Graphene.Point().init(0, inset), Graphene.Point().init(0, h - inset),
                                    [_stop(0, "#ffffff"), _stop(1, "#ececec")])
        snap.pop()
        snap.append_border(rr, [0.5] * 4, [_rgba("rgba(0,0,0,0.12)")] * 4)
        a = w * 0.62
        snap.save()
        snap.translate(Graphene.Point().init((w - a) / 2, (h - a) / 2))
        self.inner.snapshot(snap, a, a)
        snap.restore()


def _stop(offset, spec):
    st = Gsk.ColorStop()
    st.offset, st.color = offset, _rgba(spec)
    return st


def _rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


def set_image(image: Gtk.Image, gicon) -> None:
    """Show `gicon` from Sonata's theme, or from the system theme if only
    that one has it (apps from app_icon() without artwork: on the plate)."""
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


def _lookup(widget, gicon, size):
    theme = Gtk.IconTheme.get_for_display(widget.get_display())
    if _system is not None and not theme.has_gicon(gicon) and _system.has_gicon(gicon):
        theme = _system
    scale = widget.get_scale_factor()
    icon = theme.lookup_by_gicon(gicon, size, scale, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
    f = icon.get_file() if icon is not None else None
    path = f.get_path() if f is not None else None
    if path and path.endswith(".svg") and not path.endswith("-symbolic.svg") and _has_filter(path):
        tex = _rsvg_texture(path, size * scale)
        if tex is not None:
            return tex
    return icon
