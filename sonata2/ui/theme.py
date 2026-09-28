"""Loads every component's CSS, filled with the tokens of the current
appearance, into one provider; reloads it when light/dark changes.

Components call `register(template)` at import time (or when their
geometry is known). Templates use
`%(token)s` placeholders (write `%%` for a literal percent sign)."""
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


def register(template: str, key: str = None, **local) -> None:
    """Add a component's CSS. `local` = extra placeholders for this template
    only (e.g. geometry from the component's settings). Registering the same
    `key` again replaces it."""
    _templates[key or template] = (template, local)
    if _provider:
        _load()


def is_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def values() -> dict:
    """Current tokens (for code that needs a value, e.g. drawing)."""
    return {**tokens.palette(is_dark()), **_extra}


def _load(*_a) -> None:
    vals = values()
    _provider.load_from_data("\n".join(t % {**vals, **loc} for t, loc in _templates.values()).encode())


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
