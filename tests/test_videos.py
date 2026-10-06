"""Videos: time format, remembered positions, window with a fake stream and
with no media backend (xvfb-run python3 -m unittest tests.test_videos)."""
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.videos import window as vw  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeStream(Gtk.MediaStream):
    """A 90 s, 1920 x 800 movie that plays nothing."""
    def do_play(self):
        return True

    def do_pause(self):
        pass

    def do_seek(self, ts):
        self.seek_success()
        self.update(ts)

    def do_snapshot(self, snap, w, h):
        c = Gdk.RGBA()
        c.parse("#336699")
        snap.append_color(c, Graphene.Rect().init(0, 0, w, h))

    def do_get_intrinsic_width(self):
        return 1920

    def do_get_intrinsic_height(self):
        return 800


def fake(_path):
    s = FakeStream()
    s.stream_prepared(True, True, True, 90_000_000)
    return s


class LogicTest(unittest.TestCase):
    def test_fmt_time(self):
        self.assertEqual(vw.fmt_time(0), "0:00")
        self.assertEqual(vw.fmt_time(5.7), "0:05")
        self.assertEqual(vw.fmt_time(754), "12:34")
        self.assertEqual(vw.fmt_time(3723), "1:02:03")
        self.assertEqual(vw.fmt_time(44.2, remaining=True), "-0:45")
        self.assertEqual(vw.fmt_time(0, remaining=True), "-0:00")
        self.assertEqual(vw.fmt_time(-3), "0:00")

    def test_fit_size(self):
        self.assertEqual(vw.fit_size(1920, 1080, 1344, 756), (1344, 756))
        self.assertEqual(vw.fit_size(640, 480, 1344, 756), (640, 480))        # never enlarged
        w, h = vw.fit_size(1080, 1920, 1344, 756)                              # portrait
        self.assertEqual(h, 756)
        self.assertAlmostEqual(w / h, 1080 / 1920, delta=0.01)

    def test_positions(self):
        vw.save_position("/m/a.mp4", 42.3, 600)
        self.assertEqual(vw.load_position("/m/a.mp4"), 42.3)
        vw.save_position("/m/a.mp4", 3, 600)                  # near the start: forgotten
        self.assertEqual(vw.load_position("/m/a.mp4"), 0)
        vw.save_position("/m/b.mp4", 595, 600)                # near the end: forgotten
        self.assertEqual(vw.load_position("/m/b.mp4"), 0)
        for i in range(vw.MAX_POSITIONS + 5):
            vw.save_position(f"/m/{i}.mp4", 100, 600)
        pos = config.load(vw.CONFIG, vw.DEFAULTS)["positions"]
        self.assertEqual(len(pos), vw.MAX_POSITIONS)
        self.assertNotIn("/m/0.mp4", pos)                     # the oldest go first
        self.assertIn(f"/m/{vw.MAX_POSITIONS + 4}.mp4", pos)

    def test_volume_icon(self):
        self.assertEqual(vw.volume_icon(0.5, True), "sonata-volume-muted-symbolic")
        self.assertEqual(vw.volume_icon(0.1, False), "sonata-volume-1-symbolic")
        self.assertEqual(vw.volume_icon(1.0, False), "sonata-volume-3-symbolic")


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.videos")
        cls.app.register(None)

    def test_no_backend(self):
        """This environment has no GTK media module: a clear message, no HUD."""
        win = vw.VideoWindow(self.app, "/nonexistent/movie.mp4")
        win.present()
        settle()
        self.assertTrue(win.message.get_visible())
        self.assertFalse(win.hud.get_visible())
        self.assertTrue(win.reason.get_label())
        win.toggle_play()                                      # harmless
        win.close()
        settle(50)

    def test_factory_error(self):
        def broken(_p):
            raise RuntimeError("no decoder")
        win = vw.VideoWindow(self.app, "/x.mkv", stream_factory=broken)
        self.assertTrue(win.message.get_visible())
        self.assertEqual(win.reason.get_label(), "no decoder")
        win.destroy()

    def test_playback_and_resume(self):
        path = "/movies/resume.mp4"
        vw.save_position(path, 30, 90)
        win = vw.VideoWindow(self.app, path, stream_factory=fake)
        win.present()
        settle()
        s = win.stream
        self.assertAlmostEqual(s.get_timestamp() / 1e6, 30, places=3)   # resumed
        self.assertEqual(win.elapsed.get_label(), "0:30")
        self.assertEqual(win.remaining.get_label(), "-1:00")
        win.toggle_play()
        self.assertTrue(s.get_playing())
        self.assertEqual(win.play_btn.get_icon_name(), "media-playback-pause-symbolic")
        win.skip(10)
        self.assertAlmostEqual(s.get_timestamp() / 1e6, 40, places=3)
        win.skip(-100)
        self.assertEqual(s.get_timestamp(), 0)
        win.seek_to(500)                                       # clamped to the end
        self.assertEqual(s.get_timestamp(), 90_000_000)
        win.seek_to(55)
        win._hide_now()                                        # playing: controls and pointer hide
        self.assertTrue(win.hud.has_css_class("hidden"))
        win.toggle_play()                                      # paused: controls come back
        self.assertFalse(win.hud.has_css_class("hidden"))
        win.set_volume(0.3)
        self.assertAlmostEqual(s.get_volume(), 0.3)
        win.toggle_mute()
        self.assertTrue(s.get_muted())
        win.toggle_loop()
        self.assertTrue(s.get_loop())
        win.close()
        settle(50)
        self.assertEqual(vw.load_position(path), 55)           # remembered on close
        self.assertTrue(config.load(vw.CONFIG, vw.DEFAULTS)["muted"])

    def test_desktop_file(self):
        from sonata2 import apps
        d = tempfile.mkdtemp()
        orig = apps.write_desktop_file
        written = {}
        apps.write_desktop_file = lambda name, text: written.update({name: text}) or os.path.join(d, name)
        try:
            vw.videos_desktop_file("sonata2")
        finally:
            apps.write_desktop_file = orig
        text = written[vw.APP_ID + ".desktop"]
        self.assertIn("Name=Videos", text)
        self.assertIn("video/mp4;", text)
        self.assertIn("Exec=sonata2 videos %F", text)
        self.assertIn("audio/mpeg;", text)                     # songs too (Music was removed)
        self.assertIn("audio/flac;", text)


def _wav(path, seconds=1.0, rate=8000):
    import struct
    data = b"\0\0" * int(rate * seconds)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" +
                struct.pack("<I", len(data)) + data)


class SongTest(unittest.TestCase):
    """Vini: Music was removed; MP3s (and other songs) play in Videos, like
    QuickTime -- a square window with the cover, controls always shown."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.videos.songs")
        cls.app.register(None)

    def test_kinds_and_titles(self):
        self.assertTrue(vw.is_audio("/m/a.mp3"))
        self.assertTrue(vw.is_audio("/m/a.flac"))
        self.assertTrue(vw.is_audio("/m/a.m4a"))
        self.assertFalse(vw.is_audio("/m/a.mp4"))
        self.assertFalse(vw.is_audio("/m/a.mkv"))
        self.assertEqual(vw.song_title({"title": "Song", "artist": "Band"}, "/m/a.mp3"), "Band — Song")
        self.assertEqual(vw.song_title({"title": "Song"}, "/m/a.mp3"), "Song")
        self.assertEqual(vw.song_title({}, "/m/a.mp3"), "a.mp3")

    def test_songs_play_through_classic_playbin(self):
        """GTK's backend (playbin3) aborted the app on some MP3s: songs use playbin."""
        from sonata2.videos import gststream
        if not gststream.available():
            self.skipTest("no GStreamer here")
        path = os.path.join(tempfile.mkdtemp(), "beep.wav")
        _wav(path)
        s = vw.media_file(path)
        self.assertIsInstance(s, gststream.PlaybinStream)
        self.assertEqual(s.bin.get_factory().get_name(), "playbin")
        end = GLib.get_monotonic_time() + 3_000_000
        while not s.is_prepared() and GLib.get_monotonic_time() < end:
            settle(50)
        self.assertTrue(s.is_prepared())
        self.assertAlmostEqual(s.get_duration() / 1e6, 1.0, delta=0.05)
        s.set_volume(0)
        s.seek(500_000)
        self.assertEqual(s.get_timestamp(), 500_000)
        s.play()
        self.assertTrue(s.get_playing())
        s.pause()
        s.close()

    def test_song_window(self):
        import shutil
        from test_audiotags import make_mp3
        d = tempfile.mkdtemp()
        path = os.path.join(d, "song.mp3")
        import io
        from PIL import Image
        png = io.BytesIO()
        Image.new("RGB", (8, 8), (200, 40, 90)).save(png, "PNG")
        make_mp3(path, title="Song", artist="Band", cover=png.getvalue())
        plain = os.path.join(d, "plain.mp3")
        shutil.copy(path, plain)
        make_mp3(plain, title="Plain", artist="", cover=None)
        win = vw.VideoWindow(self.app, path, stream_factory=fake)
        win.present()
        settle(400)
        self.assertTrue(win.audio)
        self.assertTrue(win.has_css_class("vd-audio"))
        self.assertEqual(tuple(win.get_default_size()), (vw.AUDIO_SIZE, vw.AUDIO_SIZE))
        self.assertEqual(win.get_title(), "Band — Song")
        self.assertIsInstance(win.picture.get_paintable(), Gdk.Texture)       # the cover
        self.assertFalse(win.art.get_visible())
        self.assertFalse(win.full_btn.get_visible())
        win.toggle_fullscreen()                                                # no full screen for a song
        settle(50)
        self.assertFalse(win.is_fullscreen())
        win.toggle_play()
        self.assertEqual(win._hide_src, 0)                                     # controls never fade
        win._hide_now()
        self.assertFalse(win.hud.has_css_class("hidden"))
        win.open(plain)                                                        # no cover: the note
        settle(400)
        self.assertEqual(win.get_title(), "Plain")
        self.assertIsNone(win.picture.get_paintable())
        self.assertTrue(win.art.get_visible())
        win.open("/movies/clip.mp4")                                           # a movie again
        settle(100)
        self.assertFalse(win.audio)
        self.assertFalse(win.art.get_visible())
        self.assertTrue(win.full_btn.get_visible())
        self.assertIs(win.picture.get_paintable(), win.stream)
        win.destroy()

    def test_music_launcher_goes(self):
        from sonata2 import apps
        apps_dir = os.path.join(GLib.get_user_data_dir(), "applications")
        os.makedirs(apps_dir, exist_ok=True)
        old = os.path.join(apps_dir, "io.github.vinioliveiras.sonata2.music.desktop")
        open(old, "w").close()
        self.assertEqual(apps.remove_retired(), [old])
        self.assertFalse(os.path.exists(old))
        self.assertEqual(apps.remove_retired(), [])


if __name__ == "__main__":
    unittest.main()
