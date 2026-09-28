"""Dock logic tests on a real (virtual) display: drag-reorder, drag-out,
Keep in Dock. Run: xvfb-run python3 -m unittest tests.test_dock
Uses a temporary XDG_CONFIG_HOME and the apps installed on the machine."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config, icons  # noqa: E402
from sonata2.shell import dock as D, dock_menu  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class DockTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        icons.setup()
        self.cfg = D.load_config()
        D.load_css(self.cfg)
        if len(self.cfg["pinned"]) < 3:
            self.skipTest("needs at least 3 installed default apps")
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle()

    def tearDown(self):
        self.win.destroy()

    def keys(self):
        return [t.key for t in self.dock.app_tiles()]

    def center_x(self, key):
        ok, b = self.dock.tiles[key].compute_bounds(self.dock)
        return b.get_x() + b.get_width() / 2

    def start_drag(self, key):
        self.dock._drag = {"key": key, "index": self.keys().index(key), "left": False, "dropped": False}

    def test_reorder_first_to_last(self):
        first, last = self.keys()[0], self.keys()[-1]
        self.start_drag(first)
        self.dock._drag_motion(None, self.center_x(last) + 5, 10)
        self.assertEqual(self.keys()[-1], first)
        self.dock._drag_drop(None, first, 0, 0)
        self.assertEqual(config.load("dock", D.DEFAULTS)["pinned"][-1], first)

    def test_esc_restores_order(self):
        before = self.keys()
        self.start_drag(before[0])
        self.dock._drag_motion(None, self.center_x(before[2]) + 5, 10)
        self.dock._drag_cancel(None, None, Gdk.DragCancelReason.USER_CANCELLED,
                               self.dock.tiles[before[0]])
        self.assertEqual(self.keys(), before)

    def test_drag_out_removes(self):
        key = self.keys()[1]
        self.start_drag(key)
        self.dock._drag_leave(None)
        self.dock._drag_cancel(None, None, Gdk.DragCancelReason.NO_TARGET, self.dock.tiles[key])
        self.assertNotIn(key, self.keys())
        self.assertNotIn(key, config.load("dock", D.DEFAULTS)["pinned"])

    def test_menus_build(self):
        key = self.keys()[0]
        pop = dock_menu.app_menu(self.dock, key, self.dock.tiles[key])
        self.assertIsNotNone(pop.get_menu_model())
        pop.popdown()
        dock_menu.trash_menu(self.dock.trash).popdown()

    def test_app_file_is_real_path(self):
        path = dock_menu.app_file(self.dock.tiles[self.keys()[0]].info)
        self.assertTrue(os.path.isabs(path) and os.path.exists(path), path)

    def test_keep_in_dock_toggle(self):
        key = self.keys()[0]
        self.dock.set_pinned(key, False)
        self.assertNotIn(key, self.keys())


if __name__ == "__main__":
    unittest.main()
