"""The desktop's video wallpaper (Vini: videos as wallpapers, in a loop,
silent, playing only while the desktop has the focus, paused under any
app, and light on the machine).

- **One decoder per video.** Every display showing the same video shares
  one Gtk.MediaFile (GTK's GStreamer backend: hardware decoding where the
  system has it, frames handed to the GPU); a Gtk.Picture per display only
  draws it. The last display to let go of it stops and frees it.
- **Plays only on the desktop.** Wayfire tells (IPC "view-focused") which
  surface has the keyboard. An app window: paused (its last frame stays);
  nothing, or the desktop itself: playing. The menu bar, the Dock and the
  overlays (Spotlight, menus) change nothing -- clicking the Dock over an
  app doesn't start it, opening a menu on the desktop doesn't stop it.
- **Paused while locked or dark** (lockdisplay's files in the runtime
  folder): the lock screen takes the focus without being a window.
- **Silent and looped,** always.

    DesktopFocus.get().active          -> may a video wallpaper play now
    v = acquire(path); v.media         # a Gdk.Paintable; release(v) when done
"""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk  # noqa: E402

# the view types (ipc-rules' "type") that are the desktop, an app, or neither
DESKTOP_TYPES = {"background"}
APP_TYPES = {"toplevel"}


def desktop_has_focus(view, before: bool) -> bool:
    """After a "view-focused" event: is the desktop the one in front? (view:
    the event's view, None when nothing has the keyboard.) Panels and
    overlays -- the menu bar, the Dock, Spotlight, menus -- keep `before`."""
    if not isinstance(view, dict):
        return True
    kind = view.get("type") or "toplevel"
    if kind in DESKTOP_TYPES:
        return True
    if kind in APP_TYPES:
        return False
    return before


def front_is_desktop(views) -> bool:
    """From window-rules/list-views: no app window is the active one."""
    for v in views if isinstance(views, list) else []:
        if isinstance(v, dict) and v.get("activated") and (v.get("type") or "toplevel") in APP_TYPES \
                and v.get("mapped", True) and not v.get("minimized"):
            return False
    return True


def _runtime(name: str) -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", name)


LOCKED_FILE = "sonata2-lock-display.json"         # lockdisplay._state()
DARK_FILE = "sonata2-idle-dark"                   # lockdisplay._dark_marker()


class DesktopFocus:
    """Whether a video wallpaper may play: the desktop in front, not locked,
    the display not dark. Listeners are called when that changes."""
    _one = None

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = cls()
        return cls._one

    def __init__(self, ipc=None):
        self.listeners = []
        self.desktop = True
        self._mons = []
        if ipc is None:
            from ..wl.wfipc import WayfireIPC
            ipc = WayfireIPC()
        self.ipc = ipc
        if ipc.available:
            self.desktop = front_is_desktop(ipc.call("window-rules/list-views"))
            ipc.watch(["view-focused"], self._focused)
        for name in (LOCKED_FILE, DARK_FILE):
            try:
                mon = Gio.File.new_for_path(_runtime(name)).monitor_file(Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", lambda *_a: self._changed())
                self._mons.append(mon)
            except GLib.Error:
                pass
        self._was = self.active

    @staticmethod
    def blocked() -> bool:
        """Locked, or the display gone dark (idle)."""
        return any(os.path.exists(_runtime(n)) for n in (LOCKED_FILE, DARK_FILE))

    @property
    def active(self) -> bool:
        return self.desktop and not self.blocked()

    def _focused(self, ev) -> None:
        self.desktop = desktop_has_focus(ev.get("view"), self.desktop)
        self._changed()

    def _changed(self) -> None:
        now = self.active
        if now != self._was:
            self._was = now
            for fn in list(self.listeners):
                fn(now)


class VideoWallpaper:
    """One video, playing for every display that shows it."""

    def __init__(self, path: str):
        self.path, self.users = path, 0
        self.media = Gtk.MediaFile.new_for_filename(path)
        self.media.set_loop(True)
        self._silence()
        self.cover = CoverPaintable(self.media)        # what the desktop draws: the screen filled
        self.focus = DesktopFocus.get()
        self.focus.listeners.append(self._allowed)
        self._error = self.media.connect("notify::error", self._failed)
        self._ready = self.media.connect("notify::prepared", lambda *_a: self._silence())
        self._allowed(self.focus.active)

    def _silence(self) -> None:
        """No sound, ever (Vini: it played with sound). Set again once the
        player is up and at every play: GStreamer's player can come up with
        its own volume, and GTK only passes a change on -- so the volume is
        moved and set back, the stream muted all along."""
        m = self.media
        if m is None:
            return
        m.set_muted(True)
        m.set_volume(0.01)
        m.set_volume(0.0)

    def _allowed(self, on: bool) -> None:
        if self.media is None:
            return
        if on and self.media.get_error() is None:
            self.media.play()
            self._silence()
        else:
            self.media.pause()                   # (the frame on screen stays: no black desktop)

    def _failed(self, media, _p) -> None:
        err = media.get_error()
        if err is not None:
            print(f"sonata2 wallpaper: can't play {self.path}: {err.message}", flush=True)

    def close(self) -> None:
        if self._allowed in self.focus.listeners:
            self.focus.listeners.remove(self._allowed)
        if self.media is not None:
            self.media.disconnect(self._error)
            self.media.disconnect(self._ready)
            self.cover.release()
            self.media.pause()
            self.media.clear()                   # the decoder and its frames go now, not at the next GC
            self.media = None


class CoverPaintable(GObject.Object, Gdk.Paintable):
    """The video drawn to fill whatever it's given, like a wallpaper: scaled
    to cover, the overflow cut off, centred (Vini: it didn't fill the
    screen). Done here, not by Gtk.Picture's fit: the size a video reports
    differs between GTK versions and GStreamer sinks; the aspect ratio is
    what's asked for. No size of its own: the picture gives it the screen."""

    def __init__(self, media):
        super().__init__()
        self.media = media
        self._ids = [media.connect("invalidate-contents", lambda *_a: self.invalidate_contents()),
                     media.connect("invalidate-size", lambda *_a: self.invalidate_contents())]

    def release(self) -> None:
        for i in self._ids:
            self.media.disconnect(i)
        self._ids = []

    def do_get_intrinsic_width(self):
        return 0

    def do_get_intrinsic_height(self):
        return 0

    def do_get_intrinsic_aspect_ratio(self):
        return 0.0

    def do_get_flags(self):
        return 0

    def do_get_current_image(self):
        return self.media.get_current_image()

    def do_snapshot(self, snap, width, height):
        sw, sh = cover(self.media, width, height)
        if sw <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)        # (kept: the clip reads it later)
        snap.push_clip(bounds)
        snap.save()
        snap.translate(Graphene.Point().init((width - sw) / 2, (height - sh) / 2))
        self.media.snapshot(snap, sw, sh)
        snap.restore()
        snap.pop()


def cover(paintable, width: float, height: float) -> tuple:
    """(w, h) to draw `paintable` at so it covers width x height, keeping its
    shape; its aspect ratio, else its size; nothing known: the box itself."""
    ratio = paintable.get_intrinsic_aspect_ratio()
    if not ratio:
        iw, ih = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        ratio = iw / ih if iw > 0 and ih > 0 else 0
    if not ratio or width <= 0 or height <= 0:
        return width, height
    if width / height > ratio:                   # the box is wider: as wide as it, taller than it
        return width, width / ratio
    return height * ratio, height


_open = {}


def acquire(path: str) -> VideoWallpaper:
    v = _open.get(path)
    if v is None or v.media is None:
        v = _open[path] = VideoWallpaper(path)
    v.users += 1
    return v


def release(v) -> None:
    if v is None:
        return
    v.users -= 1
    if v.users <= 0:
        v.close()
        if _open.get(v.path) is v:
            del _open[v.path]
