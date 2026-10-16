"""XSETTINGS for X11 apps: Sonata's look for everything that runs through
XWayland (Vini: Electron apps' menu bars light in Dark Mode).

A GTK app on X11 -- Electron and Chromium in X11 mode, GTK 2/3 apps, Wine,
Java... -- doesn't read GSettings or the settings portal: it asks the X
server's XSETTINGS manager for its theme, icons, cursor and fonts (on
GNOME, gnome-settings-daemon is that manager). Sonata's session had none,
so those apps fell back to GTK's defaults: Adwaita (light), its icons and
fonts, whatever Dark Mode said. This is Sonata's manager: it owns the
display's _XSETTINGS_S0 selection and publishes Sonata's settings
(prefs.py), again whenever they change (Dark Mode switches them live).

Started by Wayfire each time XWayland starts (core/xwayland_startup_script,
tools/wayfire-config.sh); it ends with that X server.

    python3 -m sonata2.xsettings          # (DISPLAY: the XWayland display)
    encode({"Net/ThemeName": "Sonata-Dark"}, serial)   # the property's bytes
"""
import ctypes
import ctypes.util
import struct
import sys

from . import prefs

I, WM = prefs.I, prefs.WM


def values(get=prefs.get) -> dict:
    """{XSETTINGS name: int or str}, from Sonata's settings."""
    dark = get(I, "color-scheme") == "prefer-dark"
    theme = get(I, "gtk-theme") or ("Sonata-Dark" if dark else "Sonata-Light")
    try:
        scale = float(get(I, "text-scaling-factor") or 1.0)
    except ValueError:
        scale = 1.0
    try:
        cursor = int(get(I, "cursor-size") or 24)
    except ValueError:
        cursor = 24
    aa = get(I, "font-antialiasing") or "grayscale"
    hinting = get(I, "font-hinting") or "slight"
    return {
        "Net/ThemeName": theme,
        "Net/IconThemeName": get(I, "icon-theme") or "Sonata",
        "Gtk/CursorThemeName": get(I, "cursor-theme") or "Sonata-Cursors",
        "Gtk/CursorThemeSize": cursor,
        "Gtk/FontName": get(I, "font-name") or "Inter 10",
        "Gtk/MonospaceFontName": get(I, "monospace-font-name") or "Monospace 10",
        "Gtk/DecorationLayout": get(WM, "button-layout") or "close,minimize,maximize:",
        "Gtk/EnableAnimations": int(get(I, "enable-animations") != "false"),
        "Gtk/ApplicationPreferDarkTheme": int(dark),
        "Gtk/PrimaryButtonWarpsSlider": 1,
        "Net/EnableEventSounds": 0,
        "Net/CursorBlink": int(get(I, "cursor-blink") != "false"),
        "Xft/Antialias": int(aa != "none"),
        "Xft/RGBA": "rgb" if aa == "rgba" else "none",
        "Xft/Hinting": int(hinting != "none"),
        "Xft/HintStyle": {"none": "hintnone", "slight": "hintslight", "medium": "hintmedium",
                          "full": "hintfull"}.get(hinting, "hintslight"),
        "Xft/DPI": int(round(96 * 1024 * scale)),
    }


def _pad(n: int) -> int:
    return (4 - n % 4) % 4


def encode(settings: dict, serial: int) -> bytes:
    """The _XSETTINGS_SETTINGS property (the XSETTINGS spec, little endian):
    byte order, serial, count, then each setting -- type, name, the serial
    it last changed in, value (int: INT32; string: length + bytes)."""
    out = [struct.pack("<B3xII", 0, serial, len(settings))]
    for name, value in sorted(settings.items()):
        raw = name.encode()
        kind = 0 if isinstance(value, int) else 1
        out.append(struct.pack("<BxH", kind, len(raw)) + raw + b"\0" * _pad(len(raw)))
        out.append(struct.pack("<I", serial))
        if kind == 0:
            out.append(struct.pack("<i", value))
        else:
            data = str(value).encode()
            out.append(struct.pack("<I", len(data)) + data + b"\0" * _pad(len(data)))
    return b"".join(out)


# -- the manager (libX11 through ctypes: nothing to install) -------------------------------
class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]


class _Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


CLIENT_MESSAGE, STRUCTURE_NOTIFY_MASK, PROP_MODE_REPLACE, CURRENT_TIME = 33, 1 << 17, 0, 0


class Manager:
    def __init__(self, display: bytes = None):
        x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XCreateSimpleWindow.restype = ctypes.c_ulong
        x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_int] * 4 + \
            [ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        x.XSetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        x.XGetSelectionOwner.restype = ctypes.c_ulong
        x.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                 ctypes.POINTER(_Event)]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Event)]
        self.x = x
        self.dpy = x.XOpenDisplay(display)
        if not self.dpy:
            raise OSError("can't open the X display")
        self.root = x.XDefaultRootWindow(self.dpy)
        screen = x.XDefaultScreen(self.dpy)
        self.selection = x.XInternAtom(self.dpy, f"_XSETTINGS_S{screen}".encode(), 0)
        self.prop = x.XInternAtom(self.dpy, b"_XSETTINGS_SETTINGS", 0)
        self.manager = x.XInternAtom(self.dpy, b"MANAGER", 0)
        self.win = x.XCreateSimpleWindow(self.dpy, self.root, -1, -1, 1, 1, 0, 0, 0)
        self.serial = 0
        self.published = None

    def claim(self) -> bool:
        """Become the display's XSETTINGS manager and tell its clients."""
        x = self.x
        x.XSetSelectionOwner(self.dpy, self.selection, self.win, CURRENT_TIME)
        if x.XGetSelectionOwner(self.dpy, self.selection) != self.win:
            return False
        ev = _Event()
        ev.xclient.type = CLIENT_MESSAGE
        ev.xclient.window = self.root
        ev.xclient.message_type = self.manager
        ev.xclient.format = 32
        for i, v in enumerate((CURRENT_TIME, self.selection, self.win, 0, 0)):
            ev.xclient.data[i] = v
        x.XSendEvent(self.dpy, self.root, 0, STRUCTURE_NOTIFY_MASK, ctypes.byref(ev))
        x.XFlush(self.dpy)
        return True

    def publish(self, settings: dict) -> None:
        """The settings on the manager's window (clients watch it): only when
        they changed, with a new serial."""
        if settings == self.published:
            return
        self.serial += 1
        data = encode(settings, self.serial)
        self.x.XChangeProperty(self.dpy, self.win, self.prop, self.prop, 8, PROP_MODE_REPLACE, data, len(data))
        self.x.XFlush(self.dpy)
        self.published = dict(settings)

    def fileno(self) -> int:
        return self.x.XConnectionNumber(self.dpy)

    def drain(self) -> None:
        """Read what the server sends (nothing to act on), so it never piles up."""
        ev = _Event()
        while self.x.XPending(self.dpy):
            self.x.XNextEvent(self.dpy, ctypes.byref(ev))


def main() -> int:
    from gi.repository import GLib
    try:
        m = Manager()
    except OSError as e:
        print(f"sonata2 xsettings: {e}", file=sys.stderr)
        return 1
    m.publish(values())
    if not m.claim():
        print("sonata2 xsettings: another XSETTINGS manager runs on this display", file=sys.stderr)
        return 0
    loop = GLib.MainLoop()
    mon = prefs.watch(lambda: m.publish(values()))       # Dark Mode, theme, fonts: live

    def readable(_fd, cond):
        if cond & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            loop.quit()                                  # XWayland is gone: so is its manager
            return False
        m.drain()
        return True
    GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, m.fileno(),
                          GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR, readable)
    loop.run()
    del mon
    return 0


if __name__ == "__main__":
    sys.exit(main())
