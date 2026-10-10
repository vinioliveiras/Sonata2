"""Welcome screen right after logging in (`sonata2 welcome`, first in the
session's autostart; only when intro.pending()).

The login screen's look -- blurred wallpaper, picture, name -- with a
spinner while the shell comes up (wallpaper, menu bar, Dock,
Launchpad own their D-Bus names once running). Then it fades out, the
wallpaper sharpening under it, and the Dock and menu bar slide in
(intro.finish()). Gives up waiting after a few seconds."""
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import intro, layer  # noqa: E402
from ..ui.progress import Spinner  # noqa: E402
from .loginui import Backdrop, avatar, balanced, lift, password_field, wallpaper_texture  # noqa: E402

WAIT_FOR = ("wallpaper", "topbar", "dock", "launchpad")
MIN_S, MAX_S = 1.2, 9.0          # shown at least / at most
FADE_MS = 520




def login_column() -> Gtk.Box:
    """The login screen's column as it was when the password was accepted
    -- picture, name, the field's slot turning (the spinner), the links
    and hint rows -- so the picture stays exactly where it was (Vini: it
    rose on the login screen and dropped back down here). Same sizes,
    same lift (loginui.lift); the rows below the spinner are kept but
    unseen."""
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                  valign=Gtk.Align.CENTER, css_classes=["gr-fade-in"])
    col.append(avatar(108))
    col.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), css_classes=["lk-name"]))
    slot = Gtk.Stack(halign=Gtk.Align.CENTER)              # as tall as the password field
    slot.add_named(password_field(), "field")
    spin = Spinner(css_classes=["gr-spinner"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spinning=True)
    slot.add_named(spin, "progress")
    slot.set_visible_child_name("progress")                # macOS: a spinner while the desktop loads
    col.append(slot)
    links = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, opacity=0, can_target=False)
    try:
        from .greeter import sessions, users
        if len(users()) > 1 or len(sessions()) > 1:        # the login screen had a link row there
            links.append(Gtk.Button(label="Other Users", css_classes=["gr-link"], can_focus=False))
    except Exception:
        pass
    balanced(col, links, Gtk.Label(css_classes=["lk-hint"], opacity=0))
    col.spinner = spin
    return col


class Welcome:
    def __init__(self, app, app_ids: dict):
        self.app = app
        self.names = [app_ids[k] for k in WAIT_FOR if k in app_ids]
        self.started = time.monotonic()
        self.windows = []
        tex = wallpaper_texture()
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            self._window(monitors.get_item(i), tex, primary=(i == 0))
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None                     # no session bus: time alone decides
        self.ready = set()
        GLib.timeout_add(120, self._poll)

    def _window(self, monitor, tex, primary):
        win = Gtk.Window(application=self.app, decorated=False)
        win.add_css_class("sonata-lock")
        over = Gtk.Overlay()
        over.set_child(Backdrop(tex))
        if primary:
            over.add_overlay(lift(login_column(), monitor))
        win.set_child(over)
        if layer.overlay_fullscreen(win, "sonata2-welcome"):
            LS = layer.layer_shell()
            LS.set_monitor(win, monitor)
            LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        else:
            win.fullscreen_on_monitor(monitor)
        win.present()
        self.windows.append(win)

    def _poll(self) -> bool:
        for name in self.names:
            if name not in self.ready and self.bus is not None:
                try:
                    has = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                             "NameHasOwner", GLib.Variant("(s)", (name,)),
                                             GLib.VariantType("(b)"), 0, 500, None).unpack()[0]
                except GLib.Error:
                    has = False
                if has:
                    self.ready.add(name)
        elapsed = time.monotonic() - self.started
        frac = len(self.ready) / max(1, len(self.names))
        if (frac >= 1 and elapsed >= MIN_S) or elapsed >= MAX_S:
            GLib.timeout_add(250, self._leave)      # the first frames of the shell are drawn by then
            return False
        return True

    def _leave(self) -> bool:
        intro.finish()                                  # Dock and menu bar slide in now
        start = time.monotonic()

        def tick(win, _clock):
            t = min(1.0, (time.monotonic() - start) * 1000 / FADE_MS)
            win.set_opacity(1 - t * t)
            if t >= 1:
                win.set_visible(False)
                if all(not w.get_visible() for w in self.windows):
                    self.app.quit()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        for win in self.windows:
            win.add_tick_callback(tick)
        return False
