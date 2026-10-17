"""Vini: About This Computer showed a scroll bar -- it opened before its
specs came (read in the background) and kept that height. Small windows
like it show all of their content.
xvfb-run -a python3 -m unittest tests.test_about_fits"""
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
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import i18n, ui  # noqa: E402


def settle(ms=600):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


SPECS = types.SimpleNamespace(os_name="CachyOS", kernel="7.2.9-2-cachyos", machine="ASUS TUF Gaming A15 FA507NV",
                              cpu="AMD Ryzen 7 7735HS with Radeon Graphics", memory_gb=62,
                              gpus=["NVIDIA GeForce RTX 4060 Max-Q / Mobile", "AMD Radeon 680M"])


class AboutFitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def tearDown(self):
        i18n.use(None)

    def test_whole_content_in_sight(self):
        i18n.use("pt_BR")
        from sonata2.shell import topbar
        late = []
        with mock.patch.object(topbar.system, "run_async", side_effect=lambda fn, cb: late.append(cb)):
            win = topbar.AboutWindow()
        win.present()
        settle()
        width = win.get_width()
        late[0](SPECS)                                   # the specs arrive after it is on screen
        settle(900)
        adj = win.scroller.get_vadjustment()
        self.assertLessEqual(adj.get_upper(), adj.get_page_size() + 1, (adj.get_upper(), adj.get_page_size()))
        self.assertEqual(win.get_width(), width)                         # only taller
        height = win.get_height()
        settle(1500)
        self.assertEqual(win.get_height(), height)                       # and it stays (no runaway)
        win.destroy()

    def test_no_taller_than_its_content(self):
        """About This Computer at its content's height, not a fixed 300 px
        with empty glass under the specs."""
        from gi.repository import Gtk
        from sonata2.shell import topbar
        with mock.patch.object(topbar.system, "run_async", side_effect=lambda fn, cb: cb(SPECS)):
            win = topbar.AboutWindow()
        win.present()
        settle(900)
        content = win.col.measure(Gtk.Orientation.VERTICAL, win.get_width())[1]
        self.assertLessEqual(win.get_height(), content + 2, (win.get_height(), content))
        win.destroy()


if __name__ == "__main__":
    unittest.main()
