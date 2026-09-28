"""Sonata's own icons, independent of the system icon theme.

The shell process looks icons up in the bundled `Sonata` theme (our
overrides -> Sonata-MacTahoe -> hicolor), chosen in Sonata's own settings
(`appearance.json`, key `icon_theme`), never in the Linux one. Only when an
app's icon exists in none of those does it fall back to the system theme.
setup() must run once, before any widget is created."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import config  # noqa: E402

ICONS_DIR = os.path.join(os.path.dirname(__file__), "data", "icons")
APPEARANCE_DEFAULTS = {"icon_theme": "Sonata", "theme": "mac"}

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


def set_image(image: Gtk.Image, gicon) -> None:
    """Show `gicon` from Sonata's theme, or from the system theme if only
    that one has it."""
    theme = Gtk.IconTheme.get_for_display(image.get_display())
    if _system is None or theme.has_gicon(gicon) or not _system.has_gicon(gicon):
        image.set_from_gicon(gicon)
        return
    size = image.get_pixel_size()
    image.set_from_paintable(_system.lookup_by_gicon(gicon, size, image.get_scale_factor(),
                                                     Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0)))


def paintable(widget: Gtk.Widget, gicon, size: int):
    """Icon paintable (drag icons etc.) with the same fallback as set_image."""
    theme = Gtk.IconTheme.get_for_display(widget.get_display())
    if _system is not None and not theme.has_gicon(gicon) and _system.has_gicon(gicon):
        theme = _system
    return theme.lookup_by_gicon(gicon, size, widget.get_scale_factor(),
                                 Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
