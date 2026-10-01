"""Settings smoke test (xvfb-run python3 -m unittest tests.test_settings): every
section builds; Sonata settings are written to ~/.config/sonata2."""
import json
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.settings import app as S  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_all_sections(self):
        win = S.Settings(None)
        win.present()
        for sid, *_ in S.SECTIONS:
            win.select(sid)
            settle(150)
            self.assertIn(sid, win.pages)
        win.destroy()

    def test_menu_bar_text_row(self):
        """Menu bar logo > Text: custom… shows the text field; saving writes menu_text."""
        win = S.Settings(None)
        win.present()
        win.select("appearance")
        settle(150)
        found, stack = [], [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.EntryRow) and w.get_title() == "Menu bar text":
                found.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(len(found), 1)
        row = found[0]
        self.assertTrue(row.get_visible())                         # never shown / hidden: the rows under it
        self.assertFalse(row.get_sensitive())                      # don't move (a click hit the next switch)
        stack, combo = [win], None
        while stack and combo is None:
            w = stack.pop()
            if isinstance(w, Adw.ComboRow) and "text:custom" in getattr(w, "values", []):
                combo = w
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        combo.set_selected(combo.values.index("text:custom"))
        self.assertTrue(row.get_sensitive())
        row.grab_focus()
        row.set_text("Vini 🎮")
        settle(100)
        stack = [row]                                              # its Apply button, clicked
        apply_btn = None
        while stack and apply_btn is None:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and "suggested-action" in w.get_css_classes():
                apply_btn = w
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertIsNotNone(apply_btn)
        apply_btn.grab_focus()
        apply_btn.emit("clicked")
        settle(200)
        self.assertEqual(config.load("appearance", {"menu_text": ""})["menu_text"], "Vini 🎮")
        combo.set_selected(0)                                      # another logo: the field stays put
        self.assertTrue(row.get_visible())
        self.assertFalse(row.get_sensitive())
        self.assertTrue(config.load("appearance", {"system_titlebars": True})["system_titlebars"])
        win.destroy()

    def test_titlebars_on_for_new_installs(self):
        """No appearance.json yet (a new install): Sonata title bars for all apps is on."""
        from sonata2 import titlebars
        path = os.path.join(config.CONFIG_DIR, "appearance.json")
        if os.path.exists(path):
            os.remove(path)
        self.assertTrue(titlebars.enabled())
        config.save("appearance", {"theme": "mac"})              # older files without the key: on too
        self.assertTrue(titlebars.enabled())

    def test_keyring_switch_offered_for_keepassxc(self):
        """KeePassXC asked for its password at every login: Security & Privacy offers the move."""
        config.save("keyring", {"backend": "keepassxc"})
        win = S.Settings(None)
        win.present()
        win.select("privacy")
        settle(150)
        found, stack = [], [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and w.get_label() == "Use Login Password…":
                found.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(len(found), 1)
        win.destroy()
        config.save("keyring", {"backend": "gnome"})

    def test_save_keeps_other_keys(self):
        config.save("dock", {"pinned": ["a"], "icon_size": 48})
        win = S.Settings(None)
        win._save("dock", "icon_size", 64)
        with open(os.path.join(config.CONFIG_DIR, "dock.json")) as f:
            data = json.load(f)
        self.assertEqual(data, {"pinned": ["a"], "icon_size": 64})
        win.destroy()


if __name__ == "__main__":
    unittest.main()
