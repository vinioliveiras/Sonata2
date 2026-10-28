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
            GLib.timeout_add(5, lambda: (done(self, None), False)[1])   # (an idle could wait behind frames)

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
            spin(G.FROST_WAIT_MS + 350)
        return new, shown, proc

    def test_command(self):
        self.assertEqual(G.frost_command("DP-1"), ["grim", "-s", str(G.FROST_SCALE), "-l", "0", "-o", "DP-1", "-"])

    def test_picture_first_then_shown_frosted(self):
        self.bar.present()
        spin(100)
        new, shown, _p = self.open_with(red_png())
        self.assertEqual(new.call_args.args[0][0], "grim")
        self.assertEqual(shown, [1])                            # once
        self.assertIsNotNone(self.bar.frost_tex)
        self.assertTrue(self.bar.root.has_css_class("frosted"))

    def test_panels_wait_for_their_glass(self):
        """Vini: the widgets opened solid, then turned to glass. The game dims
        at once; the bar and widgets wait (a little) and pop in already in
        glass."""
        proc = FakeProc(None)
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=proc):
            self.bar._frost_then(self.bar.present)
            spin(G.FROST_WAIT_MS + 60)
        self.assertTrue(self.bar.get_visible())                  # the dimming: at once
        self.assertTrue(self.bar.root.has_css_class("waiting"))  # the panels: not yet
        proc.out = red_png()
        proc.done(proc, None)
        spin(50)
        self.assertFalse(self.bar.root.has_css_class("waiting"))
        self.assertEqual(self.bar.frost_alpha, 1.0)               # in glass from their first frame

    def test_panels_shown_solid_if_the_picture_is_too_late(self):
        proc = FakeProc(None)
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=proc):
            self.bar._frost_then(self.bar.present)
            spin(G.FROST_WAIT_MS + G.PANELS_WAIT_MS + 120)
        self.assertFalse(self.bar.root.has_css_class("waiting"))  # not kept hidden
        self.assertIsNone(self.bar.frost_tex)

    def test_too_slow_shows_solid_then_the_glass_fades_in(self):
        """Vini: some openings had no glass -- grim was slower than the wait
        and its picture was thrown away. It shows solid at once, and the
        glass comes when the picture does (fading in)."""
        proc = FakeProc(None)                                    # grim answers late
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=proc):
            self.bar._frost_then(self.bar.present)
            spin(G.FROST_WAIT_MS + 150)
        self.assertTrue(self.bar.get_visible())                  # not kept waiting
        self.assertFalse(self.bar.root.has_css_class("frosted"))
        proc.out = red_png()
        proc.done(proc, None)
        spin(100)
        self.assertIsNotNone(self.bar.frost_tex)
        self.assertTrue(self.bar.root.has_css_class("frosted"))
        spin(G.GLASS_FADE_MS + 200)
        self.assertEqual(self.bar.frost_alpha, 1.0)               # (eased in)

    def test_late_picture_ignored_once_closed_or_reopened(self):
        proc = FakeProc(None)
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=proc):
            self.bar._frost_then(self.bar.present)
            spin(G.FROST_WAIT_MS + 150)
        self.bar.root.add_css_class("closing")                   # closing meanwhile
        proc.out = red_png()
        proc.done(proc, None)
        self.assertIsNone(self.bar.frost_tex)
        self.bar.root.remove_css_class("closing")
        old = FakeProc(None)
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=old):
            self.bar._frost_then(self.bar.present)
        with mock.patch.object(G.Gio.Subprocess, "new", return_value=FakeProc(None)):
            self.bar._frost_then(self.bar.present)               # opened again: a new picture asked
        old.out = red_png()
        old.done(old, None)                                      # the first one's: not this opening's
        self.assertIsNone(self.bar.frost_tex)

    def test_without_grim_shown_at_once_solid(self):
        with mock.patch("shutil.which", return_value=None):
            shown = []
            self.bar._frost_then(lambda: shown.append(1))
        self.assertEqual(shown, [1])
        self.assertFalse(self.bar.root.has_css_class("frosted"))

    def _render(self):
        w, h = self.bar.get_width(), self.bar.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable(widget=self.bar.root).snapshot(snap, w, h)
        tex = self.bar.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
        dl = Gdk.TextureDownloader.new(tex)
        dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = dl.download_bytes()
        px = data.get_data()
        return lambda x, y: tuple(px[int(y) * stride + int(x) * 4:int(y) * stride + int(x) * 4 + 4])

    def test_glass_only_behind_the_panels(self):
        """Red picture: under the bar it shows through (tinted); on the
        dimmed game, not -- each panel draws its own slice."""
        self.bar.set_default_size(1000, 600)
        self.bar.present()
        spin(300)
        self.bar.root.add_css_class("frosted")
        self.bar._set_glass(Gdk.Texture.new_from_bytes(red_png()), 1.0)
        spin(300)
        at = self._render()
        ok, b = self.bar.bar.compute_bounds(self.bar.root)
        inside = at(b.get_x() + 30, b.get_y() + b.get_height() / 2)
        outside = at(self.bar.get_width() / 2, self.bar.get_height() / 2)
        self.assertGreater(inside[0], inside[1] + 40)               # reddish: the picture through the tint
        self.assertLess(outside[0], outside[1] + 20)                # the scrim: no red
        # the whole panel, its padding too (a snapshot starts inside it: the
        # glass was shifted in by the padding, a frame of no glass around it)
        w = self.bar.widgets["capture"]
        ok, wb = w.compute_bounds(self.bar.root)
        edge = at(wb.get_x() + wb.get_width() / 2, wb.get_y() + 3)
        self.assertGreater(edge[0], edge[1] + 40)

    def test_glass_follows_a_moved_widget(self):
        """Moved (dragged, put back): it draws its slice again, where it is."""
        w = self.bar.widgets["capture"]
        with mock.patch.object(w, "queue_draw") as qd:
            w.place((100, 80))
        qd.assert_called()

    def test_no_screen_wide_glass_layer(self):
        """Vini: slow -- one glass layer over the whole screen was drawn whole
        whenever a panel changed, and looked at on a timer."""
        src = open(G.__file__).read()
        self.assertNotIn("class _Frost", src)
        self.assertNotIn("FROST_FOLLOW_MS", src)
        self.assertIsInstance(self.bar.bar, G._Glass)
        for w in self.bar.widgets.values():
            self.assertIsInstance(w, G._Glass)

    def test_never_blurred_by_the_compositor(self):
        from sonata2.ui import glass
        self.assertIn('app_id is "sonata2-gamebar"', glass.NEVER_BLUR)



class LightOpeningTest(unittest.TestCase):
    """Vini: opening, closing and showing / hiding a widget were slow."""

    def test_no_frame_by_frame_work_while_open(self):
        src = open(G.__file__).read()
        self.assertNotIn("add_tick_callback", src)                 # the frame clock rests between changes
        self.assertLessEqual(G.FROST_WAIT_MS, 100)                 # (a late picture fades in)

    def test_states_read_after_the_opening_animation(self):
        config.save(G.CONFIG, dict(G.DEFAULTS))
        bar = G.GameBar(_app())
        try:
            with mock.patch.object(bar, "_refresh_audio") as audio, \
                    mock.patch.object(bar, "_refresh_controls") as controls:
                bar.open()
                audio.assert_not_called()
                controls.assert_not_called()
                spin(G.READ_AFTER_MS + 100)
            audio.assert_called_once()
            controls.assert_called_once()
        finally:
            bar.destroy()

if __name__ == "__main__":
    unittest.main()
