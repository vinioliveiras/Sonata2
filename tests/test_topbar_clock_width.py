"""Vini: when the minute changes, the menu bar's items nudge left. The clock
used proportional digits ("10:11" narrower than "10:08"), and the items to
its left followed its width. Tabular digits keep it one width."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class ClockWidthTest(unittest.TestCase):
    def test_every_time_is_one_width(self):
        from sonata2 import ui
        from sonata2.shell import topbar  # noqa: F401  (registers the menu bar's CSS)
        Adw.init()
        ui.setup()
        # Sonata's font (Inter): proportional digits unless asked for tabular ones
        Gtk.Settings.get_default().set_property("gtk-font-name", "Inter 13")
        b = Gtk.Button(css_classes=["topbar-item", "clock"])
        box = Gtk.Box()
        lbl = Gtk.Label()
        box.append(lbl)
        b.set_child(box)
        w = Gtk.Window(child=b)
        widths = set()
        for t in ("Mon 5 Oct  10:11", "Mon 5 Oct  10:08", "Mon 5 Oct  11:11", "Mon 5 Oct  20:40"):
            lbl.set_label(t)
            widths.add(lbl.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        self.assertEqual(len(widths), 1, widths)
        w.destroy()

    def test_the_menu_bar_clock_has_the_class(self):
        import inspect
        from sonata2.shell import topbar
        self.assertIn('on_click=self._calendar, css="clock"', inspect.getsource(topbar))


if __name__ == "__main__":
    unittest.main()
