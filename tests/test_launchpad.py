"""Launchpad UI tests on a virtual display (xvfb-run python3 -m unittest tests.test_launchpad).
Needs at least 3 installed apps; uses a temporary XDG_CONFIG_HOME."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2 import launchpad_model as M  # noqa: E402
from sonata2.shell import launchpad as L  # noqa: E402


def settle(ms=250):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class LaunchpadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.win = L.Launchpad(None)
        if len(self.win.model.all_apps()) < 3:
            self.skipTest("needs 3 installed apps")
        self.win.set_default_size(1280, 800)
        self.win.open_launchpad()
        settle(600)
        settle(600)                              # the grid fits the window (may re-layout once)
        self.grid = self.win.carousel.get_nth_page(0)

    def tearDown(self):
        self.win.destroy()

    def centre(self, index):
        w, h = self.grid.get_width(), self.grid.get_height()
        return ((index % M.COLS) + 0.5) * w / M.COLS, ((index // M.COLS) + 0.5) * h / M.ROWS

    def drag(self, item):
        self.win._drag = {"item": item, "widget": self.win._item_widget(item), "folder": None, "target": None}

    def test_reorder(self):
        first = self.win.model.pages[0][0]
        self.drag(first)
        x, y = self.centre(2)
        self.win.drag_over(self.grid, x + self.grid.get_width() / M.COLS * 0.4, y)   # beside the icon
        self.assertEqual(self.win.model.pages[0].index(first), 0)     # icons wait a moment...
        self.win._reorder_to(0, 2)                                     # ...then make way (REORDER_HOLD_MS)
        self.assertEqual(self.win.model.pages[0].index(first), 2)

    def test_edge_flips_page(self):
        if self.win.carousel.get_n_pages() < 2:
            self.skipTest("needs two pages")
        self.drag(self.win.model.pages[0][0])
        x = self.win.get_width() - 5                            # held at the right side
        self.win._edge_x = x
        self.win._edge_flip(x)
        settle(L.FLIP_HOLD_MS + 700)
        self.win._edge_x = None
        self.assertGreater(self.win.carousel.get_position(), 0.5)

    def test_make_folder(self):
        a, b = self.win.model.pages[0][0], self.win.model.pages[0][1]
        self.drag(b)
        self.win.drag_over(self.grid, *self.centre(0))
        settle(L.FOLDER_HOLD_MS + 500)          # slow software rendering in CI
        self.assertTrue(self.win._drag["target"].has_css_class("folder-target"))
        self.win.drag_drop(self.grid, *self.centre(0))
        item = self.win.model.pages[0][0]
        self.assertTrue(M.is_folder(item))
        self.assertEqual(item["apps"], [a, b])

    def test_search_and_escape(self):
        name = self.win.installed[self.win.model.all_apps()[0]].get_display_name()
        self.win.search.set_text(name[:3])
        settle(800)
        self.assertEqual(self.win.stack.get_visible_child_name(), "results")
        self.assertGreaterEqual(self.win.selected, 0)
        from gi.repository import Gdk
        self.win._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(self.win.search.get_text(), "")

    def test_toggle_hides(self):
        self.win.close_launchpad()
        settle(L.CLOSE_MS + 200)
        self.assertFalse(self.win.get_visible())


if __name__ == "__main__":
    unittest.main()
