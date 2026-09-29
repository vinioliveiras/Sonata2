"""Several displays (macOS): the wallpaper and a menu bar on every display;
the Dock, desktop icons and notifications on the main display. The main
display is Settings > Displays' choice (displays.json "main" = connector),
else the built-in panel, else the first one.

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


def main():
    ms = _list()
    if not ms:
        return None
    want = config.load("displays", DEFAULTS)["main"]
    for m in ms:
        if want and connector(m) == want:
            return m
    for m in ms:
        if connector(m).startswith(_BUILTIN):
            return m
    return ms[0]


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
    the menu bar (always there); again when a display is plugged in."""
    from ..backend import system

    def apply(*_a):
        for m in _list():
            c = connector(m)
            if c and not system.wayfire_get(f"output:{c}", "mode", ""):
                system.wayfire_set(f"output:{c}", "mode", "highrr")
        return False
    apply()
    Gdk.Display.get_default().get_monitors().connect("items-changed", lambda *_: GLib.timeout_add(300, apply))
