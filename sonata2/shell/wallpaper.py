"""Desktop wallpaper: a layer-shell background surface showing the picture
set in the Linux desktop settings (org.gnome.desktop.background
picture-uri / picture-uri-dark), updated live. The wallpaper is a *Linux*
setting (shared with other desktops unless changed inside the Sonata session,
see tools/session-env.sh); Sonata only draws it. Replaces swaybg."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402

SCHEMA = "org.gnome.desktop.background"

ui.register("""
window.sonata-wallpaper { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%,
  #e0567a 70%%, #f3a452 100%%); }
""", key="wallpaper")


class WallpaperWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Wallpaper", css_classes=["sonata-wallpaper"],
                         decorated=False, resizable=True)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, hexpand=True, vexpand=True)
        self.set_child(self.pic)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-wallpaper")
            LS.set_layer(self, LS.Layer.BACKGROUND)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, e, True)
            LS.set_exclusive_zone(self, -1)
        src = Gio.SettingsSchemaSource.get_default()
        self.settings = Gio.Settings.new(SCHEMA) if src and src.lookup(SCHEMA, True) else None
        if self.settings:
            self.settings.connect("changed", lambda *_: self.update())
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update())
        self.update()

    def update(self) -> None:
        if not self.settings:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        uri = self.settings.get_string("picture-uri-dark" if dark else "picture-uri") or \
            self.settings.get_string("picture-uri")
        f = Gio.File.new_for_uri(uri) if uri else None
        self.pic.set_file(f if f and f.query_exists(None) else None)
