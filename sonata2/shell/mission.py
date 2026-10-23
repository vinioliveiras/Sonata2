"""Mission Control's backdrop: the desktop darkens behind the windows laid
out side by side. Only darkens (Vini: blur is too heavy) -- a translucent
black layer over the real desktop, nothing decoded or blurred.

Wayfire's `scale` plugin lays the windows out (Ctrl+Up, F3, three fingers
up); its look is set in config/wayfire.ini [scale]: no dimming of the
windows, the app's title in a pill under the one under the pointer. This
module, run by the menu bar's services, puts that dark layer behind
them: a layer surface on the bottom layer of the display
where scale is active, faded in and out with it. Wayfire IPC reports when
scale starts and stops ("plugin-activation-state-changed").

With scale (not expo) the desktops bar comes too (spaces.py): the desktops
in a row at the top, + for a new one."""
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from ..i18n import _  # noqa: E402
from ..wl.wfipc import WayfireIPC  # noqa: E402
from . import layer, monitors  # noqa: E402
from .loginui import wallpaper_texture  # noqa: E402

FADE_MS = 260
DIM = 0.45                           # how dark the desktop goes behind the windows

ui.register("""
window.sonata-mission, window.sonata-mission > contents { background: rgba(0, 0, 0, %(dim)s); box-shadow: none; }
""", key="mission", dim=DIM)
ui.register("""
window.sonata-mission-close, window.sonata-mission-close > contents { background: none; box-shadow: none; }
.mc-close { min-width: %(size)dpx; min-height: %(size)dpx; padding: 0; border-radius: 99px; border: none;
  background: %(menu_bg)s; color: %(label)s; box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_control)s; }
.mc-close image { -gtk-icon-size: 12px; }
.mc-close:hover { background: %(sys_red)s; color: white; }
""", key="mission-close", size=24)
CLOSE_SIZE = 24
CLOSE_OUT = 8                        # how far the x sits out of the window's corner
PLUGINS = ("scale", "expo")          # expo (all Spaces) gets the same backdrop
BACKDROP_MIN = 480                   # decode size bounds (backdrop_size)
BACKDROP_FALLBACK = 1280             # no display known


def backdrop_size(monitors_=None) -> int:
    """Pixels on the longest side to decode the backdrop's wallpaper at: a
    quarter of the largest display's (device pixels). Blur 40 (a Gaussian
    of ~20 px) leaves nothing finer than that to see."""
    side = 0
    try:
        for m in (monitors._list() if monitors_ is None else monitors_):
            g = m.get_geometry()
            side = max(side, max(g.width, g.height) * max(1, m.get_scale_factor()))
    except Exception:
        side = 0
    return max(BACKDROP_MIN, side // 4) if side else BACKDROP_FALLBACK


def close_spot(box: dict, side) -> tuple:
    """Where the x goes for a window drawn at `box` (output-local): its top
    corner on the side of the user's window buttons, a little out of it."""
    x = box["x"] - CLOSE_OUT if side != Gtk.Align.END else box["x"] + box["width"] - CLOSE_SIZE + CLOSE_OUT
    return max(0, x), max(0, box["y"] - CLOSE_OUT)




_frame_cache = {"key": None, "tex": None}


def video_frame(dark: bool):
    """The video wallpaper's frame where it paused (videowallpaper.FRAME_FILE,
    written by the desktop as Overview opens), or None for a picture or no
    frame yet."""
    from .. import prefs, videowall
    from .videowallpaper import FRAME_FILE, _runtime
    try:
        uri = prefs.wallpaper_uri(dark)
        path = Gio.File.new_for_uri(uri).get_path() if uri else None
    except GLib.Error:
        return None
    if not (path and videowall.is_video(path)):
        return None
    frame = _runtime(FRAME_FILE)
    try:
        st = os.stat(frame)
    except OSError:
        return None
    key = (path, st.st_mtime_ns)
    if _frame_cache["key"] != key:
        try:
            _frame_cache.update(key=key, tex=Gdk.Texture.new_from_filename(frame))
        except GLib.Error:
            return None
    return _frame_cache["tex"]

class CloseOverlay(Gtk.Window):
    """A window's close button in Mission Control (Vini), over the window
    under the pointer: a layer surface just the button's size, put at its
    corner with the layer's margins (Sonata's Wayfire says which window and
    where: sonata-scale-hover#)."""

    def __init__(self, app, monitor, ipc):
        super().__init__(application=app, decorated=False, resizable=False, css_classes=["sonata-mission-close"])
        self.ipc, self.view = ipc, None
        # a plain box, its click taken by the surface: a Gtk.Button here got
        # the press and the release but never said "clicked" (Mission Control
        # holds the pointer: the window never closed)
        self.button = Gtk.Box(css_classes=["mc-close"], tooltip_text=_("Close Window"))
        self.button.set_size_request(CLOSE_SIZE, CLOSE_SIZE)
        self.button.append(Gtk.Image(icon_name="window-close-symbolic", hexpand=True, halign=Gtk.Align.CENTER))
        self.set_child(self.button)
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda *_a: self._close())
        self.add_controller(click)
        LS = self.LS = layer.layer_shell()
        LS.init_for_window(self)
        LS.set_namespace(self, "sonata2-mission-close")
        LS.set_layer(self, LS.Layer.OVERLAY)
        LS.set_anchor(self, LS.Edge.TOP, True)
        LS.set_anchor(self, LS.Edge.LEFT, True)
        LS.set_exclusive_zone(self, -1)
        LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
        if monitor is not None:
            LS.set_monitor(self, monitor)

    def hover(self, ev: dict) -> None:
        """The window under the pointer (view-id -1: none)."""
        vid = ev.get("view-id", -1)
        if vid is None or vid < 0 or ev.get("width", 0) <= 0:
            self.clear()
            return
        self.view = vid
        x, y = close_spot(ev, ui.window.close_align())
        self.LS.set_margin(self, self.LS.Edge.LEFT, x)
        self.LS.set_margin(self, self.LS.Edge.TOP, y)
        if not self.get_visible():
            self.present()

    def clear(self) -> None:
        self.view = None
        self.set_visible(False)

    def _close(self) -> None:
        if self.view is not None:
            self.ipc.call("window-rules/close-view", {"id": self.view})
        self.clear()


class MissionBackdrop:
    def __init__(self, app):
        self.app = app
        self.windows = {}             # connector -> window
        self.active = set()           # (plugin, connector)
        self.ipc = WayfireIPC()
        self._textures = {}           # dark? -> decoded wallpaper (only the current one; until system.json changes)
        self._prefs_mon = None
        try:                          # a video wallpaper's frame, written as Overview opens
            from .videowallpaper import FRAME_FILE, _runtime
            self._frame_mon = Gio.File.new_for_path(_runtime(FRAME_FILE)).monitor_file(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._frame_mon.connect("changed", lambda _m, _f, _o, ev: ev in (
                Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.MOVED_IN) and self._frame_changed())
        except GLib.Error:
            self._frame_mon = None
        self.bars = {}                # connector -> spaces.SpacesBar
        self.closers = {}             # connector -> CloseOverlay
        if layer.layer_shell() and self.ipc.available:
            self.ipc.watch(["plugin-activation-state-changed", "wset-workspace-changed", "view-mapped",
                            "view-unmapped", "view-workspace-changed", "sonata-scale-drag#",
                            "sonata-scale-hover#"], self._event)

    def _event(self, ev):
        if ev.get("event") == "sonata-scale-hover#":  # the window under the pointer: its x
            self._hover(ev)
            return
        if ev.get("event") == "sonata-scale-drag#":   # a window dragged: maybe onto a desktop
            bar = self.bars.get(ev.get("output", ""))
            if bar is not None and bar.get_visible():
                bar.dragging(ev)
            return
        if ev.get("event", "plugin-activation-state-changed") != "plugin-activation-state-changed":
            for bar in self.bars.values():          # the desktops changed while the bar shows
                if bar.get_visible():
                    bar._refresh_soon()
            return
        if ev.get("plugin") not in PLUGINS:
            return
        if ev["plugin"] == "scale":
            self._spaces((ev.get("output-data") or {}).get("name", ""), bool(ev.get("state")))
            if not ev.get("state"):
                closer = self.closers.get((ev.get("output-data") or {}).get("name", ""))
                if closer is not None:
                    closer.clear()
        name = (ev.get("output-data") or {}).get("name", "")
        key = (ev["plugin"], name)
        if ev.get("state"):
            self.active.add(key)
            self._show(name)
        else:
            self.active.discard(key)
            if not any(n == name for _p, n in self.active):
                self._hide(name)

    def _hover(self, ev) -> None:
        name = ev.get("output", "")
        closer = self.closers.get(name)
        if closer is None:
            if ev.get("view-id", -1) < 0 or not layer.layer_shell():
                return
            mon = next((m for m in monitors._list() if monitors.connector(m) == name), None)
            closer = self.closers[name] = CloseOverlay(self.app, mon, self.ipc)
        closer.hover(ev)

    def _window(self, name):
        win = self.windows.get(name)
        if win is not None:
            return win
        mon = next((m for m in monitors._list() if monitors.connector(m) == name), None)
        if mon is None:
            return None
        win = Gtk.Window(application=self.app, decorated=False, can_target=False)
        win.add_css_class("sonata-mission")           # translucent black: the desktop shows through, darker
        win.set_child(Gtk.Box())
        LS = layer.layer_shell()
        LS.init_for_window(win)
        LS.set_namespace(win, "sonata2-mission")
        LS.set_layer(win, LS.Layer.BOTTOM)             # above the wallpaper, below the windows
        for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(win, e, True)
        LS.set_exclusive_zone(win, -1)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        LS.set_monitor(win, mon)
        self.windows[name] = win
        return win

    def _show(self, name):
        win = self._window(name)
        if win is None:
            return
        if not win.get_visible():
            win.set_opacity(0.0)
            win.present()
            layer.set_input_region(win, [])
        self._fade(win, 1.0)

    def _spaces(self, name, on: bool) -> None:
        """The desktops bar with scale (Mission Control), on that display."""
        bar = self.bars.get(name)
        if not on:
            if bar is not None:
                bar.set_visible(False)
            return
        if bar is None:
            mon = next((m for m in monitors._list() if monitors.connector(m) == name), None)
            if mon is None:
                return
            from .spaces import SpacesBar
            bar = self.bars[name] = SpacesBar(self.app, mon, name, self._wallpaper())
        bar.wallpaper = self._wallpaper()
        bar.show_bar()

    def _wallpaper(self):
        """The wallpaper texture, decoded once per Light/Dark picture (not
        on every Mission Control open); a wallpaper change drops the cache.
        Memory review: decoded at a quarter of the largest display (it is
        drawn under blur 40: the same picture on screen -- test_mission_mem)
        and only the current Light/Dark one kept: full size, both variants
        held ~80 MB for the whole session."""
        if self._prefs_mon is None:
            from .. import prefs
            self._prefs_mon = prefs.watch(lambda *_a: self._textures.clear())
        dark = Adw.StyleManager.get_default().get_dark()
        frame = video_frame(dark)
        if frame is not None:                            # a video: where it paused (Vini)
            return frame
        if dark not in self._textures:
            self._textures.clear()
            self._textures[dark] = wallpaper_texture(max_size=backdrop_size())
        return self._textures[dark]

    def _frame_changed(self) -> None:
        """The video's frame landed (just after Overview opened): the
        desktops showing get it."""
        tex = self._wallpaper()
        for bar in self.bars.values():
            if bar.get_visible() and bar.wallpaper is not tex:
                bar.wallpaper = tex
                bar.refresh()

    def _hide(self, name):
        win = self.windows.get(name)
        if win is not None and win.get_visible():
            self._fade(win, 0.0, lambda: win.set_visible(False))

    def _fade(self, win, to, done=None):
        # a new fade replaces the running one: scale off then on again within
        # FADE_MS, the old fade-out's done() hid the backdrop while scale was on
        old = getattr(win, "_fade_src", 0)
        if old:
            GLib.source_remove(old)
        start, frm = time.monotonic(), win.get_opacity()

        def step():
            t = min(1.0, (time.monotonic() - start) * 1000 / FADE_MS)
            win.set_opacity(frm + (to - frm) * (1 - (1 - t) ** 3))
            if t >= 1:
                win._fade_src = 0
                if done:
                    done()
                return False
            return True
        win._fade_src = GLib.timeout_add(16, step)
