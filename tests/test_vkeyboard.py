"""Vini: the menu bar's New Tab / New Window opened Task Manager. wtype gives
the first key it types keycode 1 (Esc to Wayfire, which matches shortcuts by
keycode): Ctrl+Shift+T became Ctrl+Shift+Esc. Keys now go out with their
real (evdev) keycodes and a US keymap."""
import unittest
from unittest import mock

from sonata2.wl import vkeyboard as V


class CodesTest(unittest.TestCase):
    def test_real_keycodes(self):
        self.assertEqual(V.CODES["t"], 20)                  # KEY_T, not 1 (Esc)
        self.assertEqual(V.CODES["n"], 49)
        self.assertEqual(V.CODES["w"], 17)
        self.assertEqual(V.CODES["Tab"], 15)
        self.assertEqual(V.CODES["Page_Down"], 109)
        self.assertEqual(V.CODES["Escape"], 1)
        self.assertEqual(V.MODS["ctrl"], (29, 4))
        self.assertEqual(V.MODS["shift"], (42, 1))
        self.assertIn('include "pc+us', V.KEYMAP)

    def test_every_menu_bar_shortcut_is_known(self):
        from sonata2.shell import apptabs
        for kind in apptabs.SHORTCUTS.values():
            for key, mods in kind.values():
                self.assertIn(key, V.CODES)
                self.assertTrue(all(m in V.MODS for m in mods))

    def test_unknown_key_or_no_wayland(self):
        self.assertFalse(V.press("NoSuchKey"))
        self.assertFalse(V.press("t", ("hyper",)))
        with mock.patch.dict("os.environ", {"WAYLAND_DISPLAY": "no-such-display-xyz"}):
            self.assertFalse(V.press("t", ("ctrl",)))

    def test_menu_bar_and_controller_use_it(self):
        import inspect
        from sonata2.gamepad import vpointer
        from sonata2.shell import apptabs
        self.assertIn("vkeyboard.press", inspect.getsource(apptabs.press))
        self.assertNotIn("wtype", inspect.getsource(apptabs.press))
        self.assertIn("vkeyboard.press", inspect.getsource(vpointer.key))


if __name__ == "__main__":
    unittest.main()
