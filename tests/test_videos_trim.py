"""Videos: Trim (Vini: "Trim e Crop no preview de videos"; QuickTime's Edit >
Trim). A filmstrip with a yellow frame, the part kept saved as a new file
beside the original, which is never touched."""
import os
import sys
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
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.videos import edit as E  # noqa: E402
from sonata2.videos import window as vw  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # (its sibling: run as tests.<module> too)
from test_videos import fake, settle  # noqa: E402


def wait(cond, ms=20000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class LogicTest(unittest.TestCase):
    def test_output_beside_never_over(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "Holiday.MOV")
        self.assertEqual(E.output_path(src, "Trimmed"), os.path.join(d, "Holiday (Trimmed).mov"))
        open(os.path.join(d, "Holiday (Trimmed).mov"), "w").close()
        self.assertEqual(E.output_path(src, "Trimmed"), os.path.join(d, "Holiday (Trimmed 2).mov"))
        self.assertTrue(E.output_path(os.path.join(d, "a.webm"), "Trimmed").endswith("a (Trimmed).mp4"))
        self.assertTrue(E.output_path(os.path.join(d, "s.mp3"), "Trimmed", audio=True).endswith(".mp3"))
        self.assertTrue(E.output_path(os.path.join(d, "s.wma"), "Trimmed", audio=True).endswith(".m4a"))

    def test_commands(self):
        cmd = E.trim_command("/a.mp4", "/b.mp4", 1.5, 4.25)
        self.assertEqual(cmd[-1], "/b.mp4")
        i = cmd.index("-i")
        self.assertEqual(cmd[i + 1:i + 6], ["/a.mp4", "-ss", "1.500", "-to", "4.250"])   # exact: after -i
        self.assertIn("libx264", cmd)
        song = E.trim_command("/a.mp3", "/b.mp3", 0, 3, audio=True)
        self.assertIn("libmp3lame", song)
        self.assertIn("-vn", song)
        crop = E.crop_command("/a.mp4", "/b.mp4", 10, 20, 641, 361)
        self.assertIn("crop=640:360:10:20", crop)                   # even sizes

    def test_range_and_progress(self):
        self.assertEqual(E.clamp_range(-3, 200, 60), (0.0, 60))
        self.assertEqual(E.clamp_range(30, 30.1, 60), (30, 30 + E.MIN_LENGTH))
        self.assertAlmostEqual(E.progress_of("out_time_us=5000000\n", 10), 0.5)
        self.assertIsNone(E.progress_of("frame=12", 10))
        self.assertEqual(E.thumbnail_times(10, 5), [1.0, 3.0, 5.0, 7.0, 9.0])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "no ffmpeg")
class RealTrimTest(unittest.TestCase):
    def length(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True).stdout
        return float(out)

    def test_trims_a_movie_exactly(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
                        "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-c:v", "libx264", "-g", "100",
                        src], check=True)
        out = E.output_path(src, "Trimmed")
        seen, result = [], []
        E.Export(E.trim_command(src, out, 1.0, 2.5), 1.5, seen.append, lambda ok, m: result.append((ok, m)))
        self.assertTrue(wait(lambda: result))
        self.assertTrue(result[0][0], result[0][1])
        self.assertAlmostEqual(self.length(out), 1.5, delta=0.1)     # to the frame, not the keyframe
        self.assertAlmostEqual(self.length(src), 4.0, delta=0.1)     # the original untouched
        frames = []
        E.thumbnails(src, 4.0, 3, 48, lambda i, png: frames.append((i, png[:4])))
        self.assertTrue(wait(lambda: len(frames) == 3))
        self.assertEqual({p for _i, p in frames}, {b"\x89PNG"})

    def test_cancel_leaves_nothing(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "x (Trimmed).mp4")
        open(out, "w").close()
        done = []
        job = E.Export(["sh", "-c", "exec sleep 5", out], 5, lambda p: None, lambda ok, m: done.append(ok))
        settle(100)
        job.cancel()
        settle(400)
        self.assertFalse(os.path.exists(out))
        self.assertEqual(done, [])                                   # cancelled: no "done"


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.videos.trim")
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

    def test_right_click_menu_comes_before_the_window_handle(self):
        """Vini: Trim and Crop didn't show -- the movie is a window handle,
        whose own right-click (the window menu) took the press before the
        player's menu. The menu's click now comes first (capture phase)."""
        win, _path = self.window()
        self.assertEqual(win.menu_click.get_propagation_phase(), Gtk.PropagationPhase.CAPTURE)
        self.assertEqual(win.menu_click.get_button(), Gdk.BUTTON_SECONDARY)
        handle = win.picture.get_parent()
        self.assertIsInstance(handle, Gtk.WindowHandle)
        self.assertTrue(handle.is_ancestor(win.overlay))          # captured on the way down to it
        items = []
        with mock.patch.object(E, "available", return_value=True), \
                mock.patch.object(vw.ui.menu, "popup", side_effect=lambda _w, secs, **_k: items.extend(
                    i for sec in secs for i in sec)):
            win._context_menu(win.menu_click, 1, 10, 10)
        names = {i.label: i for i in items}
        self.assertTrue(names["Trim…"].enabled)
        self.assertTrue(names["Crop…"].enabled)

    def test_trim_and_crop_buttons_in_the_controls(self):
        """Vini (option A): Trim and Crop in sight in the controls, beside
        full screen -- they start the same trim / crop as the menu."""
        win, _path = self.window()
        end = win.hud.get_first_child().get_end_widget()
        kids = []
        c = end.get_first_child()
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        self.assertEqual(kids, [win.trim_btn, win.crop_btn, win.full_btn])
        with mock.patch.object(E, "available", return_value=True):
            win._edit_buttons()
            self.assertTrue(win.trim_btn.get_sensitive())
            self.assertTrue(win.crop_btn.get_sensitive())
            with mock.patch.object(E, "thumbnails"):
                win.trim_btn.emit("clicked")
            self.assertIsNotNone(win.trim_bar)
            win.cancel_trim()

    def test_song_has_no_crop_button(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=True):
            win._set_audio(True)
        self.assertFalse(win.crop_btn.get_visible())
        self.assertTrue(win.trim_btn.get_visible())

    def test_without_ffmpeg_they_say_so(self):
        """No FFmpeg: Trim / Crop aren't greyed out with no reason -- they say what to install."""
        win, _path = self.window()
        items = []
        with mock.patch.object(E, "available", return_value=False), \
                mock.patch.object(vw.ui.menu, "popup", side_effect=lambda _w, secs, **_k: items.extend(
                    i for sec in secs for i in sec)), \
                mock.patch.object(vw.ui.dialog, "alert") as alert:
            win._context_menu(win.menu_click, 1, 10, 10)
            names = {i.label: i for i in items}
            self.assertTrue(names["Trim…"].enabled)
            win.start_trim()
            self.assertIsNone(win.trim_bar)
            self.assertIn("FFmpeg", alert.call_args.args[0])
            win.start_crop()
            self.assertIsNone(win.crop_bar)
            self.assertEqual(alert.call_count, 2)

    def test_trim_bar_replaces_the_controls(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "thumbnails") as th:
            win.start_trim()
        self.assertIsNotNone(win.trim_bar)
        self.assertFalse(win.hud.get_visible())
        th.assert_called_once()
        settle()
        strip = win.trim_bar.strip
        self.assertGreater(strip.get_width(), 300)
        strip.set_range(10, 40)
        self.assertIn("(0:30)", win.trim_bar.times.get_label())
        self.assertEqual(strip.pick(strip.x_of(10) - 6), "start")
        self.assertEqual(strip.pick(strip.x_of(40) + 6), "end")
        self.assertEqual(strip.pick(strip.x_of(25)), "playhead")
        win.cancel_trim()
        self.assertIsNone(win.trim_bar)
        self.assertTrue(win.hud.get_visible())

    def test_trim_saves_beside_and_opens_it(self):
        win, path = self.window()
        made = []

        class FakeExport:
            def __init__(self, cmd, duration, on_progress, on_done):
                made.append((cmd, duration))
                self.done = on_done
                open(cmd[-1], "w").close()

            def cancel(self):
                pass
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "thumbnails"), \
                mock.patch.object(E, "Export", FakeExport):
            win.start_trim()
            win.trim_bar.strip.set_range(5, 20)
            win.trim_bar.trim_btn.emit("clicked")
        cmd, duration = made[0]
        self.assertEqual(duration, 15)
        self.assertEqual(cmd[-1], os.path.join(os.path.dirname(path), "movie (Trimmed).mp4"))
        self.assertFalse(win.trim_bar.trim_btn.get_sensitive())     # saving
        win._export.done(True, "")
        self.assertIsNone(win.trim_bar)
        self.assertEqual(win.path, cmd[-1])                         # the trimmed one, in the same window

    def test_nothing_cut_saves_nothing(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "thumbnails"), \
                mock.patch.object(E, "Export") as ex:
            win.start_trim()
            win.do_trim(0, 90)
        ex.assert_not_called()
        self.assertIsNone(win.trim_bar)

    def test_drawing_leaves_the_shared_colours_alone(self):
        """Review: the dimming and the playhead changed ui.rgba's shared
        black (the whole theme's)."""
        win, _path = self.window()
        before = ui.rgba("sys_black").to_string()
        with mock.patch.object(E, "available", return_value=True), mock.patch.object(E, "thumbnails"):
            win.start_trim()
        settle()
        win.trim_bar.strip.set_range(10, 40)
        win._trim_seek(20)
        settle()
        self.assertEqual(ui.rgba("sys_black").to_string(), before)

    def test_no_ffmpeg_no_trim(self):
        win, _path = self.window()
        with mock.patch.object(E, "available", return_value=False):
            self.assertFalse(win.can_edit())
            win.start_trim()
        self.assertIsNone(win.trim_bar)


if __name__ == "__main__":
    unittest.main()
