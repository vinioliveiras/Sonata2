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

    def test_room_fits_every_day_of_the_year(self):
        """Vini: a fixed room for the date and clock, so nothing moves when
        the day or month changes either."""
        import time
        from sonata2 import ui
        from sonata2.shell import topbar
        Adw.init()
        ui.setup()
        Gtk.Settings.get_default().set_property("gtk-font-name", "Inter 13")
        b = Gtk.Button(css_classes=["topbar-item", "clock"])
        box = Gtk.Box()
        lbl = Gtk.Label(label="Mon 5 Oct  10:11")
        box.append(lbl)
        b.set_child(box)
        w = Gtk.Window(child=b)
        t0 = time.monotonic()
        room = topbar.clock_width(lbl, topbar.DEFAULTS["clock_format"])
        self.assertLess(time.monotonic() - t0, 1.0)              # cheap enough at start and on a change
        self.assertEqual(lbl.get_label(), "Mon 5 Oct  10:11")    # what it showed, back
        for t in ("Wed 30 Sep  23:59", "Mon 1 May  00:00", "Sat 28 Feb  12:00"):
            lbl.set_label(t)
            self.assertLessEqual(lbl.measure(Gtk.Orientation.HORIZONTAL, -1)[1], room, t)
        w.destroy()

    def test_the_menu_bar_clock_has_the_class(self):
        import inspect
        from sonata2.shell import topbar
        self.assertIn('on_click=self._calendar, css="clock"', inspect.getsource(topbar))


if __name__ == "__main__":
    unittest.main()
