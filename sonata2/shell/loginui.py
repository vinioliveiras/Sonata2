"""Pieces shared by the lock screen (lock.py) and the login screen
(greeter.py): the blurred wallpaper backdrop, the user's picture, the
capsule password field that shakes on a wrong password, the clock.

Every colour and size of this look lives in the CSS below (tokens where
Sonata has them), so another theme can restyle both screens at once."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import ui  # noqa: E402

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
""", key="login-ui")


ui.register("""
/* macOS-like motion: everything rises and fades in; leaving fades out */
@keyframes gr-rise { from { opacity: 0; transform: translateY(18px) scale(0.97); }
                     to { opacity: 1; transform: none; } }
@keyframes gr-fade { from { opacity: 0; } to { opacity: 1; } }
.gr-rise { animation: gr-rise 620ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.gr-rise-late { animation: gr-rise 620ms cubic-bezier(0.2, 0.8, 0.2, 1) 160ms both; }
.gr-fade-in { animation: gr-fade 700ms ease-out both; }
.gr-leave { opacity: 0; transform: scale(0.97); transition: opacity 380ms ease-in, transform 380ms ease-in; }
spinner.gr-spinner { color: white; min-width: 22px; min-height: 22px; margin: 3px 0; -gtk-icon-size: 22px; }
progressbar.gr-progress { min-width: 190px; margin: 11px 0; }
progressbar.gr-progress trough { min-height: 5px; border-radius: 99px; background: rgba(255,255,255,0.25);
  border: none; box-shadow: none; }
progressbar.gr-progress progress { min-height: 5px; border-radius: 99px; background: white; border: none; }
""", key="login-motion")


ui.register("""
.gr-user { background: none; border: none; box-shadow: none; padding: 8px; border-radius: 14px;
  transition: background-color 180ms ease-out, transform 180ms ease-out; }
.gr-user:hover { background: rgba(255,255,255,0.12); }
.gr-user:active { transform: scale(0.96); }
.gr-power { min-width: 44px; min-height: 44px; padding: 0; border-radius: 99px; border: none;
  transition: background-color 160ms ease-out, transform 160ms ease-out;
  background: rgba(255,255,255,0.18); color: white; box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.22);
  -gtk-icon-size: 20px; }
.gr-power:hover { background: rgba(255,255,255,0.30); }
.gr-power:active { transform: scale(0.92); }
.gr-power-label { color: white; font-size: %(text_small)s; text-shadow: 0 1px 2px rgba(0,0,0,0.45); }
.gr-link { background: none; border: none; box-shadow: none; color: rgba(255,255,255,0.8);
  font-size: %(text_small)s; min-height: 22px; padding: 0 8px; border-radius: 99px; }
.gr-link:hover { background: rgba(255,255,255,0.14); color: white; }
""", key="login-controls")


def power_bar(action) -> Gtk.Widget:
    """Sleep / Restart / Shut Down along the bottom (macOS login and lock
    screens); action(method) gets the logind method name."""
    bar = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=40,
                  homogeneous=True, css_classes=["gr-rise-late"])
    for label, icon, method in (("Sleep", "weather-clear-night-symbolic", "Suspend"),
                                ("Restart", "view-refresh-symbolic", "Reboot"),
                                ("Shut Down", "system-shutdown-symbolic", "PowerOff")):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b = Gtk.Button(icon_name=icon, css_classes=["gr-power"], halign=Gtk.Align.CENTER, tooltip_text=label)
        b.connect("clicked", lambda _b, m=method: action(m))
        col.append(b)
        col.append(Gtk.Label(label=label, css_classes=["gr-power-label"]))
        bar.append(col)
    return bar


def logind(method: str) -> None:
    try:
        Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            method, GLib.Variant("(b)", (True,)), None, 0, -1, None, None, None)
    except GLib.Error:
        pass


def wallpaper_texture():
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


class Backdrop(Gtk.Widget):
    """The wallpaper, cover-fit, blurred and dimmed (drawn once per size)."""

    def __init__(self, texture, dim: float = 0.18, blur: float = 64):
        super().__init__(hexpand=True, vexpand=True)
        self.texture = texture
        self.dim, self.blur = dim, blur

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
        snap.push_blur(self.blur)
        snap.append_texture(self.texture, Graphene.Rect().init((w - dw) / 2 - 40, (h - dh) / 2 - 40,
                                                              dw + 80, dh + 80))
        snap.pop()
        dim = Gdk.RGBA()
        dim.parse(f"rgba(0,0,0,{self.dim})")
        snap.append_color(dim, rect)
        snap.pop()


def avatar(size=108, user=None, real_name=None, icon=None):
    """`user` None: this process's user. `icon`: a picture file to use first."""
    own = user is None
    user = user or GLib.get_user_name()
    real_name = real_name or (GLib.get_real_name() if own else "") or user
    paths = [icon] if icon else []
    if own:
        paths.append(os.path.expanduser("~/.face"))
    paths.append(f"/var/lib/AccountsService/icons/{user}")
    for p in paths:
        if os.path.isfile(p):
            av = Adw.Avatar(size=size, show_initials=False)
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(p))
                return av
            except GLib.Error:
                pass
    return Adw.Avatar(size=size, text=real_name, show_initials=True)




def password_field(placeholder="Enter Password") -> Gtk.PasswordEntry:
    return Gtk.PasswordEntry(placeholder_text=placeholder, css_classes=["lk-field"],
                             halign=Gtk.Align.CENTER, show_peek_icon=False)


def shake(entry: Gtk.Widget) -> None:
    """Wrong password (macOS): the field shakes, empties and keeps focus."""
    entry.set_sensitive(True)
    if hasattr(entry, "set_text"):
        entry.set_text("")
    entry.grab_focus()
    entry.remove_css_class("shake")
    GLib.idle_add(lambda: (entry.add_css_class("shake"), False)[1])
    GLib.timeout_add(500, lambda: (entry.remove_css_class("shake"), False)[1])


def clock(label: Gtk.Label) -> None:
    """Keep `label` on the date and time (every 10 s)."""
    def tick():
        label.set_label(GLib.DateTime.new_now_local().format("%a %-d %b  %H:%M"))
        return True
    tick()
    GLib.timeout_add_seconds(10, tick)
