"""Lock screen, macOS Big Sur style (`sonata2 lock`, Ctrl+Super+Q, the
Sonata menu, logind's "lock" requests).

ext-session-lock (Gtk4SessionLock, from gtk4-layer-shell): the compositor
keeps every output covered until we unlock, even if this process dies.
Same look and motion as the login screen (loginui.py): the wallpaper
blurred, the user's picture, name and capsule password field rising into
the middle, Sleep / Restart / Shut Down at the bottom, the date and time
at the top right. A wrong password shakes the field; while it's checked
(PAM, sonata2/pam.py, in a thread) the field turns into a spinner, and on
success everything fades before the desktop shows."""
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import pam  # noqa: E402
from .loginui import Backdrop, WaitGuard, avatar, logind, top_clock, menu_bar_format, password_field, power_bar, shake, wallpaper_texture  # noqa: E402


class LockScreen:
    def __init__(self, app):
        gi.require_version("Gtk4SessionLock", "1.0")
        from gi.repository import Gtk4SessionLock as SL
        self.app = app
        self.lock = SL.Instance.new()
        from .idlelock import mark_locked
        self.lock.connect("locked", lambda *_: mark_locked(True))   # `sonata2 lock-wait` returns
        self.lock.connect("failed", lambda *_: (mark_locked(False), app.quit()))   # another locker is active
        self.lock.connect("unlocked", lambda *_: (mark_locked(False), app.quit()))
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
        over = Gtk.Overlay(css_classes=["gr-fade-in"])
        over.set_child(Backdrop(self.texture))
        over.add_overlay(top_clock(menu_bar_format()))     # the menu bar's place, size and format (Vini)
        if primary:
            over.add_overlay(self._login())
            self.power = power_bar(self._power)
            over.add_overlay(self.power)
        from .screencorners import CornersOverlay, enabled
        if enabled():                                     # the desktop's corners hide under the lock
            over.add_overlay(CornersOverlay())
        win.set_child(over)
        self.lock.assign_window_to_monitor(win, monitor)
        win.present()
        self.windows.append(win)

    def _login(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, css_classes=["gr-rise"])
        self.column = col
        col.append(avatar())
        col.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), css_classes=["lk-name"]))
        self.entry = password_field()
        self.entry.connect("activate", lambda *_: self._check())
        self.spinner = Gtk.Spinner(css_classes=["gr-spinner"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.slot = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220,
                              halign=Gtk.Align.CENTER)
        self.slot.add_named(self.entry, "field")
        self.slot.add_named(self.spinner, "spinner")
        col.append(self.slot)
        self.hint = Gtk.Label(label="" if pam.available() else "PAM is not available: can't check passwords",
                              css_classes=["lk-hint"])
        col.append(self.hint)
        import os
        from ..throttle import Throttle
        # waits after wrong passwords, kept in the user's state folder (a restart doesn't reset it)
        self.guard = WaitGuard(self.entry, self.hint, Throttle(
            os.path.join(GLib.get_user_state_dir(), "sonata2", "password-waits.json")))
        self.guard.blocked(GLib.get_user_name())
        GLib.idle_add(lambda: (self.entry.grab_focus(), False)[1])
        return col

    def _check(self):
        pw = self.entry.get_text()
        if not pw or self.guard.blocked(GLib.get_user_name()):
            return
        self.entry.set_sensitive(False)
        self.spinner.start()
        self.slot.set_visible_child_name("spinner")
        user = GLib.get_user_name()

        def work():
            try:
                ok = pam.authenticate(user, pw)
            except Exception:          # any error must end the spinner (and keep the lock closed)
                ok = False
            GLib.idle_add(self._done, ok)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok):
        user = GLib.get_user_name()
        if ok:
            self.guard.succeeded(user)
            for w in (self.column, self.power):           # fade away, then the desktop
                w.add_css_class("gr-leave")
            GLib.timeout_add(400, lambda: (self.lock.unlock(), False)[1])
            return False
        self.spinner.stop()
        self.slot.set_visible_child_name("field")
        shake(self.entry)
        self.guard.failed(user)
        return False

    def _power(self, method):
        """Sleep keeps the lock; Restart and Shut Down ask logind (it asks
        for a password itself when other users are logged in)."""
        logind(method)
