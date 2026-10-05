"""Idle from the keyboard and mouse only, and the displays on / off -- a tiny
Wayland client of its own (no bindings to install), run on the GLib loop.

    w = IdleWatch()                                   # None-safe: w.ok False without a compositor
    w.watch(120, on_idle, on_back)                    # 120 s without input
    w.displays(False)                                 # every output off (and True: back on)

Why (Vini: the session never locked; Chrome kept it awake for hours): the
usual idle notification waits while any app asks to keep the session awake
(idle inhibitors). ext-idle-notify-v1 version 2 has an *input* idle
notification that ignores them -- Sonata decides what may keep it awake
(power.py: only media playing). Displays go off with
wlr-output-power-management (what wlopm uses), so no app keeps them lit.

The wire protocol is small: a message is the object id, then the size
(high 16 bits) and opcode (low 16 bits), then the arguments -- uints, and
strings as their length (with the NUL) and the bytes padded to 4."""
import os
import socket
import struct

from gi.repository import GLib

NOTIFIER = "ext_idle_notifier_v1"
POWER = "zwlr_output_power_manager_v1"


def _string(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)


class IdleWatch:
    def __init__(self, display: str = None):
        self.ok = False
        self.input_idle = False            # the compositor has get_input_idle_notification (v2)
        self._next = 2                     # 1 is wl_display
        self._handlers = {}                # object id -> fn(opcode, payload)
        self._buf = b""
        self.globals = {}                  # interface -> [(name, version)]
        self.seat = self.notifier = self.power = None
        self.outputs = []                  # output object ids
        self._powers = {}                  # output id -> output power object id
        self._watches = {}                 # notification id -> (on_idle, on_back)
        name = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = name if name.startswith("/") else os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), name)
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(path)
        except OSError:
            self.sock = None
            return
        registry = self._new(self._on_registry)
        self._send(1, 1, struct.pack("=I", registry))                 # wl_display.get_registry
        self._registry = registry
        self._handlers[1] = self._on_display
        self._roundtrip()
        seats = self.globals.get("wl_seat")
        notif = self.globals.get(NOTIFIER)
        if not seats or not notif:
            return
        self.seat = self._bind(seats[0], "wl_seat", 1, lambda *_a: None)
        nver = min(2, notif[0][1])
        self.notifier = self._bind(notif[0], NOTIFIER, nver, lambda *_a: None)
        self.input_idle = nver >= 2
        if self.globals.get(POWER):
            self.power = self._bind(self.globals[POWER][0], POWER, 1, lambda *_a: None)
        for g in self.globals.get("wl_output", []):
            self.outputs.append(self._bind(g, "wl_output", 1, lambda *_a: None))
        self._roundtrip()
        self.ok = True
        GLib.io_add_watch(self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP,
                          self._readable)

    # -- wire -----------------------------------------------------------------------------------
    def _new(self, handler) -> int:
        oid = self._next
        self._next += 1
        self._handlers[oid] = handler
        return oid

    def _send(self, oid: int, opcode: int, args: bytes = b"") -> None:
        self.sock.sendall(struct.pack("=II", oid, (8 + len(args)) << 16 | opcode) + args)

    def _bind(self, glob, interface: str, version: int, handler) -> int:
        oid = self._new(handler)
        self._send(self._registry, 0, struct.pack("=I", glob[0]) + _string(interface) +
                   struct.pack("=II", version, oid))                 # wl_registry.bind (untyped new_id)
        return oid

    def _dispatch(self) -> None:
        while len(self._buf) >= 8:
            oid, word = struct.unpack_from("=II", self._buf)
            size = word >> 16
            if len(self._buf) < size:
                break
            payload, self._buf = self._buf[8:size], self._buf[size:]
            h = self._handlers.get(oid)
            if h is not None:
                h(word & 0xFFFF, payload)

    def _read(self, block: bool) -> bool:
        self.sock.setblocking(block)
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not data:
            return False
        self._buf += data
        self._dispatch()
        return True

    def _roundtrip(self) -> None:
        done = []
        cb = self._new(lambda *_a: done.append(1))
        self._send(1, 0, struct.pack("=I", cb))                      # wl_display.sync
        while not done:
            if not self._read(True):
                break

    def _readable(self, *_a) -> bool:
        if not self._read(False):
            self.ok = False
            return False
        return True

    # -- events ---------------------------------------------------------------------------------
    def _on_display(self, opcode, payload) -> None:
        if opcode == 1:                                              # delete_id
            self._handlers.pop(struct.unpack_from("=I", payload)[0], None)

    def _on_registry(self, opcode, payload) -> None:
        if opcode != 0:
            return
        name, n = struct.unpack_from("=II", payload)
        interface = payload[8:8 + n - 1].decode(errors="replace")
        version = struct.unpack_from("=I", payload, 8 + n + (-n % 4))[0]
        self.globals.setdefault(interface, []).append((name, version))

    # -- what Sonata uses ---------------------------------------------------------------------------
    def watch(self, seconds: float, on_idle, on_back) -> int:
        """on_idle() after `seconds` without input (app inhibitors ignored
        when the compositor can), on_back() at the next input. Returns an id
        for unwatch()."""
        if not self.ok:
            return 0

        def event(opcode, _payload, on_idle=on_idle, on_back=on_back):
            (on_idle if opcode == 0 else on_back)()
        oid = self._new(event)
        self._send(self.notifier, 2 if self.input_idle else 1,
                   struct.pack("=III", oid, int(seconds * 1000), self.seat))
        self._watches[oid] = (on_idle, on_back)
        return oid

    def unwatch(self, oid: int) -> None:
        if self.ok and oid in self._watches:
            self._send(oid, 0)                                       # ext_idle_notification_v1.destroy
            del self._watches[oid]

    def can_power(self) -> bool:
        return self.ok and self.power is not None and bool(self.outputs)

    def displays(self, on: bool) -> bool:
        """Every display on / off (wlr-output-power-management)."""
        if not self.can_power():
            return False
        for out in self.outputs:
            p = self._powers.get(out)
            if p is None:
                p = self._powers[out] = self._new(lambda *_a: None)
                self._send(self.power, 0, struct.pack("=II", p, out))  # get_output_power
            self._send(p, 0, struct.pack("=I", 1 if on else 0))         # set_mode
        return True

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.ok = False
