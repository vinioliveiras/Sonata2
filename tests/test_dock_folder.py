"""Dock folders: the model (keys, icon grid, names), making a folder,
moving apps in and out, ungrouping, removing, the open panel, and that a
folder survives a save + reload. Run:
xvfb-run python3 -m unittest tests.test_dock_folder"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.shell import dock as D, dock_folder as F  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class ModelTest(unittest.TestCase):
    def test_keys(self):
        self.assertTrue(F.is_folder("folder:3"))
        self.assertFalse(F.is_folder("org.gnome.Calculator.desktop"))
        self.assertFalse(F.is_folder({"folder": "x"}))
        self.assertEqual(F.folder_id("folder:12"), "12")
        self.assertEqual(F.new_id({}), "1")
        self.assertEqual(F.new_id({"1": {}, "2": {}}), "3")
        self.assertEqual(F.new_id({"2": {}}), "1")

    def test_mini_grid_fits_inside(self):
        rects = F.mini_rects(48, 12)
        self.assertEqual(len(rects), 9)                       # at most 3 x 3
        for x, y, side in rects:
            self.assertGreater(x, 0)
            self.assertGreater(y, 0)
            self.assertLessEqual(x + side, 48)
            self.assertLessEqual(y + side, 48)
        self.assertEqual(rects[1][1], rects[0][1])            # row by row
        self.assertGreater(rects[3][1], rects[0][1])

    def test_launchpad_shape(self):
        self.assertEqual(F.as_launchpad({"name": "Games", "apps": ["a"]}), {"folder": "Games", "apps": ["a"]})

    def test_folders_is_a_known_key(self):
        # config.load keeps only known keys: "folders" must be a default
        self.assertIn("folders", D.DEFAULTS)


class DockFolderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        cfg = D.load_config()
        D.load_css(cfg)
        cls.apps = [k for k in cfg["pinned"] if k not in D.PERMANENT and D.apps.lookup(k)]
        if len(cls.apps) < 3:
            raise unittest.SkipTest("needs 3 installed default apps")

    def setUp(self):
        cfg = D.load_config()
        cfg["folders"] = {}
        self.win = Gtk.Window()
        self.dock = D.Dock(cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle()

    def tearDown(self):
        self.win.destroy()
        config.save("dock", {})

    def test_make_folder_takes_first_place(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        at = d.cfg["pinned"].index(a)
        fkey = d.make_folder([a, b], name="Work")
        self.assertEqual(d.cfg["pinned"][at], fkey)
        self.assertNotIn(a, d.cfg["pinned"])
        self.assertNotIn(b, d.cfg["pinned"])
        self.assertEqual(d.folder(fkey), {"name": "Work", "apps": [a, b]})
        self.assertIn(fkey, d.tiles)
        self.assertIsInstance(d.tiles[fkey].icon, F.FolderIcon)
        self.assertEqual(d.tiles[fkey].icon.keys, [a, b])
        settle()
        self.assertNotIn(a, d.tiles)                          # not running: its own icon left
        self.assertTrue(d.in_folder(a))

    def test_default_name(self):
        # regression: DesktopAppInfo.get_categories() needs an argument on
        # newer GI -- the name came from a crash instead of the category
        name = F.default_name(self.apps[:2])
        self.assertIsInstance(name, str)
        self.assertTrue(name)

    def test_saved_and_reloaded(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b], name="Work")
        cfg = D.load_config()                                 # what the next login reads
        self.assertIn(fkey, cfg["pinned"])
        self.assertEqual(cfg["folders"][F.folder_id(fkey)]["apps"], [a, b])
        d2 = D.Dock(cfg)
        self.assertIn(fkey, d2.tiles)
        d2._save_order()                                      # a reorder keeps the folder
        self.assertIn(fkey, d2.cfg["pinned"])
        d2.forget_missing()                                   # a folder isn't "uninstalled"
        self.assertIn(fkey, d2.cfg["pinned"])

    def test_add_and_remove(self):
        d, a, b, c = self.dock, *self.apps[:3]
        fkey = d.make_folder([a, b])
        d.add_to_folder(fkey, c)
        self.assertEqual(d.folder(fkey)["apps"], [a, b, c])
        self.assertNotIn(c, d.cfg["pinned"])
        self.assertEqual(d.tiles[fkey].icon.keys, [a, b, c])
        d.remove_from_folder(fkey, c)                         # back right after the folder
        i = d.cfg["pinned"].index(fkey)
        self.assertEqual(d.cfg["pinned"][i + 1], c)
        self.assertIn(c, d.tiles)

    def test_one_app_left_ungroups(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        at = d.cfg["pinned"].index(fkey)
        d.remove_from_folder(fkey, b)
        self.assertNotIn(fkey, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})
        self.assertEqual(d.cfg["pinned"][at:at + 2], [a, b])
        settle()
        self.assertNotIn(fkey, d.tiles)

    def test_ungroup_and_remove(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        d.ungroup(fkey)
        self.assertIn(a, d.cfg["pinned"])
        self.assertIn(b, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})
        fkey = d.make_folder([a, b])
        d.set_pinned(fkey, False)                             # Remove from Dock
        self.assertNotIn(fkey, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})

    def test_permanent_apps_stay_out(self):
        d = self.dock
        self.assertIsNone(d.make_folder(list(D.PERMANENT)))
        fkey = d.make_folder(self.apps[:2])
        d.add_to_folder(fkey, D.PERMANENT[0])
        self.assertNotIn(D.PERMANENT[0], d.folder(fkey)["apps"])
        self.assertEqual(F.app_items(d, D.PERMANENT[0]), [])
        self.assertEqual(F.app_items(d, fkey), [])

    def test_menu_items(self):
        d, a, b, c = self.dock, *self.apps[:3]
        self.assertEqual([i.label for i in F.app_items(d, c)], ["Add to New Folder"])
        d.make_folder([a, b], name="Work")
        self.assertEqual([i.label for i in F.app_items(d, c)], ["Add to New Folder", "Move to “Work”"])

    def test_click_opens_panel_with_apps(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b], name="Work")
        settle()
        pop = F.open_panel(d, d.tiles[fkey])
        settle()
        self.assertTrue(pop.view.has_css_class("dock-folder-view"))     # the zoom-in animation
        keys = []
        child = pop.flow.get_first_child()
        while child is not None:
            keys.append(child.get_child().key)
            child = child.get_next_sibling()
        self.assertEqual(keys, [a, b])
        F.close_panel(pop)
        self.assertTrue(pop.view.has_css_class("closing"))
        settle(F.CLOSE_MS + 100)
        self.assertFalse(pop.get_visible())

    def test_icon_draws(self):
        icon = F.FolderIcon(self.apps[:3], 48)
        self.assertEqual(icon.do_measure(Gtk.Orientation.HORIZONTAL, -1)[0], 48)
        icon.set_size(60)
        self.assertEqual(icon.do_measure(Gtk.Orientation.HORIZONTAL, -1)[0], 60)
        snap = Gtk.Snapshot()
        icon.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())


if __name__ == "__main__":
    unittest.main()
