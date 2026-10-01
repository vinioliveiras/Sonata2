"""Desktop wallpaper: a layer-shell background surface showing the picture
set in Sonata's desktop settings (prefs.py: picture-uri /
picture-uri-dark, the keys every Linux app knows), updated live. Replaces
swaybg. The
desktop icons (desktop.py) sit on top of it."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402


ui.register("""
window.sonata-wallpaper { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%,
  #e0567a 70%%, #f3a452 100%%); }
""", key="wallpaper")


class WallpaperWindow(Gtk.ApplicationWindow):
    """One per display (monitors.each), each with its desktop (icons start on the main one)."""

    def __init__(self, app, monitor=None, desktop: bool = True, main: bool = True):
        super().__init__(application=app, title="Wallpaper", css_classes=["sonata-wallpaper"],
                         decorated=False, resizable=True)
        # two pictures in a cross-fading stack: a new wallpaper (or the dark
        # variant on Dark Mode) fades in instead of cutting
        self.pics = [Gtk.Picture(content_fit=Gtk.ContentFit.COVER, hexpand=True, vexpand=True, can_target=False)
                     for _ in range(2)]
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=450,
                               can_target=False)
        for i, p in enumerate(self.pics):
            self.stack.add_named(p, str(i))
        self._uri = None
        self.sonata_no_fade = True            # fades its own pictures (ui.theme skips it)
        over = Gtk.Overlay()
        over.set_child(self.stack)
        self.desktop = None
        self.main = main
        if desktop:
            from .desktop import Desktop      # the icons of ~/Desktop on top of the picture
            from .monitors import connector
            self.desktop = Desktop(screen=connector(monitor), main=main)
            over.add_overlay(self.desktop)
        self.set_child(over)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-wallpaper")
            if monitor is not None:
                LS.set_monitor(self, monitor)
            LS.set_layer(self, LS.Layer.BACKGROUND)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, e, True)
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND if desktop else LS.KeyboardMode.NONE)
        from .. import prefs
        self._prefs_mon = prefs.watch(lambda *_: self.update())
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update())
        self.update()

    def do_size_allocate(self, w, h, baseline) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        if self.desktop is not None:
            self.desktop.resized(w, h)

    def update(self) -> None:
        from .. import prefs
        dark = Adw.StyleManager.get_default().get_dark()
        uri = prefs.get(prefs.BG, "picture-uri-dark" if dark else "picture-uri") or prefs.get(prefs.BG, "picture-uri")
        if uri == self._uri:
            return
        self._uri = uri
        f = Gio.File.new_for_uri(uri) if uri else None
        nxt = self.pics[1] if self.stack.get_visible_child() is self.pics[0] else self.pics[0]
        nxt.set_file(f if f and f.query_exists(None) else None)
        self.stack.set_visible_child(nxt)
        if self.main and f is not None:
            share_with_login_screen(f)


_shared = None


def share_with_login_screen(f: Gio.File) -> None:
    """The login screen (greeter.py) shows this user's wallpaper: copy it
    where the greeter can read it (install.sh --greeter makes the folder,
    owned by this user). Nothing to do without that folder."""
    global _shared
    import os
    from gi.repository import GLib
    folder = os.path.join("/var/lib/sonata-greeter", GLib.get_user_name())
    if f.get_uri() == _shared or not os.access(folder, os.W_OK):
        return
    _shared = f.get_uri()
    dest = Gio.File.new_for_path(os.path.join(folder, "wallpaper"))
    f.copy_async(dest, Gio.FileCopyFlags.OVERWRITE, GLib.PRIORITY_LOW, None, None,
                 lambda src, res: _copied(src, res))


def _copied(src, res) -> None:
    from gi.repository import GLib
    try:
        src.copy_finish(res)
    except GLib.Error:
        pass
