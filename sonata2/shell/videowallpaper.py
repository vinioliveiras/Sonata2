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
from gi.repository import Gio, GLib, Gtk  # noqa: E402

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
        self.media.set_muted(True)
        self.media.set_volume(0.0)
        self.focus = DesktopFocus.get()
        self.focus.listeners.append(self._allowed)
        self._error = self.media.connect("notify::error", self._failed)
        self._allowed(self.focus.active)

    def _allowed(self, on: bool) -> None:
        if self.media is None:
            return
        if on and self.media.get_error() is None:
            self.media.play()
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
            self.media.pause()
            self.media.clear()                   # the decoder and its frames go now, not at the next GC
            self.media = None


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
