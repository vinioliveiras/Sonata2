"""Vini: a menu with one item (Remove from Sidebar) had a gap under it:
the hidden scrollbar's minimum length set the menu's height."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402


def _spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def _extra(n):
    """Height of the menu's contents minus its rows'."""
    win = Gtk.Window()
    anchor = Gtk.Label(label="x")
    win.set_child(anchor)
    win.present()
    _spin(200)
    pop = ui.menu.popup(anchor, [[ui.menu.Item(f"Item {i}", lambda: None) for i in range(n)]], at=(5, 5))
    _spin(400)
    contents = pop.get_first_child()
    sw = contents.get_first_child()
    rows = sw.get_first_child().get_first_child().get_first_child()     # viewport > stack > box
    out = contents.get_height() - rows.get_height()
    pop.popdown()
    _spin(200)                     # the menu unparents itself first
    win.destroy()
    return out


class OneItemMenuTest(unittest.TestCase):
    def test_same_padding_with_one_item(self):
        Adw.init()
        ui.setup()
        self.assertEqual(_extra(1), _extra(3))


if __name__ == "__main__":
    unittest.main()
