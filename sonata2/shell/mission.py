"""Mission Control's backdrop (macOS: the desktop darkens and blurs behind
the windows laid out side by side).

Wayfire's `scale` plugin lays the windows out (Ctrl+Up, F3, three fingers
up); its look is set in config/wayfire.ini [scale]: no dimming of the
windows, the app's title in a pill under the one under the pointer. This
module, run by the menu bar's services, puts the blurred, darkened
wallpaper behind them: a layer surface on the bottom layer of the display
where scale is active, faded in and out with it. Wayfire IPC reports when
scale starts and stops ("plugin-activation-state-changed")."""
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..wl.wfipc import WayfireIPC  # noqa: E402
from . import layer, monitors  # noqa: E402
from .loginui import Backdrop, wallpaper_texture  # noqa: E402

FADE_MS = 260
PLUGINS = ("scale", "expo")          # expo (all Spaces) gets the same backdrop


class MissionBackdrop:
    def __init__(self, app):
        self.app = app
        self.windows = {}             # connector -> window
        self.active = set()           # (plugin, connector)
        self.ipc = WayfireIPC()
        self._textures = {}           # dark? -> decoded wallpaper (kept until system.json changes)
        self._prefs_mon = None
        if layer.layer_shell() and self.ipc.available:
            self.ipc.watch(["plugin-activation-state-changed"], self._event)

    def _event(self, ev):
        if ev.get("plugin") not in PLUGINS:
            return
        name = (ev.get("output-data") or {}).get("name", "")
        key = (ev["plugin"], name)
        if ev.get("state"):
            self.active.add(key)
            self._show(name)
        else:
            self.active.discard(key)
            if not any(n == name for _p, n in self.active):
                self._hide(name)

    def _window(self, name):
        win = self.windows.get(name)
        if win is not None:
            return win
        mon = next((m for m in monitors._list() if monitors.connector(m) == name), None)
        if mon is None:
            return None
        win = Gtk.Window(application=self.app, decorated=False, can_target=False)
        win.add_css_class("sonata-lock")
        win.backdrop = Backdrop(None, dim=0.32, blur=40)
        win.set_child(win.backdrop)
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
        tex = self._wallpaper()                         # the wallpaper of the moment (Dark Mode...)
        if win.backdrop.texture is not tex:
            win.backdrop.texture = tex
            win.backdrop.queue_draw()
        if not win.get_visible():
            win.set_opacity(0.0)
            win.present()
            layer.set_input_region(win, [])
        self._fade(win, 1.0)

    def _wallpaper(self):
        """The wallpaper texture, decoded once per Light/Dark picture (not
        on every Mission Control open); a wallpaper change drops the cache."""
        if self._prefs_mon is None:
            from .. import prefs
            self._prefs_mon = prefs.watch(lambda *_a: self._textures.clear())
        dark = Adw.StyleManager.get_default().get_dark()
        if dark not in self._textures:
            self._textures[dark] = wallpaper_texture()
        return self._textures[dark]

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
