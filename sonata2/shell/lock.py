"""Lock screen, macOS Big Sur style (`sonata2 lock`, Ctrl+Super+Q, the
Sonata menu, logind's "lock" requests).

ext-session-lock (Gtk4SessionLock, from gtk4-layer-shell): the compositor
keeps every output covered until we unlock, even if this process dies.
Same look and motion as the login screen (loginui.py): the wallpaper
blurred, the user's picture, name and capsule password field rising into
the middle of every display (Vini: on all monitors; a display that comes
back after sleep or a cable gets them too -- it came back without them), Sleep / Restart / Shut Down at the bottom, the date and time
at the top right. A wrong password shakes the field; while it's checked
(PAM, sonata2/pam.py, in a thread) the field turns into a spinner, and on
success everything fades before the desktop shows."""
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import pam  # noqa: E402
from .loginui import Backdrop, Mirror, WaitGuard, avatar, logind, top_clock, menu_bar_format, password_field, power_bar, shake, wallpaper_texture  # noqa: E402


class LockScreen:
    def __init__(self, app):
        gi.require_version("Gtk4SessionLock", "1.0")
        from gi.repository import Gtk4SessionLock as SL
        self.app = app
        self.lock = SL.Instance.new()
        from .idlelock import mark_locked
        self.lock.connect("locked", lambda *_: (mark_locked(True), self._usb(True), self._dark(True)))   # `sonata2 lock-wait` returns
        self.lock.connect("failed", lambda *_: (mark_locked(False), app.quit()))   # another locker is active
        self.lock.connect("unlocked", lambda *_: (self._dark(False), self._usb(False), mark_locked(False), app.quit()))
        self._idle_src = 0
        self._off = False
        self.texture = wallpaper_texture()
        self.windows = []
        self.entry = None
        self.lock.lock()
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        from . import monitors as displays
        main = displays.main()               # the first focus on the main display (built-in panel)
        for i in range(monitors.get_n_items()):
            self._window(monitors.get_item(i), primary=(monitors.get_item(i) is main))
        monitors.connect("items-changed", lambda m, pos, _r, added: [
            self._window(m.get_item(pos + k), primary=False) for k in range(added)])

    def _mirrors(self) -> None:
        """The column's controls, one copy per display."""
        if getattr(self, "entry", None) is None or not isinstance(self.entry, Mirror):
            self.entry, self.spinner, self.slot = Mirror(), Mirror(), Mirror()
            self.hint, self.column, self.power = Mirror(), Mirror(), Mirror()

    # -- the display goes dark soon after locking (lockdisplay.py) ----------------------------
    def _dark(self, locked: bool) -> None:
        from . import lockdisplay
        try:
            (lockdisplay.locked if locked else lockdisplay.unlocked)()
        except Exception as e:                   # never in the way of locking or unlocking
            print(f"sonata2-lock: display: {e}", flush=True)
        if locked:
            self._dark_after = lockdisplay.dark_seconds()      # None: you chose "never"
            self._idle_watch()
            self._input()
        else:
            if self._idle_src:
                GLib.source_remove(self._idle_src)
                self._idle_src = 0
            self._wake()                         # (through the watch, before it closes)
            w = getattr(self, "_iw", None)
            if w is not None:
                w.close()
                self._iw = None

    def _idle_watch(self) -> None:
        """Dark and back by the compositor's own input idle (ext-idle-notify):
        any key or move wakes the display, whether or not a lock window sees
        it (Vini: it stayed black until the lid was closed and opened)."""
        self._iw = None
        if self._dark_after is None:
            return
        try:
            from ..wl.idlewatch import IdleWatch
            w = IdleWatch()
        except Exception:
            return
        if not (w.ok and w.input_idle):
            w.close()
            return
        self._iw = w
        w.watch(self._dark_after, self._go_dark, self._wake)

    def _go_dark(self) -> None:
        """Black and the backlight at zero -- the display itself stays on:
        powering it off and on again failed on Vini's laptop (lockdisplay.py)."""
        from . import lockdisplay
        self._blackout(True)
        lockdisplay.dim(True)
        lockdisplay.lights(False)                # the keyboard too, whatever keeps the session awake
        self._off = True

    def _wake(self) -> None:
        if not self._off:
            return
        from . import lockdisplay
        self._off = False
        self._blackout(False)
        lockdisplay.dim(False)
        lockdisplay.lights(True)

    def _blackout(self, on: bool) -> None:
        for b in getattr(self, "blackouts", []):
            (b.add_css_class if on else b.remove_css_class)("on")

    def _input(self, *_a) -> None:
        """A key or a move on the lock screen: the displays on, the countdown
        again (the compositor's idle watch does both when it can)."""
        self._wake()
        if self._idle_src:
            GLib.source_remove(self._idle_src)
            self._idle_src = 0
        if getattr(self, "_iw", None) is not None or getattr(self, "_dark_after", None) is None:
            return

        def dark():
            self._idle_src = 0
            self._go_dark()
            return False
        self._idle_src = GLib.timeout_add_seconds(self._dark_after, dark)

    def _watch_input(self, win) -> None:
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *_a: (self._input(), False)[1])
        win.add_controller(keys)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._moved)
        win.add_controller(motion)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._input)
        win.add_controller(click)

    def _moved(self, _c, x, y) -> None:
        last = getattr(self, "_last_xy", None)
        self._last_xy = (round(x), round(y))
        if last is not None and last != self._last_xy:       # (a real move, not the surface appearing)
            self._input()

    def _usb(self, locked: bool) -> None:
        """New USB devices blocked while locked (USBGuard; usbprotect.py)."""
        try:
            from ..backend import usbprotect
            if not locked:
                usbprotect.restore()             # before quitting: never left blocked
                return
            from .. import config
            from .idlelock import DEFAULTS
            if config.load("security", DEFAULTS).get("usb_protection"):
                from ..backend import system
                system.run_async(usbprotect.block)
        except Exception as e:                   # never in the way of locking or unlocking
            print(f"sonata2-lock: USB protection: {e}", flush=True)

    def _window(self, monitor, primary=False):
        """A display's lock: the backdrop, the clock, and the picture, name,
        password and power buttons on every display."""
        self._mirrors()
        win = Gtk.Window(application=self.app)
        win.add_css_class("sonata-lock")
        over = Gtk.Overlay(css_classes=["gr-fade-in"])
        over.set_child(Backdrop(self.texture))
        over.add_overlay(top_clock(menu_bar_format()))     # the menu bar's place, size and format (Vini)
        parts = self._login()
        over.add_overlay(parts["column"])
        power = self.power.add(power_bar(self._power))
        parts["power"] = power
        over.add_overlay(power)
        from .screencorners import CornersOverlay, enabled
        if enabled():                                     # the desktop's corners hide under the lock
            over.add_overlay(CornersOverlay())
        black = Gtk.Box(css_classes=["lk-blackout"], can_target=False)   # dark while idle (_go_dark)
        if getattr(self, "_off", False):
            black.add_css_class("on")
        over.add_overlay(black)
        self.blackouts = getattr(self, "blackouts", []) + [black]
        parts["blackout"] = black
        win.set_child(over)
        self._watch_input(win)
        self.lock.assign_window_to_monitor(win, monitor)
        win.present()
        self.windows.append(win)
        if primary or len(self.windows) == 1:      # (the only display: after all of them came back)
            GLib.idle_add(lambda: (parts["entry"].grab_focus(), False)[1])
        # unplugged (or gone for a moment in sleep): its copy goes; it comes back as a new display
        monitor.connect("invalidate", lambda *_: self._gone(win, parts))
        return win

    def _gone(self, win, parts) -> None:
        for name, w in parts.items():
            if name == "blackout":
                if w in getattr(self, "blackouts", []):
                    self.blackouts.remove(w)
                continue
            getattr(self, name).remove(w)
        if win in self.windows:
            self.windows.remove(win)
        win.destroy()

    def _login(self) -> dict:
        col = self.column.add(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                                      valign=Gtk.Align.CENTER, css_classes=["gr-rise"]))
        col.append(avatar())
        col.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), css_classes=["lk-name"]))
        entry = password_field()
        if self.entry:
            entry.set_text(self.entry.get_text())
            entry.set_sensitive(self.entry.get_sensitive())
        self.entry.add(entry)
        entry.connect("activate", lambda *_: self._check())
        spinner = self.spinner.add(Gtk.Spinner(css_classes=["gr-spinner"], halign=Gtk.Align.CENTER,
                                               valign=Gtk.Align.CENTER))
        slot = self.slot.add(Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220,
                                       halign=Gtk.Align.CENTER))
        slot.add_named(entry, "field")
        slot.add_named(spinner, "spinner")
        col.append(slot)
        hint = self.hint.add(Gtk.Label(label="" if pam.available() else
                                       "PAM is not available: can't check passwords", css_classes=["lk-hint"]))
        if len(self.hint.items) > 1:
            hint.set_label(self.hint.items[0].get_label())
        col.append(hint)
        if getattr(self, "guard", None) is None:
            import os
            from ..throttle import Throttle
            # waits after wrong passwords, kept in the user's state folder (a restart doesn't reset it)
            self.guard = WaitGuard(self.entry, self.hint, Throttle(
                os.path.join(GLib.get_user_state_dir(), "sonata2", "password-waits.json")))
            self.guard.blocked(GLib.get_user_name())
        return {"column": col, "entry": entry, "spinner": spinner, "slot": slot, "hint": hint}

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
