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
from collections import OrderedDict

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
                       "menu_text": "", "glass_titlebars": False,
                       "screen_corners": True, "radius": {}, "glass": {}}

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
    from .apps import _entry_field           # (robust to how PyGObject binds GioUnix.DesktopAppInfo)
    wm = _entry_field(info, "get_startup_wm_class", "StartupWMClass")
    exe = os.path.basename(_entry_field(info, "get_executable", "Exec").split(" ")[0])
    cmd = _entry_field(info, "get_commandline", "Exec")
    if exe in LAUNCHERS or "://" in cmd:
        exe = ""        # a game's shortcut (steam steam://rungameid/...): not the launcher's icon
    for name in (did, wm, exe, (info.get_name() or "").replace(" ", "-")):
        if name:
            yield name
            yield name.lower()


LAUNCHERS = {"steam", "lutris", "heroic", "xdg-open", "gtk-launch", "gio", "env"}


def _steam_game_icon(info, shape: str = "squircle"):
    """A Steam game's shortcut (Icon=steam_icon_<id>): its picture filling the
    squircle, like the Dock shows the running game; None otherwise."""
    icon = info.get_icon()
    names = icon.get_names() if isinstance(icon, Gio.ThemedIcon) else []
    for n in names:
        if n.startswith("steam_icon_") and n[11:].isdigit():
            from . import steamgames
            pic = steamgames.icon_path(n[11:])
            return picture_icon(pic, shape=shape) if pic else None
    return None


# -- the user's choices (Settings > App Icons; icons.json) ----------------------------------------
# "shape": every frame's shape; "apps": desktop id -> {"source": "auto" |
# "package" | "file" | "theme", "path": a picture, "name": a theme icon,
# "shape": that app's own shape}. "auto": Sonata's artwork, else the package's.
SHAPES = ("squircle", "circle", "rounded")
SHAPE_TITLES = {"squircle": "Squircle", "circle": "Circle", "rounded": "Rounded Square"}
SOURCES = ("auto", "package", "file", "theme")
# "source": every app's default (an app's own choice wins): "auto" or "package"
ICON_DEFAULTS = {"shape": "squircle", "source": "auto", "apps": {}}
_prefs = {"mtime": None, "value": None}


def prefs() -> dict:
    """icons.json (cached until the file changes)."""
    path = os.path.join(config.CONFIG_DIR, "icons.json")
    try:
        st = os.stat(path)
        mtime = (st.st_mtime_ns, st.st_size)
    except OSError:
        mtime = 0
    if _prefs["value"] is None or mtime != _prefs["mtime"]:
        data = config.load("icons", ICON_DEFAULTS)
        if data.get("shape") not in SHAPES:
            data["shape"] = "squircle"
        if data.get("source") not in ("auto", "package"):
            data["source"] = "auto"
        if not isinstance(data.get("apps"), dict):
            data["apps"] = {}
        _prefs.update(mtime=mtime, value=data)
    return _prefs["value"]


# how big an app's picture sits in its frame: a fraction of the frame's width
# (1.0 fills it, cut to its shape). None: the usual size (PLATE_ARTWORK).
SCALE_RANGE = (0.3, 1.0)


def clamp_scale(v) -> float:
    return round(min(SCALE_RANGE[1], max(SCALE_RANGE[0], float(v))), 2)


def default_scale() -> float:
    return round(PLATE_ARTWORK / (1 - 2 * PLATE_INSET), 2)


def forget_prefs() -> None:
    """Read icons.json again next time (it was just changed)."""
    _prefs["value"] = None


def app_key(info) -> str:
    did = info.get_id() or ""
    return did[:-8] if did.endswith(".desktop") else did


def app_pref(info) -> dict:
    """{"source", "path", "name", "shape"} for this app (shape resolved)."""
    p = prefs()
    own = p["apps"].get(app_key(info)) or {}
    sc = own.get("scale")
    return {"source": own.get("source") if own.get("source") in SOURCES else p["source"],
            "path": own.get("path") or "", "name": own.get("name") or "",
            "scale": clamp_scale(sc) if isinstance(sc, (int, float)) else None,
            "shape": own.get("shape") if own.get("shape") in SHAPES else p["shape"]}


def set_app_pref(did: str, **values) -> None:
    """Change one app's choices (None removes a key; nothing left: back to auto)."""
    data = config.load("icons", ICON_DEFAULTS)
    apps = dict(data.get("apps")) if isinstance(data.get("apps"), dict) else {}   # (never the defaults' dict)
    cur = dict(apps.get(did) or {})
    for k, v in values.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    if cur.get("source") == prefs()["source"]:       # the same as all apps': nothing of its own
        cur.pop("source")
    if cur:
        apps[did] = cur
    else:
        apps.pop(did, None)
    config.update("icons", apps=apps)
    forget_prefs()


def package_icon(info) -> Gio.Icon:
    return info.get_icon() or Gio.ThemedIcon.new("application-x-executable")


def app_icon(info) -> Gio.Icon:
    """The icon to show for an app (Gio.AppInfo): what the user chose for it
    (Settings > App Icons), else Sonata's artwork when any of its names
    match, else the app's own icon -- on the frame, in the chosen shape."""
    pref = app_pref(info)
    shape, scale = pref["shape"], pref["scale"]
    if pref["source"] == "file" and pref["path"]:
        made = picture_icon(pref["path"], shape=shape, artwork=True, scale=scale)
        if made is not None:
            return made
    if pref["source"] == "theme" and pref["name"]:
        return _plated_icon(Gio.ThemedIcon.new(pref["name"]), shape, scale)
    if pref["source"] != "package":
        game = _steam_game_icon(info, shape)
        if game is not None:
            return game
        own = _own()
        for name in _candidates(info):
            if name in own:
                if shape == "squircle":
                    return Gio.ThemedIcon.new(name)
                made = generated(Gio.ThemedIcon.new(name), shape=shape, reshape=True)
                return made if made is not None else Gio.ThemedIcon.new(name)
    return _plated_icon(package_icon(info), shape, scale)


def plated(info) -> bool:
    """Its picture sits on a plate (its size can change): not Sonata's own
    artwork, not a Steam game's picture."""
    pref = app_pref(info)
    if pref["source"] in ("file", "theme", "package"):
        return True
    if _steam_game_icon(info, pref["shape"]) is not None:
        return False
    own = _own()
    return not any(n in own for n in _candidates(info))


def _plated_icon(icon, shape: str, scale=None):
    made = generated(icon, shape=shape, scale=scale)
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


def generated(gicon, shape: str = "squircle", reshape: bool = False, scale=None):
    """Gio.FileIcon of `gicon` on its plate (in `shape`), rendering it if
    needed; None when it can't be rendered here (no display, icon not
    found). reshape: Sonata's own artwork (already on a squircle) cut to
    another shape instead of put on a plate."""
    import hashlib
    display = Gdk.Display.get_default()
    if display is None:
        return None
    src = _resolve(display, 1, gicon, GEN_SIZE if reshape or scale else int(GEN_SIZE * PLATE_ARTWORK))
    f = src.get_file() if src is not None and hasattr(src, "get_file") else None
    path = f.get_path() if f is not None else None
    if not path and src is not None:
        # drawn by librsvg (an SVG with filters: Sonata's own artwork): its file
        # still dates the picture
        path = _theme_path(display, gicon, GEN_SIZE)
    if not path:
        return None
    try:
        stamp = f"{path}\n{int(os.path.getmtime(path))}\n{PLATE_VERSION}\n{shape}\n{reshape}\n{scale}"
    except OSError:
        return None
    name = hashlib.sha1(f"{gicon.to_string()}|{shape}|{reshape}|{scale}".encode()).hexdigest()[:20]
    png, meta = os.path.join(GENERATED, name + ".png"), os.path.join(GENERATED, name + ".src")
    try:
        with open(meta, encoding="utf-8") as fh:
            fresh = fh.read() == stamp and os.path.exists(png)
    except OSError:
        fresh = False
    if not fresh and not _render_plate(display, src, png, meta, stamp, shape=shape, reshape=reshape, scale=scale):
        return None
    return Gio.FileIcon.new(Gio.File.new_for_path(png))


PLATE_VERSION = 5       # bump when the plate's look changes: every icon is made again (4: own tiles)


def picture_icon(path: str, shape: str = "squircle", artwork: bool = False, scale=None):
    """Gio.FileIcon of a picture on the frame: a square one (a Steam game's
    icon) filling it edge to edge, like an iOS app icon; artwork (a custom
    icon the user picked): like an app's own icon (solid tiles fill, logos
    sit on the plate). None when it can't be made. Kept with the generated
    app icons."""
    import hashlib
    display = Gdk.Display.get_default()
    if display is None or not path or not os.path.exists(path):
        return None
    try:
        stamp = f"picture\n{path}\n{int(os.path.getmtime(path))}\n{PLATE_VERSION}\n{shape}\n{artwork}\n{scale}"
    except OSError:
        return None
    name = "pic-" + hashlib.sha1(f"{path}|{shape}|{artwork}|{scale}".encode()).hexdigest()[:20]
    png, meta = os.path.join(GENERATED, name + ".png"), os.path.join(GENERATED, name + ".src")
    try:
        with open(meta, encoding="utf-8") as fh:
            fresh = fh.read() == stamp and os.path.exists(png)
    except OSError:
        fresh = False
    if artwork:
        src = Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(path), GEN_SIZE, 1)
        ok = fresh or _render_plate(display, src, png, meta, stamp, shape=shape, scale=scale)
    elif fresh:
        ok = True                      # cached: the picture isn't decoded at all
    else:
        try:
            tex = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            return None
        ok = _render_plate(display, tex, png, meta, stamp, full=True, shape=shape)
    if not ok:
        return None
    return Gio.FileIcon.new(Gio.File.new_for_path(png))


def _render_plate(display, inner, png, meta, stamp, full: bool = False, shape: str = "squircle",
                  reshape: bool = False, scale=None) -> bool:
    try:
        os.makedirs(GENERATED, exist_ok=True)
        snap = Gtk.Snapshot()
        (_Reshaped(inner, GEN_SIZE, shape) if reshape else _Plate(inner, GEN_SIZE, full, shape, scale)
         ).snapshot(snap, GEN_SIZE, GEN_SIZE)
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
TILE_BLEED = 1.06                     # an icon's own tile: this much past the frame (its edge cut off)
WHITE_TILE_SIZE = 0.56                # what's on an icon's own white tile: this share of the frame
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


def shape_path(shape: str, x, y, w, h):
    """The frame's outline: macOS squircle, a circle, or a rounded square."""
    if shape == "circle":
        b = Gsk.PathBuilder.new()
        b.add_circle(Graphene.Point().init(x + w / 2, y + h / 2), min(w, h) / 2)
        return b.to_path()
    if shape == "rounded":
        rr = Gsk.RoundedRect()
        rr.init_from_rect(Graphene.Rect().init(x, y, w, h), min(w, h) * 0.2)
        b = Gsk.PathBuilder.new()
        b.add_rounded_rect(rr)
        return b.to_path()
    return _squircle(x, y, w, h)


def frame_shape() -> str:
    """The shape every frame follows (folders too)."""
    return prefs()["shape"]


class _Reshaped(GObject.Object, Gdk.Paintable):
    """Sonata's own artwork (drawn on its squircle) cut to another shape: a
    little larger, so its squircle edge falls outside the new outline."""

    def __init__(self, inner, size, shape):
        super().__init__()
        self.inner, self.size, self.shape = inner, size, shape

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_snapshot(self, snap, w, h):
        inset = w * PLATE_INSET
        pw = w - 2 * inset
        path = shape_path(self.shape, inset, inset, pw, pw)
        snap.push_fill(path, Gsk.FillRule.WINDING)
        k = 1.06                                         # the artwork's own edge, outside
        a = w * k
        snap.save()
        snap.translate(Graphene.Point().init((w - a) / 2, (h - a) / 2))
        self.inner.snapshot(snap, a, a)
        snap.restore()
        snap.pop()
        snap.append_stroke(path, Gsk.Stroke.new(max(0.5, w / 256)), _rgba("rgba(0,0,0,0.10)"))


class _Plate(GObject.Object, Gdk.Paintable):
    """An app's own icon on a Big Sur squircle -- white, or the icon's own
    colour when its edges are one solid colour (Claude's orange tile)."""

    def __init__(self, inner, size, full: bool = False, shape: str = "squircle", scale=None):
        super().__init__()
        self.scale = scale                             # the picture's width / the frame's (None: usual)
        self.inner, self.size, self.full = inner, size, full   # full: a picture filling the squircle
        self.shape = shape if shape in SHAPES else "squircle"
        tone = None if full else _solid_edge(inner)
        self.color = tone or PLATE_WHITE               # flat: the icon's own tile blends in
        # an icon with a tile of its own (Claude's orange) fills the frame: drawn
        # small on a plate of its colour, its own rounded edge showed as a thin
        # border (Vini) -- now that edge falls outside the frame's outline
        # (a white tile too: Claude's icon is a white squircle around its orange one)
        own = tone or (None if full else _solid_edge(inner, allow_white=True))
        self.tile = _tile_of(inner) if own and scale is None else None
        # a white tile keeps the usual size of what's on it (filling the frame
        # made Claude's logo too big -- Vini): its own edge is cut off instead,
        # on a plate of exactly its white
        self.white_tile = None
        if own and not tone and self.tile:
            self.color = own
            f = inner.get_file() if hasattr(inner, "get_file") else None
            try:
                pb = pixbuf_at(f.get_path(), 96) if f is not None else None
                self.white_tile = content_box(pb, own) if pb is not None else None
            except (GLib.Error, ValueError, ImportError, AttributeError):
                self.white_tile = None

    def do_get_intrinsic_width(self):
        return self.size

    def do_get_intrinsic_height(self):
        return self.size

    def do_snapshot(self, snap, w, h):
        inset = w * PLATE_INSET
        pw, ph = w - 2 * inset, h - 2 * inset
        rect = Graphene.Rect().init(inset, inset, pw, ph)
        shadow = Gsk.RoundedRect()
        shadow.init_from_rect(rect, pw * (0.5 if self.shape == "circle" else 0.3))
        snap.append_outset_shadow(shadow, _rgba("rgba(0,0,0,0.22)"), 0, w * 0.012, 0, w * 0.02)
        path = shape_path(self.shape, inset, inset, pw, ph)
        snap.push_fill(path, Gsk.FillRule.WINDING)
        snap.append_color(_rgba(self.color), rect)
        if self.full:
            snap.append_scaled_texture(self.inner, Gsk.ScalingFilter.TRILINEAR, rect)
        elif self.white_tile is not None and self.white_tile[2] > 0.2 and self.white_tile[3] > 0.2:
            # only what's on the white tile (its own white, edge and shadow left out),
            # at WHITE_TILE_SIZE of the frame, on the plate's white
            cx0, cy0, cw, ch = self.white_tile
            a = pw * WHITE_TILE_SIZE / max(cw, ch)
            ox, oy = w / 2 - (cx0 + cw / 2) * a, h / 2 - (cy0 + ch / 2) * a
            cut = min(cw, ch) * a * 0.01
            clip = Gsk.RoundedRect()
            clip.init_from_rect(Graphene.Rect().init(ox + cx0 * a + cut, oy + cy0 * a + cut,
                                                     cw * a - 2 * cut, ch * a - 2 * cut), min(cw, ch) * a * 0.22)
            snap.push_rounded_clip(clip)
            snap.save()
            snap.translate(Graphene.Point().init(ox, oy))
            self.inner.snapshot(snap, a, a)
            snap.restore()
            snap.pop()
        elif self.tile is not None and self.tile[2] > 0.3 and self.tile[3] > 0.3:
            tx, ty, tw, th = self.tile
            a = pw * TILE_BLEED / min(tw, th)             # the tile a little bigger than the frame
            cx, cy = (tx + tw / 2) * a, (ty + th / 2) * a  # the tile's centre on the frame's
            snap.save()
            snap.translate(Graphene.Point().init(w / 2 - cx, h / 2 - cy))
            self.inner.snapshot(snap, a, a)
            snap.restore()
        else:
            # the usual size, or the chosen one (up to the whole frame: cut to its shape)
            a = w * PLATE_ARTWORK if self.scale is None else pw * self.scale
            snap.save()
            snap.translate(Graphene.Point().init((w - a) / 2, (h - a) / 2))
            self.inner.snapshot(snap, a, a)
            snap.restore()
        snap.append_linear_gradient(rect, Graphene.Point().init(0, inset), Graphene.Point().init(0, h - inset),
                                    [_stop(0, PLATE_SHEEN[0]), _stop(1, PLATE_SHEEN[1])])
        snap.pop()
        snap.append_stroke(path, Gsk.Stroke.new(max(0.5, w / 256)), _rgba("rgba(0,0,0,0.10)"))


_tones = {}


def tile_box(pb):
    """(x, y, w, h) as fractions of the picture: where its opaque tile is."""
    w, h, n, stride = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    if n < 4:
        return 0.0, 0.0, 1.0, 1.0
    px = pb.get_pixels()
    pts = [(x, y) for y in range(h) for x in range(w) if px[y * stride + x * n + 3] > 200]
    if not pts:
        return 0.0, 0.0, 1.0, 1.0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) / w, min(ys) / h, (max(xs) + 1 - min(xs)) / w, (max(ys) + 1 - min(ys)) / h


_boxes = {}


def content_box(pb, tone: str):
    """(x, y, w, h) fractions: what sits on a tile of colour `tone` (what differs from it)."""
    w, h, n, stride = pb.get_width(), pb.get_height(), pb.get_n_channels(), pb.get_rowstride()
    px = pb.get_pixels()
    t = [int(tone[i:i + 2], 16) for i in (1, 3, 5)]
    pts = [(x, y) for y in range(h) for x in range(w)
           if (n < 4 or px[y * stride + x * n + 3] > 200)
           and sum(abs(px[y * stride + x * n + i] - t[i]) for i in range(3)) > 90]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) / w, min(ys) / h, (max(xs) + 1 - min(xs)) / w, (max(ys) + 1 - min(ys)) / h


def _tile_of(inner):
    """The tile's box in an icon with a tile of its own (cached per file), or None."""
    f = inner.get_file() if hasattr(inner, "get_file") else None
    path = f.get_path() if f is not None else None
    if path is None:
        return None
    if path not in _boxes:
        try:
            pb = pixbuf_at(path, 96)
            _boxes[path] = tile_box(pb) if pb is not None else None
        except (GLib.Error, ValueError, ImportError):
            _boxes[path] = None
    return _boxes[path]


def _solid_edge(inner, allow_white: bool = False):
    """The colour of an icon whose outer edge is one solid colour (a tile
    of its own, like Claude's), as "#rrggbb"; None otherwise (logos on
    transparency, gradients, white tiles). Cached per icon file."""
    path = None
    f = inner.get_file() if hasattr(inner, "get_file") else None
    if f is not None:
        path = f.get_path()
    if path is None:
        return None
    key = (path, allow_white)
    if key in _tones:
        return _tones[key]
    tone = None
    try:
        pb = pixbuf_at(path, 48)
        tone = _edge_tone(pb, allow_white) if pb is not None else None
    except (GLib.Error, ValueError, ImportError):
        pass
    _tones[key] = tone
    return tone


def _edge_tone(pb, allow_white: bool = False):
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
    if close < len(ring) * 0.85 or (min(med) > 225 and not allow_white):
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


def warm_images(images, chunk: int = 6) -> None:
    """Decode these Gtk.Images' pictures ahead of time, a few per idle moment
    (an SVG app icon with soft shadows costs milliseconds to draw the first
    time): the first open that shows them doesn't stutter (Vini: warm the
    system at login). Kept by the icon theme's / the images' own caches."""
    from gi.repository import GLib
    todo = list(images)

    def one(img):
        size = img.get_pixel_size() if img.get_pixel_size() > 0 else 48
        scale = max(1, img.get_scale_factor())
        p = None
        if img.get_storage_type() == Gtk.ImageType.PAINTABLE:
            p = img.get_paintable()
        elif img.get_storage_type() == Gtk.ImageType.GICON and img.get_gicon() is not None:
            theme = Gtk.IconTheme.get_for_display(img.get_display())
            p = theme.lookup_by_gicon(img.get_gicon(), size, scale, Gtk.TextDirection.NONE, 0)
        elif img.get_storage_type() == Gtk.ImageType.ICON_NAME and img.get_icon_name():
            theme = Gtk.IconTheme.get_for_display(img.get_display())
            p = theme.lookup_icon(img.get_icon_name(), None, size, scale, Gtk.TextDirection.NONE, 0)
        if p is not None:
            p.snapshot(Gtk.Snapshot(), size, size)

    def work():
        for _ in range(chunk):
            if not todo:
                return False
            try:
                one(todo.pop())
            except Exception:                     # a warm-up only
                pass
        return True
    GLib.idle_add(work, priority=GLib.PRIORITY_LOW)


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
_rendered = OrderedDict()   # (path, px) -> Gdk.Texture, least recently used first
_RENDERED_MAX = 16 << 20    # bytes of pixels kept (was up to 400 textures: ~100 MB at 256 px)
_rendered_bytes = 0


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
    global _rendered_bytes
    key = (path, px)
    tex = _rendered.get(key)
    if tex is not None:
        _rendered.move_to_end(key)
        return tex
    tex = _render_rsvg(path, px)
    if tex is None:
        return None
    _rendered[key] = tex
    _rendered_bytes += tex.get_width() * tex.get_height() * 4
    while _rendered_bytes > _RENDERED_MAX and len(_rendered) > 1:
        _k, old = _rendered.popitem(last=False)
        _rendered_bytes -= old.get_width() * old.get_height() * 4
    return tex


def paintable(widget: Gtk.Widget, gicon, size: int):
    """Icon paintable (drag icons etc.) with the same fallback as set_image."""
    if gicon.to_string() in _plated:
        return _Plate(_lookup(widget, gicon, int(size * 0.62) or 1), size, shape=frame_shape())
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


def _theme_path(display, gicon, size):
    gicon = for_appearance(gicon)
    theme = Gtk.IconTheme.get_for_display(display)
    if _system is not None and not theme.has_gicon(gicon) and _system.has_gicon(gicon):
        theme = _system
    icon = theme.lookup_by_gicon(gicon, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
    f = icon.get_file() if icon is not None else None
    return f.get_path() if f is not None else None


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
