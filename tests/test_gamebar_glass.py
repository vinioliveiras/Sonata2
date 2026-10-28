"""The Game Bar's glass (Vini, the "fake" one: no cost per frame). Opening,
it takes a small picture of the game's display first, blurs it once and
shows it only behind its bar and widgets, which turn see-through over it;
no picture in time (or no grim): solid as before. The compositor never blurs
behind the Game Bar.
Run: dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_gamebar_glass"""
import os
import tempfile
import unittest
from unittest import mock

for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[v] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import gamebar as G  # noqa: E402

_APP = {}


def _app():
    if "a" not in _APP:
        ui.setup()
        app = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.test.gbglass")
        app.register(None)
        _APP["a"] = app
    return _APP["a"]


def spin(ms):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def red_png() -> GLib.Bytes:
    w = h = 8
    data = bytes([255, 0, 0, 255] * (w * h))
    tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), w * 4)
    return tex.save_to_png_bytes()


class FakeProc:
    def __init__(self, out):
        self.out = out
        self.done = None

    def communicate_async(self, _i, _c, done):
        self.done = done
        if self.out is not None:
            GLib.idle_add(lambda: (done(self, None), False)[1])

    def communicate_finish(self, _res):
        return True, self.out, None


class GlassTest(unittest.TestCase):
    def setUp(self):
        config.save(G.CONFIG, dict(G.DEFAULTS))
        self.bar = G.GameBar(_app())
        self.patches = [mock.patch.object(G.layer, "layer_shell", return_value=object()),
                        mock.patch("shutil.which", return_value="/usr/bin/grim"),
                        mock.patch("sonata2.shell.capture.focused_output", return_value="DP-1")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.bar.destroy()

    def open_with(self, out):
        proc = FakeProc(out)
        shown = []
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=proc) as new:
            self.bar._frost_then(lambda: shown.append(1))
            self.assertEqual(shown, [])                         # not before the picture: it'd be in it
            spin(G.FROST_WAIT_MS + 150)
        return new, shown, proc

    def test_command(self):
        self.assertEqual(G.frost_command("DP-1"), ["grim", "-s", str(G.FROST_SCALE), "-l", "0", "-o", "DP-1", "-"])

    def test_picture_first_then_shown_frosted(self):
        self.bar.present()
        spin(100)
        new, shown, _p = self.open_with(red_png())
        self.assertEqual(new.call_args.args[0][0], "grim")
        self.assertEqual(shown, [1])                            # once
        self.assertIsNotNone(self.bar.frost.texture)
        self.assertTrue(self.bar.root.has_css_class("frosted"))

    def test_too_slow_shows_solid(self):
        _n, shown, proc = self.open_with(None)                  # grim never answers
        self.assertEqual(shown, [1])
        self.assertFalse(self.bar.root.has_css_class("frosted"))
        proc.out = red_png()
        proc.done(proc, None)                                   # late: ignored (the Game Bar is on it now)
        self.assertEqual(shown, [1])
        self.assertIsNone(self.bar.frost.texture)

    def test_without_grim_shown_at_once_solid(self):
        with mock.patch("shutil.which", return_value=None):
            shown = []
            self.bar._frost_then(lambda: shown.append(1))
        self.assertEqual(shown, [1])
        self.assertFalse(self.bar.root.has_css_class("frosted"))

    def test_glass_only_behind_the_panels(self):
        """Red picture: under the bar it shows through; on the dimmed game, not."""
        self.bar.set_default_size(1000, 600)
        self.bar.present()
        spin(300)
        self.bar.frost.set_picture(Gdk.Texture.new_from_bytes(red_png()))
        self.bar.root.add_css_class("frosted")
        spin(200)
        w, h = self.bar.get_width(), self.bar.get_height()
        snap = Gtk.Snapshot()
        self.bar.frost.do_snapshot(snap)
        tex = self.bar.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
        dl = Gdk.TextureDownloader.new(tex)
        dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = dl.download_bytes()
        px = data.get_data()

        def at(x, y):
            i = int(y) * stride + int(x) * 4
            return tuple(px[i:i + 4])
        ok, b = self.bar.bar.compute_bounds(self.bar.frost)
        self.assertEqual(at(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2)[:2], (255, 0))
        self.assertEqual(at(w / 2, h / 2)[3], 0)                 # the middle of the screen: no glass
        self.assertEqual(at(b.get_x() + 1, b.get_y() + 1)[3], 0)  # the rounded corner stays clear

    def test_glass_follows_a_panel_that_resizes(self):
        """The bar grew (the game's name, the clock) after the glass was
        drawn: it was drawn again at the bar's new size."""
        self.bar.present()
        spin(300)
        self.bar.frost.set_picture(Gdk.Texture.new_from_bytes(red_png()))
        spin(100)
        before = self.bar.frost._drawn
        self.bar.app_name.set_label("A Much Longer Game Name Than Before")
        spin(200)
        self.assertNotEqual(self.bar.frost._drawn, before)
        ok, b = self.bar.bar.compute_bounds(self.bar.frost)
        self.assertEqual(self.bar.frost._drawn[0][2], round(b.get_width()))

    def test_never_blurred_by_the_compositor(self):
        from sonata2.ui import glass
        self.assertIn('app_id is "sonata2-gamebar"', glass.NEVER_BLUR)


if __name__ == "__main__":
    unittest.main()
