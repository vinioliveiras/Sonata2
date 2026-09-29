"""Lock screen, macOS Big Sur style (`sonata2 lock`, Ctrl+Super+Q, the
Sonata menu, logind's "lock" requests).

ext-session-lock (Gtk4SessionLock, from gtk4-layer-shell): the compositor
keeps every output covered until we unlock, even if this process dies.
Look: the wallpaper, blurred and dimmed; the user's picture, name and a
capsule password field in the lower middle; date and time at the top right.
A wrong password shakes the field (macOS). Password checked with PAM
(sonata2/pam.py) in a thread."""
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import pam  # noqa: E402
from .loginui import Backdrop, avatar, clock, password_field, shake, wallpaper_texture  # noqa: E402


class LockScreen:
    def __init__(self, app):
        gi.require_version("Gtk4SessionLock", "1.0")
        from gi.repository import Gtk4SessionLock as SL
        self.app = app
        self.lock = SL.Instance.new()
        self.lock.connect("locked", lambda *_: None)
        self.lock.connect("failed", lambda *_: app.quit())         # another locker is active
        self.lock.connect("unlocked", lambda *_: app.quit())
        self.texture = wallpaper_texture()
        self.windows = []
        self.entry = None
        self.lock.lock()
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            self._window(monitors.get_item(i), primary=(i == 0))
        monitors.connect("items-changed", lambda m, pos, _r, added: [
            self._window(m.get_item(pos + k), primary=False) for k in range(added)])

    def _window(self, monitor, primary):
        win = Gtk.Window(application=self.app)
        win.add_css_class("sonata-lock")
        over = Gtk.Overlay()
        over.set_child(Backdrop(self.texture))
        when = Gtk.Label(css_classes=["lk-clock"], halign=Gtk.Align.END, valign=Gtk.Align.START,
                         margin_top=8, margin_end=16)
        clock(when)
        over.add_overlay(when)
        if primary:
            over.add_overlay(self._login())
        win.set_child(over)
        self.lock.assign_window_to_monitor(win, monitor)
        win.present()
        self.windows.append(win)

    def _login(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.END, margin_bottom=160)
        col.append(avatar())
        col.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), css_classes=["lk-name"]))
        self.entry = password_field()
        self.entry.connect("activate", lambda *_: self._check())
        col.append(self.entry)
        self.hint = Gtk.Label(label="" if pam.available() else "PAM is not available: can't check passwords",
                              css_classes=["lk-hint"])
        col.append(self.hint)
        GLib.idle_add(lambda: (self.entry.grab_focus(), False)[1])
        return col

    def _check(self):
        pw = self.entry.get_text()
        if not pw:
            return
        self.entry.set_sensitive(False)
        user = GLib.get_user_name()

        def work():
            ok = pam.authenticate(user, pw)
            GLib.idle_add(self._done, ok)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok):
        if ok:
            self.lock.unlock()
            return False
        shake(self.entry)
        return False
