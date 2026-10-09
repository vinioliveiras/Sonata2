"""Memory review of the shell (Vini: the session's RAM): caches bounded by
bytes, tiles and monitors let go once their owner is gone.
Run: xvfb-run -a python3 -m unittest tests.test_shell_memory"""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Tex:
    def __init__(self, px):
        self.px = px

    def get_width(self):
        return self.px

    def get_height(self):
        return self.px


class KeyedLogoCacheTest(unittest.TestCase):
    def test_bounded_by_bytes_lru(self):
        """icons._keyed kept a 256 px texture per tiled icon forever."""
        from sonata2 import icons
        with mock.patch.object(icons, "_keyed", icons.OrderedDict()), mock.patch.object(icons, "_keyed_bytes", 0):
            n = icons._KEYED_MAX // (256 * 256 * 4)
            for i in range(n + 5):
                icons._keyed_put((f"/x/{i}.png", "#ff0000", 256), (Tex(256), (0, 0, 1, 1)))
                if i == 0:
                    icons._keyed_put(("/none", "#000000", 256), None)           # failures cost nothing
            self.assertLessEqual(icons._keyed_bytes, icons._KEYED_MAX)
            self.assertNotIn(("/x/0.png", "#ff0000", 256), icons._keyed)       # oldest gone
            self.assertIn((f"/x/{n + 4}.png", "#ff0000", 256), icons._keyed)

    def test_hit_is_kept_fresh(self):
        from sonata2 import icons
        with mock.patch.object(icons, "_keyed", icons.OrderedDict()), mock.patch.object(icons, "_keyed_bytes", 0):
            icons._keyed_put(("a", "#fff", 256), (Tex(8), None))
            icons._keyed_put(("b", "#fff", 256), (Tex(8), None))
            self.assertEqual(icons.keyed_content("a", "#fff")[0].px, 8)
            self.assertEqual(list(icons._keyed)[-1], ("a", "#fff", 256))


class ThumbBudgetTest(unittest.TestCase):
    def test_bytes_bound_the_cache(self):
        """Files kept up to 400 thumbnails (~100 MB): now ~32 MB of pixels."""
        from sonata2.files import thumbs
        with mock.patch.dict(thumbs._mem, clear=True), mock.patch.dict(thumbs._pending, clear=True):
            each = 256 * 256 * 4
            for i in range(thumbs.MEMORY_BYTES // each + 10):
                thumbs._deliver((f"p{i}", 1), Tex(256))
            used = sum(thumbs._bytes(t) for t in thumbs._mem.values())
            self.assertLessEqual(used, thumbs.MEMORY_BYTES)
            self.assertEqual(len(thumbs._mem), thumbs.MEMORY_BYTES // each)
            self.assertIn((f"p{thumbs.MEMORY_BYTES // each + 9}", 1), thumbs._mem)
            thumbs._deliver(("big", 1), Tex(8192))                 # one huge: kept alone, never nothing
            self.assertEqual(list(thumbs._mem), [("big", 1)])


class LaunchpadPruneTest(unittest.TestCase):
    def test_gone_folders_let_go(self):
        """A removed folder's tile stayed in self.widgets under id(folder)."""
        from sonata2.shell import launchpad as L
        folder, gone = {"folder": "F", "apps": ["b", "c"]}, {"folder": "G", "apps": ["d"]}
        ns = types.SimpleNamespace(widgets={"a": 1, id(folder): 2, "b": 3, id(gone): 4, "x": 5})
        L.Launchpad._prune_widgets(ns, [["a", folder]])
        self.assertEqual(set(ns.widgets), {"a", id(folder), "b"})      # apps in folders stay (Apps Menu tabs)

    def test_render_prunes(self):
        import inspect
        from sonata2.shell import launchpad as L
        self.assertIn("self._prune_widgets(pages)", inspect.getsource(L.Launchpad.render))


class DesktopReleaseTest(unittest.TestCase):
    def test_monitors_cancelled_with_the_window(self):
        """walls.rebuild() made new desktops; the old ones' config and folder
        monitors were never cancelled and kept them alive."""
        from sonata2 import ui
        from sonata2.shell import desktop as D
        ui.setup()
        d = tempfile.mkdtemp()
        with mock.patch.object(D, "desktop_dir", return_value=Gio.File.new_for_path(d)):
            win = Gtk.Window()
            desk = D.Desktop(screen="", main=True)
            win.set_child(desk)
            win.present()
            settle(200)
            mons = (desk._mon, desk._dock_mon)
            self.assertTrue(all(m is not None and not m.is_cancelled() for m in mons))
            self.assertIsNotNone(desk.folder._monitor)
            win.destroy()
            settle(50)
            self.assertTrue(all(m.is_cancelled() for m in mons))
            self.assertIsNone(desk._mon)
            self.assertIsNone(desk.folder._monitor)


class PenListenerTest(unittest.TestCase):
    def test_pen_let_go_with_its_pill(self):
        """livedraw.pen_button appended a listener to the shared LiveDraw for
        good: every pen (and its pill window) stayed alive."""
        from sonata2 import ui
        from sonata2.shell import livedraw as L
        Adw.init()
        ui.setup()
        fake = types.SimpleNamespace(listeners=[], on=False, toggle=lambda *_a: None)
        with mock.patch.object(L, "get", return_value=fake):
            b = L.pen_button(None)
        self.assertEqual(len(fake.listeners), 1)
        win = Gtk.Window()
        win.set_child(b)
        win.present()
        settle(100)
        self.assertEqual(len(fake.listeners), 1)                       # not twice once shown
        fake.on = True
        fake.listeners[0](True)
        self.assertTrue(b.has_css_class("on"))
        win.destroy()
        settle(50)
        self.assertEqual(fake.listeners, [])


if __name__ == "__main__":
    unittest.main()
