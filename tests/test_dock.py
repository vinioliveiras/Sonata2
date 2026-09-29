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

from sonata2 import config  # noqa: E402
from sonata2.shell import dock as D, dock_menu  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class DockTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
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

    def removable(self):
        return [k for k in self.keys() if k not in D.PERMANENT]

    def center_x(self, key):
        ok, b = self.dock.tiles[key].compute_bounds(self.dock)
        return b.get_x() + b.get_width() / 2

    def start_drag(self, key):
        self.dock._drag = {"key": key, "index": self.keys().index(key), "left": False, "dropped": False}

    def test_drag_out_closes_up(self):
        key = self.removable()[0]
        self.start_drag(key)
        self.dock._drag_leave(None)
        self.assertFalse(self.dock.tiles[key].get_visible())      # the others close up
        self.dock._drag_motion(None, self.center_x(self.keys()[0]), 10)
        self.assertTrue(self.dock.tiles[key].get_visible())       # back over the Dock

    def test_drop_gap(self):
        keys = self.keys()
        x = (self.center_x(keys[0]) + self.center_x(keys[1])) / 2      # between the first two
        self.dock.show_drop_gap(x, 10)
        settle(100)
        self.assertEqual(self.dock._gap.slot, 1)
        self.assertIs(self.dock._gap.get_prev_sibling(), self.dock.tiles[keys[0]])
        self.dock._relayout()                                   # a window opening meanwhile
        self.assertIs(self.dock._gap.get_prev_sibling(), self.dock.tiles[keys[0]])
        self.assertEqual(self.dock.hide_drop_gap(), 1)
        self.assertIsNone(self.dock._gap.get_parent())
        self.assertEqual(self.keys(), keys)

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
        key = self.removable()[1]
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

    def test_pin_at_position(self):
        key = self.removable()[0]
        self.dock.set_pinned(key, False)
        self.dock.pin_at(key, before=self.dock.tiles[self.keys()[1]])
        self.assertEqual(self.keys().index(key), 1)
        self.assertIn(key, config.load("dock", D.DEFAULTS)["pinned"])

    def test_can_open_by_mime(self):
        from gi.repository import Gio
        from sonata2.shell import dock_drop
        path = os.path.join(os.environ["XDG_CONFIG_HOME"], "a.txt")
        with open(path, "w") as fh:
            fh.write("hi")
        f = Gio.File.new_for_path(path)
        info = self.dock.tiles[self.keys()[0]].info
        expect = any(Gio.content_type_is_a("text/plain", t) for t in (info.get_supported_types() or []))
        self.assertEqual(dock_drop.can_open(info, [f]), expect)

    def test_resize_saves(self):
        self.dock.set_icon_size(64)
        self.assertEqual(config.load("dock", D.DEFAULTS)["icon_size"], 64)
        self.assertEqual(self.dock.tiles[self.keys()[0]].icon._size, 64)
        self.dock.set_icon_size(500)
        self.assertEqual(self.cfg["icon_size"], D.MAX_SIZE)

    def test_magnification_wave(self):
        self.dock.set_magnification(True, 80)
        tiles = self.dock.all_tiles()
        ok, b = tiles[1].compute_bounds(self.win)
        self.dock._mag_pos, self.dock._mag_strength = b.get_x() + b.get_width() / 2, 1.0
        self.dock._apply_magnification()
        sizes = [t.icon._size for t in tiles]
        self.assertEqual(max(sizes), sizes[1])          # biggest under the pointer
        self.assertGreater(sizes[1], sizes[0])
        self.assertGreater(sizes[0], self.cfg["icon_size"] - 1)
        self.assertEqual(sizes[-1], self.cfg["icon_size"])   # far away: unchanged
        self.dock._mag_strength = 0.0
        self.dock._apply_magnification()
        self.assertTrue(all(t.icon._size == self.cfg["icon_size"] for t in tiles))

    def test_side_positions(self):
        for edge in ("left", "right"):
            cfg = dict(self.cfg, position=edge)
            d = D.Dock(cfg)
            w = Gtk.Window()
            w.set_child(d)
            w.present()
            settle()
            x, y, pw, ph = d.plate_rect()
            self.assertEqual(pw, D.plate_height(cfg))
            self.assertEqual(x, 0 if edge == "left" else d.get_width() - pw)
            self.assertTrue(d.vertical)
            w.destroy()

    def test_recents(self):
        from sonata2 import apps
        key = self.keys()[-1]
        self.dock.set_pinned(key, False)          # not running, not recent -> gone
        self.assertNotIn(key, self.dock.tiles)
        self.dock._note_recent(key)
        self.assertEqual(self.cfg["recent"][0], key)
        info = apps.lookup(key)
        self.dock._add_tile(key, info.get_display_name(), info.get_icon(), info)
        self.dock._relayout()
        self.assertTrue(self.dock.recent_sep.get_visible())
        self.assertIs(self.dock._first_extra(), self.dock.tiles[key])

    def test_stack_panel(self):
        from sonata2.shell import dock_stack
        folder = os.path.join(os.environ["XDG_CONFIG_HOME"], "stackdir")
        os.makedirs(folder, exist_ok=True)
        for n in ("a.txt", "b.txt"):
            with open(os.path.join(folder, n), "w") as fh:
                fh.write(n)
        self.dock.stacks.add(folder)
        tile = self.dock.stacks.tiles()[-1]
        self.assertEqual(len(dock_stack._items(folder, "name")), 2)
        pop = self.dock.stacks.open_panel(tile)
        settle()
        pop.popdown()
        dock_stack.stack_menu(self.dock.stacks, tile).popdown()

    def test_keep_in_dock_toggle(self):
        key = self.removable()[0]
        self.dock.set_pinned(key, False)
        self.assertNotIn(key, self.keys())

    def test_files_and_launchpad_stay(self):
        for key in [k for k in D.PERMANENT if k in self.keys()]:
            self.dock.set_pinned(key, False)
            self.start_drag(key)
            self.dock._drag_leave(None)
            self.dock._drag_cancel(None, None, Gdk.DragCancelReason.NO_TARGET, self.dock.tiles[key])
            self.dock._drag = None
            self.assertIn(key, self.keys())
            self.assertIn(key, config.load("dock", D.DEFAULTS)["pinned"])


if __name__ == "__main__":
    unittest.main()
