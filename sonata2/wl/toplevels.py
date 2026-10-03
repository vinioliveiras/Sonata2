"""Open windows of every app, via wlr-foreign-toplevel-management-v1.

Works on any wlroots compositor (Wayfire, labwc, sway...). The protocol
objects live on GTK's own Wayland connection, so events are dispatched by the
GTK main loop -- no extra socket, thread or fd watch. Python bindings are
generated once from the bundled XML with pywayland's scanner and cached in
~/.cache/sonata2 (keyed by pywayland version)."""
import ctypes
import importlib.util
import os
import struct

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

PROTO = "wlr_foreign_toplevel_management_unstable_v1"
XML = os.path.join(os.path.dirname(__file__), "protocols",
                   "wlr-foreign-toplevel-management-unstable-v1.xml")

# zwlr_foreign_toplevel_handle_v1.state values
MAXIMIZED, MINIMIZED, ACTIVATED, FULLSCREEN = 0, 1, 2, 3


def _load_protocol():
    import pywayland
    from pywayland.scanner import Protocol
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sonata2", "pywayland-" + pywayland.__version__)
    path = os.path.join(cache, PROTO + ".py")
    if not os.path.exists(path):
        # several processes start at login: each generates in its own temp
        # folder, the finished file is renamed in (never a half one imported)
        import shutil
        import tempfile
        from .. import config
        os.makedirs(cache, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".gen-", dir=cache)
        try:
            proto = Protocol.parse_file(XML)
            proto.output(tmp, {i.name: proto.name for i in proto.interface}
                         | {"wl_seat": "wayland", "wl_output": "wayland", "wl_surface": "wayland"})
            with open(os.path.join(tmp, PROTO + ".py"), encoding="utf-8") as f:
                src = f.read()
            # core interfaces come from pywayland
            config.atomic_write(path, src.replace("from .wayland import",
                                                  "from pywayland.protocol.wayland import").encode("utf-8"),
                                fsync=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    spec = importlib.util.spec_from_file_location("sonata2_" + PROTO, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gpointer(obj) -> int:
    """C address of a PyGObject object."""
    get = ctypes.pythonapi.PyCapsule_GetPointer
    get.restype, get.argtypes = ctypes.c_void_p, [ctypes.py_object, ctypes.c_char_p]
    return get(obj.__gpointer__, None)


_GTK = None


def _gtk_fn(name):
    global _GTK
    if _GTK is None:
        _GTK = ctypes.CDLL("libgtk-4.so.1")
    fn = getattr(_GTK, name)
    fn.restype, fn.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    return fn


class Toplevel:
    """One window. Read `app_id`, `title`, `states`; changes are reported to
    the manager's listeners after the compositor's `done` event."""

    def __init__(self, handle):
        self.handle = handle
        self.app_id = ""
        self.title = ""
        self.states = frozenset()

    @property
    def minimized(self) -> bool:
        return MINIMIZED in self.states

    @property
    def activated(self) -> bool:
        return ACTIVATED in self.states

    @property
    def maximized(self) -> bool:
        return MAXIMIZED in self.states


class ToplevelManager:
    """Tracks all toplevels. `listeners` get called with no arguments after
    any change (window opened/closed/retitled/state change)."""

    def __init__(self, display: Gdk.Display, ignore_app_ids=()):
        self.toplevels = []
        self.listeners = []
        self.available = False
        self._ignore = set(ignore_app_ids)
        self._seat = self._mgr = None
        try:
            from pywayland.client import Display
            from pywayland import ffi
            from pywayland.protocol.wayland import WlSeat
            self._ffi = ffi
            gi.require_version("GdkWayland", "4.0")
            from gi.repository import GdkWayland
            if not isinstance(display, GdkWayland.WaylandDisplay):
                return
            self._mod = _load_protocol()
        except (ImportError, ValueError, OSError) as e:
            print(f"sonata2: window tracking disabled ({e})")
            return
        ptr = _gtk_fn("gdk_wayland_display_get_wl_display")(_gpointer(display))
        self._display = Display()
        self._display._ptr = ffi.cast("struct wl_display *", ptr)   # borrowed: never disconnected
        self._registry = self._display.get_registry()
        mgr_iface = self._mod.ZwlrForeignToplevelManagerV1

        def on_global(reg, name, iface, version):
            if iface == mgr_iface.name and not self._mgr:
                self._mgr = reg.bind(name, mgr_iface, min(version, 3))
                self._mgr.dispatcher["toplevel"] = self._on_toplevel
            elif iface == "wl_seat" and not self._seat:
                self._seat = reg.bind(name, WlSeat, 1)
        self._registry.dispatcher["global"] = on_global
        self._display.roundtrip()
        self.available = self._mgr is not None
        if not self.available:
            print("sonata2: compositor has no wlr-foreign-toplevel-management; no window tracking")

    # -- events ----------------------------------------------------------------
    def _on_toplevel(self, _mgr, handle):
        t = Toplevel(handle)
        pending = {}

        def title(_h, s):
            pending["title"] = s

        def app_id(_h, s):
            pending["app_id"] = s

        def state(_h, arr):
            raw = bytes(arr) if not isinstance(arr, (bytes, bytearray)) else arr
            try:
                pending["states"] = frozenset(struct.unpack(f"={len(raw) // 4}I", raw))
            except (struct.error, TypeError):
                pending["states"] = frozenset(arr)

        def done(_h):
            for k, v in pending.items():
                setattr(t, k, v)
            pending.clear()
            if t not in self.toplevels and t.app_id not in self._ignore:
                self.toplevels.append(t)
            self._notify()

        def closed(_h):
            if t in self.toplevels:
                self.toplevels.remove(t)
                self._notify()
            handle.destroy()

        for ev, fn in (("title", title), ("app_id", app_id), ("state", state),
                       ("done", done), ("closed", closed)):
            handle.dispatcher[ev] = fn

    def _notify(self):
        for cb in list(self.listeners):
            cb()

    # -- requests --------------------------------------------------------------
    def _flush(self):
        self._display.flush()

    def activate(self, t: Toplevel) -> None:
        if t.minimized:
            t.handle.unset_minimized()
        if self._seat:
            t.handle.activate(self._seat)
        self._flush()

    def minimize(self, t: Toplevel) -> None:
        t.handle.set_minimized()
        self._flush()

    def set_maximized(self, t: Toplevel, on: bool) -> None:
        (t.handle.set_maximized if on else t.handle.unset_maximized)()
        self._flush()

    def unminimize(self, t: Toplevel) -> None:
        t.handle.unset_minimized()
        self._flush()

    def close(self, t: Toplevel) -> None:
        t.handle.close()
        self._flush()

    def set_rectangle(self, t: Toplevel, gdk_surface, x, y, w, h) -> None:
        """Where `t` minimizes to (the Dock icon), for the compositor's
        minimize animation. Coordinates are relative to `gdk_surface`."""
        from pywayland.protocol.wayland import WlSurface
        ptr = _gtk_fn("gdk_wayland_surface_get_wl_surface")(_gpointer(gdk_surface))
        if not ptr:
            return
        surf = WlSurface.proxy_class.__new__(WlSurface.proxy_class)   # borrowed, no gc/listener
        surf._ptr = self._ffi.cast("struct wl_proxy *", ptr)
        surf._display = self._display
        t.handle.set_rectangle(surf, int(x), int(y), int(w), int(h))
        self._flush()
