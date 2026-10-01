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
from gi.repository import Adw, GLib  # noqa: E402

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
        self.assertFalse(row.get_visible())                        # the default logo: no text field
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
        self.assertTrue(row.get_visible())
        row.set_text("Vini 🎮")
        row.emit("apply")
        self.assertEqual(config.load("appearance", {"menu_text": ""})["menu_text"], "Vini 🎮")
        win.destroy()

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
