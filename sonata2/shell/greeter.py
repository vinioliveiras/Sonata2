"""Login screen (`sonata2 greeter`), run by greetd inside a small Wayfire
(install.sh --greeter). Same look as the lock screen (loginui.py): the
user's wallpaper blurred, their picture and name, a capsule password
field; Sleep / Restart / Shut Down at the bottom like macOS.

- Users: AccountsService (real name, picture), else /etc/passwd (uid >= 1000).
  One user: straight to the password. Several: a row of pictures first.
- Sessions: /usr/share/wayland-sessions; Sonata first. A small menu under
  the field when there is more than one. Remembered per user.
- Wallpaper: the one the user's Sonata session copied to
  /var/lib/sonata-greeter/<user>/wallpaper (wallpaper.py), else a gradient.
- State (last user, sessions): /var/cache/sonata-greeter/state.json.

SONATA_GREETER_FAKE=1: no greetd (previews; password "sonata")."""
import json
import os
import pwd
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import greetd, ui  # noqa: E402
from .loginui import Backdrop, avatar, clock, password_field, shake  # noqa: E402

STATE = "/var/cache/sonata-greeter/state.json"
WALLPAPERS = "/var/lib/sonata-greeter"
SESSION_DIRS = ("/usr/local/share/wayland-sessions", "/usr/share/wayland-sessions")

ui.register("""
.gr-user { background: none; border: none; box-shadow: none; padding: 8px; border-radius: 14px; }
.gr-user:hover { background: rgba(255,255,255,0.12); }
.gr-power { min-width: 44px; min-height: 44px; padding: 0; border-radius: 99px; border: none;
  background: rgba(255,255,255,0.18); color: white; box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.22);
  -gtk-icon-size: 20px; }
.gr-power:hover { background: rgba(255,255,255,0.30); }
.gr-power-label { color: white; font-size: %(text_small)s; text-shadow: 0 1px 2px rgba(0,0,0,0.45); }
.gr-link { background: none; border: none; box-shadow: none; color: rgba(255,255,255,0.8);
  font-size: %(text_small)s; min-height: 22px; padding: 0 8px; border-radius: 99px; }
.gr-link:hover { background: rgba(255,255,255,0.14); color: white; }
""", key="greeter")


# -- data --------------------------------------------------------------------------------------
class User:
    def __init__(self, name, real, icon=""):
        self.name, self.real, self.icon = name, real or name, icon


def users() -> list:
    found = []
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        paths = bus.call_sync("org.freedesktop.Accounts", "/org/freedesktop/Accounts",
                              "org.freedesktop.Accounts", "ListCachedUsers", None,
                              GLib.VariantType("(ao)"), 0, 3000, None).unpack()[0]
        for p in paths:
            props = bus.call_sync("org.freedesktop.Accounts", p, "org.freedesktop.DBus.Properties", "GetAll",
                                  GLib.Variant("(s)", ("org.freedesktop.Accounts.User",)),
                                  GLib.VariantType("(a{sv})"), 0, 3000, None).unpack()[0]
            if props.get("SystemAccount") or props.get("Locked"):
                continue
            found.append(User(props.get("UserName", ""), props.get("RealName", ""), props.get("IconFile", "")))
    except GLib.Error:
        pass
    if not found:
        for pw in pwd.getpwall():
            if 1000 <= pw.pw_uid < 60000 and not pw.pw_shell.endswith(("nologin", "false")):
                found.append(User(pw.pw_name, pw.pw_gecos.split(",")[0]))
    return sorted([u for u in found if u.name], key=lambda u: u.real.casefold())


class Session:
    def __init__(self, key, name, cmd, desktops):
        self.key, self.name, self.cmd, self.desktops = key, name, cmd, desktops


def _kf(kf, getter, key, default):
    try:
        return getter("Desktop Entry", key)
    except GLib.Error:
        return default


def sessions() -> list:
    out, seen = [], set()
    for d in SESSION_DIRS:
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".desktop") or fn in seen:
                continue
            kf = GLib.KeyFile()
            try:
                kf.load_from_file(os.path.join(d, fn), GLib.KeyFileFlags.NONE)
                if _kf(kf, kf.get_boolean, "Hidden", False) or _kf(kf, kf.get_boolean, "NoDisplay", False):
                    continue
                name = kf.get_locale_string("Desktop Entry", "Name", None)
                argv = GLib.shell_parse_argv(kf.get_string("Desktop Entry", "Exec"))[1]
                desktops = _kf(kf, kf.get_string, "DesktopNames", "")
            except GLib.Error:
                continue
            seen.add(fn)
            argv = [a for a in argv if not (len(a) == 2 and a.startswith("%"))]
            out.append(Session(fn[:-8], name, argv, desktops.strip(";").replace(";", ":")))
    out.sort(key=lambda s: (s.key != "sonata", s.name.casefold()))
    return out


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE + ".new", "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(STATE + ".new", STATE)
    except OSError:
        pass


def user_wallpaper(name: str):
    path = os.path.join(WALLPAPERS, name, "wallpaper")
    try:
        return Gdk.Texture.new_from_filename(path) if os.path.isfile(path) else None
    except GLib.Error:
        return None


def logind(method: str) -> None:
    try:
        Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call(
            "org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            method, GLib.Variant("(b)", (True,)), None, 0, -1, None, None, None)
    except GLib.Error:
        pass


# -- the screen --------------------------------------------------------------------------------
class Greeter:
    def __init__(self, app):
        self.app = app
        self.fake = bool(os.environ.get("SONATA_GREETER_FAKE"))
        self.users = users()
        self.sessions = sessions()
        self.state = load_state()
        last = self.state.get("user")
        self.user = next((u for u in self.users if u.name == last), None)
        if self.user is None and len(self.users) == 1:
            self.user = self.users[0]
        self.backdrops = []
        self.windows = []
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            self._window(monitors.get_item(i), primary=(i == 0))

    # windows: one per display, the login column on the first
    def _window(self, monitor, primary):
        win = Gtk.Window(application=self.app, decorated=False)
        win.add_css_class("sonata-lock")
        over = Gtk.Overlay()
        bd = Backdrop(self._wallpaper())
        self.backdrops.append(bd)
        over.set_child(bd)
        when = Gtk.Label(css_classes=["lk-clock"], halign=Gtk.Align.END, valign=Gtk.Align.START,
                         margin_top=8, margin_end=16)
        clock(when)
        over.add_overlay(when)
        if primary:
            self.center = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220,
                                    halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=150)
            over.add_overlay(self.center)
            over.add_overlay(self._power())
            self._show()
        win.set_child(over)
        if self.fake:                            # a try-out inside the session: Esc leaves
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", lambda _c, k, *_a: (self.app.quit(), True)[1]
                         if k == Gdk.KEY_Escape else False)
            win.add_controller(keys)
        from . import layer
        if layer.overlay_fullscreen(win, "sonata2-greeter"):       # a layer surface per display
            LS = layer.layer_shell()
            LS.set_monitor(win, monitor)
            if not primary:
                LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        else:
            win.fullscreen_on_monitor(monitor)
        win.present()
        self.windows.append(win)

    def _show(self):
        page = self._login_page() if self.user else self._users_page()
        old = self.center.get_visible_child()
        self.center.add_child(page)
        self.center.set_visible_child(page)
        if old is not None:
            GLib.timeout_add(260, lambda: (self.center.remove(old), False)[1])
        tex = self._wallpaper()
        for bd in self.backdrops:
            bd.texture = tex
            bd.queue_draw()

    def _wallpaper(self):
        """The chosen user's; before choosing, the last user's (else anyone's)."""
        names = [self.user.name] if self.user else \
            [self.state.get("user")] + [u.name for u in self.users]
        for n in names:
            tex = user_wallpaper(n) if n else None
            if tex is not None:
                return tex
        return None

    def _users_page(self):
        row = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, homogeneous=True)
        for u in self.users:
            b = Gtk.Button(css_classes=["gr-user"])
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            col.append(avatar(96, u.name, u.real, u.icon))
            col.append(Gtk.Label(label=u.real, css_classes=["lk-name"], max_width_chars=16,
                                 ellipsize=Pango.EllipsizeMode.END))
            b.set_child(col)
            b.connect("clicked", lambda _b, u=u: self._pick(u))
            row.append(b)
        return row

    def _pick(self, user):
        self.user = user
        self._show()

    def _login_page(self):
        u = self.user
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        col.append(avatar(108, u.name, u.real, u.icon))
        col.append(Gtk.Label(label=u.real, css_classes=["lk-name"]))
        self.entry = password_field()
        self.entry.connect("activate", lambda *_: self._login())
        col.append(self.entry)
        links = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        if len(self.users) > 1:
            other = Gtk.Button(label="Other Users", css_classes=["gr-link"])
            other.connect("clicked", lambda *_: self._pick(None))
            links.append(other)
        if len(self.sessions) > 1:
            links.append(self._session_menu())
        col.append(links)
        self.hint = Gtk.Label(css_classes=["lk-hint"])
        col.append(self.hint)
        GLib.idle_add(lambda: (self.entry.grab_focus(), False)[1])
        return col

    def _session(self):
        key = self.state.get("sessions", {}).get(self.user.name) if self.user else None
        return next((s for s in self.sessions if s.key == key), self.sessions[0] if self.sessions else None)

    def _session_menu(self):
        btn = Gtk.Button(css_classes=["gr-link"])
        btn.set_label(self._session().name + "  ▾")

        def choose(s):
            self.state.setdefault("sessions", {})[self.user.name] = s.key
            btn.set_label(s.name + "  ▾")
        btn.connect("clicked", lambda b: ui.menu.popup(
            b, [[ui.menu.Item(s.name, lambda s=s: choose(s), checked=(s is self._session()))
                 for s in self.sessions]], position=Gtk.PositionType.BOTTOM, glass=True))
        return btn

    def _power(self):
        bar = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=40,
                      homogeneous=True)
        for label, icon, method in (("Sleep", "weather-clear-night-symbolic", "Suspend"),
                                    ("Restart", "view-refresh-symbolic", "Reboot"),
                                    ("Shut Down", "system-shutdown-symbolic", "PowerOff")):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            b = Gtk.Button(icon_name=icon, css_classes=["gr-power"], halign=Gtk.Align.CENTER,
                           tooltip_text=label)
            b.connect("clicked", lambda _b, m=method: None if self.fake else logind(m))
            col.append(b)
            col.append(Gtk.Label(label=label, css_classes=["gr-power-label"]))
            bar.append(col)
        return bar

    # login
    def _login(self):
        pw = self.entry.get_text()
        session = self._session()
        if not pw or session is None:
            return
        self.entry.set_sensitive(False)
        self.hint.set_label("")
        user = self.user.name

        def work():
            try:
                client = greetd.Fake() if self.fake else greetd.Client()
                greetd.login(client, user, pw)
                env = ["XDG_SESSION_TYPE=wayland", f"XDG_SESSION_DESKTOP={session.key}"]
                if session.desktops:
                    env.append(f"XDG_CURRENT_DESKTOP={session.desktops}")
                client.start_session(session.cmd, env)
                GLib.idle_add(self._started)
            except greetd.GreetdError as e:
                GLib.idle_add(self._failed, e)
            except OSError as e:
                GLib.idle_add(self._failed, greetd.GreetdError("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _failed(self, err):
        if err.error_type != "auth_error":
            self.hint.set_label(str(err) or "Couldn't log in")
        shake(self.entry)
        return False

    def _started(self):
        self.state["user"] = self.user.name
        save_state(self.state)
        self.app.quit()
        if not self.fake and os.environ.get("SONATA_GREETER_WAYFIRE"):
            # greetd starts the session when the greeter command (our Wayfire) ends
            subprocess.run(["pkill", "-TERM", "-u", str(os.getuid()), "-x", "wayfire"])
        return False
