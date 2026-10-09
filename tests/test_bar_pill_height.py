"""The recording / sharing pill is as tall as the menu bar and centred in
it (Vini: misaligned with the taller 32 px bar)."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import capture, menubar_size  # noqa: E402


class PillHeightTest(unittest.TestCase):
    def test_follows_the_bar(self):
        Gtk.init()
        for h in (menubar_size.STANDARD_H, menubar_size.TALL_H):
            with mock.patch.object(menubar_size, "height", return_value=h):
                pill = capture.BarPill(None, "t", "sonata2-test")
            self.assertEqual(pill.get_size_request()[1], h)
            self.assertEqual(pill.box.get_valign(), Gtk.Align.CENTER)
            pill.destroy()


if __name__ == "__main__":
    unittest.main()
