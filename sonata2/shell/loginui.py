"""Pieces shared by the lock screen (lock.py) and the login screen
(greeter.py): the blurred wallpaper backdrop, the user's picture, the
capsule password field that shakes on a wrong password, the clock.

Every colour and size of this look lives in the CSS below (tokens where
Sonata has them), so another theme can restyle both screens at once."""
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import ui  # noqa: E402

ui.register("""
window.sonata-lock { background: black; font-family: %(font)s; }
.lk-blackout { background: black; opacity: 0; transition: opacity 600ms ease-in-out; }
.lk-blackout.on { opacity: 1; transition: opacity 1200ms ease-in; }
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
spinner.gr-spinner { color: white; min-width: 16px; min-height: 16px; margin: 6px 0; }   /* (Vini: 22 px was too big) */
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
        # the pointer only (macOS): never keyboard focus -- while the password was
        # checked the field gave its focus away, and the next Enter restarted the
        # computer (Vini, lock screen)
        b = Gtk.Button(icon_name=icon, css_classes=["gr-power"], halign=Gtk.Align.CENTER, tooltip_text=label,
                       focusable=False, focus_on_click=False)
        b.connect("clicked", lambda _b, m=method: (power_log(f"{m}: button clicked"), action(m)))
        col.append(b)
        col.append(Gtk.Label(label=label, css_classes=["gr-power-label"]))
        bar.append(col)
    return bar


SYSTEMCTL = {"Suspend": "suspend", "Reboot": "reboot", "PowerOff": "poweroff"}


def power_log(text: str) -> None:
    """~/.cache/sonata2/power.log: what the power buttons did (Vini: Sleep and
    Restart did nothing on the lock and login screens, and nothing said why)."""
    import time
    try:
        from .. import logs
        with open(logs.path("power.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass
    print(f"sonata2-power: {text}", file=sys.stderr, flush=True)


def logind(method: str) -> None:
    """Ask logind; an error is logged and `systemctl` is tried instead (it
    errored silently before)."""
    power_log(f"{method}: asking logind")

    def fallback(why):
        power_log(f"{method}: logind refused ({why}); trying systemctl {SYSTEMCTL.get(method, '?')}")
        cmd = SYSTEMCTL.get(method)
        if not cmd:
            return

        def run():                                       # off the main loop: the screen stays responsive
            import subprocess
            try:
                p = subprocess.run(["systemctl", cmd], capture_output=True, text=True, timeout=20)
                power_log(f"{method}: systemctl exit {p.returncode} {p.stderr.strip()}")
            except (OSError, subprocess.SubprocessError) as e:
                power_log(f"{method}: systemctl failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def done(bus, res):
        try:
            bus.call_finish(res)
            power_log(f"{method}: accepted")
        except GLib.Error as e:
            fallback(e.message)
    try:
        Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            method, GLib.Variant("(b)", (True,)), None, 0, -1, None, done)
    except GLib.Error as e:
        fallback(e.message)


def wallpaper_texture(max_size: int = 0):
    """The wallpaper of the moment (Light/Dark picture) as a texture, None if
    there is none. `max_size` > 0: decoded to fit that many pixels on its
    longest side -- for backdrops drawn under a heavy blur (Mission Control),
    where a 3840x2560 picture held ~39 MB for detail the blur wipes out. The
    lock / login screens keep the default (full size)."""
    override = os.environ.get("SONATA_LOCK_WALLPAPER")        # previews
    if override:
        return _decode_wallpaper(Gio.File.new_for_path(override), max_size)
    try:
        from .. import prefs
        dark = Adw.StyleManager.get_default().get_dark()
        uri = prefs.get(prefs.BG, "picture-uri-dark" if dark else "picture-uri") or prefs.get(prefs.BG, "picture-uri")
        f = Gio.File.new_for_uri(uri) if uri else None
        if f and f.query_exists(None):
            return _decode_wallpaper(f, max_size)
    except GLib.Error:
        pass
    return None


def _decode_wallpaper(f, max_size: int):
    path = f.get_path() if max_size > 0 else None
    if path:
        try:
            from gi.repository import GdkPixbuf
            _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
            if _fmt is not None and max(w, h) > max_size:          # (never scaled up)
                # (no EXIF rotation: Gdk.Texture.new_from_file below applies none either)
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_size, max_size, True)
                if pb is not None:
                    return Gdk.Texture.new_for_pixbuf(pb)
        except GLib.Error:
            pass                                           # a format only GTK reads: full size
    return Gdk.Texture.new_from_file(f)


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


class Mirror:
    """One control shown on every display (Vini: the login and lock screens
    on all monitors, not just one). Calls go to every copy; a getter answers
    from the first. Password fields keep the same text, so typing on any
    display is typing in all of them."""

    def __init__(self, *items):
        self.items = []
        self._syncing = False
        for w in items:
            self.add(w)

    def add(self, w):
        self.items.append(w)
        if isinstance(w, Gtk.Editable):
            w.connect("changed", self._changed)
        return w

    def remove(self, w) -> None:
        if w in self.items:
            self.items.remove(w)

    def clear(self) -> None:
        self.items = []

    def __bool__(self) -> bool:
        return bool(self.items)

    def __iter__(self):
        return iter(list(self.items))

    def _changed(self, src) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            text = src.get_text()
            for w in self.items:
                if w is not src and w.get_text() != text:
                    w.set_text(text)
                    w.set_position(-1)
        finally:
            self._syncing = False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if not self.items:                       # no display shows it (yet): nothing to do
            return lambda *a, **kw: None
        first = getattr(self.items[0], name)
        if not callable(first):
            return first
        if name.startswith(("get_", "has_", "is_")):
            return first

        def every(*a, **kw):
            out = [getattr(w, name)(*a, **kw) for w in list(self.items)]
            return out[0] if out else None
        return every


def shake(entry: Gtk.Widget) -> None:
    """Wrong password (macOS): the field shakes, empties and keeps focus."""
    entry.set_sensitive(True)
    if hasattr(entry, "set_text"):
        entry.set_text("")
    entry.grab_focus()
    entry.remove_css_class("shake")
    GLib.idle_add(lambda: (entry.add_css_class("shake"), False)[1])
    GLib.timeout_add(500, lambda: (entry.remove_css_class("shake"), False)[1])


class WaitGuard:
    """Wrong passwords make the field wait (sonata2/throttle.py): the field
    is disabled and the hint counts down ("Try again in 4:59")."""

    def __init__(self, entry, hint, throttle):
        self.entry, self.hint, self.throttle = entry, hint, throttle
        self._src = 0
        self._user = None

    def blocked(self, user: str) -> bool:
        """Show the wait for `user` if there is one; True while waiting."""
        self._user = user
        if self.throttle.wait_left(user) > 0:
            self._count()
            return True
        return False

    def failed(self, user: str) -> None:
        if self.throttle.failed(user) > 0:
            self._user = user
            self._count()

    def succeeded(self, user: str) -> None:
        self.throttle.succeeded(user)

    def _count(self) -> None:
        self.entry.set_sensitive(False)
        if not self._src:
            self._tick()
            self._src = GLib.timeout_add_seconds(1, self._tick)

    def _tick(self) -> bool:
        from .. import throttle
        left = self.throttle.wait_left(self._user)
        if left > 0 and self.entry.get_root() is not None:
            self.hint.set_label(throttle.describe(left))
            return True
        self._src = 0
        self.hint.set_label("")
        self.entry.set_sensitive(True)
        self.entry.grab_focus()
        return False


CLOCK_FORMAT = "%a %-d %b  %H:%M"


def menu_bar_format() -> str:
    """The menu bar's clock format (Settings > Date & Time); the default else."""
    try:
        from .. import config
        return config.load("topbar", {"clock_format": CLOCK_FORMAT})["clock_format"] or CLOCK_FORMAT
    except Exception:
        return CLOCK_FORMAT


def top_clock(fmt: str = None) -> Gtk.Label:
    """The date and time at the top, centred, the menu bar's size and format
    (Vini): the lock screen and the login screen show the same."""
    when = Gtk.Label(css_classes=["lk-clock"], halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=6)
    clock(when, fmt or menu_bar_format())
    return when


def clock(label: Gtk.Label, fmt: str = CLOCK_FORMAT) -> None:
    """Keep `label` on the date and time (every 10 s)."""
    def tick():
        label.set_label(GLib.DateTime.new_now_local().format(fmt) or "")
        return True
    tick()
    GLib.timeout_add_seconds(10, tick)
