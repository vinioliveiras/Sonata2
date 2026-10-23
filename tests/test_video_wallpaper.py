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

    outputs = []

    def call(self, method, data=None):
        if method == "window-rules/list-outputs":
            return self.outputs
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

    def test_black_screens_with_a_video_that_fades_in(self):
        """Vini: with a video wallpaper the lock and login screens were black
        (and Mission Control's desktops): the poster, taken 1 s in, was a
        frame of the video's fade-in. A later, brighter frame is taken."""
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "fadein.webm")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=160x90:rate=10:d=4",
                        "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:d=6", "-filter_complex",
                        "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libvpx", path],
                       check=True, timeout=60)
        out = videowall.poster(path)
        self.assertIsNotNone(out)
        self.assertGreaterEqual(videowall.brightness(out), videowall.DARK_MEAN)
        black = os.path.join(folder, "black.webm")                          # all black: still a poster
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:size=160x90:rate=10:d=3",
                        "-c:v", "libvpx", black], check=True, timeout=60)
        self.assertIsNotNone(videowall.poster(black))

    def test_login_screen_gets_the_video_too(self):
        """The login screen plays the video (blurred): it's shared beside
        the poster; a picture afterwards takes it away."""
        from gi.repository import Gio
        from sonata2.shell import wallpaper as W
        root = tempfile.mkdtemp()
        folder = os.path.join(root, GLib.get_user_name())
        os.makedirs(folder)
        poster = os.path.join(root, "poster.jpg")
        open(poster, "wb").write(b"jpg")
        W._shared = None
        with mock.patch.object(W, "GREETER_FOLDER", root):
            W.share_with_login_screen(Gio.File.new_for_path(poster), video=clip())
            settle(500)
            self.assertTrue(os.path.isfile(os.path.join(folder, W.VIDEO_NAME)))
            from sonata2.shell import greeter
            with mock.patch.object(greeter, "WALLPAPERS", root):
                self.assertEqual(greeter.user_video(GLib.get_user_name()), os.path.join(folder, W.VIDEO_NAME))
            W.share_with_login_screen(Gio.File.new_for_path(poster))
            settle(300)
            self.assertFalse(os.path.exists(os.path.join(folder, W.VIDEO_NAME)))

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
        self.assertIn("share_with_login_screen(Gio.File.new_for_path(still_file[0]), video=path)", src)  # greeter: the poster (+ video)


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
        ipc.focus(None)                                                     # (a drag: the app still in front)
        self.assertFalse(f.active)
        ipc.views = []                                                      # its window closed
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

    def test_paused_with_no_window_open(self):
        """Vini: the video paused and stayed paused with no window open: the
        window just closed was still listed as the active one when the
        focus went to nothing; the windows are asked again a moment later."""
        ipc = FakeIPC([{"type": "toplevel", "activated": True, "mapped": True}])
        f = self.VW.DesktopFocus(ipc)
        timers = []
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn) or 7):
            ipc.focus(None)                                                 # closing: still listed
            self.assertFalse(f.active)
            ipc.views = []                                                  # gone now
            timers[-1]()
        self.assertTrue(f.active)
        ipc.views = [{"type": "toplevel", "activated": True, "mapped": True}]
        ipc.focus({"type": "toplevel"})
        self.assertFalse(f.active)
        ipc.views = []
        with mock.patch.object(self.VW.GLib, "timeout_add", return_value=7):
            ipc.cb({"event": "view-unmapped", "view": {"type": "toplevel"}})   # closed with the focus kept on it
        self.assertTrue(f.active)

    def test_dragging_a_window_started_the_video(self):
        """Vini: dragging a window (nothing has the keyboard during the
        drag) started the video under it."""
        ipc = FakeIPC([{"type": "toplevel", "activated": True, "mapped": True}])
        f = self.VW.DesktopFocus(ipc)
        self.assertFalse(f.active)
        ipc.focus(None)                                                     # the drag
        self.assertFalse(f.active)
        ipc.views[0]["minimized"] = True                                    # minimized: the desktop shows
        ipc.focus(None)
        self.assertTrue(f.active)

    def test_mission_control_paused_the_video(self):
        """Vini: Super+Tab (Mission Control) left the video playing."""
        ipc = FakeIPC([])
        f = self.VW.DesktopFocus(ipc)
        seen = []
        f.listeners.append(seen.append)
        self.assertTrue(f.active)
        ipc.cb({"event": "plugin-activation-state-changed", "plugin": "scale", "state": True,
                "output-data": {"name": "DP-1"}})
        self.assertFalse(f.active)
        ipc.cb({"event": "plugin-activation-state-changed", "plugin": "zoom", "state": True})
        ipc.cb({"event": "plugin-activation-state-changed", "plugin": "scale", "state": False,
                "output-data": {"name": "DP-1"}})
        self.assertTrue(f.active)
        self.assertEqual(seen, [False, True])

    OUTS = [{"id": 1, "name": "DP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
            {"id": 2, "name": "HDMI-A-1", "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440}}]

    def test_free_displays(self):
        app = {"type": "toplevel", "mapped": True, "output-id": 2,
               "geometry": {"x": 100, "y": 100, "width": 800, "height": 600}}
        free = self.VW.free_outputs(self.OUTS, [app])
        self.assertEqual(free, {"DP-1": True, "HDMI-A-1": False})
        self.assertTrue(all(self.VW.free_outputs(self.OUTS, [dict(app, minimized=True)]).values()))
        other_desktop = dict(app, geometry={"x": 2560 + 100, "y": 0, "width": 800, "height": 600})
        self.assertTrue(all(self.VW.free_outputs(self.OUTS, [other_desktop]).values()))
        self.assertTrue(all(self.VW.free_outputs(self.OUTS, [dict(app, type="panel")]).values()))

    def test_one_display_with_windows_pauses_only_there(self):
        """Vini: the video went on only while the desktop had the focus, on
        every display alike; now a display with no window keeps playing and
        one with a window shows it paused (a switch turns that off), and
        another switch keeps it playing whatever windows are open."""
        ipc = FakeIPC([{"type": "toplevel", "activated": True, "mapped": True, "output-id": 2,
                        "geometry": {"x": 0, "y": 0, "width": 2560, "height": 1440}}])
        ipc.outputs = self.OUTS
        cfg = {"video_per_display": True, "video_always": False}
        with mock.patch("sonata2.config.load", side_effect=lambda n, d: dict(d, **cfg)):
            f = self.VW.DesktopFocus(ipc)
            self.assertTrue(f.active)                                       # DP-1 is free: the player runs
            self.assertTrue(f.playing_on("DP-1"))
            self.assertFalse(f.playing_on("HDMI-A-1"))                      # the one with the window: paused
            cfg["video_per_display"] = False                                # switched off: as before
            f._setting_changed()
            self.assertFalse(f.active)                                      # (an app has the focus)
            cfg["video_always"] = True                                      # keep playing with windows
            f._setting_changed()
            self.assertTrue(f.active)
            self.assertTrue(f.playing_on("HDMI-A-1"))
            ipc.cb({"event": "plugin-activation-state-changed", "plugin": "scale", "state": True})
            self.assertFalse(f.active)                                      # Overview still pauses it

    def test_display_with_windows_shows_the_last_frame(self):
        from gi.repository import Gdk
        frame = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(b"\xff" * 16), 8)
        focus = mock.Mock(active=True, display_listeners=[])
        focus.playing_on = lambda d: d == "DP-1"
        media = mock.Mock(**{"get_current_image.return_value": frame})
        video = mock.Mock(media=media, focus=focus, cover=self.VW.CoverPaintable(None))
        here, there = self.VW.DisplayCover(video, "HDMI-A-1"), self.VW.DisplayCover(video, "DP-1")
        self.assertIs(here.frozen, frame)                                   # still: its last frame
        self.assertIsNone(there.frozen)                                     # playing
        focus.playing_on = lambda d: True                                   # its window closed
        for fn in list(focus.display_listeners):
            fn()
        self.assertIsNone(here.frozen)
        here.release()
        there.release()
        self.assertEqual(focus.display_listeners, [])

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
        self.assertTrue(m.get_playing() and m.get_muted())
        self.assertFalse(m.get_loop())                                      # looped by hand (_again)
        self.ipc.focus({"type": "toplevel", "app-id": "org.gnome.TextEditor"})
        settle(200)
        at = m.get_timestamp()
        settle(600)
        self.assertFalse(m.get_playing())
        self.assertEqual(m.get_timestamp(), at)                             # not decoding meanwhile
        self.assertIsNotNone(m.get_current_image())                        # its frame stays on the desktop
        self.ipc.focus(None)
        settle(2600)                                                        # past the 2 s end: from the start again
        self.assertTrue(v.media.get_playing())
        self.assertFalse(v.media.get_ended())
        self.assertTrue(v.media.get_muted())
        win.destroy()
        self.VW.release(v)
        self.assertIsNone(v.media)                                          # the decoder is gone

    def test_long_video_went_black_after_its_end(self):
        """Vini: a big video wallpaper went black some time after its end.
        The loop is a new player now, the last frame fading into the first;
        the picture is never empty meanwhile."""
        v = self.VW.acquire(self.path)
        self.addCleanup(lambda: v.media is not None and self.VW.release(v))
        win = self.show(v.cover)
        first = v.media
        settle(1000)
        if first.get_error() is not None:
            self.skipTest(f"no decoder: {first.get_error().message}")
        faded, end = [], GLib.get_monotonic_time() + 4_000_000
        while v.media is first and GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
            self.assertIsNotNone(v.cover.get_current_image())            # never nothing to show
        self.assertIsNot(v.media, first)                                    # a new player from the start
        self.assertIn(first, self.VW._settling)                             # the old one let go of
        faded.append(v.cover.fade_alpha())
        self.assertGreater(faded[0], 0.5)                                   # the last frame over it, fading
        settle(self.VW.FADE_MS + 200)
        self.assertEqual(v.cover.fade_alpha(), 0.0)
        self.assertTrue(v.media.get_playing())
        win.destroy()

    def test_sound_off_by_default_and_on_at_its_volume(self):
        """Vini: a switch in Settings for the video's sound (off by default)
        and its volume (25 % by default)."""
        self.assertEqual(self.VW.SOUND_DEFAULTS, {"video_sound": False, "video_volume": 25})
        with mock.patch("sonata2.config.load", return_value=dict(self.VW.SOUND_DEFAULTS)):
            self.assertEqual(self.VW.sound(), (False, 0.25))
        with mock.patch.object(self.VW, "sound", return_value=(True, 0.25)):
            v = self.VW.VideoWallpaper(self.path)
            self.addCleanup(v.close)
            settle(800)
            if v.media.get_error() is not None:
                self.skipTest("no decoder")
            self.assertFalse(v.media.get_muted())
            self.assertAlmostEqual(v.media.get_volume(), 0.25, places=2)
            first = v.media
            with mock.patch.object(self.VW, "sound", return_value=(True, 0.6)):
                v._sound_changed()                                          # a new volume: at once
            self.assertIs(v.media, first)
            self.assertAlmostEqual(v.media.get_volume(), 0.6, places=2)
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            reg = Gst.Registry.get()
            ranks = {n: reg.lookup_feature(n).get_rank() for n in self.VW.AUDIO_SINKS if reg.lookup_feature(n)}
            if "pulsesink" in ranks:                                        # Vini: black picture, sound playing
                self.assertEqual(ranks["pulsesink"], max(ranks.values()))   # not every sink back at one rank
                self.assertEqual(list(ranks.values()).count(ranks["pulsesink"]), 1)
            with mock.patch.object(self.VW, "sound", return_value=(False, 0.6)):
                v._sound_changed()                                          # off: a silent player, faded in
                settle(800)
            self.assertIsNot(v.media, first)
            self.assertTrue(v.media.get_muted())
        seen = []
        with mock.patch.object(self.VW.Gtk.MediaFile, "new_for_filename",
                               side_effect=lambda p: seen.append(os.environ.get("GST_PLUGIN_FEATURE_RANK"))):
            self.VW.new_player("/x.mp4", True)
            self.VW.new_player("/x.mp4", False)
        self.assertNotIn("pulsesink:0", seen[0] or "")                      # with sound: the sinks stay
        self.assertIn("pulsesink:0", seen[1])

    def test_lock_screen_plays_the_video_blurred(self):
        """Vini: the lock screen was black with a video wallpaper: it plays
        the video under its blur (silent, its own player, paused once the
        display is dark), the poster until the first frame."""
        from sonata2.shell import loginui
        v = self.VW.VideoWallpaper(self.path, focus=self.VW.AwakeGate(), silent=True)
        self.addCleanup(v.close)
        bd = loginui.Backdrop(None, video=v.cover)
        win = Gtk.Window(child=bd, default_width=200, default_height=120)
        win.present()
        settle(1000)
        if v.media.get_error() is not None:
            self.skipTest("no decoder")
        self.assertTrue(v.cover.has_frame())
        self.assertTrue(v.media.get_playing())
        self.assertTrue(v.media.get_muted())                                 # silent whatever Settings says
        snap = Gtk.Snapshot()
        bd.do_snapshot(snap)
        node = snap.to_node()

        def kinds(n):
            yield type(n).__name__
            if hasattr(n, "get_n_children"):
                for i in range(n.get_n_children()):
                    yield from kinds(n.get_child(i))
            elif hasattr(n, "get_child"):
                yield from kinds(n.get_child())
        self.assertIn("BlurNode", list(kinds(node)))                         # the video, blurred
        win.destroy()

    def test_sound_on_put_every_sink_back_at_one_rank(self):
        """Vini: with the sound turned on the wallpaper went black, its sound
        playing: the sinks turned off before were all put back at one rank
        (OpenAL, JACK or OSS could win over PulseAudio / PipeWire)."""
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        reg = Gst.Registry.get()
        if reg.lookup_feature("pulsesink") is None:
            self.skipTest("no pulsesink")
        self.VW._sink_ranks.clear()
        for n in self.VW.AUDIO_SINKS:                                       # off before this process saw them
            f = reg.lookup_feature(n)
            if f is not None:
                f.set_rank(0)
        self.VW._audio_sinks(True)
        ranks = {n: reg.lookup_feature(n).get_rank() for n in self.VW.AUDIO_SINKS if reg.lookup_feature(n)}
        self.assertEqual(ranks["pulsesink"], 266)
        self.assertEqual(max(ranks.values()), 266)
        self.assertEqual(list(ranks.values()).count(266), 1)
        self.VW._audio_sinks(False)
        self.assertEqual(reg.lookup_feature("pulsesink").get_rank(), 0)
        self.VW._audio_sinks(True)

    def test_overview_desktop_showed_the_poster_not_the_paused_frame(self):
        """Vini: Overview's desktop thumbnail showed the video's poster; it
        shows the frame the video paused on as Overview opened."""
        import time
        run = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": run}):
            v = self.VW.acquire(self.path)
            self.addCleanup(lambda: v.media is not None and self.VW.release(v))
            win = self.show(v.cover)
            settle(1200)
            if v.media.get_error() is not None:
                self.skipTest("no decoder")
            self.ipc.cb({"event": "plugin-activation-state-changed", "plugin": "scale", "state": True})
            frame = os.path.join(run, self.VW.FRAME_FILE)
            end = time.monotonic() + 3
            while not os.path.exists(frame) and time.monotonic() < end:
                settle(50)
            self.assertTrue(os.path.isfile(frame))
            from gi.repository import GdkPixbuf
            _f, w, h = GdkPixbuf.Pixbuf.get_file_info(frame)
            self.assertLessEqual(max(w, h), self.VW.FRAME_PX)
            from sonata2.shell import mission
            uri = "file://" + self.path
            with mock.patch.object(prefs, "wallpaper_uri", return_value=uri):
                self.assertIsNotNone(mission.video_frame(False))            # Overview: that frame
            with mock.patch.object(prefs, "wallpaper_uri", return_value="file:///x/picture.jpg"):
                self.assertIsNone(mission.video_frame(False))               # a picture: its own
            self.ipc.cb({"event": "plugin-activation-state-changed", "plugin": "scale", "state": False})
            win.destroy()

    def test_stalled_or_failed_player_is_started_again(self):
        """A player that stops giving frames (or fails) while it should
        play: started again on a new one -- a few times, not forever."""
        made = []

        def player(_path, _sound=False):
            m = mock.Mock(**{"get_timestamp.return_value": 5, "get_error.return_value": None,
                             "get_ended.return_value": False, "get_current_image.return_value": None,
                             "is_prepared.return_value": True})
            made.append(m)
            return m
        clock = [0]
        with mock.patch.object(self.VW, "new_player", side_effect=player), \
                mock.patch.object(self.VW.GLib, "timeout_add", return_value=1), \
                mock.patch.object(self.VW.GLib, "source_remove"), \
                mock.patch.object(self.VW.GLib, "get_monotonic_time", side_effect=lambda: clock[0]):
            v = self.VW.VideoWallpaper("/nowhere/stuck.mp4")
            v._check()                                                      # first look: its frame noted
            clock[0] += (self.VW.STALL_MS - 1000) * 1000
            v._check()
            self.assertEqual(len(made), 1)                                  # not yet
            clock[0] += 2000 * 1000
            v._check()
            self.assertEqual(len(made), 2)                                  # stuck: a new player
            for _ in range(10):                                             # the new one never shows a frame
                v._next = None
                clock[0] += self.VW.STALL_MS * 1000
                v._check()
            self.assertLessEqual(len(made), 1 + self.VW.RETRY_MAX + 1)      # then not every few seconds...
            n = len(made)
            v._next = None
            clock[0] += self.VW.RETRY_LATER_MS * 1000
            v._check()
            self.assertEqual(len(made), n + 1)                              # ...but never given up on
            v.close()

    def test_login_screen_video_stayed_paused_at_boot(self):
        """Vini: after a restart the login screen's video stayed paused
        (GStreamer slow to open it at boot: it was given up on); and the
        desktop's paused by itself with no window open. A player still
        opening is waited for; one left paused while it should play plays."""
        m = mock.Mock(**{"get_timestamp.return_value": 0, "get_error.return_value": None,
                         "get_ended.return_value": False, "get_current_image.return_value": None,
                         "is_prepared.return_value": False, "get_playing.return_value": False})
        made = [m]
        clock = [0]
        with mock.patch.object(self.VW, "new_player", side_effect=lambda *_a: made[0] if len(made) == 1 and not
                               made.append(None) else mock.Mock()), \
                mock.patch.object(self.VW.GLib, "timeout_add", return_value=1), \
                mock.patch.object(self.VW.GLib, "source_remove"), \
                mock.patch.object(self.VW.GLib, "get_monotonic_time", side_effect=lambda: clock[0]):
            v = self.VW.VideoWallpaper("/nowhere/slow.mp4", focus=mock.Mock(active=True, listeners=[]))
            for _ in range(10):                                             # 20 s opening: waited for
                clock[0] += self.VW.WATCH_MS * 1000
                v._check()
            self.assertIs(v.media, m)
            self.assertIsNone(v._next)
            m.is_prepared.return_value = True                               # open, but left paused
            m.play.reset_mock()
            v._check()
            m.play.assert_called_once()
            v.close()

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
        self.assertIs(shown, win._cover)                                    # the video, over its poster
        self.assertIsInstance(shown, self.VW.DisplayCover)
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
        self.assertNotIsInstance(win.stack.get_visible_child().get_paintable(), self.VW.DisplayCover)
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


class ChangedSeveralTimesTest(unittest.TestCase):
    """Vini: "it froze when I changed the wallpaper several times" and "it
    still plays sound"."""

    def setUp(self):
        from sonata2.shell import videowallpaper as VW
        self.VW = VW

    def test_player_freed_only_once_paused_and_settled(self):
        """A player freed while GStreamer's thread still held it was never
        freed (pipeline, threads, ~30 MB at every change)."""
        media = mock.Mock(**{"is_prepared.return_value": False, "get_error.return_value": None})
        timers = []
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)):
            self.VW._let_go(media)
        media.pause.assert_called()                                         # paused at once
        media.clear.assert_not_called()
        self.assertIn(media, self.VW._settling)                             # held meanwhile
        self.assertTrue(timers[0]())                                        # still opening: wait
        media.clear.assert_not_called()
        media.is_prepared.return_value = True
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)), \
                mock.patch.object(self.VW, "_off_main", side_effect=lambda fn, done: (fn(), done())):
            self.assertFalse(timers[0]())
        media.clear.assert_called_once()
        self.assertNotIn(media, self.VW._settling)
        self.assertIs(timers[-1], self.VW._trim)                            # then the memory goes back
        self.assertFalse(self.VW._trim())

    def test_wallpaper_stuck_after_leaving_a_video(self):
        """Vini: after a video wallpaper, the first picture chosen showed and
        the next ones never did until Sonata restarted -- freeing the video's
        player (GStreamer waiting for its threads) held the main thread. It
        is freed on a thread of its own now."""
        import threading
        import time
        release = threading.Event()
        media = mock.Mock(**{"is_prepared.return_value": True, "get_error.return_value": None})
        media.clear.side_effect = lambda: release.wait(5)                   # a teardown that hangs
        timers = []
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)):
            self.VW._let_go(media)
            t0 = time.monotonic()
            self.assertFalse(timers[0]())
            self.assertLess(time.monotonic() - t0, 0.5)                     # the desktop goes on
            self.assertIn(media, self.VW._settling)                         # held until it's done
            release.set()
            end = time.monotonic() + 3
            while media in self.VW._settling and time.monotonic() < end:
                GLib.MainContext.default().iteration(False)
        self.assertNotIn(media, self.VW._settling)

    def test_one_that_never_opens_is_freed_anyway(self):
        media = mock.Mock(**{"is_prepared.return_value": False, "get_error.return_value": None})
        timers = []
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)):
            self.VW._let_go(media)
        with mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)), \
                mock.patch.object(self.VW, "_off_main", side_effect=lambda fn, done: (fn(), done())):
            while timers[0]():
                pass
        media.clear.assert_called_once()

    def test_no_audio_sink_for_the_wallpaper_only(self):
        """No sound reaches the speakers (muting alone didn't do it); the
        ranks are set only while the player starts: an app opened from the
        desktop keeps its sound."""
        seen = {}
        fake = lambda path: seen.setdefault("ranks", os.environ.get("GST_PLUGIN_FEATURE_RANK"))   # noqa: E731
        for before in (None, "nvh264dec:0"):
            seen.clear()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GST_PLUGIN_FEATURE_RANK", None)
                if before:
                    os.environ["GST_PLUGIN_FEATURE_RANK"] = before
                with mock.patch.object(self.VW.Gtk.MediaFile, "new_for_filename", side_effect=fake):
                    self.VW.new_player("/x.mp4")
                self.assertEqual(os.environ.get("GST_PLUGIN_FEATURE_RANK"), before)
            for sink in ("pulsesink", "pipewiresink", "alsasink"):
                self.assertIn(f"{sink}:0", seen["ranks"])
            if before:
                self.assertTrue(seen["ranks"].startswith(before))
        self.assertEqual(self.VW.silent_ranks("pulsesink:256").count("pulsesink"), 1)   # what was set stays

    def test_changing_the_wallpaper_frees_its_player_once_settled_and_silent(self):
        """The same two bugs through acquire / release, as the desktop changes
        the wallpaper: its player made without audio sinks, and not cleared
        at once when let go (freed while GStreamer still held it, it never
        was: Vini's freeze after several changes)."""
        ranks, timers = [], []

        def made(path):
            ranks.append(os.environ.get("GST_PLUGIN_FEATURE_RANK") or "")
            return mock.Mock(**{"is_prepared.return_value": False, "get_error.return_value": None})

        old, self.VW.DesktopFocus._one = self.VW.DesktopFocus._one, self.VW.DesktopFocus(FakeIPC())
        self.addCleanup(setattr, self.VW.DesktopFocus, "_one", old)
        with mock.patch.object(self.VW.Gtk.MediaFile, "new_for_filename", side_effect=made), \
                mock.patch.object(self.VW.GLib, "timeout_add", side_effect=lambda ms, fn: timers.append(fn)):
            v = self.VW.acquire("/nowhere/changed-several-times.mp4")
            media = v.media
            self.VW.release(v)
        media.clear.assert_not_called()                                     # not while it may be held
        self.addCleanup(self.VW._settling.discard, media)
        self.assertIn("pulsesink:0", ranks[0])
        self.assertIn(media, self.VW._settling)
        self.assertTrue(timers)


class NarrowFrameTest(unittest.TestCase):
    """Vini: the video wallpaper left a black band down the right of the
    screen (only a video, never a picture). GTK's GStreamer picture draws
    its frame at frame size / video size of what it's asked, from the left:
    a decoded frame a little narrower than the video (hardware decoders)
    stopped short of the right edge."""

    def media(self, frame_w, frame_h, video_w=1920, video_h=1080):
        from gi.repository import Gdk, GObject

        class Frame(GObject.Object, Gdk.Paintable):
            def do_get_intrinsic_width(self):
                return frame_w

            def do_get_intrinsic_height(self):
                return frame_h

        class Video(GObject.Object, Gdk.Paintable):
            asked = []

            def do_get_intrinsic_width(self):
                return video_w

            def do_get_intrinsic_height(self):
                return video_h

            def do_get_intrinsic_aspect_ratio(self):
                return video_w / video_h

            def do_get_current_image(self):
                return Frame()

            def do_snapshot(self, snap, w, h):
                self.asked.append((w, h))
        return Video()

    def test_a_narrower_frame_still_reaches_the_right_edge(self):
        from sonata2.shell import videowallpaper as VW
        v = self.media(1909, 1080)
        cover = VW.CoverPaintable(v)
        snap = Gtk.Snapshot()
        cover.snapshot(snap, 2560, 1440)
        w, h = v.asked[-1]
        drawn = w * 1909 / 1920                      # what GTK's picture then draws
        self.assertAlmostEqual(drawn, 2560, delta=0.5)
        self.assertAlmostEqual(h, 1440, delta=0.5)
        cover.release()

    def test_a_whole_or_padded_frame_is_drawn_as_asked(self):
        from sonata2.shell import videowallpaper as VW
        for fw, fh in ((1920, 1080), (1920, 1088)):     # (padding: GTK cuts it itself)
            v = self.media(fw, fh)
            cover = VW.CoverPaintable(v)
            cover.snapshot(Gtk.Snapshot(), 2560, 1440)
            self.assertEqual(tuple(round(x) for x in v.asked[-1]), (2560, 1440), (fw, fh))
            cover.release()


class MenusTest(unittest.TestCase):
    """Vini: set a video as wallpaper from the right-click menu too."""

    def test_menus_offer_videos(self):
        for rel in (("shell", "desktop.py"), ("files", "window.py")):
            src = open(os.path.join(ROOT, "sonata2", *rel)).read()
            self.assertIn('_("Use Video as Wallpaper")', src, rel)
            self.assertIn("prefs.is_video(ctype)", src, rel)

    def test_second_wallpaper_change_didnt_show(self):
        """Vini: after a video wallpaper, the second wallpaper chosen didn't
        show until Sonata restarted. Choosing one (Settings, Files, the
        desktop's menu) starts the desktop's wallpaper again, fresh -- once
        for quick changes, and not when nothing changed."""
        stored = {}
        calls = []
        with mock.patch.object(prefs, "get", side_effect=lambda sc, k, d=None: stored.get(k, d)), \
                mock.patch.object(prefs, "set", side_effect=lambda sc, k, v: stored.__setitem__(k, v)), \
                mock.patch("sonata2.backend.system.restart_sonata", side_effect=lambda *n: calls.append(n)), \
                mock.patch.object(prefs, "_has_loop", return_value=True):
            prefs.set_wallpaper("file:///a.jpg", "file:///b.jpg")
            prefs.set_wallpaper("file:///c.jpg")
            self.assertEqual(calls, [])                                     # waits for changes to settle
            settle(prefs.RELOAD_AFTER_MS + 200)
            self.assertEqual(calls, [("wallpaper",)])                       # once, only the wallpaper
            self.assertEqual((stored["picture-uri"], stored["picture-uri-dark"]), ("file:///c.jpg",) * 2)
            prefs.set_wallpaper("file:///c.jpg")                            # the same: nothing to do
            settle(prefs.RELOAD_AFTER_MS + 200)
            self.assertEqual(len(calls), 1)

    def test_restart_kills_a_stuck_wallpaper(self):
        """A wallpaper stuck in GStreamer ignores SIGTERM for a while: it's
        killed, so the fresh one is the only one."""
        import sonata2.__main__ as M
        sent = []
        with mock.patch.object(M, "_pids", side_effect=lambda pat: [4242] if pat.startswith("-m sonata2 wallpaper") else []), \
                mock.patch.object(M, "_signal", side_effect=lambda pid, sig: sent.append((pid, sig))), \
                mock.patch("subprocess.Popen"), mock.patch("time.sleep"), \
                mock.patch("os.path.exists", side_effect=lambda p: p == "/proc/4242"), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tempfile.mkdtemp()}):
            M.restart(["wallpaper"])
        import signal
        self.assertIn((4242, signal.SIGTERM), sent)
        self.assertIn((4242, signal.SIGKILL), sent)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
    def test_settings_chooser_did_nothing(self):
        """Vini: Settings' Choose... for the wallpaper didn't work (GTK's file
        dialog through the portal); it's Sonata's own Open panel now, for
        pictures and videos, and its row says so."""
        from gi.repository import Adw as A
        from sonata2.files.chooser import ChooserWindow
        from sonata2.settings import app as S
        A.init()
        w = S.Settings(None, "displays")
        w.present()
        settle(300)
        try:
            row = next(r for r in _walk(w.pages["displays"]) if isinstance(r, A.ActionRow)
                       and r.get_title() == "Picture or Video")
            btn = next(b for b in _walk(row) if isinstance(b, Gtk.Button) and b.get_label() == "Choose…")
            set_to = {}
            with mock.patch.object(prefs, "set_wallpaper", side_effect=lambda u, d=None: set_to.update(
                    {"picture-uri": u, "picture-uri-dark": d or u})):
                btn.emit("clicked")
                settle(300)
                dlg = w.wall_chooser
                self.assertIsInstance(dlg, ChooserWindow)
                rules = dlg.filters[0][1] if hasattr(dlg, "filters") else []
                self.assertIn((1, "video/*"), rules)
                dlg.on_done(["file://" + clip()], 0)
            self.assertEqual(set_to.get("picture-uri"), "file://" + clip())
            self.assertEqual(set_to.get("picture-uri-dark"), "file://" + clip())
            dlg.destroy()
        finally:
            w.destroy()


def _walk(w):
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from _walk(c)
        c = c.get_next_sibling()


if __name__ == "__main__":
    unittest.main()
