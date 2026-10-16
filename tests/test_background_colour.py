"""Vini: the background colour of Light and Dark Mode picked like the
accent colour -- a few for each, the default to go back to, any colour
through Sonata's picker. Run: xvfb-run -a python3 -m unittest tests.test_background_colour"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.ui import tokens as T  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TokensTest(unittest.TestCase):
    def setUp(self):
        config.save("appearance", {})

    def test_default_unchanged(self):
        self.assertEqual(T.palette(False)["window_bg"], T.LIGHT["window_bg"])
        self.assertEqual(T.palette(True)["window_bg"], T.DARK["window_bg"])
        self.assertEqual(T.BACKGROUNDS["light"][0][1], T.LIGHT["window_bg"])     # the first is the theme's own
        self.assertEqual(T.BACKGROUNDS["dark"][0][1], T.DARK["window_bg"])

    def test_glass_follows_too(self):
        """Vini: the colour changed only where it wasn't see-through -- the
        Dock, the menu bar, menus and panels and the sidebars' glass follow,
        moved as seen (over what is behind) like the window."""
        config.save("appearance", {"background": {"dark": "#121826"}})
        base, got = T.DARK, T.palette(True)
        delta = [n - b for n, b in zip(T._rgb(got["window_bg"])[0], T._rgb(base["window_bg"])[0])]
        for k in T.BACKGROUND_GLASS:
            (g0, a), (g1, a1) = T._rgb(base[k]), T._rgb(got[k])
            self.assertEqual(a, a1, k)                                      # as see-through as before
            seen = [round((v1 - v0) * a) for v0, v1 in zip(g0, g1)]
            for s_, d, v1 in zip(seen, delta, g1):
                if 0 < v1 < 255:                                            # (not clipped)
                    self.assertLessEqual(abs(s_ - d), 1, (k, seen, delta))
        tb = [n - b for n, b in zip(T._rgb(got["titlebar_bg"])[0], T._rgb(base["titlebar_bg"])[0])]
        self.assertEqual(tb[1:], delta[1:])                                 # opaque bars: moved like the window

    def test_as_many_as_accent_colours(self):
        for mode in ("light", "dark"):
            self.assertEqual(len(T.BACKGROUNDS[mode]), len(T.ACCENTS), mode)

    def test_picked_per_mode(self):
        config.save("appearance", {"background": {"light": "#e4e9f0", "dark": "#121826"}})
        light, dark = T.palette(False), T.palette(True)
        self.assertEqual((light["window_bg"], dark["window_bg"]), ("#e4e9f0", "#121826"))
        # the surfaces around it keep their distance: content still lighter (light), sidebar lighter (dark)
        self.assertGreater(T._luminance(light["content_bg"]), T._luminance(light["window_bg"]))
        self.assertGreater(T._luminance(dark["sidebar_bg"]), T._luminance(dark["window_bg"]))
        # the opaque title bar: its (moved) glass over the theme's window -- moved as the window was
        self.assertEqual(light["titlebar_bg"], T.over(light["titlebar_glass"], T.LIGHT["window_bg"]))
        self.assertTrue(light["menu_bg"].startswith("rgba(") and light["menu_bg"].endswith("0.97)"))

    def test_text_stays_legible(self):
        config.save("appearance", {"background": {"light": "#202020", "dark": "#ffffff"}})
        self.assertGreaterEqual(T._luminance(T.palette(False)["window_bg"]), T.LIGHT_BG_MIN)
        self.assertLessEqual(T._luminance(T.palette(True)["window_bg"]), T.DARK_BG_MAX)

    def test_every_preset_already_legible(self):
        for mode, opts in T.BACKGROUNDS.items():
            for name, c in opts:
                self.assertEqual(T.legible_background(c, mode == "dark"), c, (mode, name))


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        from sonata2.settings import app as st
        cls.st = st

    def setUp(self):
        config.save("appearance", {})
        self.w = self.st.Settings(None, "appearance")
        self.w.present()
        self.w.select("appearance", from_sidebar=True)
        settle(200)

    def tearDown(self):
        self.w.destroy()

    def _row(self, title):
        from tests.test_regressions import rows_of
        return next(r for r in rows_of(self.w.pages["appearance"], Adw.ActionRow) if r.get_title() == title)

    def test_presets_custom_and_back_to_default(self):
        from sonata2 import titlebars
        with mock.patch.object(titlebars, "apply_colors"), mock.patch.object(titlebars, "apply"):
            row = self._row("Dark background")
            default = T.BACKGROUNDS["dark"][0][1]
            self.assertTrue(row.background_buttons[default].has_css_class("selected"))
            navy = dict(T.BACKGROUNDS["dark"])["Navy"]
            settle(500)
            before = titlebars.apply.call_count                       # (the page's own, if any)
            row.background_buttons[navy].emit("clicked")
            self.assertEqual(config.load("appearance", {"background": {}})["background"], {"dark": navy})
            row.custom_background.emit("clicked")                     # any colour: Sonata's own picker
            pop = row.custom_background.picker
            settle(50)
            pop.hex.set_text("#102030")
            pop.select_button.emit("clicked")
            settle(50)
            self.assertEqual(config.load("appearance", {"background": {}})["background"], {"dark": "#102030"})
            self.assertTrue(row.custom_background.has_css_class("selected"))
            self.assertFalse(row.background_buttons[navy].has_css_class("selected"))
            row.background_buttons[default].emit("clicked")           # back to the original
            self.assertEqual(config.load("appearance", {"background": {}})["background"], {})
            settle(700)
            self.assertEqual(titlebars.apply.call_count - before, 1)   # quick clicks: applied once

    def test_light_row_leaves_dark_alone(self):
        from sonata2 import titlebars
        with mock.patch.object(titlebars, "apply_colors"), mock.patch.object(titlebars, "apply"):
            config.update("appearance", background={"dark": "#121826"})
            row = self._row("Light background")
            sand = dict(T.BACKGROUNDS["light"])["Sand"]
            row.background_buttons[sand].emit("clicked")
            self.assertEqual(config.load("appearance", {"background": {}})["background"],
                             {"dark": "#121826", "light": sand})


class ThemeReloadTest(unittest.TestCase):
    def test_a_new_background_restyles(self):
        Adw.init()
        from sonata2.ui import theme
        theme.setup()
        theme._remember_appearance()
        with mock.patch.object(theme, "_load") as load:
            config.save("appearance", {"background": {"light": "#efe8dc"}})
            theme._appearance_changed()
        self.assertTrue(load.called)


if __name__ == "__main__":
    unittest.main()
