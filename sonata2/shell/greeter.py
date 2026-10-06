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
from .loginui import Backdrop, Mirror, WaitGuard, avatar, password_field, top_clock, logind, power_bar, shake  # noqa: E402

STATE = "/var/cache/sonata-greeter/state.json"
WAITS = "/var/cache/sonata-greeter/password-waits.json"     # waits after wrong passwords, per user
WALLPAPERS = "/var/lib/sonata-greeter"
SESSION_DIRS = ("/usr/local/share/wayland-sessions", "/usr/share/wayland-sessions")




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


USER_TILE = 96 + 2 * 8 + 18          # a user's picture, its button's padding, the gap


def user_grid(n: int, screen_w: int) -> tuple:
    """(users per row, picture size) for n users on a display screen_w wide."""
    per_row = max(1, min(n or 1, int(screen_w * 0.8) // USER_TILE))
    rows = -(-max(n, 1) // per_row)
    return per_row, (96 if rows <= 2 else 72)


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


# -- displays (wlr-randr: Wayfire's output management) ------------------------------------------
def outputs() -> list:
    """[{name, description, modes: [(w, h, hz)], current: (w, h, hz)}] of the enabled displays."""
    try:
        out = subprocess.run(["wlr-randr", "--json"], capture_output=True, text=True, timeout=4).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    res = []
    for o in data:
        if not o.get("enabled", True):
            continue
        modes = [(m["width"], m["height"], float(m["refresh"])) for m in o.get("modes", [])]
        cur = next(((m["width"], m["height"], float(m["refresh"])) for m in o.get("modes", []) if m.get("current")),
                   None)
        res.append({"name": o.get("name", ""), "description": o.get("description") or o.get("name", ""),
                    "modes": modes, "current": cur})
    return res


def best_modes(modes) -> list:
    """One entry per resolution, at its highest refresh rate; biggest first."""
    best = {}
    for w, h, hz in modes:
        if hz > best.get((w, h), 0):
            best[(w, h)] = hz
    return sorted(((w, h, hz) for (w, h), hz in best.items()), key=lambda m: (-m[0] * m[1], -m[2]))


def set_mode(name: str, mode) -> bool:
    w, h, hz = mode
    try:
        return subprocess.run(["wlr-randr", "--output", name, "--mode", f"{w}x{h}@{hz:.3f}Hz"],
                              capture_output=True, timeout=6).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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
        self._restore_modes()
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        from . import monitors as displays
        # the password on the main display the user chose in Settings (their
        # session shares it, see monitors.share_with_login_screen), else the built-in panel
        names = [self.user.name] if self.user else [self.state.get("user")] + [u.name for u in self.users]
        main = displays.main(displays.login_main([n for n in names if n]))
        for i in range(monitors.get_n_items()):
            self._window(monitors.get_item(i), primary=(monitors.get_item(i) is main))
        # a display plugged in later (or back after its cable moved) gets the login too
        monitors.connect("items-changed", lambda m, pos, _r, added: [
            self._window(m.get_item(pos + k), primary=False) for k in range(added)])

    def _mirrors(self) -> None:
        """The login's controls, one copy per display (Vini: on every monitor)."""
        if not isinstance(getattr(self, "center", None), Mirror):
            self.center, self.power = Mirror(), Mirror()
            self.entry, self.progress, self.slot = Mirror(), Mirror(), Mirror()
            self.links, self.hint, self.column, self.session_buttons = Mirror(), Mirror(), Mirror(), Mirror()

    # windows: one per display, each with the whole login (Vini: the column
    # was on one display only, and gone when that display came back)
    def _window(self, monitor, primary=False):
        self._mirrors()
        win = Gtk.Window(application=self.app, decorated=False)
        win.add_css_class("sonata-lock")
        over = Gtk.Overlay(css_classes=["gr-fade-in"])
        bd = Backdrop(self._wallpaper())
        self.backdrops.append(bd)
        over.set_child(bd)
        over.add_overlay(top_clock())                 # like the lock screen: top, centred (Vini)
        center = self.center.add(Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                           transition_duration=220, halign=Gtk.Align.CENTER,
                                           valign=Gtk.Align.CENTER))
        over.add_overlay(center)
        power = self.power.add(self._power())
        over.add_overlay(power)
        if not self.fake:
            over.add_overlay(self._display_button())
        self._fill(center, focus=primary)
        from .screencorners import CornersOverlay        # (always: the login screen has no settings)
        over.add_overlay(CornersOverlay())
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
            # one display holds the keyboard; every display's field shows what's typed
            if primary or getattr(self, "keys_win", None) is None:
                if getattr(self, "keys_win", None) is not None:
                    LS.set_keyboard_mode(self.keys_win, LS.KeyboardMode.NONE)
                self.keys_win = win                       # (overlay_fullscreen: exclusive keyboard)
            else:
                LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        else:
            win.fullscreen_on_monitor(monitor)
        win.present()
        self.windows.append(win)

        def gone(*_a):
            for m in (self.entry, self.progress, self.slot, self.links, self.hint, self.column,
                      self.session_buttons):
                for w in m:
                    if w.is_ancestor(center):
                        m.remove(w)
            self.center.remove(center)
            self.power.remove(power)
            if bd in self.backdrops:
                self.backdrops.remove(bd)
            if win in self.windows:
                self.windows.remove(win)
            if getattr(self, "keys_win", None) is win:     # the keyboard moves to a display still there
                self.keys_win = self.windows[0] if self.windows else None
                LS = layer.layer_shell()
                if self.keys_win is not None and LS and LS.is_layer_window(self.keys_win):
                    LS.set_keyboard_mode(self.keys_win, LS.KeyboardMode.EXCLUSIVE)
                self._focus_keys()
            win.destroy()
        monitor.connect("invalidate", gone)
        return win

    def _focus_keys(self) -> None:
        """The field (if shown) on the display with the keyboard takes the keys."""
        keys = getattr(self, "keys_win", None)
        for e in self.entry:
            if e.get_root() is keys:
                GLib.idle_add(lambda e=e: (e.grab_focus(), False)[1])
                return

    def _fill(self, center, focus=False) -> None:
        """One display's page (the login or the users), in place of the one it shows."""
        page = self._login_page(focus) if self.user else self._users_page()
        old = center.get_visible_child()
        center.add_child(page)
        center.set_visible_child(page)
        if old is not None:
            GLib.timeout_add(260, lambda: (center.remove(old), False)[1])

    def _show(self):
        for m in (self.entry, self.progress, self.slot, self.links, self.hint, self.column, self.session_buttons):
            m.clear()
        self.guard = None
        keys = getattr(self, "keys_win", None)
        for i, center in enumerate(self.center):
            # the field on the display that has the keyboard (the first one
            # could be a display that gets no keys: typing went nowhere)
            self._fill(center, focus=(center.get_root() is keys) if keys is not None else (i == 0))
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
        """The users, in rows that fit the display (Vini: many users ran off a
        small screen): as many per row as fit, smaller pictures from 3 rows on,
        scrolling past ~half the display's height."""
        screen = ui.window.screen_size() or (1920, 1080)
        per_row, px = user_grid(len(self.users), screen[0])
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=18,
                           row_spacing=12, max_children_per_line=per_row,
                           min_children_per_line=min(len(self.users), per_row) or 1,
                           halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        for u in self.users:
            b = Gtk.Button(css_classes=["gr-user"])
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            col.append(avatar(px, u.name, u.real, u.icon))
            col.append(Gtk.Label(label=u.real, css_classes=["lk-name"], max_width_chars=16,
                                 ellipsize=Pango.EllipsizeMode.END))
            b.set_child(col)
            b.connect("clicked", lambda _b, u=u: self._pick(u))
            grid.append(b)
        scroll = Gtk.ScrolledWindow(child=grid, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_width=True, propagate_natural_height=True,
                                    max_content_height=int(screen[1] * 0.55), css_classes=["gr-rise"])
        scroll.grid = grid                            # (tests)
        return scroll

    def _pick(self, user):
        if getattr(self, "_busy", False):          # logging in: the page must stay until it ends
            return
        self.user = user
        self._show()

    def _login_page(self, focus=True):
        u = self.user
        col = self.column.add(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                      halign=Gtk.Align.CENTER, css_classes=["gr-rise"]))
        col.append(avatar(108, u.name, u.real, u.icon))
        col.append(Gtk.Label(label=u.real, css_classes=["lk-name"]))
        entry = password_field()
        if self.entry:                                 # another display's field: the same text
            entry.set_text(self.entry.get_text())
            entry.set_sensitive(self.entry.get_sensitive())
        self.entry.add(entry)
        entry.connect("activate", lambda *_: self._login())
        # the field turns into a spinner while logging in (macOS)
        progress = self.progress.add(Gtk.Spinner(css_classes=["gr-spinner"], halign=Gtk.Align.CENTER,
                                                 valign=Gtk.Align.CENTER))
        slot = self.slot.add(Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                       transition_duration=220, halign=Gtk.Align.CENTER))
        slot.add_named(entry, "field")
        slot.add_named(progress, "progress")
        col.append(slot)
        links = self.links.add(Gtk.Box(spacing=4, halign=Gtk.Align.CENTER))
        if len(self.users) > 1:
            other = Gtk.Button(label="Other Users", css_classes=["gr-link"])
            other.connect("clicked", lambda *_: self._pick(None))
            links.append(other)
        if len(self.sessions) > 1:
            links.append(self._session_menu())
        col.append(links)
        hint = self.hint.add(Gtk.Label(css_classes=["lk-hint"]))
        col.append(hint)
        if getattr(self, "guard", None) is None:
            from ..throttle import Throttle
            self.guard = WaitGuard(self.entry, self.hint, Throttle(WAITS))
            self.guard.blocked(u.name)
        elif len(self.hint.items) > 1:
            hint.set_label(self.hint.items[0].get_label())
        if focus:
            GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
        return col

    def _session(self):
        key = self.state.get("sessions", {}).get(self.user.name) if self.user else None
        return next((s for s in self.sessions if s.key == key), self.sessions[0] if self.sessions else None)

    def _session_menu(self):
        btn = self.session_buttons.add(Gtk.Button(css_classes=["gr-link"]))
        btn.set_label(self._session().name + "  ▾")

        def choose(s):
            self.state.setdefault("sessions", {})[self.user.name] = s.key
            self.session_buttons.set_label(s.name + "  ▾")         # every display's
        btn.connect("clicked", lambda b: ui.menu.popup(
            b, [[ui.menu.Item(s.name, lambda s=s: choose(s), checked=(s is self._session()))
                 for s in self.sessions]], position=Gtk.PositionType.BOTTOM, glass=True))
        return btn

    # displays: the saved resolution of each, else its highest refresh rate
    def _restore_modes(self):
        if self.fake:
            return
        saved = self.state.get("modes", {})
        for o in outputs():
            want = saved.get(o["name"])
            modes = o["modes"]
            if want and tuple(want) in {(w, h, round(hz, 3)) for w, h, hz in modes}:
                mode = next(m for m in modes if (m[0], m[1], round(m[2], 3)) == tuple(want))
            else:
                cur = o["current"]
                same = [m for m in modes if cur and (m[0], m[1]) == cur[:2]]
                mode = max(same or modes, key=lambda m: m[2], default=None)
            if mode and mode != o["current"]:
                set_mode(o["name"], mode)

    def _display_button(self):
        btn = Gtk.Button(icon_name="video-display-symbolic", css_classes=["gr-power"], tooltip_text="Displays",
                         halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=24, margin_bottom=24)
        btn.connect("clicked", lambda b: self._display_menu(b))
        return btn

    def _display_menu(self, btn):
        sections = []
        for o in outputs():
            items = [ui.menu.Item(o["description"], enabled=False)]
            cur = o["current"]
            for m in best_modes(o["modes"])[:14]:
                label = f"{m[0]} \u00d7 {m[1]}  \u00b7  {round(m[2])} Hz"
                items.append(ui.menu.Item(label, lambda o=o, m=m: self._try_mode(o, m),
                                          checked=bool(cur) and cur[:2] == m[:2]))
            sections.append(items)
        if sections:
            ui.menu.popup(btn, sections, position=Gtk.PositionType.TOP, glass=True)

    def _try_mode(self, o, mode):
        """Apply, and keep it only if confirmed within 15 s (a mode the display
        can't show would otherwise leave the screen black)."""
        old = o["current"]
        if not set_mode(o["name"], mode):
            return
        timer = {"src": 0}

        def keep(ok):
            if timer["src"]:
                GLib.source_remove(timer["src"])
            elif timer.get("done"):
                return
            timer["src"], timer["done"] = 0, True
            if ok:
                self.state.setdefault("modes", {})[o["name"]] = [mode[0], mode[1], round(mode[2], 3)]
                save_state(self.state)
            elif old:
                set_mode(o["name"], old)

        # in the login screen's own window: a separate one would open below it
        dlg = ui.dialog.alert("Keep these display settings?", "Going back to the previous ones in 15 seconds.",
                              [("revert", "Revert", ""), ("keep", "Keep", "default")],
                              lambda r: keep(r == "keep"), parent=self.windows[0] if self.windows else None)

        def expired():
            timer["src"] = 0
            keep(False)
            if hasattr(dlg, "force_close"):
                dlg.force_close()
            return False
        timer["src"] = GLib.timeout_add_seconds(15, expired)

    def _power(self):
        return power_bar(lambda method: None if self.fake else logind(method))

    # login
    def _login(self):
        pw = self.entry.get_text()
        session = self._session()
        if not pw or session is None or self.guard.blocked(self.user.name):
            return
        self.entry.set_sensitive(False)
        self.links.set_sensitive(False)              # no "Other Users"/session change mid-login
        self._busy = True
        self.hint.set_label("")
        user = self.user.name
        self.progress.start()
        self.slot.set_visible_child_name("progress")

        def work():
            client = None
            try:
                client = greetd.Fake() if self.fake else greetd.Client()
                greetd.login(client, user, pw)
                env = ["XDG_SESSION_TYPE=wayland", f"XDG_SESSION_DESKTOP={session.key}"]
                if session.desktops:
                    env.append(f"XDG_CURRENT_DESKTOP={session.desktops}")
                client.start_session(session.cmd, env)
                GLib.idle_add(self._started, user)
            except Exception as e:          # any error must end the spinner, never leave it turning
                if client is not None:
                    client.close()
                if not isinstance(e, greetd.GreetdError):
                    e = greetd.GreetdError("error", str(e) or type(e).__name__)
                GLib.idle_add(self._failed, e, user)
        threading.Thread(target=work, daemon=True).start()

    def _stop_pulse(self):
        self.progress.stop()

    def _failed(self, err, user=None):
        user = user or self.user.name
        self._busy = False
        self.links.set_sensitive(True)
        self._stop_pulse()
        self.slot.set_visible_child_name("field")
        if err.error_type != "auth_error":
            self.hint.set_label(str(err) or "Couldn't log in")
        shake(self.entry)
        if err.error_type == "auth_error":
            self.guard.failed(user)
        return False

    def _started(self, user=None):
        user = user or self.user.name
        self.guard.succeeded(user)
        self.state["user"] = user
        save_state(self.state)
        # (the spinner keeps turning while) the picture, name and bar fade away over the blurred wallpaper; the
        # session's welcome screen (welcome.py) picks up from the same look
        for w in (getattr(self, "column", None), self.power):
            if w is not None:
                w.add_css_class("gr-leave")
        GLib.timeout_add(420, self._quit)
        return False

    def _quit(self):
        self.app.quit()
        if not self.fake and os.environ.get("SONATA_GREETER_WAYFIRE"):
            # greetd starts the session when the greeter command (our Wayfire) ends
            subprocess.run(["pkill", "-TERM", "-u", str(os.getuid()), "-x", "wayfire"])
        return False
