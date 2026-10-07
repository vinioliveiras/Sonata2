"""Every status item of the menu bar can be taken out of it (Vini):
Settings > Menu Bar > Show in Menu Bar. Control Center and the clock stay."""
import tempfile
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from sonata2.shell import topbar as T  # noqa: E402

Adw.init()
KEYS = ("show_tray", "show_now_playing", "show_input", "show_sound", "show_battery", "battery_percent",
        "show_bluetooth", "show_wifi", "show_spotlight")


def bar(**cfg):
    b = T.Bar.__new__(T.Bar)
    b.cfg = dict(T.DEFAULTS, **cfg)
    for name in ("wifi", "battery", "battery_pct", "input_btn", "spotlight"):
        setattr(b, name, Gtk.Button())
    b._set_icon = lambda *_a: None
    b._set_text = lambda *_a: None
    return b


class ItemsTest(unittest.TestCase):
    def test_every_item_has_a_setting_shown_by_default(self):
        for k in ("show_wifi", "show_battery", "show_spotlight", "show_input", "show_bluetooth", "show_tray"):
            self.assertTrue(T.DEFAULTS[k], k)

    def test_wifi_hidden_when_turned_off(self):
        b = bar(show_wifi=False)
        T.Bar._wifi_state(b, (True, True, ("Home", 80, False)))
        self.assertFalse(b.wifi.get_visible())
        b = bar()
        T.Bar._wifi_state(b, (True, True, ("Home", 80, False)))
        self.assertTrue(b.wifi.get_visible())

    def test_battery_hidden_when_turned_off(self):
        b = bar(show_battery=False)
        T.Bar._battery_state(b, (80, "Discharging", False, None))
        self.assertFalse(b.battery.get_visible())
        b = bar()
        T.Bar._battery_state(b, (80, "Discharging", False, None))
        self.assertTrue(b.battery.get_visible())

    def test_input_source_hidden_when_turned_off(self):
        b = bar(show_input=False)
        b._layouts = lambda: ["us", "br"]
        T.Bar._update_input(b)
        self.assertFalse(b.input_btn.get_visible())
        b = bar()
        b._layouts = lambda: ["us", "br"]
        T.Bar._update_input(b)
        self.assertTrue(b.input_btn.get_visible())

    def test_settings_lists_every_item(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(T.config, "CONFIG_DIR", tmp):
            from sonata2.settings import app as S
            win = S.Settings.__new__(S.Settings)
            saved = []
            win._save = lambda name, key, value: saved.append((name, key, value))
            groups = S.Settings._page_menubar(win)
            rows = []

            def walk(w):
                if isinstance(w, Adw.SwitchRow):
                    rows.append(w)
                c = w.get_first_child()
                while c is not None:
                    walk(c)
                    c = c.get_next_sibling()
            for g in groups:
                walk(g)
            titles = {r.get_title(): r for r in rows}
            for t in ("Background apps", "Now Playing", "Keyboard layout", "Sound", "Battery", "Battery percentage",
                      "Bluetooth", "Wi-Fi", "Search"):
                self.assertIn(t, titles)
            titles["Wi-Fi"].set_active(False)
            titles["Search"].set_active(False)
        self.assertIn(("topbar", "show_wifi", False), saved)
        self.assertIn(("topbar", "show_spotlight", False), saved)


if __name__ == "__main__":
    unittest.main()
