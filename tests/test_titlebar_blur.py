"""Other apps' windows aren't blurred behind (python3 -m unittest tests.test_titlebar_blur).
Regression: every window was blurred on every frame of a move/resize (heavy;
the blur pass is also where the Radeon 680M hung)."""
import os
import unittest
from unittest import mock

from sonata2 import titlebars


class BlurTest(unittest.TestCase):
    def test_only_sonata_surfaces(self):
        self.assertNotIn("toplevel", titlebars.BLUR)
        self.assertIn('app_id contains "sonata2"', titlebars.BLUR)
        ini = open(os.path.join(os.path.dirname(__file__), "..", "config", "wayfire.ini")).read()
        self.assertIn("blur_by_default = " + titlebars.BLUR, ini)

    def test_blur_is_light(self):
        """The live blur: 2 passes on a third-size picture (was 3 on half-size), same radius."""
        import configparser
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(os.path.join(os.path.dirname(__file__), "..", "config", "wayfire.ini"))
        b = cp["blur"]
        it, deg, off = int(b["kawase_iterations"]), int(b["kawase_degrade"]), float(b["kawase_offset"])
        self.assertLessEqual(it, 2)
        self.assertGreaterEqual(deg, 3)
        self.assertAlmostEqual(off * 2 ** it * deg, 3.5 * 8 * 2, delta=6)      # about the same frosting

    def test_apply_colors_sets_it(self):
        calls = {}
        with mock.patch("sonata2.backend.system.wayfire_set",
                        side_effect=lambda sec, key, val: calls.__setitem__((sec, key), val)), \
                mock.patch.object(titlebars, "glass_bars", return_value=False):
            for dark in (False, True):
                titlebars.apply_colors(dark)
                # each glass part's own rule (Settings > Appearance > Glass); never every window
                from sonata2.ui import glass as G
                self.assertEqual(calls[("blur", "blur_by_default")], G.blur_rule(G.settings()))
                self.assertNotIn('type is "toplevel"', calls[("blur", "blur_by_default")])

    def test_glass_title_bars_toggle(self):
        """Settings > Appearance > Glass title bars (off by default): every window
        blurred again, see-through bars, GNOME apps' header band too."""
        from sonata2 import adwstyle, icons
        from sonata2.ui import tokens
        self.assertFalse(icons.APPEARANCE_DEFAULTS["glass_titlebars"])
        calls = {}
        with mock.patch("sonata2.backend.system.wayfire_set",
                        side_effect=lambda sec, key, val: calls.__setitem__((sec, key), val)), \
                mock.patch.object(titlebars, "glass_bars", return_value=True):
            titlebars.apply_colors(True)
        from sonata2.ui import glass as G
        self.assertEqual(calls[("blur", "blur_by_default")], G.blur_rule(G.settings()) + ' | type is "toplevel"')
        glass = tokens.wayfire_color(tokens.palette(True)["titlebar_glass"], premultiplied=True)
        self.assertEqual(calls[("pixdecor", "fg_color")], "\\" + glass)
        css = adwstyle.css("/x", bars=True, glass=True)
        self.assertIn(tokens.palette(True)["titlebar_glass"], css)
        self.assertIn(f"transparent {adwstyle.ADW_HEADER_H}px", css)      # only the header's band
        plain = adwstyle.css("/x", bars=True, glass=False)
        self.assertNotIn("background-color: transparent; background-image", plain)


if __name__ == "__main__":
    unittest.main()
