"""gtk4-layer-shell helpers.

The library must be loaded before libwayland-client, so the process is
re-executed once with LD_PRELOAD (see ensure_preload). Without layer-shell
(no Wayland, GNOME, or --preview) surfaces fall back to plain windows."""
import ctypes.util
import os
import sys

_PRELOAD_FLAG = "SONATA2_PRELOADED"
_ORIG_PRELOAD = "SONATA2_ORIG_LD_PRELOAD"


def ensure_preload() -> None:
    """Re-exec the current process with libgtk4-layer-shell preloaded.

    After the re-exec the environment is restored, so apps started from the
    shell (and a nested dev session started from them) don't inherit the
    preload or the flag -- an inherited flag would skip the preload there
    and turn every shell surface into a plain window."""
    if os.environ.get(_PRELOAD_FLAG):
        os.environ.pop(_PRELOAD_FLAG)
        orig = os.environ.pop(_ORIG_PRELOAD, "")
        if orig:
            os.environ["LD_PRELOAD"] = orig
        else:
            os.environ.pop("LD_PRELOAD", None)
        return
    if not os.environ.get("WAYLAND_DISPLAY"):
        return
    lib = ctypes.util.find_library("gtk4-layer-shell")
    if not lib:
        return
    env = dict(os.environ, **{_PRELOAD_FLAG: "1", _ORIG_PRELOAD: os.environ.get("LD_PRELOAD", "")})
    env["LD_PRELOAD"] = " ".join(filter(None, (lib, env.get("LD_PRELOAD"))))
    os.execve(sys.executable, sys.orig_argv, env)   # orig_argv keeps "-m sonata2"


def layer_shell():
    """The Gtk4LayerShell module when usable on this display, else None."""
    try:
        import gi
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LS
    except (ImportError, ValueError):
        return None
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    if type(Gdk.Display.get_default()).__name__ != "WaylandDisplay":
        return None            # X11 / previews: plain windows (no assertion spam)
    return LS if LS.is_supported() else None


def anchor_edge(win, namespace: str, edge: str, exclusive: int) -> bool:
    """Make `win` a layer surface spanning the whole `edge` ("bottom", "left",
    "right"), so its content can grow (magnification) without resizing the
    surface; restrict clicks with set_input_region(). False = plain window."""
    LS = layer_shell()
    if not LS:
        return False
    LS.init_for_window(win)
    LS.set_namespace(win, namespace)
    LS.set_layer(win, LS.Layer.TOP)
    LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
    set_edge(win, edge, exclusive)
    return True


def set_edge(win, edge: str, exclusive: int) -> None:
    LS = layer_shell()
    E = LS.Edge
    main = {"bottom": E.BOTTOM, "left": E.LEFT, "right": E.RIGHT}[edge]
    across = (E.LEFT, E.RIGHT) if edge == "bottom" else (E.TOP, E.BOTTOM)
    for e in (E.TOP, E.BOTTOM, E.LEFT, E.RIGHT):
        LS.set_anchor(win, e, e == main or e in across)
    LS.set_exclusive_zone(win, exclusive)


def set_exclusive(win, exclusive: int) -> None:
    LS = layer_shell()
    if LS and LS.is_layer_window(win):
        LS.set_exclusive_zone(win, exclusive)


def set_input_region(win, rects) -> None:
    """Only these (x, y, w, h) rectangles of `win` receive pointer input;
    the rest of the transparent surface lets clicks through."""
    import cairo
    surface = win.get_surface()
    if not surface:
        return
    region = cairo.Region()
    for x, y, w, h in rects:
        region.union(cairo.RectangleInt(int(x), int(y), int(w) + 1, int(h) + 1))
    surface.set_input_region(region)


def overlay_fullscreen(win, namespace: str) -> bool:
    """Full-screen surface above everything (Launchpad) that takes the
    keyboard; ignores other surfaces' exclusive zones. False = plain window."""
    LS = layer_shell()
    if not LS:
        return False
    LS.init_for_window(win)
    LS.set_namespace(win, namespace)
    LS.set_layer(win, LS.Layer.OVERLAY)
    for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
        LS.set_anchor(win, e, True)
    LS.set_exclusive_zone(win, -1)
    LS.set_keyboard_mode(win, LS.KeyboardMode.EXCLUSIVE)
    return True
