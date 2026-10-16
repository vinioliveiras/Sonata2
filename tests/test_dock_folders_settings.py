"""Vini: Downloads removed from the Dock couldn't be brought back. Settings >
Dock > Folders in the Dock lists the Dock's folders, adds Downloads back,
any folder, removes one; the running Dock follows.
xvfb-run -a python3 -m unittest tests.test_dock_folders_settings"""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import dock_stack  # noqa: E402


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.down = tempfile.mkdtemp()
        self.other = tempfile.mkdtemp()
        p = mock.patch.object(dock_stack, "_downloads", return_value=self.down)
        p.start()
        self.addCleanup(p.stop)
        from sonata2.settings import app as S
        self.win = S.Settings.__new__(S.Settings)
        self.win._save = lambda name, key, value: config.update(name, **{key: value})
        self.group = Adw.PreferencesGroup()
        self.win._dock_folders_group = self.group

    def paths(self):
        from sonata2.shell import dock as D
        return [s["path"] for s in config.load("dock", D.DEFAULTS).get("stacks") or []]

    def titles(self):
        return [r.get_title() for r in self.group.folder_rows]

    def test_downloads_back_after_removing_it(self):
        config.update("dock", stacks=[])                                  # removed from the Dock
        self.win._fill_dock_folders()
        self.assertEqual(self.titles(), ["Downloads", "Another folder"])
        self.win._add_dock_folder(self.down)                              # its "Add to Dock" button
        self.assertEqual(self.paths(), [self.down])
        self.assertEqual(self.titles()[0], os.path.basename(self.down))  # listed now, no "Add" row
        self.assertNotIn("Downloads", self.titles())

    def test_add_and_remove_a_folder(self):
        config.update("dock", stacks=[dict(dock_stack.STACK_DEFAULTS, path=self.down)])
        self.win._fill_dock_folders()
        self.win._add_dock_folder(self.other)
        self.win._add_dock_folder(self.other)                             # once
        self.assertEqual(self.paths(), [self.down, self.other])
        self.win._set_dock_stacks([s for s in self.win._dock_stacks() if s["path"] != self.down])
        self.assertEqual(self.paths(), [self.other])
        self.assertIn("Downloads", self.titles())                         # offered back

    def test_never_set_means_the_default(self):
        config.update("dock", stacks=None)
        self.assertEqual([s["path"] for s in self.win._dock_stacks()], [self.down])


class DockFollowsTest(unittest.TestCase):
    def test_sync_adds_and_removes_without_writing(self):
        a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
        row = dock_stack.StackRow.__new__(dock_stack.StackRow)
        opened, closed = [], []
        row.dock = types.SimpleNamespace(cfg={"stacks": []}, open_up=opened.append, close_up=closed.append)
        tiles = []

        def add_tile(spec):
            t = types.SimpleNamespace(spec=spec, label=types.SimpleNamespace(unparent=lambda: None))
            tiles.append(t)
            return t
        row._tiles = tiles
        row._add_tile = add_tile
        row._unwatch = lambda t: None
        row._save = lambda: self.fail("Settings already wrote it")
        row.sync([{"path": a}, {"path": b}])
        self.assertEqual([t.spec["path"] for t in tiles], [a, b])
        self.assertEqual(len(opened), 2)                                   # their places open
        row.sync([{"path": b}])
        self.assertEqual([t.spec["path"] for t in tiles], [b])
        self.assertEqual(len(closed), 1)
        self.assertEqual([s["path"] for s in row.dock.cfg["stacks"]], [b])

    def test_dock_listens(self):
        import inspect
        from sonata2.shell import dock
        src = inspect.getsource(dock)
        self.assertIn('d.stacks.sync(new["stacks"])', src)


if __name__ == "__main__":
    unittest.main()
