"""Vini: in the Files and Settings sidebars, clicking another section or
pinned place should animate. The selection slides from the old row to the
new one (ui.transition.SlidingSelection), then the row's own highlight takes
over."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402


def spin(cond, ms=3000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class SlidingSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def test_pill_slides_between_rows_then_hands_over(self):
        from sonata2 import ui
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        rows = []
        for i in range(6):
            r = Gtk.ListBoxRow(child=Gtk.Label(label=f"Row {i}"))
            r.set_size_request(-1, 30)
            lb.append(r)
            rows.append(r)
        side = ui.transition.SlidingSelection(lb, ms=300)
        self.assertIs(lb.get_parent(), side)                         # in place of the list
        win = Gtk.Window(child=side, default_width=200, default_height=260)
        win.present()
        spin(lambda: rows[5].get_mapped() and rows[5].get_height() > 0)
        lb.select_row(rows[0])
        self.assertFalse(side.pill.get_visible())                    # nothing selected before: no slide
        y0, y5 = side._bounds(rows[0])[1], side._bounds(rows[5])[1]
        lb.select_row(rows[5])
        self.assertTrue(side.pill.get_visible())
        self.assertIn("sliding", lb.get_css_classes())               # the row's own highlight off
        self.assertAlmostEqual(side.under.rect[1], y0, delta=1)      # starts on the old row
        seen = []
        spin(lambda: (seen.append(side.under.rect[1]), y0 + 10 < seen[-1] < y5 - 10)[1])
        self.assertTrue(y0 + 10 < seen[-1] < y5 - 10)                # on its way
        self.assertTrue(spin(lambda: not side.pill.get_visible()))   # arrived: handed over
        self.assertNotIn("sliding", lb.get_css_classes())
        self.assertAlmostEqual(side.under.rect[1], y5, delta=1)
        # mid-slide click: from where the pill is, not from the old row
        lb.select_row(rows[0])
        spin(lambda: side.under.rect[1] < y5 - 20)
        here = side.under.rect[1]
        lb.select_row(rows[3])
        self.assertAlmostEqual(side.under.rect[1], here, delta=1)
        self.assertTrue(spin(lambda: not side.pill.get_visible()))
        win.destroy()

    def test_files_and_settings_sidebars_use_it(self):
        import inspect
        from sonata2.files import sidebar
        from sonata2.settings import app
        self.assertIn("SlidingSelection(self.list)", inspect.getsource(sidebar))
        self.assertIn("SlidingSelection(self.listbox", inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
