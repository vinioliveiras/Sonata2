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
from unittest import mock

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


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class SmallPreviewsTest(unittest.TestCase):
    """Vini: the preview showed a big empty frame -- each window a small
    picture of itself; and a window can be closed from its preview."""

    def popover(self, close=None):
        import types
        from gi.repository import Gtk
        from sonata2.shell import dock_preview as P
        tile = Gtk.Button()
        tile.name, tile.gicon = "Files", None
        win = Gtk.Window(child=tile)
        win.present()
        self.addCleanup(win.destroy)
        wins = [{"id": 7, "title": "Downloads", "minimized": False},
                {"id": 8, "title": "Music", "minimized": True}]
        restore = mock.Mock()
        return P, P.PreviewPopover(tile, Gtk.PositionType.TOP, wins, restore, close), restore, types

    def test_small_and_in_the_window_s_shape(self):
        from gi.repository import Gdk, GLib
        P, pop, _r, _t = self.popover()
        pic = pop.pics[7]
        self.assertEqual(pic.size, (32, 32))                      # the icon, small: no empty frame
        self.assertFalse(pic.has_css_class("shot-pic"))
        raw = bytes(4 * 1600 * 900)
        tex = Gdk.MemoryTexture.new(1600, 900, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(raw), 1600 * 4)
        pop.set_shot(7, tex)
        w, h = pic.size
        self.assertLessEqual(w, P.PIC_W)
        self.assertLessEqual(h, P.PIC_H)
        self.assertAlmostEqual(w / h, 16 / 9, delta=0.05)                         # its own shape
        from sonata2.shell import spaces
        self.assertEqual((P.PIC_W, P.PIC_H), (spaces.THUMB_W, spaces.THUMB_H))   # as the desktops' pictures
        pop.set_shot(7, None)
        self.assertIs(pic.get_paintable(), tex)                                    # a failed refresh keeps it

    def test_the_preview_is_as_small_as_its_pictures(self):
        """Vini: "a very big frame" -- a whole window's picture made the
        preview ~1000 px wide (its natural size won over the request)."""
        from gi.repository import Gdk, GLib, Gtk
        P, pop, _r, _t = self.popover()
        raw = bytes(4 * 1800 * 1120)
        for vid in (7, 8):
            pop.set_shot(vid, Gdk.MemoryTexture.new(1800, 1120, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(raw), 1800 * 4))
        nat = pop.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        self.assertLess(nat, 2 * P.PIC_W + 80)                                  # two pictures and their frames
        self.assertEqual(pop.pics[7].measure(Gtk.Orientation.HORIZONTAL, -1)[1], P.PIC_W)

    def test_close_from_the_preview(self):
        close = mock.Mock()
        P, pop, restore, _t = self.popover(close)
        self.assertEqual(len(pop.items), 2)
        pop._close(7)
        close.assert_called_once_with(7)
        restore.assert_not_called()
        self.assertNotIn(7, pop.items)
        self.assertEqual(len(pop.items), 1)
        with mock.patch.object(pop, "popdown") as down:
            pop._close(8)
        down.assert_called_once()                                                  # the last one: the preview goes

    def test_close_asks_wayfire(self):
        from sonata2.shell import dock_preview as P
        fake = mock.Mock()
        with mock.patch.object(P, "shots", return_value=mock.Mock(ipc=fake)):
            P.close_window(42)
        fake.call.assert_called_once_with("window-rules/close-view", {"id": 42})


class HoverRulesTest(unittest.TestCase):
    """Vini: previews only after 1 s on the icon, only for an app with more
    than one window, and as far from the Dock as a maximized window."""

    def test_rules_in_the_code(self):
        from sonata2.shell import dock_preview as P
        src = open(P.__file__).read()
        self.assertEqual(P.HOVER_MS, 1000)
        self.assertIn("state[\"wait\"] = GLib.timeout_add(HOVER_MS, due)", src)
        self.assertIn("if not found:", src)                                       # one window too (Vini)
        self.assertIn("gap_from_dock(pop, tile, dock)", src)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
    def test_hover_shows_again_after_a_window_was_picked(self):
        """Vini: "sometimes the hover doesn't show". Picking a window closed
        the previews by themselves, they were still taken as open, and that
        icon never showed them again."""
        import types
        from gi.repository import GObject, Gtk
        from sonata2.shell import dock_preview as P

        class Pop(GObject.Object):
            __gsignals__ = {"closed": (GObject.SignalFlags.RUN_FIRST, None, ())}
            made = []

            def __init__(self, *_a):
                super().__init__()
                self.pics = {}
                Pop.made.append(self)

            def add_controller(self, _c):
                pass

            def popup(self):
                pass

            def popdown(self):
                self.emit("closed")

        added = []
        tile = types.SimpleNamespace(key="files", label=mock.Mock(), add_controller=added.append)
        dock = types.SimpleNamespace(windows={"files": [1]}, away=None)
        shots = mock.Mock(enabled=True)
        shots.open_windows.return_value = [{"id": 1, "title": "a", "minimized": False, "tex": None}]
        now = lambda ms, fn, *a: (fn(*a) if ms == P.HOVER_MS else None) or 1      # noqa: E731
        with mock.patch.object(P, "PreviewPopover", Pop), mock.patch.object(P, "gap_from_dock"), \
                mock.patch.object(P, "shots", return_value=shots), \
                mock.patch.object(P.GLib, "timeout_add", side_effect=now), \
                mock.patch.object(P.GLib, "source_remove"), mock.patch.object(P.GLib, "idle_add"):
            P.attach(tile, dock)
            motion = added[0]
            self.assertIsInstance(motion, Gtk.EventControllerMotion)
            motion.emit("enter", 1.0, 1.0)
            self.assertEqual(len(Pop.made), 1)
            Pop.made[0].popdown()                                     # a window picked
            motion.emit("leave")
            motion.emit("enter", 1.0, 1.0)
            self.assertEqual(len(Pop.made), 2)                        # shown again

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
    def test_gap_is_the_maximized_windows(self):
        """Pointing at edge_gap past the Dock's plate (SHADOW in from its window)."""
        import types
        from gi.repository import Gtk
        from sonata2.shell import dock_preview as P
        from sonata2.shell.dock import SHADOW
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        spacer = Gtk.Box()
        spacer.set_size_request(60, 20)
        tile = Gtk.Button(label="x")
        tile.set_size_request(60, 60)
        box.append(spacer)
        box.append(tile)
        win = Gtk.Window(child=box)
        win.present()
        self.addCleanup(win.destroy)
        from gi.repository import GLib
        ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 400_000
        while GLib.get_monotonic_time() < end:
            ctx.iteration(False)
        pop = mock.Mock()
        P.gap_from_dock(pop, tile, types.SimpleNamespace(edge="bottom", cfg={"edge_gap": 4}))
        rect = pop.set_pointing_to.call_args[0][0]
        from gi.repository import Graphene
        _ok, pt = tile.compute_point(win, Graphene.Point().init(0, 0))
        self.assertGreater(pt.y, 19)                                            # (below the spacer)
        self.assertEqual(rect.y, round(SHADOW - pt.y) - 4)                      # edge_gap above the plate
