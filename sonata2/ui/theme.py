"""Loads every component's CSS, filled with the tokens of the current
appearance, into one provider; reloads it when light/dark changes.

Components call `register(template)` at import time (or when their
geometry is known). Templates use
`%(token)s` placeholders (write `%%` for a literal percent sign)."""
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

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
        _reduce = bool(config.load("appearance", {}).get("reduce_transparency", False))
    return _reduce


_reduce = None


def values() -> dict:
    """Current tokens (for code that needs a value, e.g. drawing), plus the
    materials resolved for this compositor: `sidebar_material`."""
    v = {**tokens.palette(is_dark(), _theme()), **_extra}
    # One glass for the whole system: sidebars use exactly the Dock's tint
    # over the same compositor blur.
    v["sidebar_material"] = v["glass_tint" if glass() else "sidebar_bg"]
    return v


_theme_name = None


def _theme() -> str:
    """Visual theme from Sonata's appearance settings (read once)."""
    global _theme_name
    if _theme_name is None:
        from .. import config
        _theme_name = config.load("appearance", {"theme": "mac"})["theme"]
    return _theme_name


def _load(*_a) -> None:
    vals = values()
    _provider.load_from_data("\n".join(t % {**vals, **loc} for t, loc in _templates.values()).encode())
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
    """A colour token as Gdk.RGBA (cached per appearance)."""
    key = (token, is_dark())
    if key not in _parsed:
        c = Gdk.RGBA()
        c.parse(values()[token])
        _parsed[key] = c
    return _parsed[key]


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
    _load()


def force_appearance(appearance: str) -> None:
    """"light" / "dark" for this process (previews); "auto" follows the system."""
    Adw.StyleManager.get_default().set_color_scheme(
        {"dark": Adw.ColorScheme.FORCE_DARK, "light": Adw.ColorScheme.FORCE_LIGHT}
        .get(appearance, Adw.ColorScheme.DEFAULT))
