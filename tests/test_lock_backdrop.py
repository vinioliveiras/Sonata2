"""Lock / login backdrop and column (Vini): the lock screen's background
once showed a ghost of the Claude window -- now the wallpaper is blurred
once on the CPU (a small, finished picture; no GPU blur pass), looking the
same; and the picture, name and password sit a little higher."""
import inspect
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sonata2.shell import greeter, lock, loginui as L  # noqa: E402


def texture(w=640, h=400):
    data = bytes((x * 255 // w) for _y in range(h) for x in range(w) for _c in range(3))
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(data), w * 3)


class BackdropTest(unittest.TestCase):
    def test_blurred_small_picture(self):
        Gtk.init()
        b = L.blurred(texture(), 1920, 1080, 64)
        self.assertIsNotNone(b)
        self.assertEqual((b.get_width(), b.get_height()), (1920 // L.BLUR_SCALE, 1080 // L.BLUR_SCALE))

    def test_snapshot_uses_it_once_per_size(self):
        Gtk.init()
        bd = L.Backdrop(texture())
        with mock.patch.object(L, "blurred", wraps=L.blurred) as bl, \
                mock.patch.object(bd, "get_width", return_value=800), \
                mock.patch.object(bd, "get_height", return_value=500):
            bd.do_snapshot(Gtk.Snapshot())
            bd.do_snapshot(Gtk.Snapshot())
        self.assertEqual(bl.call_count, 1)

    def test_no_pillow_falls_back(self):
        Gtk.init()
        bd = L.Backdrop(texture())
        with mock.patch.object(L, "blurred", return_value=None), \
                mock.patch.object(bd, "get_width", return_value=800), \
                mock.patch.object(bd, "get_height", return_value=500):
            snap = Gtk.Snapshot()
            bd.do_snapshot(snap)
            self.assertIsNotNone(snap.to_node())

    def test_lock_decodes_a_small_wallpaper(self):
        self.assertIn("wallpaper_texture(LOCK_WALLPAPER_PX)", inspect.getsource(lock))


class LiftTest(unittest.TestCase):
    def test_lift_margin(self):
        Gtk.init()
        mon = mock.Mock()
        mon.get_geometry.return_value = mock.Mock(height=1000)
        w = L.lift(Gtk.Box(), mon)
        self.assertEqual(w.get_margin_bottom(), int(1000 * L.COLUMN_LIFT))
        self.assertEqual(L.lift(Gtk.Box(), None).get_margin_bottom(), 0)

    def test_lock_and_login_use_it(self):
        self.assertIn('lift(parts["column"], monitor)', inspect.getsource(lock))
        self.assertIn("lift(center, monitor)", inspect.getsource(greeter))


if __name__ == "__main__":
    unittest.main()
