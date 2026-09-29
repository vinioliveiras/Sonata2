"""Preview: opens a picture, goes to the next one, zooms, rotates
(xvfb-run python3 -m unittest tests.test_preview)."""
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GdkPixbuf, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class PreviewTest(unittest.TestCase):
    def test_pictures(self):
        Adw.init()
        ui.setup()
        from sonata2.preview.window import PreviewWindow
        d = tempfile.mkdtemp()
        for n, w in (("a.png", 300), ("b.png", 200)):
            pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, 100)
            pb.fill(0x3366ccff)
            pb.savev(os.path.join(d, n), "png", [], [])
        app = Adw.Application(application_id="io.test.preview")
        app.register(None)
        win = PreviewWindow(app, os.path.join(d, "a.png"))
        win.present()
        settle()
        self.assertEqual(win.title_size.get_label(), "300 × 100")
        win.go(1)
        self.assertTrue(win.path.endswith("b.png"))
        win.step_zoom(1)
        self.assertIsNotNone(win.zoom)
        win.rotate(90)
        self.assertEqual(win.texture.get_width(), 100)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
