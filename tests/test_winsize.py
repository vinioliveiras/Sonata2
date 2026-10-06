"""Vini: every app should open at the size its window last had (only
TextEdit did). winsize keeps it; ui.window.remember_size is every app's."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2 import config, winsize  # noqa: E402
from sonata2.ui import window as UW  # noqa: E402

APPS = {"files/window.py": "files", "notes/window.py": "notes",
        "settings/app.py": "settings", "activity/window.py": "activity", "calendar/window.py": "calendar",
        "camera/window.py": "camera", "assistant/window.py": "assistant", "diskutil/window.py": "diskutil",
        "clock/window.py": "clock", "terminal/window.py": "terminal", "feedback/window.py": "feedback",
        "textedit/window.py": "textedit"}


class WinsizeTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_save_and_read(self):
        self.assertIsNone(winsize.saved("notes"))
        self.assertTrue(winsize.save("notes", 900, 600, True))
        self.assertFalse(winsize.save("notes", 900, 600, True))              # unchanged: not written
        self.assertEqual(winsize.saved("notes"), {"width": 900, "height": 600, "maximized": True})
        self.assertFalse(winsize.save("notes", 10, 10))                      # a glitch
        winsize.save("files", 700, 500)
        self.assertEqual(winsize.size_or("notes", 1, 1), (900, 600))         # each app its own
        self.assertEqual(winsize.size_or("calendar", 1100, 720), (1100, 720))

    def test_window_opens_at_it_and_keeps_it(self):
        winsize.save("t", 700, 500)
        win = Gtk.Window()
        with mock.patch.object(UW, "screen_size", return_value=(1920, 1080)):
            UW.remember_size(win, "t", 1000, 800)
        self.assertEqual(win.get_default_size(), (700, 500))
        win.set_default_size(820, 560)
        win.emit("close-request")
        self.assertEqual(winsize.saved("t")["width"], 820)

    def test_first_time_and_maximized(self):
        winsize.save("m", 900, 600, True)
        win = Gtk.Window()
        with mock.patch.object(UW, "screen_size", return_value=(1920, 1080)):
            UW.remember_size(win, "m", 1000, 800)
            self.assertTrue(win.is_maximized() or win.get_property("maximized"))
            win2 = Gtk.Window()
            UW.remember_size(win2, "new", 1000, 800)
        self.assertEqual(win2.get_default_size(), (1000, 800))

    def test_every_app_uses_it(self):
        root = os.path.join(os.path.dirname(__file__), "..", "sonata2")
        for path, key in APPS.items():
            with open(os.path.join(root, path), encoding="utf-8") as f:
                self.assertIn(f'remember_size(self, "{key}"', f.read(), path)


if __name__ == "__main__":
    unittest.main()
