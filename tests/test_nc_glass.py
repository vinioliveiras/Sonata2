"""Vini: Notification Center's glass, as macOS's -- behind the whole column
the blurred display, fading out to nothing to the left; blurred more, with
its colours lifted. Still a picture blurred once as it opens (frost.py),
never the compositor's blur per frame.

    xvfb-run -a python3 -m unittest tests.test_nc_glass
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gsk, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import frost  # noqa: E402
from sonata2.shell import notifications as N  # noqa: E402


def solid(w, h, rgb):
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes(rgb) * (w * h)), w * 3)


def pixel(tex, x, y):
    d = Gdk.TextureDownloader.new(tex)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = d.download_bytes()
    raw = data.get_data()
    return tuple(raw[y * stride + x * 4:y * stride + x * 4 + 4])


def nodes(node):
    """Every node of a render tree, depth first."""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        out.append(n)
        t = n.get_node_type()
        if t == Gsk.RenderNodeType.CONTAINER_NODE:
            stack.extend(n.get_child(i) for i in range(n.get_n_children()))
        elif t == Gsk.RenderNodeType.MASK_NODE:
            stack.extend([n.get_source(), n.get_mask()])
        elif hasattr(n, "get_child"):
            stack.append(n.get_child())
    return out


class GlassLookTest(unittest.TestCase):
    def test_glass_washed_out(self):
        """Its picture was blurred as the Game Bar's and looked flat: now
        blurred more and more vivid -- a colour more saturated and a little
        brighter, a grey only brighter. The Game Bar's is unchanged."""
        s, b = frost.NC_VIBRANCY
        self.assertGreater(s, 1)
        self.assertGreater(b, 1)
        self.assertGreater(frost.NC_BLUR, frost.FROST_BLUR)
        rgb = (200 / 255, 100 / 255, 60 / 255)
        out = frost.vibrant(rgb, s, b)
        self.assertGreater(out[0] - out[2], rgb[0] - rgb[2])
        self.assertGreater(out[0], rgb[0])
        for c in frost.vibrant((0.5, 0.5, 0.5), s, b):
            self.assertAlmostEqual(c, 0.5 * b)
        self.assertEqual(frost.blur_texture.__defaults__, (frost.FROST_BLUR, None))

    def test_vibrancy_drawn_as_computed(self):
        """GSK applies the matrix as vibrant() says (its row/column order)."""
        Gtk.init()
        r = Gsk.CairoRenderer()
        r.realize_for_display(Gdk.Display.get_default())
        try:
            tex = solid(64, 64, (200, 100, 60))
            want = frost.vibrant((200 / 255, 100 / 255, 60 / 255), *frost.NC_VIBRANCY)
            got = pixel(frost.blur_texture(r, tex, frost.NC_BLUR, frost.NC_VIBRANCY), 8, 8)
            for g, w in zip(got, want):
                self.assertLessEqual(abs(g - w * 255), 2)
            plain = pixel(frost.blur_texture(r, tex), 8, 8)
            self.assertEqual(plain[:3], (200, 100, 60))
        finally:
            r.unrealize()

    def test_capture_passes_the_look_on(self):
        """capture() blurs with the look it is given (Notification Center's)."""
        proc = mock.Mock()
        proc.communicate_finish.return_value = (True, b"x", b"")
        with mock.patch.object(frost, "available", return_value=True), \
                mock.patch.object(frost.Gio.Subprocess, "new") as new, \
                mock.patch.object(frost, "texture_from", return_value="tex"), \
                mock.patch.object(frost, "blur_texture", side_effect=lambda *a: a) as blur:
            frost.capture("DP-1", lambda t: None, lambda: "renderer", blur=frost.NC_BLUR,
                          vibrancy=frost.NC_VIBRANCY)
            new.return_value.communicate_async.call_args[0][2](proc, None)
        blur.assert_called_once_with("renderer", "tex", frost.NC_BLUR, frost.NC_VIBRANCY)


class BackdropTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        owner = N.Notifications.__new__(N.Notifications)
        owner.notes, owner.listeners, owner._banners, owner._conn, owner.nc = [], [], {}, None, None
        self.c = N._Center(None, owner)
        self.c._rebuild()
        w = self.c.rev.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        self.w, self.h = w, 900
        self.c.rev.allocate(w, self.h, -1, None)

    def tearDown(self):
        self.c.destroy()

    def test_glass_only_under_the_panels(self):
        """Vini: the glass was only under each card and the calendar, the
        rest of the screen sharp around them. Behind the whole column now,
        the blurred display: from under the menu bar down, fully there from
        the column's left edge, fading to nothing BACKDROP_FADE px left of
        it."""
        (x, y, w, h), (x0, x1) = N.backdrop_geometry(400, 900, 32)
        self.assertEqual((x, y, w, h), (-N.BACKDROP_FADE, 32, 400 + N.BACKDROP_FADE, 868))
        self.assertEqual((x0, x1), (-N.BACKDROP_FADE, 0))
        self.c.frost_tex = solid(4, 4, (10, 20, 30))
        with mock.patch.object(self.c, "get_width", return_value=1920), \
                mock.patch.object(self.c, "get_height", return_value=self.h):
            snap = Gtk.Snapshot()
            self.assertTrue(N.backdrop(snap, self.c.rev, self.c, self.c.frost_tex))
        node = snap.to_node()
        masks = [n for n in nodes(node) if n.get_node_type() == Gsk.RenderNodeType.MASK_NODE]
        self.assertEqual(len(masks), 1)
        fade = masks[0].get_mask()
        self.assertEqual(fade.get_node_type(), Gsk.RenderNodeType.LINEAR_GRADIENT_NODE)
        self.assertEqual(fade.get_color_stops()[0].color.alpha, 0)    # transparent on the left
        self.assertEqual(fade.get_color_stops()[-1].color.alpha, 1)   # the whole picture at the column
        b = node.get_bounds()
        self.assertAlmostEqual(b.get_x(), -N.BACKDROP_FADE)
        self.assertAlmostEqual(b.get_x() + b.get_width(), self.w)

    def test_no_picture_no_backdrop(self):
        """No grim, no picture: solid panels and nothing behind them."""
        self.c.frost_tex = None
        with mock.patch.object(N, "backdrop") as drawn:
            self.c.rev.do_snapshot(Gtk.Snapshot())
        drawn.assert_not_called()
        self.c.frost_tex = solid(4, 4, (10, 20, 30))
        with mock.patch.object(N, "backdrop") as drawn:
            self.c.rev.do_snapshot(Gtk.Snapshot())
        drawn.assert_called_once()

    def test_glass_adjusted_after_the_slide(self):
        """The backdrop and the panels' glass are the display's picture,
        fixed where the display is: drawn again on every frame of the slide
        (the column's own drawing was kept while it moved)."""
        ticks = []
        with mock.patch.object(self.c.rev, "add_tick_callback", side_effect=ticks.append):
            self.c.rev.set_reveal_child(True)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(self.c.rev.follow, [self.c.top, self.c.col])
        with mock.patch.object(self.c.rev, "queue_draw") as a, \
                mock.patch.object(self.c.top, "queue_draw") as b, \
                mock.patch.object(self.c.col, "queue_draw") as c:
            self.assertTrue(ticks[0](self.c.rev, None))                     # (still sliding)
        a.assert_called_once()
        b.assert_called_once()
        c.assert_called_once()

    def test_center_picture_blurred_more(self):
        """Notification Center asks frost for its own look."""
        asked = {}
        with mock.patch.object(frost, "capture", side_effect=lambda out, done, r, **kw: (asked.update(kw), True)[1]), \
                mock.patch.object(N.layer, "layer_shell", return_value=object()):
            self.c._frost()
        self.assertEqual(asked, {"blur": frost.NC_BLUR, "vibrancy": frost.NC_VIBRANCY})


if __name__ == "__main__":
    unittest.main()
