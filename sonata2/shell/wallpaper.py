"""Desktop wallpaper: a layer-shell background surface showing the picture
set in Sonata's desktop settings (prefs.py: picture-uri /
picture-uri-dark, the keys every Linux app knows), updated live. Replaces
swaybg. The
desktop icons (desktop.py) sit on top of it."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402
import threading  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402


ui.register("""
window.sonata-wallpaper { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%,
  #e0567a 70%%, #f3a452 100%%); }
""", key="wallpaper")


def cover_size(img_w: int, img_h: int, w: int, h: int) -> tuple:
    """The picture's size scaled to just cover w x h (never enlarged)."""
    if img_w <= 0 or img_h <= 0 or w <= 0 or h <= 0:
        return img_w, img_h
    f = min(1.0, max(w / img_w, h / img_h))
    return max(1, round(img_w * f)), max(1, round(img_h * f))


def screen_texture(path: str, w: int, h: int):
    """The picture decoded at the display's size: a 4K photo kept whole held
    ~40 MB of graphics memory per copy (Vini: Sonata's wallpaper process had
    228 MB on the card while a game needed it all). None when unreadable."""
    try:
        _fmt, iw, ih = GdkPixbuf.Pixbuf.get_file_info(path)
        tw, th = cover_size(iw, ih, w, h)
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, tw, th, True)
        return Gdk.Texture.new_for_pixbuf(pb)
    except (GLib.Error, TypeError):
        return None


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
        self._monitor = monitor
        self._loaded_for = (0, 0)                # the pixel size the picture was decoded for
        # after a cross-fade the hidden picture lets go of its texture
        self.stack.connect("notify::transition-running", lambda st, _p: st.get_transition_running() or [
            p.set_paintable(None) for p in self.pics if p is not st.get_visible_child()])
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
        self._dark_id = Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update())
        self.update()

    def destroy(self) -> None:
        # (not the "destroy" signal: it only comes once the last reference
        #  is gone, and the StyleManager handler is one)
        self._release()
        Gtk.ApplicationWindow.destroy(self)

    def _release(self) -> None:
        """Destroyed (display unplugged, main display changed): let go of the
        app-wide StyleManager and the prefs monitor, or they keep this window
        and its decoded pictures (~33 MB at 4K) alive."""
        if self._dark_id:
            Adw.StyleManager.get_default().disconnect(self._dark_id)
            self._dark_id = 0
        if self._prefs_mon is not None:
            self._prefs_mon.cancel()
            self._prefs_mon = None
        self._uri = object()                  # a decode still running is dropped when it lands
        for p in self.pics:
            p.set_paintable(None)

    def do_size_allocate(self, w, h, baseline) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        if self.desktop is not None:
            self.desktop.resized(w, h)
        px = self._pixels()
        if px[0] > self._loaded_for[0] or px[1] > self._loaded_for[1]:      # a bigger display: sharper copy
            GLib.idle_add(lambda: (self.update(force=True), False)[1])

    def _pixels(self) -> tuple:
        """This display's size in device pixels."""
        mon = self._monitor
        if mon is None:
            mons = self.get_display().get_monitors()
            mon = mons.get_item(0) if mons.get_n_items() else None
        if mon is None:
            return 3840, 2160
        g, s = mon.get_geometry(), mon.get_scale()
        return int(g.width * s), int(g.height * s)

    def update(self, force: bool = False) -> None:
        from .. import prefs
        dark = Adw.StyleManager.get_default().get_dark()
        uri = prefs.get(prefs.BG, "picture-uri-dark" if dark else "picture-uri") or prefs.get(prefs.BG, "picture-uri")
        if uri == self._uri and not force:
            return
        self._uri = uri
        f = Gio.File.new_for_uri(uri) if uri else None
        path = f.get_path() if f is not None and f.query_exists(None) else None
        w, h = self._loaded_for = self._pixels()

        def show(tex):
            if uri != self._uri:                 # another picture was chosen meanwhile
                return False
            nxt = self.pics[1] if self.stack.get_visible_child() is self.pics[0] else self.pics[0]
            nxt.set_paintable(tex)
            self.stack.set_visible_child(nxt)
            return False
        first = all(p.get_paintable() is None for p in self.pics)
        if path is None:
            show(None)
        elif first:
            # at login: decoded before the window shows (a thread let the gradient
            # behind it flash first -- Vini: "the default wallpaper, then mine")
            show(screen_texture(path, w, h))
        else:                                    # a change later: decoding a 4K photo takes a moment, off the UI thread
            threading.Thread(target=lambda: GLib.idle_add(show, screen_texture(path, w, h)), daemon=True).start()
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
