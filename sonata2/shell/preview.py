"""Development preview: a shell surface over a sample wallpaper, in a normal
window, so it can be judged (and screenshotted) without a Wayland session."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..style import install_css  # noqa: E402

# Big Sur-like gradient, busy enough to judge the Dock's translucency.
CSS = """
.preview-wall { background-image: linear-gradient(160deg, #1d3b8f 0%, #6b3fa0 38%, #e0567a 70%, #f3a452 100%); }
.dark .preview-wall { background-image: linear-gradient(160deg, #0b1533 0%, #2a1a4a 45%, #5a2141 75%, #7a4a2a 100%); }
"""


class PreviewWindow(Gtk.ApplicationWindow):
    def __init__(self, app, dock, width: int = 960, height: int = 260):
        super().__init__(application=app, title="Sonata 2 preview",
                         default_width=width, default_height=height, decorated=False)
        install_css(CSS)
        theme = os.environ.get("SONATA2_ICON_THEME")   # e.g. MacTahoe, for screenshots
        if theme:
            Gtk.Settings.get_default().set_property("gtk-icon-theme-name", theme)
        self.dock = dock
        over = Gtk.Overlay()
        over.set_child(Gtk.Box(css_classes=["preview-wall"], hexpand=True, vexpand=True))
        dock.set_margin_bottom(4)
        over.add_overlay(dock)
        self.set_child(over)
