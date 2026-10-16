"""Vini: the Copy window was much taller than its one copy. It is as tall
as its rows: shorter when a copy ends, and made new for the next batch
(a hidden one came back at its old height).
xvfb-run -a python3 -m unittest tests.test_progress_height"""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import progress as P  # noqa: E402


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class CopyHeightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_as_tall_as_its_rows(self):
        a = P.start("Copying a", on_cancel=lambda: None)
        b = P.start("Copying b", on_cancel=lambda: None)
        settle(P.SHOW_DELAY_MS + 600)
        w = P._Window.get()
        two = w.get_height()
        b.finish()
        settle(600)
        self.assertLess(w.get_height(), two)                         # a row less: shorter
        self.assertLessEqual(w.get_height(), w.get_content().measure(
            __import__("gi").repository.Gtk.Orientation.VERTICAL, -1)[1] + 2)
        a.finish()
        settle(200)
        self.assertIsNone(P._Window._instance)                       # gone, not kept hidden
        c = P.start("Copying c", on_cancel=lambda: None)
        settle(P.SHOW_DELAY_MS + 600)
        self.assertIsNot(P._Window.get(), w)                          # a new one, its own height
        self.assertLess(P._Window.get().get_height(), two)
        c.finish()

    def test_finish_twice_or_late_is_harmless(self):
        a = P.start("Copying a")
        a.finish()
        a.finish()
        self.assertIsNone(P._Window._instance)


if __name__ == "__main__":
    unittest.main()
