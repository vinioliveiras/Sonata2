"""Vini: in Alt+Tab, an app with a long name (or a window with a long title)
made its tile wider when selected -- the name under the icon asked for up
to 18 characters, so the tiles stopped being the same size. The name now
never asks for more than the icon's width: a long one ends in "...".
xvfb-run -a python3 -m unittest tests.test_switcher_name_width"""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import switcher as SW  # noqa: E402


def item_width(size, name=None):
    """The width a switcher tile asks for, its name under it or not (as _open / _mark build it)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sw-item"])
    box.append(Gtk.Image(pixel_size=size))
    if name is not None:
        label = SW.name_label()
        label.set_label(name)
        box.append(label)
    return box.measure(Gtk.Orientation.HORIZONTAL, -1)[1]


@unittest.skipUnless(Gtk.init_check(), "needs a display")
class NameWidthTest(unittest.TestCase):
    def test_long_app_name_widens_the_selected_tile(self):
        long = "Visual Studio Code - Insiders -- a very long window title.py"
        for size in (SW.ICON, 64, SW.ICON_MIN):
            plain = item_width(size)
            self.assertEqual(item_width(size, long), plain, size)
            self.assertEqual(item_width(size, "Files"), plain, size)


if __name__ == "__main__":
    unittest.main()
