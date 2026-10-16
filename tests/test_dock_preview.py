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
        self.shots = True

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
            elif msg["method"] == "window-rules/list-views":
                _send(c, VIEWS)
            elif msg["method"] == "sonata/window-shot":
                if not self.shots:
                    _send(c, {"result": "error", "error": "No such method found!"})
                    continue
                d = msg["data"]
                with open(d["path"], "wb") as f:
                    f.write(bytes([255, 0, 0, 255]) * (4 * 2))       # 4 x 2, opaque red
                _send(c, {"result": "ok", "width": 4, "height": 2})
            else:
                _send(c, {"result": "ok"})


VIEWS = [dict(VIEW, **{"last-focus-timestamp": 5, "mapped": True, "role": "toplevel"}),
         dict(VIEW, **{"id": 8, "title": "todo.txt", "minimized": True, "last-focus-timestamp": 9,
                       "mapped": True, "role": "toplevel"}),
         {"id": 9, "app-id": "other.App", "type": "toplevel", "mapped": True, "title": "x"},
         {"id": 10, "app-id": "sonata2-dock", "type": "panel", "mapped": True, "title": ""}]


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
        key = P.apps.match_app_id(VIEW["app-id"]) or VIEW["app-id"]
        self.assertEqual([v for v, _t, _n in s.windows_for(key)], [7])       # open windows too
        _send(fake.watcher, {"event": "view-minimized", "view": dict(VIEW, minimized=True)})
        settle(200)
        self.assertEqual([v for v, _t, _n in s.windows_for(key)], [7])
        _send(fake.watcher, {"event": "view-unmapped", "view": VIEW})
        settle(200)
        self.assertNotIn(7, s.shots)


class CompositorShotTest(unittest.TestCase):
    """Vini: hovering an app shows every window it has open, live -- drawn by
    Sonata's Wayfire plugin (sonata/window-shot), not a screen grab."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.sock = os.path.join(tmp, "wf.sock")
        self.fake = FakeWayfire(self.sock)
        self.fake.start()
        os.environ["WAYFIRE_SOCKET"] = self.sock
        os.environ["XDG_RUNTIME_DIR"] = tmp

    def test_every_open_window_last_used_first(self):
        from sonata2.shell import dock_preview as P
        s = P.WindowShots()
        key = P.apps.match_app_id(VIEW["app-id"]) or VIEW["app-id"]
        wins = s.open_windows(key)
        self.assertEqual([w["id"] for w in wins], [8, 7])              # the other app and the Dock left out
        self.assertTrue(wins[0]["minimized"])

    def test_picture_from_the_compositor(self):
        from sonata2.shell import dock_preview as P
        s = P.WindowShots()
        got = []
        s.snapshot(7, lambda vid, tex: got.append((vid, tex.get_width(), tex.get_height())))
        settle(600)
        self.assertEqual(got, [(7, 4, 2)])
        self.assertTrue(s.compositor)
        self.assertEqual([n for n in os.listdir(os.environ["XDG_RUNTIME_DIR"]) if n.startswith("sonata2-shot")], [])

    def test_older_plugin_falls_back(self):
        from sonata2.shell import dock_preview as P
        self.fake.shots = False
        s = P.WindowShots()
        s.snapshot(7, lambda *a: self.fail("no picture expected"))
        settle(500)
        self.assertIs(s.compositor, False)


if __name__ == "__main__":
    unittest.main()
