"""Camera (sonata2/camera): a GStreamer test source stands in for the webcam.
Run: xvfb-run -a python3 -m unittest tests.test_camera"""
import os
import tempfile
import time
import unittest

_home = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(_home, ".config")
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, GLib, Gst  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.camera import engine as E, window as W  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def test_source():
    return Gst.ElementFactory.make("videotestsrc")


class LogicTest(unittest.TestCase):
    def test_names_never_overwrite(self):
        folder = tempfile.mkdtemp()
        first = W.unique(folder, "Photo 2026-09-30 at 15.00.00.jpg")
        open(first, "w").close()
        self.assertTrue(W.unique(folder, "Photo 2026-09-30 at 15.00.00.jpg").endswith("15.00.00 2.jpg"))

    def test_square_thumbnail(self):
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 320, 180)
        tex = W.square_texture(pb, 88)
        self.assertEqual((tex.get_width(), tex.get_height()), (88, 88))

    def test_recent_photos_newest_first(self):
        folder = tempfile.mkdtemp()
        for i, name in enumerate(("a.jpg", "b.png", "c.txt", "d.jpeg")):
            path = os.path.join(folder, name)
            open(path, "w").close()
            os.utime(path, (time.time() + i, time.time() + i))
        self.assertEqual([os.path.basename(p) for p in W.recent_photos(folder)], ["d.jpeg", "b.png", "a.jpg"])


class CameraTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.pics = tempfile.mkdtemp()
        self._dir = W.photos_dir
        W.photos_dir = lambda: self.pics

    def tearDown(self):
        W.photos_dir = self._dir

    def test_engine_live_frames_and_photo(self):
        errors = []
        cam = E.Camera(on_error=errors.append, source=test_source)
        self.assertTrue(cam.start(mirror=True))
        settle(800)
        path = os.path.join(self.pics, "x.jpg")
        self.assertTrue(cam.photo(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")                 # a JPEG
        cam.set_mirror(False)
        cam.stop()
        self.assertEqual(errors, [])
        self.assertFalse(cam.running)

    def test_photo_keeps_the_window_size(self):
        """The window grew after the first photo (the thumbnail strip appeared)."""
        win = W.CameraWindow(None, source=test_source)
        win.cfg["countdown"] = False
        win.present()
        settle(1000)
        size = (win.get_width(), win.get_height())
        self.assertTrue(win.shutter.get_sensitive())
        win.capture()
        settle(500)
        self.assertEqual(len(os.listdir(self.pics)), 1)
        self.assertTrue(win.last_photo and win.last_photo.startswith(self.pics))
        self.assertEqual((win.get_width(), win.get_height()), size)
        win.close()
        self.assertFalse(win.cam.running)                            # closing frees the camera

    def test_video_mode_records_with_sound(self):
        vids = tempfile.mkdtemp()
        old, W.videos_dir = W.videos_dir, (lambda: vids)
        live = lambda n: Gst.ElementFactory.make_with_properties(n, ["is-live"], [True])  # noqa: E731
        try:
            win = W.CameraWindow(None, source=lambda: live("videotestsrc"))
            win.cam.audio_source = lambda: live("audiotestsrc")
            win.cfg["countdown"] = False
            win.present()
            settle(800)
            win._set_mode("video", save=False)
            win.capture()
            self.assertTrue(win.cam.recording)
            self.assertTrue(win.shutter.has_css_class("recording"))
            self.assertTrue(win.rec_time.get_visible())
            settle(1500)
            win.capture()                                           # stop
            settle(2500)
            files = os.listdir(vids)
            self.assertEqual(len(files), 1)
            self.assertGreater(os.path.getsize(os.path.join(vids, files[0])), 10000)
            self.assertFalse(win.shutter.has_css_class("recording"))
            self.assertEqual(win.last_photo, os.path.join(vids, files[0]))
            win.close()
        finally:
            W.videos_dir = old

    def test_countdown_and_escape(self):
        win = W.CameraWindow(None, source=test_source)
        win.cfg["countdown"] = True
        win.cfg["flash"] = False
        win.present()
        settle(800)
        win.capture()
        self.assertTrue(win.count.get_visible())
        self.assertEqual(win.count.get_label(), "3")
        win._cancel_count()
        settle(1300)
        self.assertEqual(os.listdir(self.pics), [])                  # cancelled: no photo
        win.close()

    def test_no_camera_says_so(self):
        devices, E.devices = E.devices, lambda: []
        try:
            win = W.CameraWindow(None)
            win.present()
            settle(400)
            self.assertTrue(win.msg.get_visible())
            self.assertEqual(win.msg_title.get_label(), "No Camera")
            self.assertFalse(win.shutter.get_sensitive())
            win.close()
        finally:
            E.devices = devices


if __name__ == "__main__":
    unittest.main()
