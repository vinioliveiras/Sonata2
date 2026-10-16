"""Videos as wallpapers (Vini: natively, looped, silent, playing only while
the desktop has the focus -- paused under any other app -- light on the
machine; the lock and login screens blur a frame of it; "set as
wallpaper" in the right-click menus).

- videowall.py: what a video is, its poster (ffmpeg), made once;
- videowallpaper.py: when it may play (focus, lock, dark), one decoder
  shared by every display, muted, looped, paused / freed;
- wallpaper.py: the poster first, the video over it, a picture again;
- the still picture for the lock screen and Settings; the menus.

    xvfb-run -a python3 -m unittest tests.test_video_wallpaper
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import prefs, videowall  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAS_FFMPEG = bool(shutil.which("ffmpeg"))
_clip = {}


def clip() -> str:
    """A 2 s test video (made once per run), or skip."""
    if not HAS_FFMPEG:
        raise unittest.SkipTest("needs ffmpeg")
    if "path" not in _clip:
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "waves.webm")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                        "-t", "2", "-c:v", "libvpx", path], check=True, timeout=60)
        _clip["path"] = path
    return _clip["path"]


def settle(ms: int) -> None:
    ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class FakeIPC:
    available = True

    def __init__(self, views=None):
        self.views, self.cb = views or [], None

    def call(self, method, data=None):
        return self.views if method == "window-rules/list-views" else None

    def watch(self, events, cb):
        self.cb = cb
        return True

    def focus(self, view):
        self.cb({"event": "view-focused", "view": view})


class VideoFileTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.cache, ignore_errors=True)

    def test_what_is_a_video(self):
        for s in ("/a/b.mp4", "/a/B.WEBM", "x.mkv", "video/mp4", "video/webm"):
            self.assertTrue(videowall.is_video(s), s)
        for s in ("/a/b.jpg", "image/png", "", None, "/a/mp4"):
            self.assertFalse(videowall.is_video(s), s)
        self.assertTrue(prefs.is_video("video/quicktime"))

    def test_poster_made_once(self):
        path = clip()
        out = videowall.poster(path)
        self.assertTrue(out and out.startswith(self.cache) and os.path.getsize(out) > 0)
        from gi.repository import GdkPixbuf
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(out)
        self.assertEqual((fmt.get_name(), w, h), ("jpeg", 320, 180))
        stamp = os.stat(out).st_mtime_ns
        with mock.patch.object(videowall, "_ffmpeg", side_effect=AssertionError("made again")):
            self.assertEqual(videowall.poster(path), out)                 # cached
        self.assertEqual(os.stat(out).st_mtime_ns, stamp)
        self.assertEqual(videowall.still(path), out)
        self.assertEqual(videowall.still("/x/pic.jpg"), "/x/pic.jpg")

    def test_unreadable_video_has_no_poster(self):
        bad = os.path.join(self.cache, "broken.mp4")
        with open(bad, "wb") as f:
            f.write(b"not a video")
        self.assertIsNone(videowall.poster(bad))
        self.assertIsNone(videowall.poster(os.path.join(self.cache, "missing.mp4")))
        self.assertEqual([n for n in os.listdir(os.path.join(self.cache, "sonata2", "wallpapers"))
                          if n.endswith(".tmp.jpg")], [])                  # nothing half written left

    def test_lock_screen_blurs_the_poster(self):
        """Vini: the lock and login screens' blurred backdrop is a frame of the video."""
        path = clip()
        uri = "file://" + path
        with mock.patch.object(prefs, "get", side_effect=lambda s, k, d=None: uri if "picture-uri" in k else d):
            self.assertTrue(prefs.usable_picture(uri))                    # a video is a wallpaper
            self.assertEqual(prefs.wallpaper_uri(True), uri)               # the desktop plays it
            self.assertEqual(prefs.wallpaper_still_uri(True), "file://" + videowall.poster(path))
        broken = os.path.join(self.cache, "broken.webm")
        open(broken, "wb").write(b"x")
        with mock.patch.object(prefs, "get", side_effect=lambda s, k, d=None: "file://" + broken
                               if "picture-uri" in k else d):
            self.assertEqual(prefs.wallpaper_still_uri(False), prefs._WALL[0])   # no frame: Sonata's own

    def test_lock_and_login_use_the_still(self):
        src = open(os.path.join(ROOT, "sonata2", "shell", "loginui.py")).read()
        self.assertIn("prefs.wallpaper_still_uri(dark)", src)
        src = open(os.path.join(ROOT, "sonata2", "shell", "wallpaper.py")).read()
        self.assertIn("share_with_login_screen(Gio.File.new_for_path(still_file[0]))", src)  # greeter: the poster


class FocusTest(unittest.TestCase):
    """When a video wallpaper may play."""

    def setUp(self):
        from sonata2.shell import videowallpaper as VW
        self.VW = VW
        self.run_dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run_dir})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_rules(self):
        has = self.VW.desktop_has_focus
        self.assertTrue(has(None, False))                                   # nothing focused
        self.assertTrue(has({"type": "background"}, False))                 # the desktop itself
        self.assertFalse(has({"type": "toplevel"}, True))                   # an app
        for kind in ("panel", "overlay", "x-or", "unmanaged"):              # Dock, menu bar, Spotlight, menus
            self.assertTrue(has({"type": kind}, True), kind)
            self.assertFalse(has({"type": kind}, False), kind)

    def test_front_at_start(self):
        app = {"type": "toplevel", "activated": True, "mapped": True}
        self.assertFalse(self.VW.front_is_desktop([app]))
        self.assertTrue(self.VW.front_is_desktop([dict(app, minimized=True)]))
        self.assertTrue(self.VW.front_is_desktop([dict(app, activated=False)]))
        self.assertTrue(self.VW.front_is_desktop(None))

    def test_events_lock_and_dark(self):
        ipc = FakeIPC([{"type": "toplevel", "activated": True}])
        f = self.VW.DesktopFocus(ipc)
        seen = []
        f.listeners.append(seen.append)
        self.assertFalse(f.active)                                          # an app in front at login
        ipc.focus(None)
        self.assertTrue(f.active)
        ipc.focus({"type": "panel"})                                        # the Dock: no change
        self.assertTrue(f.active)
        lock = os.path.join(self.run_dir, self.VW.LOCKED_FILE)
        open(lock, "w").write("{}")
        f._changed()                                                        # (the file monitor's call)
        self.assertFalse(f.active)                                          # locked: paused
        os.unlink(lock)
        f._changed()
        open(os.path.join(self.run_dir, self.VW.DARK_FILE), "w").write("1")
        f._changed()
        self.assertFalse(f.active)                                          # the display dark
        self.assertEqual(seen, [True, False, True, False])

    def test_same_files_as_the_lock(self):
        from sonata2.shell import lockdisplay
        self.assertEqual(os.path.basename(lockdisplay._state()), self.VW.LOCKED_FILE)
        self.assertEqual(os.path.basename(lockdisplay._dark_marker()), self.VW.DARK_FILE)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class PlaybackTest(unittest.TestCase):
    def setUp(self):
        from sonata2.shell import videowallpaper as VW
        self.VW = VW
        self.path = clip()
        self.ipc = FakeIPC()
        self.old, VW.DesktopFocus._one = VW.DesktopFocus._one, VW.DesktopFocus(self.ipc)

    def tearDown(self):
        self.VW.DesktopFocus._one = self.old

    def show(self, media):
        win = Gtk.Window(child=Gtk.Picture(paintable=media), default_width=160, default_height=90)
        win.present()
        return win

    def test_silent_loop_paused_under_apps(self):
        v = self.VW.acquire(self.path)
        m = v.media
        win = self.show(m)
        settle(1200)
        if m.get_error() is not None:
            self.skipTest(f"no decoder: {m.get_error().message}")
        self.assertTrue(m.get_playing() and m.get_muted() and m.get_loop())
        self.ipc.focus({"type": "toplevel", "app-id": "org.gnome.TextEditor"})
        settle(200)
        at = m.get_timestamp()
        settle(600)
        self.assertFalse(m.get_playing())
        self.assertEqual(m.get_timestamp(), at)                             # not decoding meanwhile
        self.assertIsNotNone(m.get_current_image())                        # its frame stays on the desktop
        self.ipc.focus(None)
        settle(1800)                                                        # past the 2 s end: from the start again
        self.assertTrue(m.get_playing())
        self.assertFalse(m.get_ended())
        win.destroy()
        self.VW.release(v)
        self.assertIsNone(v.media)                                          # the decoder is gone

    def test_one_decoder_for_every_display(self):
        a, b = self.VW.acquire(self.path), self.VW.acquire(self.path)
        self.assertIs(a, b)
        self.VW.release(a)
        self.assertIsNotNone(b.media)                                       # the other display still shows it
        self.VW.release(b)
        self.assertIsNone(b.media)
        self.assertNotIn(self.path, self.VW._open)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class DesktopWindowTest(unittest.TestCase):
    def setUp(self):
        from sonata2.shell import videowallpaper as VW
        from sonata2.shell import wallpaper as W
        self.VW, self.W = VW, W
        Adw.init()
        self.cache = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache})
        self.env.start()
        self.path = clip()
        self.ipc = FakeIPC()
        self.old, VW.DesktopFocus._one = VW.DesktopFocus._one, VW.DesktopFocus(self.ipc)
        self.uri = "file://" + self.path
        self.app = Gtk.Application(application_id=f"io.github.test.videowall{os.getpid()}{id(self)}")
        self.app.register(None)

    def tearDown(self):
        self.VW.DesktopFocus._one = self.old
        self.env.stop()
        shutil.rmtree(self.cache, ignore_errors=True)

    def window(self, uris):
        get = lambda s, k, d=None: uris[0] if "picture-uri" in k else d          # noqa: E731
        with mock.patch.object(prefs, "get", side_effect=lambda s, k, d=None: get(s, k, d)), \
                mock.patch.object(self.W, "share_with_login_screen") as shared:
            win = self.W.WallpaperWindow(self.app, desktop=False)
            win.present()
            settle(1500)
        return win, shared, get

    def test_poster_then_video_then_picture(self):
        uris = [self.uri]
        win, shared, _get = self.window(uris)
        shown = win.stack.get_visible_child().get_paintable()
        self.assertIsNotNone(win._video)
        if win._video.media.get_error() is not None:
            self.skipTest("no decoder")
        self.assertIs(shown, win._video.cover)                              # the video, over its poster
        shared.assert_called()
        self.assertTrue(shared.call_args[0][0].get_path().endswith(".jpg"))    # the login screen: the poster
        v = win._video
        from sonata2 import wallpapers
        uris[0] = wallpapers.uri(wallpapers.CATALOG[1].light)
        with mock.patch.object(prefs, "get", side_effect=lambda s, k, d=None: uris[0] if "picture-uri" in k else d), \
                mock.patch.object(self.W, "share_with_login_screen"):
            win.update()
            settle(1200)
        self.assertIsNone(win._video)
        self.assertIsNone(v.media)                                          # freed once the picture faded in
        self.assertNotIsInstance(win.stack.get_visible_child().get_paintable(), self.VW.CoverPaintable)
        win.destroy()

    def test_poster_first_at_login(self):
        """At login the poster shows at once (no gradient), the video follows."""
        videowall.poster(self.path)                                         # (made when it was set)
        get = lambda s, k, d=None: self.uri if "picture-uri" in k else d      # noqa: E731
        with mock.patch.object(prefs, "get", side_effect=get), mock.patch.object(self.W, "share_with_login_screen"):
            win = self.W.WallpaperWindow(self.app, desktop=False)
        self.assertIsNotNone(win.stack.get_visible_child().get_paintable())   # before any main loop turn
        win.destroy()
        self.assertIsNone(win._video)                                        # destroyed: the decoder goes


class CoverTest(unittest.TestCase):
    """Vini: the video didn't fill the screen -- it's drawn to cover it."""

    def paintable(self, ratio=0.0, w=0, h=0):
        from unittest import mock as m
        return m.Mock(get_intrinsic_aspect_ratio=lambda: ratio, get_intrinsic_width=lambda: w,
                      get_intrinsic_height=lambda: h)

    def test_cover_sizes(self):
        from sonata2.shell import videowallpaper as VW
        self.assertEqual(VW.cover(self.paintable(16 / 9), 1920, 1080), (1920, 1080))
        self.assertEqual(VW.cover(self.paintable(16 / 9), 1080, 1920)[1], 1920)                # a tall box: as tall
        w, h = VW.cover(self.paintable(9 / 16), 2560, 1080)                 # a phone video on a wide screen
        self.assertEqual(w, 2560)
        self.assertGreater(h, 1080)
        w, h = VW.cover(self.paintable(0, 640, 360), 1000, 1000)          # no ratio: from its size
        self.assertEqual(h, 1000)
        self.assertGreater(w, 1000)
        self.assertEqual(VW.cover(self.paintable(), 300, 200), (300, 200))  # nothing known: the box


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class FillAndSilenceTest(unittest.TestCase):
    def setUp(self):
        from sonata2.shell import videowallpaper as VW
        self.VW = VW
        self.old, VW.DesktopFocus._one = VW.DesktopFocus._one, VW.DesktopFocus(FakeIPC())
        if not HAS_FFMPEG:
            self.skipTest("needs ffmpeg")
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.path = os.path.join(folder, "tone.mp4")                        # 16:9, with a sound track
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", self.path], check=True, timeout=60)

    def tearDown(self):
        self.VW.DesktopFocus._one = self.old

    def test_silent_once_playing(self):
        """Vini: it played with sound."""
        v = self.VW.acquire(self.path)
        win = Gtk.Window(child=Gtk.Picture(paintable=v.cover), default_width=200, default_height=200)
        win.present()
        settle(1200)
        m = v.media
        if m.get_error() is not None:
            self.skipTest("no decoder")
        self.assertTrue(m.is_prepared() and m.has_audio() and m.get_playing())
        self.assertTrue(m.get_muted())
        self.assertEqual(m.get_volume(), 0.0)
        with mock.patch.object(v, "_silence") as again:
            v._allowed(True)
        again.assert_called()                                               # at every play
        win.destroy()
        self.VW.release(v)

    def test_fills_a_square_screen(self):
        """A 16:9 video on a square box: no bars -- every corner is the video."""
        from gi.repository import Gdk, Graphene
        v = self.VW.acquire(self.path)
        pic = Gtk.Picture(paintable=v.cover, content_fit=Gtk.ContentFit.COVER)
        win = Gtk.Window(child=pic, default_width=200, default_height=200)
        win.present()
        settle(1200)
        if v.media.get_error() is not None:
            self.skipTest("no decoder")
        snap = Gtk.Snapshot()
        v.cover.snapshot(snap, 200, 200)
        tex = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, 200, 200))
        dl = Gdk.TextureDownloader.new(tex)
        dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = dl.download_bytes()
        px = data.get_data()
        for x, y in ((2, 2), (197, 2), (2, 197), (197, 197), (100, 100)):
            r, g, b, a = px[y * stride + 4 * x: y * stride + 4 * x + 4]
            self.assertEqual(a, 255, (x, y))
            self.assertGreater(r, 180, (x, y))                              # red: the video, not a bar
        win.destroy()
        self.VW.release(v)


class MenusTest(unittest.TestCase):
    """Vini: set a video as wallpaper from the right-click menu too."""

    def test_menus_offer_videos(self):
        for rel in (("shell", "desktop.py"), ("files", "window.py")):
            src = open(os.path.join(ROOT, "sonata2", *rel)).read()
            self.assertIn('_("Use Video as Wallpaper")', src, rel)
            self.assertIn("prefs.is_video(ctype)", src, rel)

    def test_settings_chooser_takes_videos(self):
        src = open(os.path.join(ROOT, "sonata2", "settings", "app.py")).read()
        self.assertIn('filt.add_mime_type("video/*")', src)


if __name__ == "__main__":
    unittest.main()
