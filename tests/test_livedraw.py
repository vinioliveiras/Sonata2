"""Vini: draw on the screen while recording or sharing it -- the pill's pen
turns it on and shows the palette, again (or Esc) gives the pointer back;
marks can fade by themselves; the recording's end clears them."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402


def spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class LiveDrawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.drawtest")
        cls.app.register(None)

    def test_on_draw_fade_stop(self):
        from sonata2.shell import livedraw as L
        d = L.LiveDraw(self.app)
        states = []
        d.listeners.append(states.append)
        d.set_on(True)
        spin(300)
        self.assertTrue(d.overlays)
        self.assertTrue(d.palette.get_visible())
        ov = next(iter(d.overlays.values()))
        d.proto.color = "#34c759"                     # chosen on the palette
        d.proto.tool = "shape"
        d.proto.shape = "arrow"
        lay = ov.layer
        lay._begin(None, 10, 10)
        lay._update(None, 100, 60)
        lay._end(None, 100, 60)
        self.assertEqual((lay.items[0]["t"], lay.items[0]["c"]), ("arrow", "#34c759"))   # the palette's
        self.assertIn("ts", lay.items[0])
        d.set_on(False)
        self.assertEqual(states, [True, False])
        self.assertFalse(d.palette.get_visible())
        self.assertTrue(ov.get_visible())             # the marks stay on screen
        with mock.patch.object(L, "FADE_AFTER_S", 0.05), mock.patch.object(L, "FADE_S", 0.05):
            d.toggle_fade()
            spin(400)
        self.assertEqual(lay.items, [])               # faded away
        self.assertFalse(ov.get_visible())            # and the surface with them
        d.fade = False
        d.set_on(True)
        lay._begin(None, 10, 10)
        lay._update(None, 50, 50)
        lay._end(None, 50, 50)
        d.stop()                                      # the recording ended
        self.assertEqual(lay.items, [])
        self.assertFalse(d.on)

    def test_input_region_covers_the_display(self):
        """Vini: drawing didn't work, the screen stayed clickable -- the input
        region was taken from the window's size at map (0 x 0). It comes from
        the display: all of it while drawing (less the menu bar and the
        palette), nothing when off."""
        from unittest import mock
        from sonata2.shell import livedraw as L
        d = L.LiveDraw(self.app)
        geo = mock.Mock(width=1920, height=1080)
        mon = mock.Mock(get_geometry=lambda: geo)
        ov = L.Overlay.__new__(L.Overlay)
        ov.monitor, ov.owner = mon, d
        with mock.patch("sonata2.shell.menubar_size.height", lambda: 32):
            rects = ov.region(True)
            self.assertEqual(ov.region(False), [])
        pw, ph = L.PALETTE_SIZE
        self.assertIn((0, 32 + ph, 1920, 1080 - 32 - ph), rects)       # everything under the palette
        area = sum(w * h for _x, _y, w, h in rects)
        self.assertEqual(area, 1920 * (1080 - 32) - pw * ph)           # all but the menu bar and palette

    def test_pills_have_the_pen(self):
        import inspect
        from sonata2.shell import capture, sharing
        self.assertIn("pen_button(app", inspect.getsource(capture.RecordingControl))
        self.assertIn("pen_button(app", inspect.getsource(sharing.SharingControl))
        self.assertIn("livedraw.get(", inspect.getsource(capture.RecordingControl.stop))


class CaptureHiddenTest(unittest.TestCase):
    def test_palette_left_out_of_display_captures(self):
        """The palette is on screen, never in a screen share: sonata-corners
        serves the display captures itself, without the surfaces in
        capture_hidden, and hides wlroots' own source."""
        from pathlib import Path
        from sonata2.shell import livedraw
        root = Path(__file__).resolve().parent.parent
        cpp = (root / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()
        self.assertIn("capture_rendering = true;", cpp)
        self.assertIn("return g != their_global;", cpp)
        self.assertIn("paint_cursors()", cpp)                 # the pointer still shows
        meson = (root / "wayfire-plugin" / "meson.build").read_text()
        self.assertIn("-DSONATA_OUTPUT_CAPTURE", meson)
        # the capture-source code names ext_foreign_toplevel_handle_v1_interface:
        # without our own copy the plugin didn't load at all ("undefined
        # symbol") and every window lost its rounded corners (Vini: Steam's)
        self.assertIn("ext-foreign-toplevel-list-v1-protocol.c", meson)
        ini = (root / "config" / "wayfire.ini").read_text()
        self.assertIn(f"capture_hidden = {livedraw.PALETTE_NS}", ini)
        self.assertIn('name="capture_hidden"', (root / "wayfire-plugin" / "metadata" / "sonata-corners.xml").read_text())


if __name__ == "__main__":
    unittest.main()
