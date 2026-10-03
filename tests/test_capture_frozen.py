"""Screenshots of an open menu (Vini: the menu bar's or a right-click menu
closed on the shortcut -- the portion selection / the list took the
keyboard first). The picture comes from the screen frozen at the start."""
import unittest
from unittest import mock

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from sonata2.shell import capture as C  # noqa: E402

OUTS = [{"name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 40, "height": 20}},
        {"name": "eDP-1", "geometry": {"x": 40, "y": 0, "width": 20, "height": 20}}]


def screen(scale=1):
    """Left display red, right display blue."""
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 60 * scale, 20 * scale)
    pb.fill(0xff0000ff)
    pb.new_subpixbuf(40 * scale, 0, 20 * scale, 20 * scale).fill(0x0000ffff)
    return pb


def rgb(pb, x, y):
    px = pb.get_pixels()
    i = y * pb.get_rowstride() + x * pb.get_n_channels()
    return tuple(px[i:i + 3])


class FrozenTest(unittest.TestCase):
    def test_crop_and_display(self):
        f = C.Frozen(screen(), OUTS)
        part = f.crop("35,5 10x10")                       # across both displays
        self.assertEqual((part.get_width(), part.get_height()), (10, 10))
        self.assertEqual(rgb(part, 0, 0), (255, 0, 0))
        self.assertEqual(rgb(part, 9, 9), (0, 0, 255))
        edp = f.output("eDP-1")
        self.assertEqual((edp.get_width(), rgb(edp, 0, 0)), (20, (0, 0, 255)))
        self.assertIsNone(f.crop("100,100 5x5"))           # off screen
        self.assertIsNone(f.crop("nonsense"))

    def test_scaled_screen(self):
        f = C.Frozen(screen(2), OUTS)                     # grim: the largest display scale
        part = f.crop("38,0 4x2")
        self.assertEqual((part.get_width(), part.get_height()), (8, 4))
        self.assertEqual((rgb(part, 0, 0), rgb(part, 7, 0)), ((255, 0, 0), (0, 0, 255)))

    def test_frozen_before_the_list_and_the_selection(self):
        """The screen is frozen before anything takes the keyboard, and the
        shot is cut from it; a recording stays live."""
        order = []
        frozen = C.Frozen(screen(), OUTS)
        cap = C.Capture(None, None)
        cap._pick = lambda *a: order.append("list")
        cap._shoot = lambda geo, cfg, output=None, frozen=None: order.append(("shot", geo, frozen))
        cap._record = lambda geo, cfg, output=None: order.append(("rec", geo))
        with mock.patch.object(C.Frozen, "take", side_effect=lambda: (order.append("freeze"), frozen)[1]), \
             mock.patch.object(C, "outputs", return_value=OUTS), \
             mock.patch.object(C, "_ipc") as ipc, \
             mock.patch.object(C, "_FrozenCover") as cover, \
             mock.patch.object(C.shutil, "which", return_value="/usr/bin/x"), \
             mock.patch.object(C, "_slurp_async", side_effect=lambda a, done: (order.append("select"),
                                                                                done("1,2 3x4"))), \
             mock.patch.object(C.GLib, "timeout_add", side_effect=lambda ms, fn: fn()):
            ipc.return_value.call.return_value = []
            cap._run("display", {})
            self.assertEqual(order[:2], ["freeze", "list"])
            order.clear()
            cap._run("area", {})
            self.assertEqual(order, ["freeze", "select", ("shot", "1,2 3x4", frozen)])
            cover.return_value.close.assert_called_once()
            order.clear()
            cap._run("rec-area", {})
            self.assertNotIn("freeze", order)

    def test_shortcut_shoots_at_once(self):
        """No toolbar to hide: no wait (the menu is still open)."""
        cap = C.Capture(None, None)
        with mock.patch.object(C.GLib, "timeout_add") as t:
            cap.run("area", {"timer": 0})
        self.assertEqual(t.call_args[0][0], 0)


if __name__ == "__main__":
    unittest.main()
