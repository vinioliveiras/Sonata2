"""Window buttons with a bigger target (Vini: the dots were hard to hit).
The dots look and sit where they did; only the clickable area grows: half
the gap to each neighbour (no dead space between them), up to the window's
top edge, out to its side edge for the close dot, half a gap below.
Run: xvfb-run -a python3 -m unittest tests.test_traffic_hit_area"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import tokens, window as W  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class HitAreaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def _window(self, side="left", close_only=False):
        with mock.patch.object(W, "buttons_side", return_value=side):
            self.calls = []
            lights = (W.close_button(lambda: self.calls.append("close")) if close_only else
                      W.traffic_lights(lambda: self.calls.append("close"), lambda: self.calls.append("min"),
                                       lambda: self.calls.append("zoom")))
        win = Gtk.Window(decorated=False, default_width=300, default_height=120)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        box.append(lights)
        win.set_child(box)
        win.present()
        settle()
        self.addCleanup(win.destroy)
        return win, lights

    def _dot(self, win, b):
        ok, r = b.compute_bounds(win)
        self.assertTrue(ok)
        return r.get_x(), r.get_y(), r.get_width(), r.get_height()

    def test_dots_dont_move(self):
        f = tokens.frame()
        win, lights = self._window()
        x, y, w, h = self._dot(win, lights.get_first_child())
        self.assertAlmostEqual(x + w / 2, f["dot_left"], delta=1)
        self.assertAlmostEqual(y + h / 2, f["dot_top"], delta=1)
        self.assertEqual(w, f["dot"])

    def test_top_left_corner_hits_close(self):
        """Fitts: the window's very corner (the screen's, maximized) is the close dot."""
        win, lights = self._window()
        close = lights.get_first_child()
        self.assertIs(win.pick(0.5, 0.5, Gtk.PickFlags.DEFAULT), close)

    def test_no_dead_gap_between_dots(self):
        win, lights = self._window()
        close = lights.get_first_child()
        mini = close.get_next_sibling()
        cx, cy, cw, ch = self._dot(win, close)
        mx, _my, _mw, _mh = self._dot(win, mini)
        mid = (cx + cw + mx) / 2
        self.assertIs(win.pick(mid - 0.5, cy + ch / 2, Gtk.PickFlags.DEFAULT), close)
        self.assertIs(win.pick(mid + 0.5, cy + ch / 2, Gtk.PickFlags.DEFAULT), mini)
        self.assertIs(win.pick(mx + 1, 0.5, Gtk.PickFlags.DEFAULT), mini)       # up to the top edge

    def test_not_far_below_or_beyond(self):
        win, lights = self._window()
        zoom = lights.get_last_child()
        zx, zy, zw, zh = self._dot(win, zoom)
        r = W._reach()
        below = win.pick(zx + 2, zy + zh + r["below"] + 2, Gtk.PickFlags.DEFAULT)
        beyond = win.pick(zx + zw + r["gap"] + 2, zy + 2, Gtk.PickFlags.DEFAULT)
        for w in (below, beyond):
            self.assertFalse(w is not None and (w is zoom or w.is_ancestor(zoom)))

    def test_right_side_reaches_right_edge(self):
        win, lights = self._window("right")
        lights.set_halign(Gtk.Align.END)
        settle()
        close = lights.get_last_child()
        self.assertIn("tl-close", close.get_css_classes())
        self.assertIs(win.pick(win.get_width() - 0.5, 0.5, Gtk.PickFlags.DEFAULT), close)

    def test_dialog_close_dot_too(self):
        win, lights = self._window(close_only=True)
        self.assertIs(win.pick(0.5, 0.5, Gtk.PickFlags.DEFAULT), lights.get_first_child())

    def test_reach_follows_button_size(self):
        W._forget_reach()
        small = dict(W._reach())
        with mock.patch.object(tokens, "user_button_size", return_value=16):
            W._forget_reach()
            big = W._reach()
            self.assertGreater(big["gap"], small["gap"])
        W._forget_reach()


if __name__ == "__main__":
    unittest.main()
