"""Menu bar logo as text: Sonata, your name, your own words with emoji in
one colour (xvfb-run python3 -m unittest tests.test_menu_logo)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import logo as L  # noqa: E402


class TextLogoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def test_choices(self):
        values = [v for v, _l in L.choices()]
        for kind in ("text:sonata", "text:user", "text:custom"):
            self.assertIn(kind, values)

    def test_words(self):
        self.assertEqual(L.text_for("text:sonata"), "Sonata")
        with mock.patch.object(GLib, "get_real_name", return_value="Vinicius Oliveira"):
            self.assertEqual(L.text_for("text:user"), "Vinicius")
        with mock.patch.object(GLib, "get_real_name", return_value="Unknown"), \
                mock.patch.object(GLib, "get_user_name", return_value="vini"):
            self.assertEqual(L.text_for("text:user"), "vini")
        self.assertEqual(L.text_for("text:custom", "  Hi\n there 🎮 "), "Hi there 🎮")
        self.assertEqual(len(L.text_for("text:custom", "x" * 100)), L.TEXT_MAX)
        self.assertEqual(L.text_for("text:custom", "   "), "Sonata")
        self.assertEqual(L.text_for("distro"), "")

    def test_emoji_solid_with_holes(self):
        """A light face with dark eyes: the face solid (no greys), the eyes holes."""
        w = h = 10
        px = bytearray(w * h * 4)
        for i in range(w * h):
            px[i * 4:i * 4 + 4] = bytes((255, 210, 40, 255))      # yellow face
        for x0 in (1, 6):                                         # dark eyes, 3 x 3 each
            for x in range(x0, x0 + 3):
                for y in range(2, 5):
                    i = (y * w + x) * 4
                    px[i:i + 4] = bytes((20, 20, 20, 255))
        out = L.one_colour(px, w * 4, w, h, [(0, w)])
        self.assertEqual(out[(7 * w + 5) * 4 + 3], 255)
        self.assertEqual(out[(3 * w + 2) * 4 + 3], 0)
        letters = bytearray(px)
        for i in range(w * h):
            letters[i * 4:i * 4 + 4] = bytes((0, 0, 0, 255))      # plain text: one tone, all solid
        self.assertTrue(all(L.one_colour(letters, w * 4, w, h, [(0, w)])[i * 4 + 3] == 255 for i in range(w * h)))

    def test_glyph_is_one_colour_and_as_wide_as_the_text(self):
        g = L.LogoGlyph(16)
        g.set_kind("text:custom", "Vini 🎮")
        self.assertIsNotNone(g.texture)
        w = g.measure(gi.repository.Gtk.Orientation.HORIZONTAL, -1)[0]
        self.assertGreater(w, 16 * 2)
        data = g.texture.save_to_png_bytes().get_data()      # mono_mask: colour channels all 0
        self.assertTrue(data)
        # drawn at the menus' own text size, not scaled up (it looked bigger than the menu titles)
        self.assertEqual(w, g.text_size[0])
        self.assertLessEqual(g.text_size[1], 18)
        g.set_kind("shape:circle")
        self.assertEqual(g.text, "")
        self.assertEqual(g.measure(gi.repository.Gtk.Orientation.HORIZONTAL, -1)[0], 16)


    def test_menu_bar_logo_size(self):
        """Vini: the Sonata menu's icon a bit smaller than the 16 px status icons."""
        from sonata2.shell import topbar
        self.assertEqual(topbar.LOGO_PX, 12)
        g = L.LogoGlyph(topbar.LOGO_PX)
        g.set_kind("shape:circle")
        self.assertEqual(g.measure(gi.repository.Gtk.Orientation.HORIZONTAL, -1)[0], 12)


    def test_emoji_runs(self):
        self.assertEqual(L.emoji_runs("Vini 🎮"), [(5, 9)])
        self.assertEqual(L.emoji_runs("🍎"), [(0, 4)])
        self.assertEqual(L.emoji_runs("❤️ ok"), [(0, 6)])          # with its presentation selector
        self.assertEqual(L.emoji_runs("Sonata"), [])

    def test_emoji_the_logo_size(self):
        """An emoji came out ~17 px next to 12 px icons: now about the logo's size."""
        g = L.LogoGlyph(12)
        g.set_kind("text:custom", "🍎")
        self.assertLessEqual(g.text_size[1], 15)
        self.assertLessEqual(g.text_size[0], 15)


if __name__ == "__main__":
    unittest.main()
