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

    def test_battery_icon_follows_state(self):
        bar = topbar.Bar(None)

        def icon():
            c = bar.battery.get_child().get_first_child()
            while not isinstance(c, Gtk.Image):
                c = c.get_next_sibling()
            return c.get_icon_name()
        for res, want in (((64, "Discharging", False, None), "sonata-battery-60-symbolic"),
                          ((64, "Charging", True, None), "sonata-battery-60-charging-symbolic"),
                          ((80, "Not charging", True, None), "sonata-battery-80-plugged-symbolic"),
                          ((15, "Discharging", False, "power-saver"), "sonata-battery-20-saver-symbolic")):
            bar._battery_state(res)
            self.assertEqual(icon(), want)

    def test_split_nmcli(self):
        self.assertEqual(system._split_nmcli(r"yes:My\:Net:80"), ["yes", "My:Net", "80"])

    def test_battery_menu_energy_mode(self):
        from unittest import mock
        win = Gtk.Window()
        bar = topbar.Bar(None)
        win.set_child(bar)
        win.present()
        settle()
        picked = []
        with mock.patch.object(topbar.system, "power_profile_fast", lambda: "balanced"), \
                mock.patch.object(topbar.system, "set_power_profile", lambda p: picked.append(p) or True):
            bar.battery.set_visible(True)
            pop = bar._battery_panel(bar.battery)
            settle(300)
            labels = []
            def walk(w):
                if isinstance(w, Gtk.Label):
                    labels.append(w.get_label())
                c = w.get_first_child()
                while c is not None:
                    walk(c)
                    c = c.get_next_sibling()
            walk(pop)
            self.assertIn("Energy Mode", labels)
            self.assertIn("Low Power", labels)
            if os.environ.get("SHOT"):
                import subprocess
                subprocess.run(["import", "-window", "root", os.environ["SHOT"]])
            pop.popdown()
        settle()                                               # (the panel unparents itself first)
        win.destroy()

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


class PanelHoverTest(unittest.TestCase):
    """Regression: opening the Bluetooth menu lit a row up for a moment
    (hover from the pointer's last spot), then it went dark."""

    def test_no_hover_until_the_pointer_moves(self):
        ui.setup()
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle()
        col = ui.panel.column(ui.panel.row(None, "Device", on_click=lambda: None))
        pop = ui.panel.popup(anchor, col)
        self.assertTrue(pop.has_css_class("fresh"))
        pop.hover_motion(None, 40.0, 30.0)                     # where GTK says it is: still fresh
        pop.hover_motion(None, 40.5, 30.5)
        self.assertTrue(pop.has_css_class("fresh"))
        pop.hover_motion(None, 48.0, 36.0)                     # a real move
        self.assertFalse(pop.has_css_class("fresh"))
        pop.popdown()
        settle()                                               # (the panel unparents itself first)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
