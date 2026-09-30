"""Keyboard shortcuts (sonata2/shortcuts.py, Settings > Keyboard Shortcuts).
Run: xvfb-run -a python3 -m unittest tests.test_shortcuts"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2 import shortcuts as S  # noqa: E402


class ShortcutsTest(unittest.TestCase):
    def setUp(self):
        path = os.path.join(os.environ["XDG_CONFIG_HOME"], "sonata2", "wayfire-overrides.ini")
        if os.path.exists(path):
            os.remove(path)

    def test_every_shortcut_has_a_default_and_no_combo_is_shared(self):
        seen = {}
        for s in S.SHORTCUTS:
            self.assertTrue(S.default(s), s.id)
            for combo in S.split(S.default(s)):
                self.assertNotIn(combo, seen, f"{combo}: {s.id} and {seen.get(combo)}")
                seen[combo] = s.id

    def test_windows_shortcuts_are_there(self):
        by_id = {s.id: S.split(S.default(s)) for s in S.SHORTCUTS}
        self.assertIn("<super> KEY_D", by_id["desktop"])
        self.assertIn("<super> KEY_E", by_id["files"])
        self.assertIn("<super> KEY_L", by_id["lock"])
        self.assertIn("<super> KEY_I", by_id["settings"])
        self.assertIn("<ctrl> <shift> KEY_ESC", by_id["activity"])
        self.assertIn("<super> <shift> KEY_S", by_id["shot_area"])
        self.assertIn("<super> KEY_LEFT", by_id["snap_left"])
        self.assertIn("<ctrl> <super> KEY_RIGHT", by_id["space_right"])

    def test_words_and_key_caps(self):
        self.assertEqual(S.describe("<super> <shift> KEY_S"), "Super+Shift+S")
        self.assertEqual(S.describe("<super>"), "Super")                  # pressed and released alone
        self.assertEqual(S.describe("swipe up 3"), "Swipe up with 3 fingers")
        self.assertEqual(S.accelerator("<ctrl> <super> KEY_LEFT"), "<Control><Super>Left")
        self.assertEqual(S.accelerator("<super> KEY_DOT"), "<Super>period")
        self.assertIsNone(S.accelerator("swipe up 3"))
        self.assertEqual(S.split("<super> KEY_Q | <alt>  KEY_F4"), ["<super> KEY_Q", "<alt> KEY_F4"])

    def test_pressed_keys_become_wayfire_text(self):
        self.assertEqual(S.combo_from_key(32 + 8, super_=True), "<super> KEY_D")          # evdev 32 = D
        self.assertEqual(S.combo_from_key(1 + 8, ctrl=True, shift=True), "<ctrl> <shift> KEY_ESC")
        self.assertIsNone(S.combo_from_key(125 + 8, super_=True))                        # Super alone: wait

    def test_change_add_turn_off_reset(self):
        s = next(x for x in S.SHORTCUTS if x.id == "desktop")
        S.set_binding(s, ["<super> KEY_H"])
        self.assertEqual(S.current(s), "<super> KEY_H")
        S.set_binding(s, S.split(S.current(s)) + ["<super> KEY_J"])
        self.assertEqual(S.split(S.current(s)), ["<super> KEY_H", "<super> KEY_J"])
        S.set_binding(s, [])
        self.assertEqual(S.split(S.current(s)), [])
        S.reset(s)
        self.assertEqual(S.current(s), S.default(s))


class ShortcutsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from sonata2 import ui
        ui.setup()

    def test_page_builds_and_a_used_combo_moves(self):
        from sonata2.settings.shortcuts_page import ShortcutsPage
        toasts = []
        win = type("W", (), {"toast": lambda s, t: toasts.append(t)})()
        page = ShortcutsPage(win)
        groups = page.groups()
        self.assertEqual(len(page.rows), len(S.SHORTCUTS))
        self.assertGreater(len(groups), len(S.GROUPS))
        files = next(x for x in S.SHORTCUTS if x.id == "files")
        desktop = next(x for x in S.SHORTCUTS if x.id == "desktop")
        page._apply(desktop, "<super> KEY_E", add=False)         # Super+E belonged to Files
        self.assertEqual(S.split(S.current(desktop)), ["<super> KEY_E"])
        self.assertNotIn("<super> KEY_E", S.split(S.current(files)))
        self.assertTrue(toasts and "Open Files" in toasts[0])
        S.reset(files)
        S.reset(desktop)


if __name__ == "__main__":
    unittest.main()
