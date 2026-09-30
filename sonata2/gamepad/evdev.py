"""Game controllers read straight from /dev/input (no python-evdev needed).

The Linux gamepad layout (kernel docs, gamepad.rst): face buttons
BTN_SOUTH (A / cross) .. , shoulders BTN_TL/TR, sticks ABS_X/Y and
ABS_RX/RY, triggers ABS_Z/RZ (or ABS_BRAKE/GAS), d-pad ABS_HAT0X/Y or
BTN_DPAD_*. Controllers are readable by the logged-in user (logind's
"uaccess" for ID_INPUT_JOYSTICK devices). Devices are only listened to,
never grabbed: games keep receiving them.

    pads = find_gamepads()                  # ["/dev/input/event17", ...]
    pad = Gamepad(path, on_event)           # on_event(pad, type, code, value)
    pad.norm(ABS_X, raw)                    # -> -1.0 .. 1.0 (triggers 0 .. 1)"""
import fcntl
import os
import struct

from gi.repository import GLib

EV_KEY, EV_ABS = 0x01, 0x03
EVENT = struct.Struct("llHHi")          # struct input_event (64-bit)

# buttons
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
BTN_GAMEPAD = BTN_SOUTH
KEY_HOMEPAGE = 0xac     # the Xbox button over Bluetooth (a "Consumer Control" device of its own)
# axes
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y = 0x09, 0x0a, 0x10, 0x11
TRIGGERS = (ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE)


def _ioc_read(nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


def _bits(fd, ev_type: int, count: int) -> bytes:
    buf = bytearray((count + 7) // 8)
    try:
        fcntl.ioctl(fd, _ioc_read(0x20 + ev_type, len(buf)), buf)
    except OSError:
        pass
    return bytes(buf)


def _has(bits: bytes, code: int) -> bool:
    return code // 8 < len(bits) and bool(bits[code // 8] & (1 << (code % 8)))


def name_of(fd) -> str:
    buf = bytearray(256)
    try:
        n = fcntl.ioctl(fd, _ioc_read(0x06, len(buf)), buf)
        return bytes(buf[:n if isinstance(n, int) and n > 0 else len(buf)]).split(b"\0")[0].decode("utf-8", "replace")
    except OSError:
        return ""


def is_gamepad(path: str) -> bool:
    """A device with the gamepad's south button and two stick axes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        keys, axes = _bits(fd, EV_KEY, 0x300), _bits(fd, EV_ABS, 0x40)
        return _has(keys, BTN_GAMEPAD) and _has(axes, ABS_X) and _has(axes, ABS_Y)
    finally:
        os.close(fd)


def _events(folder):
    try:
        return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.startswith("event")]
    except OSError:
        return []


def find_gamepads(folder: str = "/dev/input") -> list:
    return [p for p in _events(folder) if is_gamepad(p)]


def find_guide_devices(pad_names, folder: str = "/dev/input") -> list:
    """Extra devices of a controller that carry its Xbox / home button: over
    Bluetooth, Xbox controllers send it from "<name> Consumer Control" as
    KEY_HOMEPAGE instead of BTN_MODE on the gamepad itself."""
    found = []
    for path in _events(folder):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            name = name_of(fd)
            if (name not in pad_names and any(name.startswith(p) for p in pad_names if p)
                    and _has(_bits(fd, EV_KEY, 0x300), KEY_HOMEPAGE)):
                found.append(path)
        finally:
            os.close(fd)
    return found


def gamepad_name(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        return name_of(fd)
    finally:
        os.close(fd)


class Gamepad:
    """One open controller; events come on the GLib main loop."""

    def __init__(self, path: str, on_event):
        self.path, self.on_event = path, on_event
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.name = name_of(self.fd)
        self.ranges = {}
        for code in (ABS_X, ABS_Y, ABS_RX, ABS_RY) + TRIGGERS + (ABS_HAT0X, ABS_HAT0Y):
            info = bytearray(24)                            # struct input_absinfo
            try:
                fcntl.ioctl(self.fd, _ioc_read(0x40 + code, 24), info)
                _value, lo, hi, _fuzz, flat, _res = struct.unpack("6i", info)
                if hi > lo:
                    self.ranges[code] = (lo, hi, flat)
            except OSError:
                continue
        self._watch = GLib.io_add_watch(self.fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                                        self._readable)

    def norm(self, code: int, raw: int) -> float:
        lo, hi, _flat = self.ranges.get(code, (-32768, 32767, 0))
        if code in TRIGGERS:
            return max(0.0, min(1.0, (raw - lo) / (hi - lo)))
        mid = (lo + hi) / 2
        return max(-1.0, min(1.0, (raw - mid) / ((hi - lo) / 2)))

    def _readable(self, _fd, cond) -> bool:
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self.close()
            return False
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except BlockingIOError:
            return True
        except OSError:                     # unplugged
            self.close()
            return False
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _us, typ, code, value = EVENT.unpack_from(data, i)
            if typ in (EV_KEY, EV_ABS):
                self.on_event(self, typ, code, value)
        return True

    def close(self) -> None:
        if self._watch:
            GLib.source_remove(self._watch) if GLib.MainContext.default().find_source_by_id(self._watch) else None
            self._watch = 0
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self.on_event(self, None, None, None)      # gone
