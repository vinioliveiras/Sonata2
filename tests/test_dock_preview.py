"""Dock window previews: Wayfire IPC client + WindowShots against a fake
Wayfire socket and a fake grim (no compositor needed)."""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

os.environ.setdefault("GDK_BACKEND", "x11")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

VIEW = {"id": 7, "app-id": "org.gnome.TextEditor", "title": "notes.txt", "type": "toplevel",
        "minimized": False, "activated": True, "geometry": {"x": 10, "y": 20, "width": 800, "height": 600}}


def _send(c, obj):
    b = json.dumps(obj).encode()
    c.sendall(struct.pack("<I", len(b)) + b)


def _recv(c):
    n = struct.unpack("<I", c.recv(4))[0]
    return json.loads(c.recv(n))


class FakeWayfire(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(8)
        self.watcher = None
        self.calls = []

    def run(self):
        while True:
            c, _ = self.srv.accept()
            msg = _recv(c)
            self.calls.append(msg["method"])
            if msg["method"] == "window-rules/events/watch":
                _send(c, {"result": "ok"})
                self.watcher = c
            elif msg["method"] == "window-rules/view-info":
                _send(c, {"result": "ok", "info": VIEW})
            else:
                _send(c, {"result": "ok"})


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class PreviewTest(unittest.TestCase):
    def test_capture_and_minimized(self):
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "wf.sock")
        fake = FakeWayfire(sock)
        fake.start()
        png = os.path.join(tmp, "shot.png")             # what the fake grim prints
        from gi.repository import GdkPixbuf
        GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 4, 3).savev(png, "png", [], [])
        grim = os.path.join(tmp, "grim")
        with open(grim, "w") as f:
            f.write(f"#!/bin/sh\ncat '{png}'\n")
        os.chmod(grim, 0o755)
        os.environ["WAYFIRE_SOCKET"] = sock
        os.environ["PATH"] = tmp + os.pathsep + os.environ["PATH"]
        from sonata2.shell import dock_preview as P
        from sonata2.shell import dock_preview
        dock_preview.CAPTURE_DELAY_MS = 50
        s = P.WindowShots()
        self.assertTrue(s.enabled)
        settle(100)
        _send(fake.watcher, {"event": "view-focused", "view": VIEW})
        settle(800)
        self.assertIsNotNone(s.shots[7]["tex"])
        _send(fake.watcher, {"event": "view-minimized", "view": dict(VIEW, minimized=True)})
        settle(200)
        key = P.apps.match_app_id(VIEW["app-id"]) or VIEW["app-id"]
        self.assertEqual([v for v, _t, _n in s.minimized_for(key)], [7])
        _send(fake.watcher, {"event": "view-unmapped", "view": VIEW})
        settle(200)
        self.assertNotIn(7, s.shots)


if __name__ == "__main__":
    unittest.main()
