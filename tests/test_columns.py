"""ui.columns.fill_last: in a list view only the last visible column takes
the room that's left (macOS). A column in the middle that expanded fought
every resize drag (Files, Task Manager, Music), so this has to hold when
columns are added, removed, reordered, hidden and shown.
Run: xvfb-run -a python3 -m unittest tests.test_columns"""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.ui import columns  # noqa: E402


class FillLastTest(unittest.TestCase):
    def setUp(self):
        self.view = Gtk.ColumnView()
        self.cols = [Gtk.ColumnViewColumn(title=t) for t in ("Name", "Size", "Kind")]
        for c in self.cols:
            c.set_expand(True)                     # what a caller may have set: fill_last decides
            self.view.append_column(c)

    def expanding(self):
        """The shown columns that expand (a hidden one's flag takes no room)."""
        return [c.get_title() for c in self.cols if c.get_expand() and c.get_visible()]

    def test_only_the_last_expands(self):
        columns.fill_last(self.view)
        self.assertEqual(self.expanding(), ["Kind"])

    def test_hiding_the_last_moves_it(self):
        """Hidden last column (View > Show Kind off): the one before fills."""
        columns.fill_last(self.view)
        self.cols[2].set_visible(False)
        self.assertEqual(self.expanding(), ["Size"])
        self.cols[2].set_visible(True)
        self.assertEqual(self.expanding(), ["Kind"])

    def test_columns_added_later(self):
        """fill_last before the columns exist (or a column added afterwards)
        still ends with the new last one filling -- and it is watched too."""
        view = Gtk.ColumnView()
        columns.fill_last(view)
        a, b = Gtk.ColumnViewColumn(title="A"), Gtk.ColumnViewColumn(title="B")
        view.append_column(a)
        view.append_column(b)
        self.assertEqual((a.get_expand(), b.get_expand()), (False, True))
        b.set_visible(False)
        self.assertTrue(a.get_expand())

    def test_reordered(self):
        """A column dragged to the end takes over; the old last one stops."""
        columns.fill_last(self.view)
        self.view.insert_column(2, self.cols[0])           # Size, Kind, Name
        self.assertEqual(self.expanding(), ["Name"])

    def test_removed(self):
        columns.fill_last(self.view)
        self.view.remove_column(self.cols[2])
        self.assertTrue(self.cols[1].get_expand())
        self.assertFalse(self.cols[0].get_expand())

    def test_all_hidden_is_harmless(self):
        columns.fill_last(self.view)
        for c in self.cols:
            c.set_visible(False)
        self.assertEqual([c.get_visible() for c in self.cols], [False] * 3)


if __name__ == "__main__":
    unittest.main()
