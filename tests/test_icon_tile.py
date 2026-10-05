"""Vini: Claude's Dock icon showed a thin border of its own tile -- drawn
small on a plate of its colour. An icon with a tile of its own now fills the
frame, its edge cut off outside it."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, Gtk  # noqa: E402

from sonata2 import icons  # noqa: E402


def tile_png(path, margin=20, size=256):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((margin, margin, size - margin, size - margin), radius=50,
                                         fill=(217, 119, 87, 255), outline=(190, 95, 66, 255), width=3)
    im.save(path)


class TileTest(unittest.TestCase):
    def test_box(self):
        import tempfile
        p = tempfile.mktemp(suffix=".png")
        tile_png(p)
        box = icons.tile_box(GdkPixbuf.Pixbuf.new_from_file(p))
        self.assertAlmostEqual(box[0], 20 / 256, places=2)
        self.assertAlmostEqual(box[2], 217 / 256, places=2)

    def test_plate_fills_the_frame(self):
        import tempfile
        p = tempfile.mktemp(suffix=".png")
        tile_png(p)
        plate = icons._Plate(Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(p), 256, 1), 128)
        self.assertNotEqual(plate.color, icons.PLATE_WHITE)
        self.assertIsNotNone(plate.tile)
        self.assertGreater(icons.TILE_BLEED, 1.0)

    def test_white_tile_fills_too(self):
        """Claude's real icon: a white squircle around its orange tile (the white
        squircle showed as a second border inside the frame)."""
        import tempfile
        from PIL import Image, ImageDraw
        p = tempfile.mktemp(suffix=".png")
        im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((16, 16, 240, 240), radius=56, fill=(250, 250, 250, 255))
        d.rounded_rectangle((60, 60, 196, 196), radius=30, fill=(217, 119, 87, 255))
        im.save(p)
        plate = icons._Plate(Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(p), 256, 1), 128)
        self.assertEqual(plate.color, icons.PLATE_WHITE)
        self.assertIsNotNone(plate.tile)
        self.assertGreaterEqual(icons.PLATE_VERSION, 3)            # cached plates made again

    def test_logo_on_transparency_untouched(self):
        import tempfile
        from PIL import Image, ImageDraw
        p = tempfile.mktemp(suffix=".png")
        im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((80, 80, 176, 176), fill=(30, 120, 220, 255))
        im.save(p)
        plate = icons._Plate(Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(p), 256, 1), 128)
        self.assertEqual(plate.color, icons.PLATE_WHITE)
        self.assertIsNone(plate.tile)


if __name__ == "__main__":
    unittest.main()
