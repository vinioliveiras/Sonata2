"""Wayfire IPC (plugins `ipc` + `ipc-rules`): the socket in $WAYFIRE_SOCKET,
messages framed as a 4-byte little-endian length + JSON. Used for what the
Wayland protocols don't give a client: window geometry and focus/minimize
events (Dock window previews).

    ipc = WayfireIPC()                         # .available False without Wayfire
    ipc.call("window-rules/list-views")       # -> list of views (dicts)
    ipc.watch(["view-focused"], on_event)     # on_event(dict) on the GTK main loop
"""
import json
import os
import socket
import struct

from gi.repository import GLib


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Wayfire closed the IPC socket")
        buf += chunk
    return buf


def _send(sock, method: str, data=None) -> None:
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)


def _read(sock):
    (n,) = struct.unpack("<I", _recv_exact(sock, 4))
    return json.loads(_recv_exact(sock, n))


class WayfireIPC:
    def __init__(self):
        self.path = os.environ.get("WAYFIRE_SOCKET", "")
        self._watch_sock = None
        self._src = 0

    @property
    def available(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(self.path)
        return s

    def call(self, method: str, data=None):
        """One request on its own connection (blocking, short); None on error."""
        if not self.available:
            return None
        try:
            with self._connect() as s:
                _send(s, method, data)
                return _read(s)
        except (OSError, ValueError, ConnectionError, struct.error):
            return None

    def watch(self, events, callback) -> bool:
        """Deliver the given events (dicts with "event") to callback."""
        if not self.available:
            return False
        try:
            s = self._connect()
            _send(s, "window-rules/events/watch", {"events": list(events)})
            _read(s)                                   # {"result": "ok"}
            s.settimeout(None)
        except (OSError, ValueError, ConnectionError, struct.error):
            return False
        self._watch_sock = s

        def readable(_fd, _cond):
            try:
                msg = _read(s)
            except (OSError, ValueError, ConnectionError, struct.error):
                return False                           # Wayfire went away
            if isinstance(msg, dict) and "event" in msg:
                callback(msg)
            return True
        self._src = GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, readable)
        return True
