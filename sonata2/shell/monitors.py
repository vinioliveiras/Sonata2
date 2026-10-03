"""Several displays (macOS): the wallpaper and a menu bar on every display;
the Dock, desktop icons and notifications on the main display. The main
display is Settings > Displays' choice (displays.json "main" = connector),
else an external monitor when one is connected, else the built-in panel.

    monitors.each(create, destroy)   # a surface per display, hotplug included
    monitors.main()                  # Gdk.Monitor of the main display
    monitors.on_main_changed(cb)     # main display chosen elsewhere / unplugged

Refresh rate: a display nobody picked a mode for runs at its highest
refresh rate (Wayfire `mode = highrr`, written once per connector)."""
from gi.repository import Gdk, GLib

from .. import config

DEFAULTS = {"main": ""}
_BUILTIN = ("eDP", "LVDS", "DSI")


def _list():
    ms = Gdk.Display.get_default().get_monitors()
    return [ms.get_item(i) for i in range(ms.get_n_items())]


def connector(m) -> str:
    return (m.get_connector() or "") if m is not None else ""


def main(want: str = None):
    """want: a connector to prefer (the login screen: the user's choice)."""
    ms = _list()
    if not ms:
        return None
    if want is None:
        want = config.load("displays", DEFAULTS)["main"]
    for m in ms:
        if want and connector(m) == want:
            return m
    # nothing chosen (or the chosen one is unplugged): an external monitor when
    # one is connected (Vini), else the built-in panel
    for m in ms:
        if connector(m) and not connector(m).startswith(_BUILTIN):
            return m
    return ms[0]


GREETER = "/var/lib/sonata-greeter"          # per user, writable by them (install.sh --greeter)


def share_with_login_screen() -> None:
    """The login screen runs before the session, as another user: it can't
    read ~/.config. Copy the main display there, like the wallpaper
    (wallpaper.py); nothing to do without the login screen's folder."""
    import json
    import os
    folder = os.path.join(GREETER, GLib.get_user_name())
    if not os.access(folder, os.W_OK):
        return
    data = json.dumps({"main": config.load("displays", DEFAULTS)["main"]}).encode()
    try:
        config.atomic_write(os.path.join(folder, "displays.json"), data, fsync=False)
    except OSError:
        pass


def login_main(users) -> str:
    """The main display a user chose (the first of `users` that has one), for the login screen."""
    import json
    import os
    for name in users:
        try:
            with open(os.path.join(GREETER, name, "displays.json"), encoding="utf-8") as f:
                want = json.load(f).get("main")
            if isinstance(want, str) and want:
                return want
        except (OSError, ValueError, AttributeError):
            continue
    return ""


class Surfaces:
    """create(monitor) -> surface for every display now and when one is
    plugged in; destroy(surface) when its display goes away. rebuild()
    makes them all again (e.g. the main display changed)."""

    def __init__(self, create, destroy):
        self.create, self.destroy, self.surfaces = create, destroy, {}
        self.sync()
        # connector names arrive a moment after items-changed on some compositors
        Gdk.Display.get_default().get_monitors().connect("items-changed",
                                                         lambda *_: GLib.timeout_add(200, self.sync))

    def sync(self, *_a) -> bool:
        ms = _list()
        for m in list(self.surfaces):
            if m not in ms:
                self.destroy(self.surfaces.pop(m))
        for m in ms:
            if m not in self.surfaces:
                self.surfaces[m] = self.create(m)
        return False

    def rebuild(self) -> None:
        for m in list(self.surfaces):
            self.destroy(self.surfaces.pop(m))
        self.sync()


def each(create, destroy) -> Surfaces:
    return Surfaces(create, destroy)


_watch = None


def on_main_changed(callback) -> None:
    """callback(main monitor) when displays.json or the display list change."""
    global _watch
    state = {"main": main()}

    def check(*_a):
        m = main()
        if m is not state["main"]:
            state["main"] = m
            callback(m)
        return False
    _watch = config.watch("displays", check)
    Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: GLib.timeout_add(250, check))


def ensure_refresh() -> None:
    """Displays without a mode of their own: highest refresh rate. Runs in
    the menu bar (always there); again when a display is plugged in.
    Never cap it to spare the GPU: some monitors glitch below their top
    rate (Vini's external one does); a user's own mode in Settings wins."""
    from ..backend import system

    def apply(*_a):
        for m in _list():
            c = connector(m)
            if c and not system.wayfire_get(f"output:{c}", "mode", ""):
                system.wayfire_set(f"output:{c}", "mode", "highrr")
        return False
    apply()
    Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: GLib.timeout_add(300, apply))
