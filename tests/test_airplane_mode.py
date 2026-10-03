"""Airplane Mode in the Control Center, where macOS has AirDrop (Vini)."""
import os
import tempfile
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.backend import system  # noqa: E402

Adw.init()

RFKILL = """0: hci0: Bluetooth
\tSoft blocked: {a}
\tHard blocked: no
1: phy0: Wireless LAN
\tSoft blocked: {b}
\tHard blocked: no
"""


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class BackendTest(unittest.TestCase):
    def test_state_from_rfkill(self):
        with mock.patch.object(system, "_run", return_value=(0, RFKILL.format(a="yes", b="yes"))):
            self.assertTrue(system.airplane_mode())
        with mock.patch.object(system, "_run", return_value=(0, RFKILL.format(a="yes", b="no"))):
            self.assertFalse(system.airplane_mode())
        with mock.patch.object(system, "_run", return_value=(0, "")):
            self.assertIsNone(system.airplane_mode())           # no radios: unavailable
        with mock.patch.object(system, "_run", return_value=(127, "")):
            self.assertIsNone(system.airplane_mode())

    def test_radios_come_back_as_they_were(self):
        calls = []
        with mock.patch.object(system, "_run", side_effect=lambda a, **k: calls.append(a) or (0, "")), \
                mock.patch.object(system, "wifi_enabled", return_value=True), \
                mock.patch.object(system, "bluetooth_state", return_value=False):
            system.set_airplane_mode(True)
            self.assertIn(["rfkill", "block", "all"], calls)
            calls.clear()
            system.set_airplane_mode(False)
        self.assertIn(["rfkill", "unblock", "all"], calls)
        self.assertIn(["nmcli", "radio", "wifi", "on"], calls)              # Wi-Fi was on
        self.assertNotIn(["bluetoothctl", "power", "on"], calls)            # Bluetooth was off


class ModuleTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)

    def test_in_the_connectivity_module_and_it_fits(self):
        from sonata2 import ui
        from sonata2.shell import controlcenter as C, topbar as T
        ui.theme.setup()                                   # the real CSS: sizes as on screen
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None, mixer=None)
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"):
            cc = T.ControlCenter(bar)
        win = Gtk.Window()
        win.set_child(cc)
        win.present()
        settle(150)
        conn = cc.modules["connectivity"]
        self.assertIs(cc.plane.get_parent(), conn)
        need = conn.measure(Gtk.Orientation.VERTICAL, cc.grid.rect("connectivity")[2])[0]
        self.assertLessEqual(need, C.span_height(C.CATALOG["connectivity"][1][1]))   # 3 rows in its 2x2
        cc._fill_toggles((True, ("Home", 80, False), True, None))
        self.assertFalse(cc.plane.button.get_sensitive())                     # no radios: greyed out
        with mock.patch.object(T.system, "run_async") as ra:
            cc._fill_toggles((True, ("Home", 80, False), True, False))
            cc.plane.button.emit("clicked")
        self.assertEqual(ra.call_args[0][0], T.system.set_airplane_mode)
        self.assertEqual(ra.call_args[0][2], True)
        self.assertFalse(cc.wifi.button.has_css_class("on"))                  # radios shown off at once
        self.assertFalse(cc.bt.button.has_css_class("on"))
        win.destroy()


if __name__ == "__main__":
    unittest.main()
