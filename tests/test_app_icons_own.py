"""Vini: Calculator and Videos get Sonata's own icons (the soft, full-tile
look); Videos' old one imitated another company's logo."""
import os
import unittest

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "sonata2")
ICONS = os.path.join(ROOT, "data", "icons", "Sonata", "apps", "scalable")


class OwnIconsTest(unittest.TestCase):
    def test_icons_render_and_are_used(self):
        for name, mod in (("sonata-calculator", "calculator"), ("sonata-videos", "videos")):
            path = os.path.join(ICONS, name + ".svg")
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, 64, 64)
            self.assertEqual((pb.get_width(), pb.get_height()), (64, 64))
            with open(os.path.join(ROOT, mod, "window.py"), encoding="utf-8") as f:
                self.assertIn(f"Icon={name}\\n", f.read())


class StoreIconTest(unittest.TestCase):
    """Vini: Bazaar wears Sonata's own store icon (a frosted glass bag on
    the blue tile), not a copy of another company's."""

    def test_bazaar(self):
        for name in ("io.github.kolunmi.Bazaar", "Bazaar"):
            path = os.path.join(ICONS, name + ".svg")
            with open(path, encoding="utf-8") as f:
                self.assertIn("frosted glass", f.read())
            GdkPixbuf.Pixbuf.new_from_file_at_size(path, 64, 64)


if __name__ == "__main__":
    unittest.main()
