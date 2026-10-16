"""Vini: the Apps Menu open shows a dot under Launchpad's icon in the
Dock, like an app with a window. xvfb-run -a python3 -m unittest tests.test_apps_menu_dot"""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from sonata2.shell import dock as D  # noqa: E402
from sonata2.shell import launchpad as L  # noqa: E402


class DockSideTest(unittest.TestCase):
    def _dock(self):
        tile = mock.Mock()
        d = types.SimpleNamespace(windows={}, launcher_open=False, tiles={D.LAUNCHPAD_KEY: tile},
                                  folder=lambda k: None)
        d.window_count = lambda k: D.Dock.window_count(d, k)
        return d, tile

    def test_dot_while_open(self):
        d, tile = self._dock()
        self.assertEqual(d.window_count(D.LAUNCHPAD_KEY), 0)
        D.Dock.set_launcher_open(d, True)
        tile.set_running.assert_called_with(1)
        self.assertEqual(d.window_count(D.LAUNCHPAD_KEY), 1)        # (window updates keep it)
        self.assertEqual(d.window_count("org.other.App"), 0)
        D.Dock.set_launcher_open(d, False)
        tile.set_running.assert_called_with(0)

    def test_action_registered(self):
        import inspect
        from sonata2 import __main__ as M
        self.assertIn('"launcher-open"', inspect.getsource(M))


class LaunchpadSideTest(unittest.TestCase):
    def test_tells_the_dock_once_per_change(self):
        pad = types.SimpleNamespace()
        told = []
        pad._tell_dock = lambda a, on: told.append((a, on))
        for on in (True, True, False, False, True):
            L.Launchpad._dock_dot(pad, on)
        self.assertEqual(told, [("launcher-open", True), ("launcher-open", False), ("launcher-open", True)])


if __name__ == "__main__":
    unittest.main()
