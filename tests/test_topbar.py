"""Menu bar smoke test on a virtual display (xvfb-run python3 -m unittest tests.test_topbar):
every menu / extra opens without errors; backend parsers."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.backend import system  # noqa: E402
from sonata2.shell import topbar  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TopBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_all_menus_open(self):
        win = Gtk.Window()
        bar = topbar.Bar(None)
        win.set_child(bar)
        win.present()
        settle()
        for i, item in enumerate(bar.items):
            bar.open_menu(i)
            settle(100)
            for pop in list(ui.menu.OPEN):
                pop.popdown()
            settle(50)
        win.destroy()

    def test_split_nmcli(self):
        self.assertEqual(system._split_nmcli(r"yes:My\:Net:80"), ["yes", "My:Net", "80"])

    def test_battery_sysfs(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "BAT0"))
        for name, value in (("capacity", "73"), ("status", "Discharging")):
            with open(os.path.join(d, "BAT0", name), "w") as f:
                f.write(value)
        self.assertEqual(system.battery(d), (73, "Discharging"))
        self.assertEqual(system.battery(tempfile.mkdtemp()), (None, ""))

    def test_machine_name(self):
        self.assertEqual(system._machine_name("ASUSTeK COMPUTER INC.", "ASUS TUF Gaming A15",
                                              "ASUS TUF Gaming A15 FA507NV_FA507NV"),
                         "ASUS TUF Gaming A15 FA507NV")


if __name__ == "__main__":
    unittest.main()
