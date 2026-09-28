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
