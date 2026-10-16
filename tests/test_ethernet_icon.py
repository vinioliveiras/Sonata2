"""The wired network icon (Vini picked MacTahoe's Ethernet port, option 5):
in the menu bar when the connection isn't Wi-Fi, and Settings' Network
section. Sonata's own icon, so no installed theme draws another one.

    xvfb-run -a python3 -m unittest tests.test_ethernet_icon
"""
import os
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "sonata-ethernet-symbolic"
FILE = os.path.join(ROOT, "sonata2", "data", "icons", "Sonata", "status", "symbolic", NAME + ".svg")
PICKED = os.path.join(ROOT, "sonata2", "data", "icons", "Sonata-MacTahoe", "status", "22",
                      "network-wired-activated-symbolic.svg")


class EthernetIconTest(unittest.TestCase):
    def test_the_picked_drawing(self):
        import re
        paths = lambda f: re.findall(r' d="([^"]+)"', open(f).read())        # noqa: E731
        self.assertEqual(paths(FILE), paths(PICKED))

    def test_menu_bar_on_a_cable(self):
        from sonata2.shell import topbar
        names = []
        fake = types.SimpleNamespace(wifi=mock.Mock(), cfg={}, _set_icon=lambda _b, n: names.append(n))
        topbar.Bar._wifi_state(fake, (False, False, ("", 0, True)))          # no Wi-Fi hardware, a cable
        topbar.Bar._wifi_state(fake, (True, True, ("", 0, True)))            # Wi-Fi on but not joined, a cable
        self.assertEqual(names, [NAME, NAME])

    def test_settings_network_section(self):
        from sonata2.settings import app
        self.assertEqual(next(s[2] for s in app.SECTIONS if s[0] == "network"), NAME)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
    def test_theme_finds_it(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        from sonata2 import icons
        icons.setup()                                                         # as every Sonata process
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        icon = theme.lookup_icon(NAME, None, 16, 1, Gtk.TextDirection.NONE, 0)
        self.assertEqual(icon.get_file().get_path(), FILE)
        self.assertTrue(icon.is_symbolic())                                   # recoloured with the text


if __name__ == "__main__":
    unittest.main()
