"""gtk4-layer-shell helpers.

The library must be loaded before libwayland-client, so the process is
re-executed once with LD_PRELOAD (see ensure_preload). Without layer-shell
(no Wayland, GNOME, or --preview) surfaces fall back to plain windows."""
import ctypes.util
import os
import sys

_PRELOAD_FLAG = "SONATA2_PRELOADED"


def ensure_preload() -> None:
    """Re-exec the current process with libgtk4-layer-shell preloaded."""
    if os.environ.get(_PRELOAD_FLAG) or not os.environ.get("WAYLAND_DISPLAY"):
        return
    lib = ctypes.util.find_library("gtk4-layer-shell")
    if not lib:
        return
    env = dict(os.environ, **{_PRELOAD_FLAG: "1"})
    env["LD_PRELOAD"] = " ".join(filter(None, (lib, env.get("LD_PRELOAD"))))
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


def layer_shell():
    """The Gtk4LayerShell module when usable on this display, else None."""
    try:
        import gi
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
    except (ImportError, ValueError):
        return None
    return LS if LS.is_supported() else None


def anchor_bottom(win, namespace: str, margin: int, exclusive: int) -> bool:
    """Make `win` a bottom-centered overlay surface. False = plain window."""
    LS = layer_shell()
    if not LS:
        return False
    LS.init_for_window(win)
    LS.set_namespace(win, namespace)
    LS.set_layer(win, LS.Layer.TOP)
    LS.set_anchor(win, LS.Edge.BOTTOM, True)
    LS.set_margin(win, LS.Edge.BOTTOM, margin)
    LS.set_exclusive_zone(win, exclusive)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
    return True
