"""The wallpaper is decoded at the display's size (shell/wallpaper.py).
Vini: while a game filled the graphics card, Sonata's wallpaper process
held 228 MB of it (the 4K photo, whole, in two pictures per display).
Run: xvfb-run -a python3 -m unittest tests.test_wallpaper_memory"""
import os
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2 import wallpapers  # noqa: E402
from sonata2.shell import wallpaper as W  # noqa: E402


class WallpaperMemoryTest(unittest.TestCase):
    def test_cover_size(self):
        self.assertEqual(W.cover_size(3840, 2560, 1920, 1080), (1920, 1280))     # covers, no bigger
        self.assertEqual(W.cover_size(1000, 800, 1920, 1080), (1000, 800))       # never enlarged

    def test_texture_at_screen_size(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        path = wallpapers.DEFAULT.light
        self.assertTrue(os.path.exists(path))
        tex = W.screen_texture(path, 1920, 1080)
        self.assertEqual((tex.get_width(), tex.get_height()), W.cover_size(3840, 2543, 1920, 1080))

    def test_hidden_picture_lets_go(self):
        src = open(W.__file__).read()
        self.assertIn("p.set_paintable(None) for p in self.pics if p is not st.get_visible_child()", src)
        self.assertNotIn("nxt.set_file(", src)                                   # never the whole file


class WallpaperFirstFrameTest(unittest.TestCase):
    """Vini: at login the default (gradient) showed for a moment before his
    wallpaper -- the picture was decoded in a thread after the window mapped."""

    def test_first_picture_before_the_window_shows(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        from unittest import mock
        from sonata2 import prefs
        app = Gtk.Application(application_id="io.github.test.wallfirst")
        app.register(None)
        uri = wallpapers.uri(wallpapers.CATALOG[1].light)
        with mock.patch.object(prefs, "get", side_effect=lambda s, k, d=None: uri if "picture-uri" in k else d), \
                mock.patch.object(W, "share_with_login_screen"):
            win = W.WallpaperWindow(app, desktop=False)
        shown = win.stack.get_visible_child()
        self.assertIsNotNone(shown.get_paintable())               # already there, nothing to wait for
        win.destroy()
