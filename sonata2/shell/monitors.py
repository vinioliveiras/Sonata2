"""Several displays (macOS): the wallpaper and a menu bar on every display;
the Dock, desktop icons and notifications on the main display. The main
display is Settings > Displays' choice (displays.json "main" = connector),
else an external monitor when one is connected, else the built-in panel.

    monitors.each(create, destroy)   # a surface per display, hotplug included
    monitors.main()                  # Gdk.Monitor of the main display
    monitors.on_main_changed(cb)     # main display chosen elsewhere / unplugged

Refresh rate: a display nobody picked a mode for runs at its highest
refresh rate (Wayfire `mode = highrr`, written once per connector). Scale:
one nobody picked a scale for gets auto_scale (its DPI; a 1080p desktop = 1)."""
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
    from .. import displaysleep
    data = json.dumps({"main": config.load("displays", DEFAULTS)["main"],
                       "off_after": displaysleep.seconds()}).encode()   # the login screen goes dark too
    try:
        config.atomic_write(os.path.join(folder, "displays.json"), data, fsync=False)
    except OSError:
        pass


def _login_shared(users, key, ok):
    """`key` from what the first of `users` shared (share_with_login_screen), or None."""
    import json
    import os
    for name in users:
        try:
            with open(os.path.join(GREETER, name, "displays.json"), encoding="utf-8") as f:
                want = json.load(f).get(key)
            if ok(want):
                return want
        except (OSError, ValueError, AttributeError):
            continue
    return None


def login_main(users) -> str:
    """The main display a user chose (the first of `users` that has one), for the login screen."""
    return _login_shared(users, "main", lambda v: isinstance(v, str) and v) or ""


def login_off_after(users) -> int:
    """Seconds before the login screen goes dark: what the user chose in
    Settings (<= 0 never), else Sonata's default."""
    from .. import displaysleep
    v = _login_shared(users, "off_after", lambda v: isinstance(v, int) and not isinstance(v, bool))
    return displaysleep.DEFAULT if v is None else v


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


# Sizes look right on a 1080p desktop monitor (Vini; ~92 DPI at 24"). Other
# displays get the scale that keeps that size: from their DPI (the size the
# display reports), else their resolution. Laptops' panels are seen closer:
# a higher reference. Never under 1 (a downscaled picture is blurry and
# costs the GPU more), quarter steps.
DESKTOP_DPI, BUILTIN_DPI = 96, 125
SCALE_REFERENCE = 1080                 # resolution fallback: the shorter side
SCALE_RANGE = (1.0, 3.0)


def physical_dpi(w_px: int, h_px: int, w_mm: int, h_mm: int):
    """Pixels per inch from the size a display reports, or None when that
    size is missing or made up (projectors and TVs: 0, tiny, huge, or not
    the picture's shape)."""
    if min(w_px, h_px, w_mm, h_mm) <= 0:
        return None
    long_mm = max(w_mm, h_mm)
    if not 120 <= long_mm <= 1500:
        return None
    px_ratio = max(w_px, h_px) / min(w_px, h_px)
    mm_ratio = long_mm / min(w_mm, h_mm)
    if abs(px_ratio / mm_ratio - 1) > 0.1:
        return None
    dpi = max(w_px, h_px) / (long_mm / 25.4)
    return dpi if 50 <= dpi <= 500 else None            # (centimetres reported as millimetres...)


def auto_scale(width: int, height: int, w_mm: int = 0, h_mm: int = 0, builtin: bool = False) -> float:
    """The scale for a display of width x height pixels (and w_mm x h_mm)."""
    dpi = physical_dpi(width, height, w_mm, h_mm)
    if dpi is not None:
        s = dpi / (BUILTIN_DPI if builtin else DESKTOP_DPI)
    elif min(width, height) > 0:
        s = min(width, height) / SCALE_REFERENCE
    else:
        return 1.0
    return min(SCALE_RANGE[1], max(SCALE_RANGE[0], round(s * 4) / 4))


def _auto_scale_of(m) -> float:
    g = m.get_geometry()
    k = m.get_scale() if hasattr(m, "get_scale") else m.get_scale_factor()     # logical -> pixels
    return auto_scale(round(g.width * k), round(g.height * k), m.get_width_mm(), m.get_height_mm(),
                      connector(m).startswith(_BUILTIN))


def ensure_refresh() -> None:
    """Displays without a mode of their own: highest refresh rate; without
    a scale of their own: auto_scale, kept up to date (resolution changed,
    display plugged in) until one is picked in Settings > Displays -- what
    was set automatically is remembered (display-scale.json), anything
    else is the user's. Runs in the menu bar (always there). Never cap the
    rate to spare the GPU: some monitors glitch below their top rate
    (Vini's external one does); a user's own mode or scale in Settings wins."""
    from ..backend import system

    def apply(*_a):
        auto = config.load("display-scale", {"auto": {}})["auto"]
        changed = False
        for m in _list():
            c = connector(m)
            if not c:
                continue
            if not system.wayfire_get(f"output:{c}", "mode", ""):
                system.wayfire_set(f"output:{c}", "mode", "highrr")
            cur = system.wayfire_get(f"output:{c}", "scale", "")
            try:
                mine = cur == "" or (c in auto and abs(float(cur) - auto[c]) < 1e-3)
            except ValueError:
                mine = False
            if not mine or m.get_geometry().width <= 0:
                continue
            want = _auto_scale_of(m)
            if cur == "" or abs(float(cur) - want) > 1e-3:
                system.wayfire_set(f"output:{c}", "scale", want)
            if auto.get(c) != want:
                auto[c], changed = want, True
        if changed:
            config.save("display-scale", {"auto": auto})
        return False

    pending = {"src": 0}

    def soon(*_a):                         # a burst of changes (a mode switch): once
        if pending["src"]:
            GLib.source_remove(pending["src"])

        def run():
            pending["src"] = 0
            return apply()
        pending["src"] = GLib.timeout_add(300, run)

    def watch(*_a):
        for m in _list():
            if not getattr(m, "_sonata_scale_watch", False):
                m._sonata_scale_watch = True
                m.connect("notify::geometry", soon)      # its resolution changed
        soon()
    watch()
    Gdk.Display.get_default().get_monitors().connect("items-changed", watch)       # plugged in: watched too
