"""Lock screen, macOS Big Sur style (`sonata2 lock`, Ctrl+Super+Q, the
Sonata menu, logind's "lock" requests).

ext-session-lock (Gtk4SessionLock, from gtk4-layer-shell): the compositor
keeps every output covered until we unlock, even if this process dies.
Look: the wallpaper, blurred and dimmed; the user's picture, name and a
capsule password field in the lower middle; date and time at the top right.
A wrong password shakes the field (macOS). Password checked with PAM
(sonata2/pam.py) in a thread."""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import pam, ui  # noqa: E402

ui.register("""
window.sonata-lock { background: black; font-family: %(font)s; }
.lk-name { color: white; font-size: 17px; font-weight: 600; text-shadow: 0 1px 3px rgba(0,0,0,0.4); }
.lk-hint { color: rgba(255,255,255,0.75); font-size: %(text_small)s; text-shadow: 0 1px 2px rgba(0,0,0,0.4); }
.lk-clock { color: white; font-size: %(text_body)s; font-weight: 500; text-shadow: 0 1px 3px rgba(0,0,0,0.4); }
entry.lk-field { min-height: 28px; min-width: 190px; border-radius: 99px; padding: 0 12px;
  background: rgba(255,255,255,0.22); color: white; border: none; box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.25);
  caret-color: white; font-size: %(text_body)s; }
entry.lk-field:focus-within { box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.35); outline: none; }
entry.lk-field > text > placeholder { color: rgba(255,255,255,0.65); }
entry.lk-field image { color: rgba(255,255,255,0.85); }
@keyframes lk-shake { 0%% { margin-left: 0; margin-right: 0; } 20%% { margin-left: 24px; margin-right: 0; }
  40%% { margin-left: 0; margin-right: 24px; } 60%% { margin-left: 16px; margin-right: 0; }
  80%% { margin-left: 0; margin-right: 8px; } 100%% { margin-left: 0; margin-right: 0; } }
entry.lk-field.shake { animation: lk-shake 420ms ease-in-out; }
""", key="lock")


def _wallpaper_texture():
    override = os.environ.get("SONATA_LOCK_WALLPAPER")        # previews
    if override:
        return Gdk.Texture.new_from_filename(override)
    try:
        s = Gio.Settings.new("org.gnome.desktop.background")
        dark = Adw.StyleManager.get_default().get_dark()
        uri = s.get_string("picture-uri-dark" if dark else "picture-uri") or s.get_string("picture-uri")
        f = Gio.File.new_for_uri(uri) if uri else None
        if f and f.query_exists(None):
            return Gdk.Texture.new_from_file(f)
    except GLib.Error:
        pass
    return None


class _Backdrop(Gtk.Widget):
    """The wallpaper, cover-fit, blurred and dimmed (drawn once per size)."""

    def __init__(self, texture):
        super().__init__(hexpand=True, vexpand=True)
        self.texture = texture

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        rect = Graphene.Rect().init(0, 0, w, h)
        if self.texture is None:
            c = Gdk.RGBA()
            c.parse("#2a3550")
            snap.append_color(c, rect)
            return
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = max(w / tw, h / th)
        dw, dh = tw * scale, th * scale
        snap.push_clip(rect)
        snap.push_blur(64)
        snap.append_texture(self.texture, Graphene.Rect().init((w - dw) / 2 - 40, (h - dh) / 2 - 40,
                                                              dw + 80, dh + 80))
        snap.pop()
        dim = Gdk.RGBA()
        dim.parse("rgba(0,0,0,0.18)")
        snap.append_color(dim, rect)
        snap.pop()


def _avatar(size=108):
    user = GLib.get_user_name()
    for p in (os.path.expanduser("~/.face"), f"/var/lib/AccountsService/icons/{user}"):
        if os.path.isfile(p):
            av = Adw.Avatar(size=size, show_initials=False)
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(p))
                return av
            except GLib.Error:
                pass
    return Adw.Avatar(size=size, text=GLib.get_real_name() or user, show_initials=True)


class LockScreen:
    def __init__(self, app):
        gi.require_version("Gtk4SessionLock", "1.0")
        from gi.repository import Gtk4SessionLock as SL
        self.app = app
        self.lock = SL.Instance.new()
        self.lock.connect("locked", lambda *_: None)
        self.lock.connect("failed", lambda *_: app.quit())         # another locker is active
        self.lock.connect("unlocked", lambda *_: app.quit())
        self.texture = _wallpaper_texture()
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
        over.set_child(_Backdrop(self.texture))
        clock = Gtk.Label(css_classes=["lk-clock"], halign=Gtk.Align.END, valign=Gtk.Align.START,
                          margin_top=8, margin_end=16)
        self._tick(clock)
        GLib.timeout_add_seconds(10, lambda: (self._tick(clock), True)[1])
        over.add_overlay(clock)
        if primary:
            over.add_overlay(self._login())
        win.set_child(over)
        self.lock.assign_window_to_monitor(win, monitor)
        win.present()
        self.windows.append(win)

    def _tick(self, label):
        label.set_label(GLib.DateTime.new_now_local().format("%a %-d %b  %H:%M"))

    def _login(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.END, margin_bottom=160)
        col.append(_avatar())
        col.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), css_classes=["lk-name"]))
        self.entry = Gtk.PasswordEntry(placeholder_text="Enter Password", css_classes=["lk-field"],
                                       halign=Gtk.Align.CENTER, show_peek_icon=False)
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
        self.entry.set_sensitive(True)
        self.entry.set_text("")
        self.entry.grab_focus()
        self.entry.remove_css_class("shake")
        GLib.idle_add(lambda: (self.entry.add_css_class("shake"), False)[1])
        GLib.timeout_add(500, lambda: (self.entry.remove_css_class("shake"), False)[1])
        return False
