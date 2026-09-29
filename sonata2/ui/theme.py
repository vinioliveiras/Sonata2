"""Loads every component's CSS, filled with the tokens of the current
appearance, into one provider; reloads it when light/dark changes.

Components call `register(template)` at import time (or when their
geometry is known). Templates use
`%(token)s` placeholders (write `%%` for a literal percent sign)."""
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gtk  # noqa: E402

from . import tokens  # noqa: E402

# Above USER priority: a user GTK theme (e.g. a macOS-look theme in
# ~/.config/gtk-4.0) must not repaint Sonata's components.
PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_USER + 10

_templates = {}       # key -> (template, local placeholders)
_extra = {}           # token overrides (future: user customization)
_provider = None
_listeners = []       # called after every reload (light/dark switch)
_parsed = {}          # (token, dark) -> parsed value, for snapshot drawing


def register(template: str, key: str = None, **local) -> None:
    """Add a component's CSS. `local` = extra placeholders for this template
    only (e.g. geometry from the component's settings). Registering the same
    `key` again replaces it."""
    _templates[key or template] = (template, local)
    if _provider:
        _load()


def is_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def glass() -> bool:
    """True when the compositor blurs Sonata windows (Sonata session or the
    dev session: SONATA_GLASS=1 from session-env.sh). Elsewhere materials
    fall back to their solid tokens."""
    import os
    return os.environ.get("SONATA_GLASS") == "1" and not reduce_transparency()


def reduce_transparency() -> bool:
    """Accessibility > Reduce transparency (read when the process starts)."""
    global _reduce
    if _reduce is None:
        from .. import config
        _reduce = bool(config.load("appearance", _appearance_defaults())["reduce_transparency"])
    return _reduce


_reduce = None


def reduce_transparency_now() -> bool:
    """The setting as it is on disk now (for code that follows changes live)."""
    from .. import config
    return bool(config.load("appearance", _appearance_defaults())["reduce_transparency"])


def values() -> dict:
    """Current tokens (for code that needs a value, e.g. drawing), plus the
    materials resolved for this compositor: `sidebar_material`, `panel_material`."""
    v = {**tokens.palette(is_dark(), _theme()), **tokens.accent_tokens(_accent(), is_dark()), **_extra}
    # One glass for the whole system: sidebars use exactly the Dock's tint
    # over the same compositor blur.
    v["sidebar_material"] = v["glass_tint" if glass() else "sidebar_bg"]
    v["panel_material"] = v["glass_tint" if glass() else "menu_bg"]      # the Dock's / sidebars' glass
    return v


_theme_name = None
_accent_name = None


def _appearance_defaults() -> dict:
    from ..icons import APPEARANCE_DEFAULTS
    return APPEARANCE_DEFAULTS


def _accent() -> str:
    """Accent colour from Sonata's appearance settings (live: see setup)."""
    global _accent_name
    if _accent_name is None:
        from .. import config
        _accent_name = config.load("appearance", _appearance_defaults())["accent"]
    return _accent_name


def _appearance_changed() -> None:
    """appearance.json changed (Settings): new accent, re-style everything."""
    global _accent_name
    old = _accent_name
    _accent_name = None
    if _accent() != old:
        _parsed.clear()
        _load(fade=True)


def _theme() -> str:
    """Visual theme from Sonata's appearance settings (read once)."""
    global _theme_name
    if _theme_name is None:
        from .. import config
        _theme_name = config.load("appearance", _appearance_defaults())["theme"]
    return _theme_name


# -- cross-fade on appearance changes (Dark Mode, accent) --------------------------------
# Every window fades from a picture of its old look (_fade_window); what is
# painted with rgba() (Dock, menu bar...) also blends old and new colours.
FADE_MS = 320
_last_vals = None
_fade = None            # {"old": vals, "t0": µs} while fading

def _content(win):
    get = getattr(win, "get_content", None)
    return get() if get and not isinstance(win, Gtk.Popover) else win.get_child()


def _set_content(win, child) -> None:
    if hasattr(win, "set_content"):
        win.set_content(child)
    else:
        win.set_child(child)


def _fade_window(win) -> None:
    """Cross-fade one window: a picture of how it looked, over it, fading
    out while the new style shows underneath. (GTK doesn't run CSS
    transitions for stylesheet changes, so this is done with a snapshot.)"""
    child = _content(win)
    w, h = (child.get_width(), child.get_height()) if child else (0, 0)
    if not child or w <= 0 or h <= 0 or not win.get_renderer():
        return
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(child).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return
    tex = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    over = getattr(win, "_sonata_fade_overlay", None)
    if over is None or over.get_child() is not child:
        _set_content(win, None)
        over = Gtk.Overlay()
        over.set_child(child)
        _set_content(win, over)
        win._sonata_fade_overlay = over
    pic = Gtk.Picture(paintable=tex, can_target=False, content_fit=Gtk.ContentFit.FILL)
    over.add_overlay(pic)
    t0 = GLib.get_monotonic_time()

    def tick(*_a):
        t = min(1.0, (GLib.get_monotonic_time() - t0) / (FADE_MS * 1000))
        pic.set_opacity(1.0 - t * t * (3 - 2 * t))
        if t >= 1.0:
            over.remove_overlay(pic)
            return False
        return True
    GLib.timeout_add(16, tick)


def _start_fade(old_vals) -> None:
    global _fade
    if not old_vals:
        return
    for w in Gtk.Window.list_toplevels():
        if w.get_visible() and w.get_mapped() and not getattr(w, "sonata_no_fade", False):
            try:
                _fade_window(w)
            except (GLib.Error, TypeError, AttributeError):
                pass                                   # never block the switch itself
    _fade = {"old": old_vals, "t0": GLib.get_monotonic_time()}

    def tick():
        global _fade
        done = _fade is None or GLib.get_monotonic_time() - _fade["t0"] >= FADE_MS * 1000
        if done:
            _fade = None
            for k in [k for k in _parsed if isinstance(k[0], str) and k[0].startswith("old:")]:
                del _parsed[k]
        for cb in list(_listeners):
            cb()
        return not done
    GLib.timeout_add(16, tick)


def _fade_t() -> float:
    if _fade is None:
        return 1.0
    t = min(1.0, (GLib.get_monotonic_time() - _fade["t0"]) / (FADE_MS * 1000))
    return t * t * (3 - 2 * t)                     # ease in-out


def _load(*_a, fade=False) -> None:
    global _last_vals
    vals = values()
    old = _last_vals
    _last_vals = vals
    if fade or (_a and isinstance(_a[0], Adw.StyleManager)):      # notify::dark
        _start_fade(old)
    css = "\n".join(t % {**vals, **loc} for t, loc in _templates.values())
    _provider.load_from_string(css)
    for cb in list(_listeners):
        cb()


def on_change(callback) -> None:
    """Call `callback()` whenever the appearance (and so the tokens) changes;
    for widgets that draw with rgba()/shadow() in their snapshot."""
    _listeners.append(callback)


def px(token: str) -> float:
    """A size token ("18px") as a number (cached per appearance)."""
    key = ("px:" + token, is_dark())
    if key not in _parsed:
        _parsed[key] = float(values()[token].rstrip("px"))
    return _parsed[key]


def rgba(token: str) -> Gdk.RGBA:
    """A colour token as Gdk.RGBA (cached per appearance); during a
    cross-fade, the blend of the previous and the new colour."""
    key = (token, is_dark())
    if key not in _parsed:
        c = Gdk.RGBA()
        c.parse(values()[token])
        _parsed[key] = c
    new = _parsed[key]
    if _fade is None or token not in _fade["old"]:
        return new
    okey = ("old:" + token, _fade["t0"])
    if okey not in _parsed:
        o = Gdk.RGBA()
        o.parse(_fade["old"][token])
        _parsed[okey] = o
    o, t = _parsed[okey], _fade_t()
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = (o.red + (new.red - o.red) * t, o.green + (new.green - o.green) * t,
                                       o.blue + (new.blue - o.blue) * t, o.alpha + (new.alpha - o.alpha) * t)
    return c


_SHADOW = re.compile(r"(-?[\d.]+)(?:px)?\s+(-?[\d.]+)(?:px)?\s+([\d.]+)(?:px)?\s+(.+)$")


def shadow(token: str):
    """A shadow token ("dx dy blur colour") as (dx, dy, blur, Gdk.RGBA)."""
    key = ("shadow:" + token, is_dark())
    if key not in _parsed:
        dx, dy, blur, col = _SHADOW.match(values()[token].strip()).groups()
        c = Gdk.RGBA()
        c.parse(col)
        _parsed[key] = (float(dx), float(dy), float(blur), c)
    return _parsed[key]


def setup() -> None:
    """Install the provider once per process (idempotent)."""
    global _provider
    if _provider:
        return
    from .. import icons
    icons.setup()     # Sonata's own icon theme, before any widget
    _provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _provider, PRIORITY)
    Adw.StyleManager.get_default().connect("notify::dark", _load)
    from .. import config
    global _appearance_mon
    _appearance_mon = config.watch("appearance", _appearance_changed)
    _follow_color_scheme()
    _load()
    _sliders_ignore_wheel()


def _sliders_ignore_wheel() -> None:
    """No slider moves under the mouse wheel (macOS): the wheel scrolls the
    page instead. GtkRange's own scroll controller is taken off every
    slider as it is realized, so the event goes on to the scrolled window."""
    def strip(scale, *_a):
        # the hook sees every widget's "realize" (the signal is GtkWidget's): sliders only
        if not isinstance(scale, Gtk.Range):
            return True
        ctrls = scale.observe_controllers()
        for i in range(ctrls.get_n_items() - 1, -1, -1):
            c = ctrls.get_item(i)
            if isinstance(c, Gtk.EventControllerScroll):
                scale.remove_controller(c)
        return True
    GObject.add_emission_hook(Gtk.Range, "realize", strip)


_appearance_mon = None
_forced = False
_iface = None


def _follow_color_scheme() -> None:
    """Dark Mode comes from Sonata's own settings (prefs.py), read directly
    and followed live."""
    global _iface
    from .. import prefs
    state = {"dark": None}

    def apply(*_a):
        if _forced:
            return
        dark = prefs.get(prefs.I, "color-scheme") == "prefer-dark"
        if dark == state["dark"]:
            return
        state["dark"] = dark
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    _iface = prefs.watch(apply)
    apply()


def force_appearance(appearance: str) -> None:
    """"light" / "dark" for this process (previews); "auto" follows the system."""
    global _forced
    _forced = appearance in ("dark", "light")
    Adw.StyleManager.get_default().set_color_scheme(
        {"dark": Adw.ColorScheme.FORCE_DARK, "light": Adw.ColorScheme.FORCE_LIGHT}
        .get(appearance, Adw.ColorScheme.DEFAULT))
