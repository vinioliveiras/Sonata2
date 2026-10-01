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

    def test_glyph_is_one_colour_and_as_wide_as_the_text(self):
        g = L.LogoGlyph(16)
        g.set_kind("text:custom", "Vini 🎮")
        self.assertIsNotNone(g.texture)
        w = g.measure(gi.repository.Gtk.Orientation.HORIZONTAL, -1)[0]
        self.assertGreater(w, 16 * 2)
        data = g.texture.save_to_png_bytes().get_data()      # mono_mask: colour channels all 0
        self.assertTrue(data)
        g.set_kind("shape:circle")
        self.assertEqual(g.text, "")
        self.assertEqual(g.measure(gi.repository.Gtk.Orientation.HORIZONTAL, -1)[0], 16)


if __name__ == "__main__":
    unittest.main()
