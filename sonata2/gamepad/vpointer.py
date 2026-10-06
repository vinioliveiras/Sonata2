"""A virtual mouse (wlr-virtual-pointer-unstable-v1, which Wayfire offers)
and keys through `wtype` (virtual-keyboard). Its own Wayland connection:
only requests are sent, nothing has to be dispatched.

    vp = VirtualPointer()      # None-safe: vp.ok is False without the protocol
    vp.move(dx, dy); vp.button(BTN_LEFT, True); vp.scroll(dx, dy)
    key("Return")              # wtype -k Return"""
import shutil
import subprocess
import sys
import time

PROTO = "wlr_virtual_pointer_unstable_v1"
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112


def _load_protocol():
    from ..wl import scan
    return scan.load("wlr-virtual-pointer-unstable-v1.xml", PROTO)


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
            print(f"sonata2-gamepad: no virtual pointer ({e})", file=sys.stderr, flush=True)
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
    """Press a key (xkb name: Return, Escape, Up...) with modifiers held:
    with its real keycode (wl/vkeyboard); wtype only for a key it doesn't know."""
    from ..wl import vkeyboard
    if name in vkeyboard.CODES and vkeyboard.press(name, tuple(mods)):
        return
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
