"""Vini: icons with a tile of their own (FilmCraft's purple square, a
squircle), a disc (BYOD's) or a full square looked small on the white
plate. Their shape is found from their transparency: a tile or a square
fills the frame, a disc sits on a plate of its own edge's colour; anything
else (an amplifier, a logo) stays on the plate."""
import os
import tempfile
import unittest

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from sonata2 import icons  # noqa: E402

N = 64


def pb_of(im):
    path = os.path.join(tempfile.mkdtemp(), "i.png")
    im.save(path)
    return GdkPixbuf.Pixbuf.new_from_file(path)


def canvas():
    return Image.new("RGBA", (N, N), (0, 0, 0, 0))


class ShapeTest(unittest.TestCase):
    def test_rounded_tile(self):
        im = canvas()
        ImageDraw.Draw(im).rounded_rectangle((4, 4, 59, 59), radius=12, fill=(130, 90, 230, 255))
        ImageDraw.Draw(im).ellipse((20, 20, 44, 44), fill=(240, 240, 240, 255))   # what's on it
        self.assertEqual(icons.shape_of(pb_of(im)), ("tile", "#825ae6"))

    def test_tile_with_something_past_its_edge(self):
        """FilmCraft: the owl goes over the tile's bottom edge -- still a tile."""
        im = canvas()
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((4, 4, 59, 59), radius=12, fill=(130, 90, 230, 255))
        d.rectangle((24, 40, 40, 59), fill=(200, 200, 200, 255))
        self.assertEqual(icons.shape_of(pb_of(im))[0], "tile")

    def test_disc(self):
        im = canvas()
        ImageDraw.Draw(im).ellipse((2, 2, 61, 61), fill=(40, 40, 40, 255))
        ImageDraw.Draw(im).text((14, 26), "chow", fill=(255, 255, 255, 255))
        self.assertEqual(icons.shape_of(pb_of(im)), ("circle", "#282828"))

    def test_full_square(self):
        im = Image.new("RGBA", (N, N), (20, 120, 220, 255))
        self.assertEqual(icons.shape_of(pb_of(im)), ("square", "#1478dc"))

    def test_objects_and_logos_stay_on_the_plate(self):
        amp = canvas()
        ImageDraw.Draw(amp).rectangle((6, 14, 57, 50), fill=(30, 30, 30, 255))      # wide: not a tile
        self.assertIsNone(icons.shape_of(pb_of(amp)))
        logo = canvas()
        ImageDraw.Draw(logo).polygon([(32, 4), (60, 58), (4, 58)], fill=(200, 30, 30, 255))
        self.assertIsNone(icons.shape_of(pb_of(logo)))
        small = canvas()
        ImageDraw.Draw(small).rounded_rectangle((22, 22, 41, 41), radius=4, fill=(0, 200, 0, 255))
        self.assertIsNone(icons.shape_of(pb_of(small)))

    def test_plate_uses_it(self):
        src = open(icons.__file__).read()
        part = src[src.index("class _Plate"):src.index("def tile_box")]
        self.assertIn("_shape(inner)", part)
        self.assertIn("CIRCLE_SIZE", part)
        self.assertGreaterEqual(icons.PLATE_VERSION, 11)        # icons made again


if __name__ == "__main__":
    unittest.main()
