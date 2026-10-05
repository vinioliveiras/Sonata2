"""Vini: the newest MacBooks' menu bar is a little taller -- Settings > Menu
Bar > "Taller menu bar" (32 px instead of 24); what lies under it follows."""
import inspect
import tempfile
import unittest
from unittest import mock

from sonata2 import config
from sonata2.shell import menubar_size as M


class TallMenuBarTest(unittest.TestCase):
    def test_height_from_the_setting(self):
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()):
            self.assertEqual(M.height(), 24)
            config.update("topbar", tall=True)
            self.assertEqual(M.height(), 32)

    def test_everything_under_it_follows(self):
        from sonata2.shell import desktop, notifications, topbar
        self.assertEqual(topbar.BAR_H, M.height())
        self.assertEqual(notifications.TOP_GAP, M.height() + 6)
        self.assertEqual(desktop.TOP, M.height() + 10)
        self.assertIn("tall", topbar.DEFAULTS)

    def test_settings_switch_restarts_the_menu_bar(self):
        from sonata2.settings import app
        src = inspect.getsource(app)
        self.assertIn('"Taller menu bar"', src)
        self.assertIn('["restart", "topbar"]', inspect.getsource(app.SettingsWindow._set_tall_menubar)
                      if hasattr(app, "SettingsWindow") else src)


if __name__ == "__main__":
    unittest.main()
