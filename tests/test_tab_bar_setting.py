"""Vini: Terminal, Files and TextEdit open with their tab bar showing
(Settings > Appearance > Always show the tab bar, on by default)."""
import inspect
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

from sonata2 import config  # noqa: E402
from sonata2.ui import window as W  # noqa: E402


class TabBarSettingTest(unittest.TestCase):
    def test_rule(self):
        config.update("appearance", always_show_tabs=True)
        self.assertTrue(W.tab_bar_shown(1))
        config.update("appearance", always_show_tabs=False)
        self.assertFalse(W.tab_bar_shown(1))
        self.assertTrue(W.tab_bar_shown(2))                       # more tabs: always
        config.update("appearance", always_show_tabs=True)

    def test_every_app_follows_it(self):
        from sonata2.files import tabs
        from sonata2.terminal import window as term
        from sonata2.textedit import window as text
        for mod in (tabs, term, text):
            src = inspect.getsource(mod)
            self.assertIn("ui.window.tab_bar_shown(", src, mod.__name__)
            self.assertIn("ui.window.follow_tab_bar(", src, mod.__name__)       # live, from Settings
            self.assertNotIn("> 1)", src.split("def _show")[1][:200] if "def _show" in src else "", mod.__name__)

    def test_settings_switch(self):
        from sonata2.settings import app
        src = inspect.getsource(app)
        self.assertIn('switch_row("Always show the tab bar"', src)
        self.assertIn('"always_show_tabs")', src)                 # reset with the rest of Appearance


if __name__ == "__main__":
    unittest.main()
