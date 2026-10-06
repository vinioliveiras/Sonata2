"""Videos: Crop (Vini: Trim and Crop in Videos; crop with shape chips). A
frame over the movie, the part kept saved as a new file beside the original."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.videos import edit as E  # noqa: E402
from sonata2.videos import window as vw  # noqa: E402

from test_videos import fake, settle  # noqa: E402
from test_videos_trim import wait  # noqa: E402


class GeometryTest(unittest.TestCase):
    def test_letterbox(self):
        self.assertEqual(E.video_rect(1920, 800, 960, 800), (0.0, 200.0, 0.5))

    def test_shapes(self):
        self.assertEqual(E.ratio_rect(1920, 1080, 1.0), (420, 0, 1080, 1080))
        self.assertEqual(E.ratio_rect(1920, 1080, None), (0, 0, 1920, 1080))
        x, y, w, h = E.ratio_rect(1920, 1080, 9 / 16)
        self.assertAlmostEqual(w / h, 9 / 16, places=2)

    def test_drag_stays_inside_and_keeps_the_shape(self):
        r = (100, 100, 800, 450)
        self.assertEqual(E.drag_crop(r, "move", 5000, -50, 1920, 1080), (1120, 50, 800, 450))
        self.assertEqual(E.drag_crop(r, "r", 5000, 0, 1920, 1080), (100, 100, 1820, 450))
        self.assertEqual(E.drag_crop(r, "l", 5000, 0, 1920, 1080)[2], E.MIN_CROP)       # never under the minimum
        for part in ("tl", "br", "b", "l"):
            x, y, w, h = E.drag_crop(r, part, -700, 400, 1920, 1080, 16 / 9)
            self.assertAlmostEqual(w / h, 16 / 9, delta=0.02, msg=part)
            self.assertTrue(x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080, part)
        self.assertEqual(E.drag_crop(r, "br", 100, 0, 1920, 1080, 16 / 9)[:2], (100, 100))   # the other corner stays

    def test_what_a_press_takes(self):
        r = (100, 100, 800, 450)
        self.assertEqual(E.crop_part(r, 101, 300, 10), "l")
        self.assertEqual(E.crop_part(r, 899, 549, 10), "br")
        self.assertEqual(E.crop_part(r, 500, 300, 10), "move")
        self.assertEqual(E.crop_part(r, 10, 10, 10), "")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "no ffmpeg")
class RealCropTest(unittest.TestCase):
    def test_crops_a_movie(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2",
                        "-c:v", "libx264", src], check=True)
        out = E.output_path(src, "Cropped")
        result = []
        E.Export(E.crop_command(src, out, 100, 50, 301, 201), 2, lambda p: None, lambda ok, m: result.append((ok, m)))
        self.assertTrue(wait(lambda: result))
        self.assertTrue(result[0][0], result[0][1])
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", out], capture_output=True, text=True).stdout
        self.assertEqual(size.strip(), "300,200")


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.videos.crop")
        cls.app.register(None)

    def window(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "movie.mp4")
        open(path, "w").close()
        win = vw.VideoWindow(self.app, path, stream_factory=fake)
        win.present()
        self.addCleanup(win.destroy)
        settle()
        return win, path

    def test_crop_frame_and_shapes(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=True):
            win.start_crop()
        settle()
        self.assertIsNotNone(win.crop_frame)
        self.assertFalse(win.hud.get_visible())
        self.assertEqual(win.crop_frame.rect, (0, 0, 1920, 800))            # the whole picture to start
        win.crop_bar.shapes.buttons["1:1"].emit("clicked")
        self.assertEqual(win.crop_frame.rect, (560, 0, 800, 800))
        self.assertEqual(win.crop_bar.size.get_label(), "800 × 800")
        x, y, w, h = win.crop_frame.on_screen()
        self.assertEqual(win.crop_frame.part_at(x + 2, y + h / 2), "l")
        self.assertEqual(win.crop_frame.part_at(x + w / 2, y + h / 2), "move")
        with mock.patch.object(E, "available", return_value=True):
            win.start_trim()                                                  # one at a time
        self.assertIsNone(win.trim_bar)
        # the frame's bottom corners stay above the bar (Review: the bar covered them)
        fx, fy, fw, fh = win.crop_frame.on_screen()
        ok, bar = win.crop_bar.compute_bounds(win.crop_frame)
        self.assertLess(fy + fh, bar.get_y())
        win.cancel_crop()
        self.assertIsNone(win.crop_bar)
        self.assertTrue(win.hud.get_visible())
        self.assertEqual(win.picture.get_margin_bottom(), 0)                  # the movie back in place

    def test_crop_saves_beside_and_opens_it(self):
        win, path = self.window()
        made = []

        class FakeExport:
            def __init__(self, cmd, duration, on_progress, on_done):
                made.append(cmd)
                self.done = on_done
                open(cmd[-1], "w").close()

            def cancel(self):
                pass
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "Export", FakeExport):
            win.start_crop()
            settle()
            win.crop_bar.pick("16:9")
            win.crop_bar.crop_btn.emit("clicked")
        self.assertIn("crop=1422:800:249:0", made[0])
        self.assertEqual(made[0][-1], os.path.join(os.path.dirname(path), "movie (Cropped).mp4"))
        win._export.done(True, "")
        self.assertIsNone(win.crop_bar)
        self.assertEqual(win.path, made[0][-1])

    def test_whole_picture_saves_nothing(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "Export") as ex:
            win.start_crop()
            win.do_crop((0, 0, 1920, 800))
        ex.assert_not_called()
        self.assertIsNone(win.crop_bar)

    def test_not_for_songs(self):
        win, _path = self.window()
        win.audio = True
        with mock.patch.object(E, "available", return_value=True):
            win.start_crop()
        self.assertIsNone(win.crop_bar)


if __name__ == "__main__":
    unittest.main()
