"""Vini: the window buttons' colours in Settings -- Colourful (default),
Graphite, Black & White (black on light, white on dark) and Custom (a colour
per button). Every place draws the same pictures: Sonata's windows, other
GTK 4 apps, Wayfire's title bars, Steam."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

from sonata2 import config, trafficlights as T  # noqa: E402


def look(style, colors=None):
    config.update("appearance", buttons_style=style, buttons_colors=colors or {})


class ColorsTest(unittest.TestCase):
    def tearDown(self):
        look("color")

    def test_four_looks(self):
        from sonata2.ui.tokens import TL_COLORS
        self.assertEqual(T.colors(False, "color"), dict(TL_COLORS))
        self.assertEqual(set(T.colors(True, "graphite").values()), {T.GRAPHITE})
        self.assertEqual(set(T.colors(False, "mono").values()), {"#1d1d1f"})      # black on light
        self.assertEqual(set(T.colors(True, "mono").values()), {"#f5f5f7"})       # white on dark
        c = T.colors(False, "custom", {"close": "#123456", "minimize": "#abcdef", "maximize": "#00ff00",
                                       "restore": "#00ff00"})
        self.assertEqual(c["close"], "#123456")

    def test_custom_fills_gaps_and_restore_follows_zoom(self):
        from sonata2.ui.tokens import TL_COLORS
        look("custom", {"maximize": "#336699"})
        c = T.custom()
        self.assertEqual(c["close"], TL_COLORS["close"])
        self.assertEqual(c["restore"], "#336699")

    def test_unknown_style_is_colourful(self):
        look("neon")
        self.assertEqual(T.style(), "color")

    def test_glyph_readable_on_every_dot(self):
        self.assertEqual(T.glyph_rgba("#f5f5f7")[:3], (0, 0, 0))     # white dot: dark glyph
        self.assertEqual(T.glyph_rgba("#1d1d1f")[:3], (1, 1, 1))     # black dot: light glyph


class PicturesTest(unittest.TestCase):
    def tearDown(self):
        look("color")

    def test_colourful_keeps_the_bundled_pictures(self):
        look("color")
        self.assertTrue(T.folder(False).endswith(os.path.join("apps", "scalable")))
        self.assertTrue(T.png_folder(False).endswith(os.path.join("data", "decor")))
        self.assertEqual(T.css_rules(False), "")

    def test_mono_light_and_dark_are_drawn(self):
        from PIL import Image
        look("mono")
        light, dark = T.folder(False), T.folder(True)
        self.assertNotEqual(light, dark)
        for d, want in ((light, 0x1d), (dark, 0xf5)):
            for n in T.NAMES:
                for suf in ("", "-hover"):
                    self.assertTrue(os.path.isfile(os.path.join(d, f"sonata-tl-{n}{suf}.svg")))
                    self.assertTrue(os.path.isfile(os.path.join(d, f"{n}{suf}.png")))
            im = Image.open(os.path.join(d, "close.png")).convert("RGBA")
            w, h = im.size
            r, g, b, a = im.getpixel((w // 2, h // 2))
            self.assertEqual(a, 255)
            self.assertAlmostEqual(r, want, delta=3)
        hover = Image.open(os.path.join(light, "close-hover.png")).convert("RGBA")
        plain = Image.open(os.path.join(light, "close.png")).convert("RGBA")
        self.assertNotEqual(hover.tobytes(), plain.tobytes())   # the glyph shows

    def test_a_new_custom_colour_gets_new_pictures(self):
        look("custom", {"close": "#112233"})
        a = T.folder(False)
        look("custom", {"close": "#445566"})
        self.assertNotEqual(a, T.folder(False))

    def test_css_and_wayfire_point_at_the_look(self):
        look("graphite")
        d = T.folder(False)
        css = T.css_rules(False)
        self.assertIn(os.path.join(d, "sonata-tl-close-hover.svg"), css)
        self.assertIn(".traffic button.tl-zoom", css)
        opts = dict(((s, k), v) for s, k, v in T.wayfire_options(False))
        self.assertEqual(opts[("pixdecor", "button_close_image")], os.path.join(d, "close.png"))
        self.assertEqual(len(opts), 2 * len(T.NAMES))


class WiringTest(unittest.TestCase):
    def test_every_place_uses_the_look(self):
        import inspect
        from sonata2 import adwstyle, icons, steamtheme, titlebars
        from sonata2.settings import app
        self.assertEqual(icons.APPEARANCE_DEFAULTS["buttons_style"], "color")
        self.assertIn("trafficlights.folder", inspect.getsource(adwstyle))
        self.assertIn("trafficlights.folder", inspect.getsource(steamtheme))
        self.assertIn("trafficlights.apply_wayfire", inspect.getsource(titlebars.apply_colors))
        src = inspect.getsource(app)
        self.assertIn('"buttons_style", "buttons_colors"', src)
        self.assertIn("Button colours", src)


class SettingsRowTest(unittest.TestCase):
    def test_choosing_custom_shows_the_dots_and_applies(self):
        from unittest import mock
        from gi.repository import Adw, GLib
        from sonata2 import ui
        from sonata2.settings import app as S
        look("color")
        Adw.init()
        ui.setup()
        win = S.Settings(None)
        win.present()
        end = GLib.get_monotonic_time() + 300_000
        while GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        win.select("appearance", from_sidebar=True)
        self.assertFalse(win.button_custom_row.get_visible())
        self.assertEqual(set(win.button_dots), {"close", "minimize", "maximize"})
        with mock.patch.object(S.system, "run_async") as run:
            win._set_button_style("custom")
        self.assertTrue(win.button_custom_row.get_visible())
        self.assertEqual(T.style(), "custom")
        self.assertIs(run.call_args[0][0], T.apply)
        win.destroy()
        look("color")


if __name__ == "__main__":
    unittest.main()
