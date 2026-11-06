"""Now Playing's art: an app's icon handed over as the art (Chrome's, for
Netflix) is drawn smaller in the square, not stretched over it (Vini: the
Chrome icon far too big). Clear corners say it's an icon."""
import unittest

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

try:
    from sonata2.shell import topbar as T  # noqa: E402
except (ImportError, ValueError, RuntimeError) as e:     # (no GTK here)
    T = None
    _why = str(e)


@unittest.skipIf(T is None, "topbar needs GTK")
class NowPlayingArtTest(unittest.TestCase):
    def test_chrome_icon_too_big_in_now_playing(self):
        icon = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 64)
        icon.fill(0x00000000)
        icon.new_subpixbuf(8, 8, 48, 48).fill(0x3080ffff)
        self.assertTrue(T.corners_clear(icon))
        self.assertEqual(T.art_size(36, T.corners_clear(icon)), 24)
        cover = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 64)
        cover.fill(0x202020ff)
        self.assertFalse(T.corners_clear(cover))
        self.assertEqual(T.art_size(36, T.corners_clear(cover)), 36)
        jpeg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 64)
        self.assertFalse(T.corners_clear(jpeg))


if __name__ == "__main__":
    unittest.main()
