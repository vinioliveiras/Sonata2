"""Vini: an app quitting closes its Dock place up smoothly, but an app
opening popped in -- its place opens the same way now (not for the windows
already open when the Dock starts).
xvfb-run -a python3 -m unittest tests.test_dock_open_animates"""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2.shell import dock as D  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def win(app_id):
    return types.SimpleNamespace(app_id=app_id, title=app_id, activated=False, states=(), minimized=False,
                                 set_rectangle=lambda *a: None)


class OpenAnimatesTest(unittest.TestCase):
    def test_new_app_opens_its_place(self):
        Gtk.init()
        cfg = D.load_config()
        D.load_css(cfg)
        manager = types.SimpleNamespace(toplevels=[win("org.example.Old")], available=True, listeners=[])
        dock = D.Dock(cfg, manager)
        w = Gtk.Window()
        w.set_child(dock)
        w.present()
        settle(300)
        opened = []
        with mock.patch.object(D.Dock, "open_up", lambda self, tile: opened.append(tile.key)), \
                mock.patch.object(D.Dock, "_update_rectangles_bg", lambda self: False):
            dock._sync()                                       # what was open at start: no animation
            self.assertEqual(opened, [])
            manager.toplevels = manager.toplevels + [win("org.example.New")]
            dock._sync()
        self.assertEqual(opened, ["org.example.New"])
        w.destroy()

    def test_real_animation_ends_with_the_icon_shown(self):
        Gtk.init()
        cfg = D.load_config()
        D.load_css(cfg)
        manager = types.SimpleNamespace(toplevels=[], available=True, listeners=[])
        dock = D.Dock(cfg, manager)
        w = Gtk.Window()
        w.set_child(dock)
        w.present()
        settle(300)
        with mock.patch.object(D.Dock, "_update_rectangles_bg", lambda self: False):
            dock._sync()
            manager.toplevels = [win("org.example.New")]
            dock._sync()
            tile = dock.tiles["org.example.New"]
            self.assertFalse(tile.get_visible())                  # its place opens first
            settle(3 * D.OPEN_UP_MS + 300)
        self.assertTrue(tile.get_visible())
        self.assertEqual(tile.get_opacity(), 1.0)
        w.destroy()

    def test_open_up_grows_then_shows(self):
        import inspect
        src = inspect.getsource(D.Dock._sync)
        self.assertIn("self.open_up(self.tiles[key])", src)


if __name__ == "__main__":
    unittest.main()
