"""Vini: a Control Center module to switch the keyboard layout."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402


class KeyboardModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def test_sources_in_a_row_a_click_switches(self):
        from sonata2.shell import kbdmodule as K
        used = []
        bar = mock.Mock(_use_layout=used.append)
        with mock.patch.object(K.system, "keyboard_layouts", return_value=["pt", "us(intl)"]):
            m = K.module(bar)
            self.assertEqual(list(m.seg.buttons), ["pt", "us(intl)"])
            self.assertEqual(m.seg.buttons["pt"].get_label(), "PT")
            self.assertTrue(m.seg.buttons["pt"].has_css_class("on"))
            self.assertEqual(m.name.get_label(), "Portuguese")
            self.assertFalse(m.add_btn.get_visible())
            m.seg.buttons["us(intl)"].emit("clicked")
        self.assertEqual(used, [1])
        self.assertTrue(m.seg.buttons["us(intl)"].has_css_class("on"))
        self.assertEqual(m.name.get_label(), "U.S. International")

    def test_one_source_offers_to_add(self):
        from sonata2.shell import kbdmodule as K
        closed = []
        with mock.patch.object(K.system, "keyboard_layouts", return_value=["pt"]):
            m = K.module(mock.Mock(), lambda: closed.append(1))
        self.assertTrue(m.add_btn.get_visible())
        with mock.patch("sonata2.shell.topbar.open_settings") as opened:
            m.add_btn.emit("clicked")
        opened.assert_called_once_with("keyboard")
        self.assertEqual(closed, [1])

    def test_in_add_controls_and_one_row_tall(self):
        from sonata2.shell import controlcenter as C, kbdmodule as K
        self.assertEqual(C.CATALOG["keyboard"], ("Keyboard", (4, 1)))
        self.assertNotIn("keyboard", C.DEFAULT_ORDER)
        with mock.patch.object(K.system, "keyboard_layouts", return_value=["pt", "us"]):
            m = K.module(None)
        from gi.repository import Gtk
        h = m.measure(Gtk.Orientation.VERTICAL, C.WIDTH_MIN)[1]
        self.assertLessEqual(h, C.UNIT_H)


if __name__ == "__main__":
    unittest.main()
