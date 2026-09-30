"""A virtual mouse (wlr-virtual-pointer-unstable-v1, which Wayfire offers)
and keys through `wtype` (virtual-keyboard). Its own Wayland connection:
only requests are sent, nothing has to be dispatched.

    vp = VirtualPointer()      # None-safe: vp.ok is False without the protocol
    vp.move(dx, dy); vp.button(BTN_LEFT, True); vp.scroll(dx, dy)
    key("Return")              # wtype -k Return"""
import importlib.util
import os
import shutil
import subprocess
import time

PROTO = "wlr_virtual_pointer_unstable_v1"
XML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wl", "protocols",
                   "wlr-virtual-pointer-unstable-v1.xml")
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112


def _load_protocol():
    import pywayland
    from pywayland.scanner import Protocol
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sonata2", "pywayland-" + pywayland.__version__)
    path = os.path.join(cache, PROTO + ".py")
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        proto = Protocol.parse_file(XML)
        proto.output(cache, {i.name: proto.name for i in proto.interface}
                     | {"wl_seat": "wayland", "wl_output": "wayland", "wl_pointer": "wayland"})
        with open(path, encoding="utf-8") as f:
            src = f.read()
        with open(path, "w", encoding="utf-8") as f:           # core interfaces from pywayland
            f.write(src.replace("from .wayland import", "from pywayland.protocol.wayland import"))
    spec = importlib.util.spec_from_file_location("sonata2_" + PROTO, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class VirtualPointer:
    def __init__(self):
        self.ok = False
        self.display = self.pointer = None
        try:
            from pywayland.client import Display
            from pywayland.protocol.wayland import WlSeat
            mod = _load_protocol()
            Manager = mod.ZwlrVirtualPointerManagerV1
            self.display = Display()
            self.display.connect()
            found = {}

            def global_(reg, name, iface, version):
                if iface == Manager.name:
                    found["mgr"] = reg.bind(name, Manager, min(version, 1))
                elif iface == "wl_seat" and "seat" not in found:
                    found["seat"] = reg.bind(name, WlSeat, 1)
            registry = self.display.get_registry()
            registry.dispatcher["global"] = global_
            self.display.roundtrip()
            if "mgr" not in found:
                self.display.disconnect()
                self.display = None
                return
            self.pointer = found["mgr"].create_virtual_pointer(found.get("seat"))
            self.display.roundtrip()
            self.ok = True
        except Exception as e:           # no Wayland, no pywayland, no protocol: no virtual mouse
            print(f"sonata2-gamepad: no virtual pointer ({e})")
            self.ok = False

    def _flush(self) -> None:
        try:
            self.display.flush()
        except Exception:
            self.ok = False

    def move(self, dx: float, dy: float) -> None:
        if self.ok and (dx or dy):
            self.pointer.motion(_ms(), dx, dy)
            self.pointer.frame()
            self._flush()

    def button(self, button: int, pressed: bool) -> None:
        if self.ok:
            self.pointer.button(_ms(), button, 1 if pressed else 0)
            self.pointer.frame()
            self._flush()

    def scroll(self, dx: float, dy: float) -> None:
        """Smooth scrolling in pixels (positive dy: down)."""
        if not self.ok or not (dx or dy):
            return
        t = _ms()
        self.pointer.axis_source(2)                  # continuous
        if dy:
            self.pointer.axis(t, 0, dy)               # vertical
        if dx:
            self.pointer.axis(t, 1, dx)               # horizontal
        self.pointer.frame()
        self._flush()

    def close(self) -> None:
        if self.display is not None:
            try:
                if self.pointer is not None:
                    self.pointer.destroy()
                    self.pointer = None
                self.display.roundtrip()
                self.display.disconnect()
            except Exception:
                pass
            self.display = None
        self.ok = False


def key(name: str, mods=()) -> None:
    """Press a key (xkb name: Return, Escape, Up...) with modifiers held."""
    if not shutil.which("wtype"):
        return
    args = ["wtype"]
    for m in mods:
        args += ["-M", m]
    args += ["-k", name]
    for m in reversed(mods):
        args += ["-m", m]
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
