"""Launchpad in a window (shell/launchpad_window.py; Settings > Launchpad >
Style). Run: xvfb-run python3 -m unittest tests.test_launchpad_window"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import launchpad_window as LW  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeInfo:
    def __init__(self, name, cats):
        self.name, self.cats = name, cats
        self.launched = 0

    def get_display_name(self):
        return self.name

    def get_categories(self):
        return self.cats

    def get_string(self, key):
        return self.cats if key == "Categories" else ""

    def get_generic_name(self):
        return ""

    def get_icon(self):
        from gi.repository import Gio
        return Gio.ThemedIcon.new("application-x-executable")

    def get_id(self):
        return self.name.lower() + ".desktop"

    def get_name(self):
        return self.name

    def get_executable(self):
        return self.name.lower()

    def get_commandline(self):
        return self.name.lower()

    def get_startup_wm_class(self):
        return None

    def launch(self, *_a):
        self.launched += 1
        return True


APPS = {"discord": FakeInfo("Discord", "Network;InstantMessaging;"),
        "gimp": FakeInfo("GIMP", "Graphics;2DGraphics;"),
        "spotify": FakeInfo("Spotify", "Audio;Music;Player;AudioVideo;"),
        "calc": FakeInfo("Calc", "Office;Spreadsheet;"),
        "files": FakeInfo("Files", "System;FileManager;"),
        "odd": FakeInfo("Odd", "")}


class ModelTest(unittest.TestCase):
    def setUp(self):
        config.save(LW.NAME, {})
        config.save("dock", {})

    def test_categories(self):
        got = {k: LW.category_of(v) for k, v in APPS.items()}
        self.assertEqual(got, {"discord": "social", "gimp": "creativity", "spotify": "entertainment",
                               "calc": "productivity", "files": "utilities", "odd": "other"})

    def test_style_default_and_bad_value(self):
        self.assertEqual(LW.style(), "fullscreen")              # the current Launchpad stays the default
        config.update(LW.NAME, style="window")
        self.assertEqual(LW.style(), "window")
        config.update(LW.NAME, style="???")
        self.assertEqual(LW.style(), "fullscreen")

    def test_suggestions_recent_then_dock(self):
        config.save("dock", {"pinned": ["files.desktop", "gimp", "nope"]})
        LW.note_opened("calc")
        LW.note_opened("spotify")
        LW.note_opened("calc")
        self.assertEqual(LW.suggestions(APPS), ["calc", "spotify", "files", "gimp"])


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        config.save(LW.NAME, {})
        config.save("dock", {})
        self.p = mock.patch.object(LW, "listed_apps", return_value=dict(APPS))
        self.p.start()
        self.w = LW.LaunchpadWindow(None)
        self.w.set_default_size(1400, 900)
        self.w.open_window()
        settle(300)

    def tearDown(self):
        self.w.destroy()
        self.p.stop()

    def test_tabs_only_with_apps_and_filter(self):
        self.assertEqual(list(self.w.tab_buttons), ["social", "creativity", "entertainment", "productivity",
                                                    "utilities", "other"])
        self.w.tab_buttons["creativity"].emit("clicked")
        self.assertEqual([t.key for t in self.w.tiles], ["gimp"])
        self.assertTrue(self.w.tab_buttons["creativity"].get_active())
        self.w.tab_buttons["creativity"].emit("clicked")          # again: all
        self.assertIsNone(self.w.tab)
        self.assertEqual(len(self.w.tiles), len(APPS))

    def test_search_and_enter_opens(self):
        self.w.search.set_text("spo")
        settle(150)
        self.assertEqual([t.key for t in self.w.tiles], ["spotify"])
        self.w.open_selected()
        settle(LW.CLOSE_MS + 200)
        self.assertEqual(APPS["spotify"].launched, 1)
        self.assertEqual(config.load(LW.NAME, LW.DEFAULTS)["recent"][0], "spotify")
        self.assertFalse(self.w.get_visible())

    def test_escape_steps_back(self):
        self.w.set_tab("social")
        self.w.search.set_text("d")
        self.w.escape()
        self.assertEqual(self.w.search.get_text(), "")
        self.w.escape()
        self.assertIsNone(self.w.tab)
        self.w.escape()
        self.assertTrue(self.w.panel.has_css_class("closing"))     # fades out, then hides

    def test_tiles_built_once(self):
        """Smooth typing: a search reuses the tiles instead of making new ones."""
        first = {t.key: t for t in self.w.tiles}
        self.w.search.set_text("g")
        self.w.search.set_text("")
        settle(100)
        self.assertIs({t.key: t for t in self.w.tiles}["gimp"], first["gimp"])

    def test_columns_follow_the_width(self):
        self.assertTrue(4 <= self.w.cols <= LW.COLS)

    def test_arrows_and_click_outside(self):
        self.w._key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(self.w.selected, 0)
        self.w._key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(self.w.selected, 1)
        self.w._outside(None, 1, 2, 2)                              # the corner: outside the panel
        self.assertTrue(self.w.panel.has_css_class("closing"))


class RoutingTest(unittest.TestCase):
    def test_style_picks_the_launchpad(self):
        from sonata2 import __main__ as main
        src = open(main.__file__).read()
        self.assertIn('LW.style() == "window"', src)
        self.assertIn("pw.toggle()", src)
        from sonata2.settings import app as st
        self.assertIn("LW.STYLES", open(st.__file__).read())


if __name__ == "__main__":
    unittest.main()
