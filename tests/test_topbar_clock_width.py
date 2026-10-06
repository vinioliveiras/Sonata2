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

    def test_room_is_todays(self):
        """Vini: a room for the widest date of the year left a gap beside the
        clock ("ter 6 out" in room for "dom 28 mar"). The room is today's:
        every hour fits, a wider day's date doesn't get room today."""
        import time
        from gi.repository import GLib
        from sonata2 import ui
        from sonata2.shell import topbar
        Adw.init()
        ui.setup()
        Gtk.Settings.get_default().set_property("gtk-font-name", "Inter 13")
        b = Gtk.Button(css_classes=["topbar-item", "clock"])
        box = Gtk.Box()
        lbl = Gtk.Label(label="Tue 6 Oct  10:11")
        box.append(lbl)
        b.set_child(box)
        w = Gtk.Window(child=b)
        fmt = topbar.DEFAULTS["clock_format"]
        day = GLib.DateTime.new_local(2026, 10, 6, 9, 0, 0)        # a Tuesday
        t0 = time.monotonic()
        room = topbar.clock_width(lbl, fmt, day)
        self.assertLess(time.monotonic() - t0, 0.2)
        self.assertEqual(lbl.get_label(), "Tue 6 Oct  10:11")    # what it showed, back
        for h in range(24):
            lbl.set_label(GLib.DateTime.new_local(2026, 10, 6, h, 59, 0).format(fmt))
            self.assertLessEqual(lbl.measure(Gtk.Orientation.HORIZONTAL, -1)[1], room)
        wide = topbar.clock_width(lbl, fmt, GLib.DateTime.new_local(2026, 3, 29, 9, 0, 0))   # Sun 29 Mar
        self.assertGreater(wide, room)
        w.destroy()

    def test_new_day_measures_again(self):
        import inspect
        from sonata2.shell import topbar
        tick = inspect.getsource(topbar.Bar._tick_clock) if hasattr(topbar, "Bar") else inspect.getsource(topbar)
        self.assertIn("self._clock_room()                          # a new day: its own width", tick)

    def test_measured_once_per_format(self):
        """Review: a thousand measurements on every reveal of the bar."""
        from unittest import mock
        from sonata2 import ui
        from sonata2.shell import topbar
        Adw.init()
        ui.setup()
        lbl = Gtk.Label(label="x")
        w = Gtk.Window(child=lbl)
        topbar._CLOCK_WIDTHS.clear()
        a = topbar.clock_width(lbl, "%H:%M")
        with mock.patch.object(lbl, "measure", side_effect=AssertionError("measured again")):
            self.assertEqual(topbar.clock_width(lbl, "%H:%M"), a)          # the same day: kept
        self.assertGreater(topbar.clock_width(lbl, "%a %-d %b  %H:%M"), a)     # another format: measured
        w.destroy()

    def test_the_menu_bar_clock_has_the_class(self):
        import inspect
        from sonata2.shell import topbar
        self.assertIn('on_click=self._calendar, css="clock"', inspect.getsource(topbar))


if __name__ == "__main__":
    unittest.main()
