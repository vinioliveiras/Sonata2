"""The network icons (Vini's picks, MacTahoe's drawings): in the menu bar,
a computer with a cable when the connection isn't Wi-Fi (option 2); in
Settings, the network globe for its Network section (option 5). Sonata's
own icons, so no installed theme draws another one. (Before: one Ethernet
port for both.)

    xvfb-run -a python3 -m unittest tests.test_ethernet_icon
"""
import os
import re
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(ROOT, "sonata2", "data", "icons")
WIRED, NETWORK = "sonata-wired-symbolic", "sonata-network-symbolic"


def own(name):
    return os.path.join(ICONS, "Sonata", "status", "symbolic", name + ".svg")


PICKED = {WIRED: os.path.join(ICONS, "Sonata-MacTahoe", "status", "symbolic", "network-wired-symbolic.svg"),
          NETWORK: os.path.join(ICONS, "Sonata-MacTahoe", "places", "symbolic", "network-workgroup-symbolic.svg")}


class NetworkIconsTest(unittest.TestCase):
    def test_the_picked_drawings(self):
        paths = lambda f: re.findall(r' d="([^"]+)"', open(f).read())        # noqa: E731
        for name, picked in PICKED.items():
            self.assertEqual(paths(own(name)), paths(picked), name)
            self.assertNotIn("#363636", open(own(name)).read())               # recoloured with the text

    def test_menu_bar_on_a_cable(self):
        from sonata2.shell import topbar
        names = []
        fake = types.SimpleNamespace(wifi=mock.Mock(), cfg={}, _set_icon=lambda _b, n: names.append(n))
        topbar.Bar._wifi_state(fake, (False, False, ("", 0, True)))          # no Wi-Fi hardware, a cable
        topbar.Bar._wifi_state(fake, (True, True, ("", 0, True)))            # Wi-Fi on but not joined, a cable
        self.assertEqual(names, [WIRED, WIRED])

    def test_settings_network_section(self):
        from sonata2.settings import app
        self.assertEqual(next(s[2] for s in app.SECTIONS if s[0] == "network"), NETWORK)

    @unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
    def test_theme_finds_them(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        from sonata2 import icons
        icons.setup()                                                         # as every Sonata process
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for name in PICKED:
            icon = theme.lookup_icon(name, None, 16, 1, Gtk.TextDirection.NONE, 0)
            self.assertEqual(icon.get_file().get_path(), own(name))
            self.assertTrue(icon.is_symbolic())


if __name__ == "__main__":
    unittest.main()
