"""Warm-up at login (Vini: no lag the first time Apps or a panel opens).
Run: xvfb-run -a python3 -m unittest tests.test_warmup"""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import icons  # noqa: E402
from sonata2.shell import layer  # noqa: E402


class WarmupTest(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check():
            self.skipTest("no display")

    def test_prewarm_plays_the_animation(self):
        win = Gtk.Window()
        seen = []
        with mock.patch.object(layer, "layer_shell", return_value=None):
            layer.prewarm(win, frames=6, delay_ms=1, step=seen.append)
            import time
            ctx, end = GLib.MainContext.default(), time.monotonic() + 3
            GLib.timeout_add(10, lambda: True)                  # keeps the loop turning
            while time.monotonic() < end and not (seen and seen[-1] == 1.0 and not win.get_visible()):
                ctx.iteration(True)
        self.assertGreaterEqual(len(seen), 5)
        self.assertEqual(seen[-1], 1.0)                       # through to the open state
        self.assertEqual(seen, sorted(seen))
        self.assertFalse(win.get_visible())
        self.assertEqual(win.get_opacity(), 1.0)
        win.destroy()

    def test_warm_images_decodes_them(self):
        imgs = [Gtk.Image(icon_name="folder", pixel_size=64) for _ in range(10)]
        done = []
        real = Gtk.IconTheme.lookup_icon

        def look(theme, *a):
            done.append(1)
            return real(theme, *a)
        with mock.patch.object(Gtk.IconTheme, "lookup_icon", look):
            icons.warm_images(imgs, chunk=3)
            ctx = GLib.MainContext.default()
            for _ in range(50):
                ctx.iteration(False)
        self.assertEqual(len(done), 10)

    def test_wired_in(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "sonata2"
        main = (root / "__main__.py").read_text()
        self.assertIn("launchpad.warm_icons(win)", main)
        self.assertIn("step=lambda t: (setattr(win.bin, \"progress\", t)", main)
        warm = main[main.index("def warm(step=0)"):main.index("_later(8000, warm)")]
        for name in ("Switcher(app", "OSD(app)", "ControlCenter(win.bar)"):
            self.assertIn(name, warm)
        # memory review: the emoji / clipboard pickers are built on first use
        # (emoji() / clipboard()), never kept for the session by the warm-up
        for name in ("EmojiPicker(app)", "ClipboardPicker(app"):
            self.assertNotIn(name, warm)
            self.assertIn(name, main[main.index("def run_topbar"):main.index("def warm(step=0)")])
