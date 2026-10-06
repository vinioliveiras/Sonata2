"""The screen dark while you're away, unlocked (Vini: it only dimmed, and
the pointer stayed on it). Black over every display (layer-shell overlay),
fading in like the lock screen's (.lk-blackout, loginui.py), the pointer
hidden; any key or move fades it out again (IdleLock's idle watch).

    blackout.show(); blackout.hide()
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import layer, loginui  # noqa: E402,F401  (loginui: the .lk-blackout look)

ui.register("""
window.sonata-blackout, window.sonata-blackout > contents { background: none; box-shadow: none; }
""", key="blackout")

NAMESPACE = "sonata2-blackout"
FADE_OUT_MS = 650              # .lk-blackout fades out in 600 ms; then the surfaces go
_WINDOWS = []


def _window(monitor) -> Gtk.Window:
    win = Gtk.Window(title="Blackout", decorated=False, css_classes=["sonata-blackout"])
    black = Gtk.Box(css_classes=["lk-blackout"], hexpand=True, vexpand=True)
    win.set_child(black)
    win.black = black
    win.set_cursor(Gdk.Cursor.new_from_name("none"))            # no pointer on a dark screen
    LS = layer.layer_shell()
    if LS:
        layer.overlay_fullscreen(win, NAMESPACE)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        LS.set_monitor(win, monitor)
    return win


def pointer(hidden: bool) -> None:
    """The pointer off / back, by the compositor (sonata-corners): the
    blackout's own "no cursor" only applies once the pointer moves onto it,
    and a still pointer stayed in the middle of the dark screen (Vini)."""
    try:
        from ..wl.wfipc import WayfireIPC
        WayfireIPC().call("sonata/cursor", {"hidden": bool(hidden)})
    except Exception:
        pass


def showing() -> bool:
    return any(w.black.has_css_class("on") for w in _WINDOWS)


def show() -> None:
    if showing():
        return
    _close()
    mons = Gdk.Display.get_default().get_monitors()
    for i in range(mons.get_n_items()):
        w = _window(mons.get_item(i))
        _WINDOWS.append(w)
        w.present()
    pointer(True)
    # one frame transparent, then black: the fade shows (as the lock screen's)
    GLib.timeout_add(50, lambda: ([w.black.add_css_class("on") for w in _WINDOWS], False)[1])


def hide() -> None:
    pointer(False)
    for w in _WINDOWS:
        w.black.remove_css_class("on")
    gone = list(_WINDOWS)
    GLib.timeout_add(FADE_OUT_MS, lambda: (_close(gone), False)[1])


def _close(wins=None) -> None:
    for w in list(wins if wins is not None else _WINDOWS):
        if w in _WINDOWS and (wins is None or not w.black.has_css_class("on")):
            _WINDOWS.remove(w)
            w.destroy()
