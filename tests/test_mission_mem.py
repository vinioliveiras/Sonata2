"""Memory review: Mission Control's backdrop decoded the wallpaper at full
size (3840x2560: ~39 MB per Light/Dark picture, both kept all session) to
draw it under blur 40. Now a quarter of the display, the current picture
only -- and it must look the same on screen.
Run: xvfb-run -a python3 -m unittest tests.test_mission_mem"""
import random
import tempfile
import unittest
from unittest import mock

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gio, Graphene, Gsk, Gtk  # noqa: E402

from sonata2.shell import loginui, mission  # noqa: E402


def picture(w=3840, h=2560):
    """A wallpaper with what a blur could betray: a gradient, sharp-edged
    blocks and specks of white."""
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    rnd = random.Random(1)
    g = cairo.LinearGradient(0, 0, w, h)
    g.add_color_stop_rgb(0, .1, .2, .6)
    g.add_color_stop_rgb(1, .9, .5, .1)
    cr.set_source(g)
    cr.paint()
    for _ in range(300):
        cr.set_source_rgb(rnd.random(), rnd.random(), rnd.random())
        cr.rectangle(rnd.random() * w, rnd.random() * h, rnd.random() * 300 + 2, rnd.random() * 300 + 2)
        cr.fill()
    cr.set_source_rgb(1, 1, 1)
    for _ in range(2000):
        cr.rectangle(rnd.random() * w, rnd.random() * h, 2, 2)
        cr.fill()
    path = tempfile.mktemp(suffix=".png")
    surf.write_to_png(path)
    return path


def render(tex, w, h):
    b = loginui.Backdrop(tex, dim=0.32, blur=40)            # Mission Control's look
    b.get_width, b.get_height = (lambda: w), (lambda: h)
    snap = Gtk.Snapshot()
    b.do_snapshot(snap)
    r = Gsk.CairoRenderer()
    r.realize(None)
    out = r.render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    r.unrealize()
    d = Gdk.TextureDownloader.new(out)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    return d.download_bytes()[0].get_data()


class FakeMonitor:
    def __init__(self, w, h, scale=1):
        self.g, self.scale = Gdk.Rectangle(), scale
        self.g.width, self.g.height = w, h

    def get_geometry(self):
        return self.g

    def get_scale_factor(self):
        return self.scale


class MissionMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        cls.path = picture()

    def test_size(self):
        self.assertEqual(mission.backdrop_size([FakeMonitor(2560, 1600)]), 640)
        self.assertEqual(mission.backdrop_size([FakeMonitor(1920, 1080), FakeMonitor(1440, 900, 2)]), 720)
        self.assertEqual(mission.backdrop_size([FakeMonitor(1280, 800)]), mission.BACKDROP_MIN)
        self.assertEqual(mission.backdrop_size([]), mission.BACKDROP_FALLBACK)

    def test_decoded_small_never_upscaled(self):
        f = Gio.File.new_for_path(self.path)
        small = loginui._decode_wallpaper(f, 640)
        self.assertEqual((small.get_width(), small.get_height()), (640, 427))
        self.assertEqual(loginui._decode_wallpaper(f, 0).get_width(), 3840)     # lock / login: full size
        self.assertEqual(loginui._decode_wallpaper(f, 8000).get_width(), 3840)

    def test_looks_the_same_under_the_blur(self):
        f = Gio.File.new_for_path(self.path)
        w, h = 2560, 1600
        full = render(loginui._decode_wallpaper(f, 0), w, h)
        small = render(loginui._decode_wallpaper(f, mission.backdrop_size([FakeMonitor(w, h)])), w, h)
        diffs = [abs(a - b) for a, b in zip(full[::31], small[::31])]
        self.assertLessEqual(max(diffs), 6)                    # of 255: invisible (CPU-blurred, lock_backdrop)
        self.assertLess(sum(diffs) / len(diffs), 0.5)

    def test_only_the_current_variant_kept(self):
        with mock.patch.object(mission.layer, "layer_shell", return_value=None):
            mb = mission.MissionBackdrop(None)
        sm = mock.Mock()
        with mock.patch.object(mission, "wallpaper_texture", side_effect=lambda **k: object()) as wt, \
                mock.patch.object(mission.Adw.StyleManager, "get_default", return_value=sm):
            sm.get_dark.return_value = False
            light = mb._wallpaper()
            sm.get_dark.return_value = True
            mb._wallpaper()
            self.assertEqual(list(mb._textures), [True])         # the light picture let go
            self.assertGreater(wt.call_args.kwargs["max_size"], 0)
            sm.get_dark.return_value = False
            self.assertIsNot(mb._wallpaper(), light)


if __name__ == "__main__":
    unittest.main()
