"""Vini: Files' Copy window wasn't glass like Get Info and the alerts."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402,F401

from sonata2.ui import progress  # noqa: E402


class ProgressGlassTest(unittest.TestCase):
    def test_copy_window_is_glass(self):
        w = progress._Window()
        self.assertTrue(w.has_css_class("sonata-glass-window"))
        w.destroy()

    def test_no_solid_background_over_the_glass(self):
        import inspect
        src = inspect.getsource(progress)
        rule = src[src.index("window.sonata-progress-window {"):]
        self.assertNotIn("background", rule[:rule.index("}")])


if __name__ == "__main__":
    unittest.main()
