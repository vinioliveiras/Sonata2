"""Review tests for the menu bar process and its panels (topbar, tray,
notifications, capture, sharepicker, spotlight, switcher, clipboard,
clip_picker, osd, mpris, nightshift, monitors, emoji).
Run: xvfb-run -a python3.12 -m unittest tests.test_review_topbar -v

Mostly pure logic: no D-Bus, no Wayfire, no grim/wl-paste; the real home
and config are never touched (XDG_CONFIG_HOME is a temp folder)."""
import base64
import datetime
import io
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, GdkPixbuf, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.backend import system  # noqa: E402
from sonata2.shell import capture, clip_picker, clipboard, mpris, nightshift, notifications as N  # noqa: E402
from sonata2.shell import monitors, osd, sharepicker, spotlight, topbar, tray  # noqa: E402


def setUpModule():
    Adw.init()
    ui.setup()


def sync_run_async(fn, callback=None, *args):
    """system.run_async, but at once on this thread."""
    res = fn(*args)
    if callback:
        callback(res)


def png_bytes(w, h):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    pb.fill(0xff0000ff)
    ok, data = pb.save_to_bufferv("png", [], [])
    return bytes(data)


# -- nightshift ---------------------------------------------------------------------------------
class NightShiftLogicTest(unittest.TestCase):
    def test_temperature_range_and_clamp(self):
        """Warmth 0..100 maps to 5500..2700 K and out-of-range values are clamped."""
        self.assertEqual(nightshift.temperature(0), 5500)
        self.assertEqual(nightshift.temperature(100), 2700)
        self.assertEqual(nightshift.temperature(-20), 5500)
        self.assertEqual(nightshift.temperature(250), 2700)

    def test_tz_location_parses_both_coordinate_forms(self):
        """zone1970.tab coordinates: ±DDMM±DDDMM and ±DDMMSS±DDDMMSS, both signs."""
        tab = os.path.join(tempfile.mkdtemp(), "zone1970.tab")
        with open(tab, "w") as f:
            f.write("# comment\n"
                    "AU\t-3352+15113\tAustralia/Sydney\tNew South Wales\n"
                    "US\t+404251-0740023\tAmerica/New_York\tEastern\n")
        with mock.patch.object(nightshift, "ZONE_TAB", tab):
            lat, lon = nightshift.tz_location("Australia/Sydney")
            self.assertAlmostEqual(lat, -33.8667, places=3)
            self.assertAlmostEqual(lon, 151.2167, places=3)
            lat, lon = nightshift.tz_location("America/New_York")
            self.assertAlmostEqual(lat, 40.7142, places=3)
            self.assertAlmostEqual(lon, -74.0064, places=3)
            self.assertIsNone(nightshift.tz_location("Mars/Olympus"))

    def test_command_off_custom_manual_and_sunset_fallback(self):
        """wlsunset arguments for each schedule; an unknown time zone falls back to custom hours."""
        base = dict(nightshift.DEFAULTS, warmth=50)
        self.assertEqual(nightshift.command(base), [])
        custom = nightshift.command(dict(base, schedule="custom", **{"from": "21:30", "to": "06:45"}))
        self.assertEqual(custom[:3], ["wlsunset", "-t", "4100"])
        self.assertEqual(custom[custom.index("-S") + 1], "06:45")      # day starts
        self.assertEqual(custom[custom.index("-s") + 1], "21:30")      # night starts
        future = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat(timespec="minutes")
        manual = nightshift.command(dict(base, manual_until=future))
        self.assertEqual(manual, ["wlsunset", "-t", "4100", "-T", "4101"])     # constant warmth
        with mock.patch.object(nightshift, "tz_location", return_value=None):
            fallback = nightshift.command(dict(base, schedule="sunset"))
        self.assertIn("-S", fallback)
        with mock.patch.object(nightshift, "tz_location", return_value=(-23.5, -46.62)):
            sun = nightshift.command(dict(base, schedule="sunset"))
        self.assertEqual(sun[sun.index("-l") + 1], "-23.50")
        self.assertEqual(sun[sun.index("-L") + 1], "-46.62")

    def test_manual_until_expiry_and_garbage(self):
        """Turn On Until Tomorrow ends at 07:00 in the future; a bad stamp means off."""
        until = datetime.datetime.fromisoformat(nightshift.until_tomorrow())
        self.assertGreater(until, datetime.datetime.now())
        self.assertEqual((until.hour, until.minute), (7, 0))
        self.assertTrue(nightshift.manual_active({"manual_until": nightshift.until_tomorrow()}))
        self.assertFalse(nightshift.manual_active({"manual_until": "2001-01-01T07:00"}))
        self.assertFalse(nightshift.manual_active({"manual_until": "not a date"}))
        self.assertFalse(nightshift.manual_active({}))

    def test_service_starts_restarts_and_stops_wlsunset(self):
        """NightShift.apply: starts once, leaves a running identical process alone,
        restarts a dead one and stops it when Night Shift is turned off."""
        ns = nightshift.NightShift.__new__(nightshift.NightShift)       # no timers, no file watch
        ns.proc, ns.cmd, ns._gen, ns._waiting = None, None, 0, False
        procs = []

        def popen(cmd, **_kw):
            p = mock.Mock()
            p.poll.return_value = None
            p.cmd = cmd
            procs.append(p)
            return p
        cfg = dict(nightshift.DEFAULTS, schedule="custom")
        with mock.patch.object(nightshift.shutil, "which", return_value="/usr/bin/wlsunset"), \
                mock.patch.object(nightshift.subprocess, "Popen", side_effect=popen), \
                mock.patch.object(nightshift.config, "load", side_effect=lambda *_a: dict(cfg)):
            ns.apply()
            ns.apply()
            self.assertEqual(len(procs), 1)
            procs[0].poll.return_value = 1                   # wlsunset died
            ns.apply()
            self.assertEqual(len(procs), 2)
            cfg["schedule"] = "off"
            ns.apply()
            procs[1].send_signal.assert_called_once()
            self.assertIsNone(ns.proc)

    def test_is_on_inside_the_schedule(self):
        """The Control Center toggle is on while the custom schedule is active."""
        now = datetime.datetime.now()
        config.save("nightshift", dict(nightshift.DEFAULTS, schedule="custom",
                                       **{"from": (now - datetime.timedelta(hours=1)).strftime("%H:%M"),
                                          "to": (now + datetime.timedelta(hours=1)).strftime("%H:%M")}))
        try:
            self.assertTrue(nightshift.is_on())
        finally:
            config.save("nightshift", nightshift.DEFAULTS)


# -- clipboard history --------------------------------------------------------------------------
class ClipboardHistoryTest(unittest.TestCase):
    def history(self):
        with mock.patch.object(clipboard.shutil, "which", return_value=None):
            h = clipboard.History()
        self.assertFalse(h.available)
        return h

    def test_clip_image_size_and_identity(self):
        """A PNG copy reads its size from the header; equal bytes are the same copy."""
        png = png_bytes(8, 5)
        a, b = clipboard.ClipImage(png), clipboard.ClipImage(png)
        self.assertEqual(a.size, (8, 5))
        self.assertEqual(str(a), "Image · 8×5")
        self.assertEqual(a, b)
        self.assertEqual(len({a, b}), 1)
        self.assertFalse(a == "Image · 8×5")               # never equal to a text copy
        self.assertEqual(str(clipboard.ClipImage(b"junk")), "Image")
        tex = a.texture(4, 4)
        self.assertLessEqual(max(tex.get_width(), tex.get_height()), 4)

    def test_dedupe_newest_first_and_caps(self):
        """Copying again moves an item to the top; text is capped at KEEP, images at KEEP_IMAGES."""
        h = self.history()
        calls = []
        h.listeners.append(lambda: calls.append(1))
        h._add("one")
        h._add("two")
        h._add("one")
        self.assertEqual(h.items, ["one", "two"])
        h._add("   \n")                                       # whitespace: ignored
        self.assertEqual(len(calls), 3)
        for i in range(clipboard.KEEP + 5):
            h._add(f"t{i}")
        self.assertEqual(len(h.items), clipboard.KEEP)
        self.assertEqual(h.items[0], f"t{clipboard.KEEP + 4}")
        for i in range(clipboard.KEEP_IMAGES + 2):
            h._add(clipboard.ClipImage(png_bytes(i + 1, 1)))
        imgs = [t for t in h.items if isinstance(t, clipboard.ClipImage)]
        self.assertEqual(len(imgs), clipboard.KEEP_IMAGES)
        self.assertEqual(imgs[0].size, (clipboard.KEEP_IMAGES + 2, 1))      # newest kept

    def test_watch_stream_protocol(self):
        """wl-paste output ("T"text / "I"base64 PNG, NUL-separated, split across reads) becomes items;
        oversized text is dropped."""
        png = png_bytes(3, 2)
        stream = (b"Thello\0I" + base64.b64encode(png) + b"\0T" + b"x" * (clipboard.MAX_BYTES + 1) + b"\0Tbye\0")

        class Chunked(io.BytesIO):
            def read1(self, _n=-1):
                return super().read1(7)                      # tiny reads: items cut mid-way
        proc = mock.Mock(stdout=Chunked(stream))
        h = self.history()
        with mock.patch.object(clipboard.subprocess, "Popen", return_value=proc), \
                mock.patch.object(clipboard.GLib, "idle_add", side_effect=lambda fn, *a: fn(*a)):
            h._watch()
        self.assertEqual(h.items[0], "bye")
        self.assertIsInstance(h.items[1], clipboard.ClipImage)
        self.assertEqual(h.items[1].size, (3, 2))
        self.assertEqual(h.items[2], "hello")
        self.assertEqual(len(h.items), 3)

    def test_picker_text_helpers_and_terminal_detection(self):
        """Super+V rows: preview keeps the first non-empty lines; terminals get Ctrl+Shift+V."""
        self.assertEqual(clip_picker._preview("\n  a \n\n b\nc\nd"), "a\nb\nc")
        self.assertEqual(clip_picker._meta("x"), "1 character")
        self.assertEqual(clip_picker._meta("ab\ncd"), "5 characters · 2 lines")

        def views(app_id):
            ipc = mock.Mock()
            ipc.return_value.call.return_value = [{"app-id": "other", "activated": False},
                                                  {"app-id": app_id, "activated": True}]
            return mock.patch("sonata2.wl.wfipc.WayfireIPC", ipc)
        with views("org.gnome.Ptyxis"):
            self.assertTrue(clip_picker._terminal_focused())
        with views("firefox"):
            self.assertFalse(clip_picker._terminal_focused())


# -- notifications ------------------------------------------------------------------------------
class NotifyServerTest(unittest.TestCase):
    """notify(): ids, replacement, actions, images, per-app settings (no D-Bus, no windows)."""

    def setUp(self):
        config.save("notifications", {"dnd": False, "apps": {}})
        n = self.nc = N.Notifications.__new__(N.Notifications)
        n.notes, n._next, n.nc, n.listeners, n._conn, n._banners = [], 1, None, [], None, {}
        n.cfg = config.load("notifications", N.DEFAULTS)
        self.banners = []
        n._banner = lambda note: (self.banners.append(note.id), n._banners.__setitem__(note.id, None))
        n._hide_banner = lambda nid, animate=True: n._banners.pop(nid, None)
        self.locked = mock.patch.object(N, "locked", return_value=False)
        self.locked.start()

    def tearDown(self):
        self.locked.stop()

    def send(self, app="Chat", replaces=0, actions=(), hints=None):
        return self.nc.notify(app, replaces, "", "Hi", "body", list(actions),
                              dict({"desktop-entry": app.lower()}, **(hints or {})), -1)

    def set_app(self, key, **values):
        cfg = config.load("notifications", N.DEFAULTS)
        cfg["apps"][key] = dict(N.APP_DEFAULTS, name=key, **values)
        config.save("notifications", cfg)
        self.nc.cfg = cfg

    def test_ids_and_replacement(self):
        """New notifications get new ids; replaces_id of a listed one keeps its id and slot."""
        a, b = self.send(), self.send()
        self.assertEqual((a, b), (1, 2))
        self.assertEqual(self.send(replaces=a), a)
        self.assertEqual([n.id for n in self.nc.notes], [b, a])      # replaced one is now newest
        self.assertEqual(self.send(replaces=99), 3)                  # unknown id: a new one

    def test_actions_and_image_hints(self):
        """Actions pair up (a dangling key is dropped); file:// image paths become paths, others go."""
        self.send(actions=["default", "Open", "reply", "Reply", "orphan"],
                  hints={"image-path": "file:///tmp/shot%20one.png"})
        n = self.nc.notes[-1]
        self.assertEqual(n.actions, [("default", "Open"), ("reply", "Reply")])
        self.assertEqual(n.image, "/tmp/shot one.png")
        self.send(hints={"image-path": "relative.png"})
        self.assertEqual(self.nc.notes[-1].image, "")

    def test_new_app_listed_in_settings(self):
        """An app's first notification adds it to Settings > Notifications with defaults."""
        self.send(app="Telegram")
        apps = config.load("notifications", N.DEFAULTS)["apps"]
        self.assertEqual(apps["telegram"]["name"], "Telegram")
        self.assertTrue(apps["telegram"]["allow"])

    def test_per_app_settings(self):
        """allow=False drops it; style none keeps it listed without a banner; center=False banners only."""
        self.set_app("muted", allow=False)
        self.send(app="Muted")
        self.assertEqual((self.nc.notes, self.banners), ([], []))
        self.set_app("quiet", style="none")
        nid = self.send(app="Quiet")
        self.assertEqual([n.id for n in self.nc.notes], [nid])
        self.assertEqual(self.banners, [])
        self.set_app("loud", center=False)
        nid = self.send(app="Loud")
        self.assertEqual(self.banners, [nid])
        self.assertNotIn(nid, [n.id for n in self.nc.notes])

    def test_replacement_without_center(self):
        """An update of a banner-only app's notification keeps its id."""
        self.set_app("player", center=False)
        first = self.send(app="Player")
        self.assertEqual(self.send(app="Player", replaces=first), first)

    def test_close_removes_and_tells_listeners(self):
        """close() removes the note and notifies listeners only when something changed."""
        nid = self.send()
        seen = []
        self.nc.listeners.append(lambda: seen.append(1))
        self.nc.close(nid)
        self.nc.close(nid)
        self.assertEqual(self.nc.notes, [])
        self.assertEqual(len(seen), 1)

    def test_ago_buckets(self):
        """Card times: now, minutes, hours, then a date."""
        now = N.time.time()
        self.assertEqual(N._ago(now - 5), "now")
        self.assertEqual(N._ago(now - 125), "2m ago")
        self.assertEqual(N._ago(now - 3 * 3600 - 10), "3h ago")
        self.assertRegex(N._ago(now - 3 * 86400), r"^\d\d \w+")

    def test_markup_kept_or_escaped(self):
        """Valid Pango markup in a body is kept; broken markup is shown escaped."""
        self.assertEqual(N._markup("<b>hi</b>"), "<b>hi</b>")
        self.assertEqual(N._markup("a < b & c"), "a &lt; b &amp; c")


# -- topbar helpers ----------------------------------------------------------------------------
class TopbarHelpersTest(unittest.TestCase):
    def test_layouts_and_switching(self):
        """xkb_layout/xkb_variant lists pair up; picking one moves it (and its variant) first."""
        store = {("input", "xkb_layout"): "us,br", ("input", "xkb_variant"): "intl,"}
        fake = types.SimpleNamespace(_update_input=lambda: None)
        fake._layouts = lambda: topbar.Bar._layouts(fake)
        with mock.patch.object(system, "wayfire_get", side_effect=lambda s, k, d="": store.get((s, k), d)), \
                mock.patch.object(system, "wayfire_set", side_effect=lambda s, k, v: store.__setitem__((s, k), v)), \
                mock.patch.object(system, "run_async", side_effect=sync_run_async):
            self.assertEqual(fake._layouts(), ["us(intl)", "br"])
            topbar.Bar._use_layout(fake, 1)
            self.assertEqual(store[("input", "xkb_layout")], "br,us")
            self.assertEqual(store[("input", "xkb_variant")], ",intl")
            self.assertEqual(fake._layouts(), ["br", "us(intl)"])

    def test_status_icons(self):
        """Wi-Fi and sound icons follow signal / level; no Wi-Fi hardware hides the item."""
        names = []
        fake = types.SimpleNamespace(wifi=mock.Mock(), sound=mock.Mock(), cfg={"show_sound": True},
                                     _set_icon=lambda _b, n: names.append(n))
        for res in ((True, True, ("Home", 80, False)), (True, True, ("Home", 45, False)),
                    (True, True, ("", 0, False)), (True, False, ("", 0, False)), (False, False, ("", 0, True))):
            topbar.Bar._wifi_state(fake, res)
        self.assertEqual(names, ["sonata-wifi-3-symbolic", "sonata-wifi-2-symbolic", "sonata-wifi-0-symbolic",
                                 "sonata-wifi-off-symbolic", "network-wired-symbolic"])
        names.clear()
        topbar.Bar._wifi_state(fake, (False, False, ("", 0, False)))
        fake.wifi.set_visible.assert_called_with(False)
        for res in ((80, False), (50, False), (10, False), (0, False), (90, True)):
            topbar.Bar._sound_state(fake, res)
        self.assertEqual(names, ["sonata-volume-3-symbolic", "sonata-volume-2-symbolic", "sonata-volume-1-symbolic",
                                 "sonata-volume-muted-symbolic", "sonata-volume-muted-symbolic"])
        self.assertEqual([topbar._speaker_icon(v) for v in (0, 20, 50, 90)],
                         ["audio-volume-muted-symbolic", "audio-volume-low-symbolic",
                          "audio-volume-medium-symbolic", "audio-volume-high-symbolic"])
        self.assertEqual(topbar._bt_icon("WH-1000XM4"), "audio-headphones-symbolic")
        self.assertEqual(topbar._bt_icon("DualSense Wireless Controller"), "input-gaming-symbolic")
        self.assertEqual(topbar._bt_icon("Unknown"), "sonata-bluetooth-symbolic")

    def test_cached_menus_fill_once_per_change(self):
        """cached(): last answer shown at once, refilled only when the new answer differs."""
        fills = []
        answers = iter([["a"], ["a"], ["b"], None])
        topbar._CACHE.pop("t", None)
        with mock.patch.object(system, "run_async", side_effect=sync_run_async):
            topbar.cached("t", lambda: next(answers), fills.append)      # first: fill
            topbar.cached("t", lambda: next(answers), fills.append)      # cached + same answer
            topbar.cached("t", lambda: next(answers), fills.append)      # cached + changed
            topbar.cached("t", lambda: next(answers), fills.append)      # cached + failed read
        self.assertEqual(fills, [["a"], ["a"], ["a"], ["b"], ["b"]])
        topbar._CACHE.pop("t", None)


# -- mpris ----------------------------------------------------------------------------------------
class MprisTest(unittest.TestCase):
    def players(self, props, name="org.mpris.MediaPlayer2.spotify.instance12"):
        p = mpris.Players.__new__(mpris.Players)          # no session bus
        p.listeners, p.names, p.bus = [], [], None
        p.proxy = mock.Mock()
        p.proxy.get_name.return_value = name
        p.proxy.get_cached_property.side_effect = lambda k: GLib.Variant(*props[k]) if k in props else None
        return p

    def test_properties(self):
        """Title / artist / art / app name / playing come from the player's cached properties."""
        p = self.players({"PlaybackStatus": ("s", "Playing"),
                          "Metadata": ("a{sv}", {"xesam:title": GLib.Variant("s", "Song"),
                                                 "xesam:artist": GLib.Variant("as", ["A", "B"]),
                                                 "mpris:artUrl": GLib.Variant("s", "file:///a.png")})})
        self.assertTrue(p.active and p.playing)
        self.assertEqual((p.title, p.artist, p.art, p.app), ("Song", "A, B", "file:///a.png", "Spotify"))
        empty = self.players({})
        self.assertEqual((empty.title, empty.artist, empty.art), ("Not Playing", "", ""))
        self.assertFalse(empty.playing)

    def test_newest_player_wins(self):
        """NameOwnerChanged: a new player becomes current, a closed one hands back to the previous."""
        p = self.players({})
        picks = []
        p._pick = lambda: picks.append(p.names[-1] if p.names else None)
        sig = lambda n, new: p._owner(None, None, None, None, None, GLib.Variant("(sss)", (n, "", new)))  # noqa: E731
        sig("org.mpris.MediaPlayer2.vlc", ":1.5")
        sig("org.mpris.MediaPlayer2.firefox", ":1.6")
        sig("org.other.Name", ":1.7")                               # not a player: ignored
        sig("org.mpris.MediaPlayer2.firefox", "")
        self.assertEqual(picks, ["org.mpris.MediaPlayer2.vlc", "org.mpris.MediaPlayer2.firefox",
                                 "org.mpris.MediaPlayer2.vlc"])

    def test_artist_as_plain_string(self):
        """A string artist is shown as is."""
        p = self.players({"Metadata": ("a{sv}", {"xesam:artist": GLib.Variant("s", "Muse")})})
        self.assertEqual(p.artist, "Muse")


# -- capture --------------------------------------------------------------------------------------
class CaptureLogicTest(unittest.TestCase):
    OUTS = [{"id": 1, "name": "eDP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
            {"id": 2, "name": "HDMI-A-1", "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440}}]

    def test_window_list_filters_and_orders(self):
        """Pickable windows: mapped app toplevels on the shown workspace, most recent first,
        in layout coordinates."""
        v = lambda **kw: dict({"role": "toplevel", "mapped": True, "output-id": 1,  # noqa: E731
                               "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}}, **kw)
        views = [v(title="old", **{"last-focus-timestamp": 1}),
                 v(title="new", **{"last-focus-timestamp": 9, "output-id": 2, "app-id": "kitty"}),
                 v(title="min", minimized=True), v(title="panel", role="desktop-environment"),
                 v(title="bg", layer="background"), v(title="unmapped", mapped=False),
                 v(title="other ws", geometry={"x": 1920, "y": 0, "width": 300, "height": 200}),
                 v(title="zero", geometry={"x": 0, "y": 0, "width": 0, "height": 10}), "junk"]
        wins = capture.window_list(views, self.OUTS)
        self.assertEqual([w["title"] for w in wins], ["new", "old"])
        self.assertEqual(wins[0]["geo"], "1930,20 300x200")
        self.assertEqual(wins[0]["app"], "kitty")
        self.assertEqual(capture.window_boxes(views, self.OUTS), ["1930,20 300x200", "10,20 300x200"])

    def test_box_parse_and_output_at(self):
        """"x,y wxh" geometry parsing and the display holding a region."""
        self.assertEqual(capture._box("1.5,2 30x40"), (1.5, 2.0, 30.0, 40.0))
        self.assertIsNone(capture._box("garbage"))
        self.assertIsNone(capture._box(None))
        with mock.patch.object(capture, "outputs", return_value=self.OUTS):
            self.assertEqual(capture.Capture._output_at("2000,100 50x50"), "HDMI-A-1")
            self.assertEqual(capture.Capture._output_at("5,5 50x50"), "eDP-1")
            self.assertIsNone(capture.Capture._output_at("9000,0 1x1"))
            self.assertIsNone(capture.Capture._output_at("nope"))

    def test_recorder_command_and_encoder_order(self):
        """wf-recorder gets geometry before output, audio inline; last good encoder is tried first."""
        with mock.patch.object(capture.shutil, "which", return_value=None):
            cmd = capture.recorder_command("/v.mp4", geo="0,0 10x10", output="HDMI-A-1", audio="sink.monitor",
                                           encoder="vaapi")
        self.assertEqual(cmd[:6], ["wf-recorder", "-y", "-f", "/v.mp4", "-r", str(capture.FPS)])
        self.assertIn("h264_vaapi", cmd)
        self.assertIn("-g", cmd)
        self.assertNotIn("-o", cmd)
        self.assertEqual(cmd[-1], "--audio=sink.monitor")
        with mock.patch.object(capture.os.path, "exists", return_value=True):
            self.assertEqual(capture.encoders(), ["nvenc", "vaapi", "x264"])
            self.assertEqual(capture.encoders("x264"), ["x264", "nvenc", "vaapi"])
        with mock.patch.object(capture.os.path, "exists", return_value=False):
            self.assertEqual(capture.encoders("nvenc"), ["x264"])

    def test_audio_device(self):
        """System sound records the default sink's monitor; the mic its source; none when pactl fails."""
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="alsa_output.pci\n"))
        with mock.patch.object(capture.shutil, "which", return_value="/usr/bin/pactl"), \
                mock.patch.object(capture.subprocess, "run", run):
            self.assertEqual(capture.audio_device("system"), "alsa_output.pci.monitor")
            self.assertEqual(capture.audio_device("mic"), "alsa_output.pci")
            self.assertIsNone(capture.audio_device("none"))
            run.return_value = mock.Mock(returncode=1, stdout="")
            self.assertIsNone(capture.audio_device("system"))

    def test_encoder_fallback(self):
        """A recorder that quits at once: the next encoder is tried (empty file removed); a
        stale check for an older process is ignored."""
        bar = types.SimpleNamespace()
        c = capture.Capture(None, bar)
        fd, c.rec_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        dead = mock.Mock()
        dead.poll.return_value = 1
        c.recorder, c._rec = dead, {"encoder": "nvenc", "encoders": ["x264"]}
        c._spawn = mock.Mock(return_value=True)
        self.assertFalse(c._check_started(dead))
        c._spawn.assert_called_once()
        self.assertFalse(os.path.exists(c.rec_path))
        c._spawn.reset_mock()
        c.recorder = mock.Mock()
        self.assertFalse(c._check_started(dead))                # a stale check: ignored
        c._spawn.assert_not_called()

    def test_working_encoder_is_remembered(self):
        """The encoder of a recorder that keeps running is the first one tried next time."""
        c = capture.Capture(None, types.SimpleNamespace())
        alive = mock.Mock()
        alive.poll.return_value = None
        c.recorder, c._rec, c.rec_path = alive, {"encoder": "x264", "encoders": []}, "/nonexistent.mp4"
        c._check_started(alive)
        with mock.patch.object(capture.os.path, "exists", return_value=True):
            preferred = capture.encoders(config.load("capture", capture.DEFAULTS).get("encoder"))
        self.assertEqual(preferred[0], "x264")


# -- share picker -----------------------------------------------------------------------------
class SharePickerTest(unittest.TestCase):
    def test_parse_portal_lines(self):
        """xdg-desktop-portal-wlr chooser lines: bare connector, "Monitor: ..." and "Window: ...: title"."""
        self.assertEqual(sharepicker._parse("HDMI-A-1"), ("screen", "HDMI-A-1", "HDMI-A-1"))
        self.assertEqual(sharepicker._parse("Monitor: DP-2"), ("screen", "DP-2", "DP-2"))
        self.assertEqual(sharepicker._parse("Window: 42: Firefox: Docs"), ("window", "Firefox: Docs", None))
        self.assertEqual(sharepicker._parse("Window: Docs (a1b2)"), ("window", "Docs", "a1b2"))
        self.assertEqual(sharepicker._parse("Window: "), ("window", "Window", None))
        self.assertEqual(sharepicker._display_name("eDP-1"), "Built-in Display")

    def test_picker_keys_and_single_answer(self):
        """Arrows wrap, Return answers the selected value, the answer is given only once."""
        got = []
        items = [{"name": n, "icon": "video-display-symbolic", "value": n} for n in ("a", "b", "c")]
        p = sharepicker.Picker(None, "Pick", None, items, "Share", got.append)
        p._key(None, sharepicker.Gdk.KEY_Left, 0, 0)
        self.assertEqual(p.index, 2)
        p._key(None, sharepicker.Gdk.KEY_Right, 0, 0)
        self.assertEqual(p.index, 0)
        self.assertTrue(p.items[0].has_css_class("selected"))
        p._key(None, sharepicker.Gdk.KEY_Right, 0, 0)
        p._key(None, sharepicker.Gdk.KEY_Return, 0, 0)
        p._key(None, sharepicker.Gdk.KEY_Escape, 0, 0)
        self.assertEqual(got, ["b"])
        p.destroy()


# -- spotlight -------------------------------------------------------------------------------
class SpotlightTest(unittest.TestCase):
    def test_calculator(self):
        """Safe arithmetic: operators, unicode signs, decimal comma, grouping; no names or calls."""
        c = spotlight.calculate
        self.assertEqual(c("2+2"), "4")
        self.assertEqual(c("1000*1000"), "1 000 000")
        self.assertEqual(c("7÷2"), "3.5")
        self.assertEqual(c("3×4"), "12")
        self.assertEqual(c("2^10"), "1 024")
        self.assertEqual(c("1,5+1"), "2.5")
        self.assertEqual(c("0.1+0.2"), "0.3")
        for bad in ("42", "-5", "hello", "1/0", "__import__('os')+1", "len('a')+1", "2**1000", "abc+1"):
            self.assertIsNone(c(bad), bad)

    def test_calculator_huge_result_does_not_raise(self):
        """A result too big to print is no answer, not an exception."""
        self.assertIsNone(spotlight.calculate("(10**100)**100"))

    def test_calculator_linear_in_nesting(self):
        """Each power in a chain is computed once."""
        calls = []
        ops = dict(spotlight._OPS)
        ops[spotlight.ast.Pow] = lambda a, b: (calls.append(1), a ** b)[1]
        with mock.patch.object(spotlight, "_OPS", ops):
            self.assertEqual(spotlight.calculate("1" + "**1" * 12 + "+1"), "2")
        self.assertLessEqual(len(calls), 12)

    def test_index_build_and_search_order(self):
        """Home index skips hidden folders and caps depth; prefix matches (shortest first)
        come before substring ones."""
        home = tempfile.mkdtemp()
        for p in ("Docs/report.txt", "Docs/annual-report.pdf", ".secret/report.txt", "a/b/c/d/e/f/report-deep.txt",
                  "Reports/x.txt"):
            os.makedirs(os.path.dirname(os.path.join(home, p)), exist_ok=True)
            open(os.path.join(home, p), "w").close()
        idx = spotlight.Index()
        with mock.patch.object(spotlight.GLib, "get_home_dir", return_value=home):
            idx._build()
        names = [os.path.relpath(p, home) for p, _d in idx.search("report")]
        self.assertEqual(names[:2], ["Reports", "Docs/report.txt"])
        self.assertIn("Docs/annual-report.pdf", names)
        self.assertNotIn(".secret/report.txt", names)
        self.assertNotIn("a/b/c/d/e/f/report-deep.txt", names)
        self.assertTrue(dict(idx.search("reports"))[os.path.join(home, "Reports")])     # a folder


# -- tray (pure parts) ----------------------------------------------------------------------
class TrayLogicTest(unittest.TestCase):
    def test_split_service_and_mnemonics(self):
        """SNI registration strings: path only (sender's), name only, name/path; "_" mnemonics."""
        self.assertEqual(tray.split_service("/org/ayatana/X", ":1.9"), (":1.9", "/org/ayatana/X"))
        self.assertEqual(tray.split_service("org.kde.StatusNotifierItem-7-1"),
                         ("org.kde.StatusNotifierItem-7-1", "/StatusNotifierItem"))
        self.assertEqual(tray.split_service(":1.4/a/b"), (":1.4", "/a/b"))
        self.assertEqual(tray._strip_mnemonic("_Open__File_"), "Open_File")

    def test_watcher_registers_and_drops_by_owner(self):
        """Watcher: duplicate registrations ignored; a vanished bus name drops only its items."""
        w = tray.Watcher.__new__(tray.Watcher)
        w.conn, w.items, w.hosts = mock.Mock(), [], set()
        inv = mock.Mock()
        reg = lambda svc, sender: w._call(None, sender, None, None, "RegisterStatusNotifierItem",  # noqa: E731
                                          GLib.Variant("(s)", (svc,)), inv)
        reg("/StatusNotifierItem", ":1.5")
        reg("/StatusNotifierItem", ":1.5")
        reg("org.app.Tray", ":1.6")
        self.assertEqual(w.items, [":1.5/StatusNotifierItem", "org.app.Tray/StatusNotifierItem"])
        w._owner_changed(None, None, None, None, None, GLib.Variant("(sss)", (":1.5", ":1.5", "")))
        self.assertEqual(w.items, ["org.app.Tray/StatusNotifierItem"])
        self.assertEqual(inv.return_value.call_count, 3)

    def test_item_state_from_properties(self):
        """TrayItem status / visibility / tooltip (markup stripped) / attention icon first."""
        it = tray.TrayItem.__new__(tray.TrayItem)
        it.name, it._tex, it.ready = ":1.2", {}, True
        it.props = {"Status": "NeedsAttention", "Title": "App", "IconName": "/nonexistent/a.png",
                    "AttentionIconName": "/nonexistent/b.png",
                    "ToolTip": ("", [], "", "<b>3</b> unread")}
        self.assertTrue(it.visible)
        self.assertEqual(it.tooltip, "App\n3 unread")
        with mock.patch.object(tray, "_file_texture", side_effect=lambda p: ("paintable", p)):
            self.assertEqual(it.icon(1), ("paintable", "/nonexistent/b.png"))
        it.props["Status"] = "Passive"
        self.assertFalse(it.visible)
        self.assertEqual(it.id, ":1.2")

    def test_mono_mask(self):
        """Tray silhouettes: transparent icon -> None; half see-through body made solid;
        light details on a dark body become holes."""
        w = h = 4
        empty = bytes(w * h * 4)
        self.assertIsNone(tray.mono_mask(empty, w * 4, w, h))
        half = bytes([200, 200, 200, 128]) * (w * h)
        tex = tray.mono_mask(half, w * 4, w, h)
        alpha = self._alphas(tex)
        self.assertTrue(all(a == 255 for a in alpha))
        px = bytearray(bytes([10, 10, 10, 255]) * (w * h))       # dark disc...
        for i in (5, 6):                                         # ...with two white pixels
            px[i * 4:i * 4 + 3] = b"\xff\xff\xff"
        alpha = self._alphas(tray.mono_mask(bytes(px), w * 4, w, h))
        self.assertEqual(alpha[5], 0)
        self.assertEqual(alpha[0], 255)

    @staticmethod
    def _alphas(tex):
        d = tray.Gdk.TextureDownloader.new(tex)
        d.set_format(tray.Gdk.MemoryFormat.R8G8B8A8)
        data, _stride = d.download_bytes()
        return list(data.get_data()[3::4])

    def test_empty_submenus(self):
        """Lazily filled (Qt) submenus are found at any depth."""
        lay = (0, {}, [(1, {"children-display": "submenu"}, []),
                       (2, {}, [(3, {"children-display": "submenu"}, []), (4, {}, [])]),
                       (5, {"label": "x"}, [])])
        self.assertEqual(tray._empty_submenus(lay), [1, 3])


# -- monitors -------------------------------------------------------------------------------
class MonitorsTest(unittest.TestCase):
    @staticmethod
    def mon(conn):
        m = mock.Mock()
        m.get_connector.return_value = conn
        return m

    def test_main_display_choice(self):
        """Main display: the one picked in Settings, else an external monitor (Vini),
        else the built-in panel."""
        hdmi, edp, dp = self.mon("HDMI-A-1"), self.mon("eDP-1"), self.mon("DP-2")
        with mock.patch.object(monitors, "_list", return_value=[edp, hdmi, dp]):
            config.save("displays", {"main": "DP-2"})
            self.assertIs(monitors.main(), dp)
            config.save("displays", {"main": "gone"})
            self.assertIs(monitors.main(), hdmi)                      # external first
            config.save("displays", monitors.DEFAULTS)
            self.assertIs(monitors.main(), hdmi)
        with mock.patch.object(monitors, "_list", return_value=[edp]):
            self.assertIs(monitors.main(), edp)                       # the laptop alone
        with mock.patch.object(monitors, "_list", return_value=[hdmi, dp]):
            self.assertIs(monitors.main(), hdmi)
        with mock.patch.object(monitors, "_list", return_value=[]):
            self.assertIsNone(monitors.main())
        config.save("displays", monitors.DEFAULTS)
        self.assertEqual(monitors.connector(None), "")

    def test_surfaces_follow_hotplug(self):
        """Surfaces: one per display, destroyed when it goes, all made again on rebuild()."""
        a, b = self.mon("A"), self.mon("B")
        shown = [a]
        made, gone = [], []
        with mock.patch.object(monitors, "_list", side_effect=lambda: list(shown)):
            s = monitors.Surfaces(lambda m: (made.append(m), "s-" + m.get_connector())[1], gone.append)
            shown.append(b)
            s.sync()
            shown.remove(a)
            s.sync()
            self.assertEqual(made, [a, b])
            self.assertEqual(gone, ["s-A"])
            s.rebuild()
            self.assertEqual(gone, ["s-A", "s-B"])
            self.assertEqual(made, [a, b, b])


# -- OSD ----------------------------------------------------------------------------------------
class OsdTest(unittest.TestCase):
    def test_blocks_follow_level(self):
        """Volume HUD: 16 blocks lit by percent (clamped); muted lights none and shows the muted icon."""
        w = osd.OSD(None)
        lit = lambda: sum(not b.has_css_class("off") for b in w.blocks)  # noqa: E731
        w.show_level("volume", 50)
        self.assertEqual(lit(), 8)
        w.show_level("volume", 250)
        self.assertEqual(lit(), osd.BLOCKS)
        w.show_level("brightness", -5)
        self.assertEqual(lit(), 0)
        w.show_level("volume", 80, muted=True)
        self.assertEqual(lit(), 0)
        self.assertEqual(w.icon.get_icon_name(), "sonata-volume-muted-symbolic")
        GLib.source_remove(w._src)
        w.destroy()


# -- fixes from the October review ----------------------------------------------------------------
def spin(ms=0):
    """Run the GLib main loop for about `ms` milliseconds."""
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if GLib.get_monotonic_time() >= end:
            break
        ctx.iteration(False)


class ReviewFixesTest(unittest.TestCase):
    def new_bar(self):
        with mock.patch.object(system, "run_async"), mock.patch.object(tray, "host") as host:
            host.return_value = types.SimpleNamespace(items={}, listeners=[])
            return topbar.Bar()

    def test_bars_share_watchers_and_stop_releases_them(self):
        """Every display's bar shares one clipboard watcher, gamemode watcher, BlueZ watch and
        power subscription; stop() lets go of its listeners, theme hook and config monitor."""
        gm = types.SimpleNamespace(listeners=[], active=False, light=None)
        bt = types.SimpleNamespace(listeners=[], powered=lambda: None)
        made = {"clip": 0, "power": 0}

        def clip():
            made["clip"] += 1
            return mock.Mock()

        def watch(cb):
            made["power"] += 1
            return True
        with mock.patch.dict(topbar._SHARED, clear=True), \
                mock.patch.object(clipboard, "History", side_effect=clip), \
                mock.patch("sonata2.gamemode.Watcher", return_value=gm), \
                mock.patch.object(topbar, "_Bluetooth", return_value=bt), \
                mock.patch.object(topbar.power, "watch", side_effect=watch):
            a, b = self.new_bar(), self.new_bar()
            self.assertIs(a.clip, b.clip)
            self.assertIs(a.fullscreen_first, b.fullscreen_first)
            self.assertEqual(made, {"clip": 1, "power": 1})
            self.assertEqual((len(gm.listeners), len(bt.listeners)), (2, 2))
            with mock.patch.object(ui.theme, "off_change") as off:
                b.stop()
            off.assert_called_once_with(b._theme_cb)
            self.assertTrue(b._cfg_mon.is_cancelled())
            self.assertEqual((len(gm.listeners), len(bt.listeners)), (1, 1))
            self.assertNotIn(b._extras_visibility, b.players.listeners)
            self.assertNotIn(b, topbar._BARS)
            with mock.patch.object(a, "_poll_battery") as pa, mock.patch.object(b, "_poll_battery") as pb:
                topbar._power_changed()
            pa.assert_called_once()
            pb.assert_not_called()
            a.stop()

    def test_bluetooth_proxy_is_async(self):
        """The BlueZ adapter proxy is made asynchronously (no sync D-Bus on the main loop)."""
        bt = topbar._Bluetooth.__new__(topbar._Bluetooth)
        bt.proxy, bt.listeners = None, []
        with mock.patch.object(topbar.Gio.DBusProxy, "new") as new, \
                mock.patch.object(topbar.Gio.DBusProxy, "new_sync", side_effect=AssertionError):
            bt._appeared(mock.Mock(), "org.bluez", ":1.2")
        new.assert_called_once()

    def test_album_art_cache_bounded_and_small(self):
        """Album art is decoded at the shown size (x2) and only the last few are kept."""
        folder = tempfile.mkdtemp()
        topbar._art_cache.clear()
        with mock.patch.object(system, "run_async", side_effect=sync_run_async):
            for i in range(topbar.ART_KEEP + 3):
                path = os.path.join(folder, f"{i}.png")
                with open(path, "wb") as f:
                    f.write(png_bytes(1000 + i, 800))
                topbar._load_art("file://" + path, Gtk.Image(), 40)
        self.assertEqual(len(topbar._art_cache), topbar.ART_KEEP)
        tex = next(iter(topbar._art_cache.values()))
        self.assertLessEqual(max(tex.get_width(), tex.get_height()), 80)
        topbar._art_cache.clear()

    def test_active_window_stamped_and_switcher_raises_last_used_on_top(self):
        """Super+Tab brings all of an app's windows forward with the last used one activated last."""
        from sonata2.shell import switcher
        w1, w2, w3 = (types.SimpleNamespace(app_id="kitty", activated=False, minimized=False) for _ in range(3))
        fake = types.SimpleNamespace(manager=types.SimpleNamespace(toplevels=[w1, w2, w3]))
        for w in (w2, w1):                                    # w1 used last, w3 never
            w.activated = True
            topbar.Bar._active(fake)
            w.activated = False
        sw = types.SimpleNamespace(keys=["kitty"], index=0, groups={"kitty": [w1, w2, w3]},
                                   manager=mock.Mock(), get_visible=lambda: True, _close=lambda: None)
        switcher.Switcher._switch(sw)
        self.assertEqual([c.args[0] for c in sw.manager.activate.call_args_list], [w3, w2, w1])

    def test_notes_capped_and_closed_center_rebuilds_once(self):
        """The Notification Center list is capped; while closed a burst of notifications
        rebuilds it once; the state key follows each note's time (n.at)."""
        n = N.Notifications.__new__(N.Notifications)
        n.notes, n._next, n.nc, n.listeners, n._conn, n._banners = [], 1, None, [], None, {}
        n.cfg = {"dnd": True, "apps": {"chat": dict(N.APP_DEFAULTS, name="Chat")}}
        with mock.patch.object(N, "locked", return_value=False):
            for _ in range(N.MAX_NOTES + 5):
                n.notify("Chat", 0, "", "Hi", "", [], {"desktop-entry": "chat"}, -1)
        self.assertEqual(len(n.notes), N.MAX_NOTES)
        self.assertEqual(n.notes[-1].id, N.MAX_NOTES + 5)
        c = N._Center(None, n)
        with mock.patch.object(c, "_rebuild") as rebuild:
            for _ in range(5):
                c._changed()
            rebuild.assert_not_called()
            c._built_for = None
            spin(1100)
            rebuild.assert_called_once()
        key = c._state_key()
        n.notes[-1].at += 1
        self.assertNotEqual(key, c._state_key())
        c.destroy()

    def test_calculator_too_many_digits(self):
        """A product too long to print is no answer, not an exception."""
        self.assertIsNone(spotlight.calculate("*".join(["9**100"] * 50)))

    def test_mpris_proxy_async_and_stale_answer_dropped(self):
        """The player proxy is made asynchronously; a proxy for a player that is no longer
        the newest is dropped."""
        p = mpris.Players.__new__(mpris.Players)
        p.listeners, p.names, p.bus, p.proxy, p._wanted = [], ["org.mpris.MediaPlayer2.a"], mock.Mock(), None, None
        with mock.patch.object(mpris.Gio.DBusProxy, "new") as new, \
                mock.patch.object(mpris.Gio.DBusProxy, "new_sync", side_effect=AssertionError):
            p._pick()
        new.assert_called_once()
        p._wanted = "org.mpris.MediaPlayer2.b"
        with mock.patch.object(mpris.Gio.DBusProxy, "new_finish", return_value=mock.Mock()):
            p._made(None, None, "org.mpris.MediaPlayer2.a")
        self.assertIsNone(p.proxy)

    def test_nightshift_sunset_schedule_and_polar(self):
        """Sunset to Sunrise: warm at night, not at noon; polar night / day handled."""
        cfg = dict(nightshift.DEFAULTS, schedule="sunset")
        with mock.patch.object(nightshift, "tz_location", return_value=(-23.5, -46.6)):
            sp = datetime.timezone(datetime.timedelta(hours=-3))
            self.assertFalse(nightshift.in_schedule(cfg, datetime.datetime(2026, 10, 3, 12, 0, tzinfo=sp)))
            self.assertTrue(nightshift.in_schedule(cfg, datetime.datetime(2026, 10, 3, 23, 0, tzinfo=sp)))
            self.assertTrue(nightshift.in_schedule(cfg, datetime.datetime(2026, 10, 3, 3, 0, tzinfo=sp)))
        self.assertEqual(nightshift.sun_times(78, 15, datetime.date(2026, 12, 21))[0], float("inf"))
        self.assertEqual(nightshift.sun_times(78, 15, datetime.date(2026, 6, 21))[0], float("-inf"))
        self.assertFalse(nightshift.in_schedule(dict(cfg, schedule="custom", **{"from": "x"})))

    def test_nightshift_restart_does_not_block(self):
        """A settings change while wlsunset runs: the old one is reaped off the main loop and
        the new one starts only after it is gone."""
        ns = nightshift.NightShift.__new__(nightshift.NightShift)
        ns.cmd, ns._gen, ns._waiting = ["wlsunset", "-t", "1"], 0, False
        import threading
        release = threading.Event()
        old = mock.Mock()
        old.poll.return_value = None
        old.wait.side_effect = lambda timeout=None: release.wait(timeout)
        ns.proc = old
        started = []
        cfg = dict(nightshift.DEFAULTS, schedule="custom", warmth=10)
        with mock.patch.object(nightshift.shutil, "which", return_value="/usr/bin/wlsunset"), \
                mock.patch.object(nightshift.subprocess, "Popen", side_effect=lambda cmd, **_k: started.append(cmd)), \
                mock.patch.object(nightshift.config, "load", side_effect=lambda *_a: dict(cfg)):
            t0 = GLib.get_monotonic_time()
            ns.apply()
            self.assertLess(GLib.get_monotonic_time() - t0, 200_000)
            old.send_signal.assert_called_once()
            self.assertEqual(started, [])
            ns.apply()                                     # a 60 s tick meanwhile: still waiting
            self.assertEqual(started, [])
            release.set()
            spin(200)
        self.assertEqual(len(started), 1)

    def test_wallpaper_window_lets_go_when_destroyed(self):
        """A destroyed wallpaper window disconnects from the app-wide StyleManager and
        cancels its prefs monitor (it was kept alive with its pictures)."""
        from gi.repository import GObject
        from sonata2.shell import wallpaper
        with mock.patch.object(wallpaper.WallpaperWindow, "update"):
            w = wallpaper.WallpaperWindow(None, desktop=False, main=False)
        hid, mon = w._dark_id, w._prefs_mon
        sm = Adw.StyleManager.get_default()
        self.assertTrue(GObject.signal_handler_is_connected(sm, hid))
        w.destroy()
        self.assertFalse(GObject.signal_handler_is_connected(sm, hid))
        self.assertTrue(mon.is_cancelled())

    def test_tray_silhouette_not_recomputed(self):
        """The same tray icon again makes no new silhouette; a change to one item updates only it."""
        icon = tray.MonoIcon()
        tex = ("paintable", tray.Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,
                                                                                    True, 8, 4, 4)))
        with mock.patch.object(tray, "_silhouette", side_effect=lambda t: t) as sil:
            icon.set_icon(tex, 1)
            icon.set_icon(tex, 1)
            self.assertEqual(sil.call_count, 1)
            icon.set_icon(tex, 2)
            self.assertEqual(sil.call_count, 2)
        items = {k: types.SimpleNamespace(key=k, visible=True, tooltip="", icon=lambda _s: None) for k in "ab"}
        src = types.SimpleNamespace(items=items, listeners=[])
        box = tray.TrayBox(source=src)
        with mock.patch.object(box, "_update") as upd:
            box._changed(items["b"])
        self.assertEqual([c.args[1] for c in upd.call_args_list], [items["b"]])
        box.stop()

    def test_picker_destroyed_after_answer(self):
        """A display / window picker is destroyed once it has answered."""
        got, gone = [], []
        p = sharepicker.Picker(None, "Pick", None, [{"name": "a", "icon": "x", "value": "a"}], "Go", got.append)
        with mock.patch.object(p, "destroy", side_effect=lambda: gone.append(1)):
            p._key(None, sharepicker.Gdk.KEY_Return, 0, 0)
            p._key(None, sharepicker.Gdk.KEY_Escape, 0, 0)       # answered once, destroyed once
        self.assertEqual((got, gone), (["a"], [1]))
        p.destroy()

    def test_capture_pickers_use_frozen_screen(self):
        """Display / window pickers (recording ones too) take their thumbnails from one frozen
        screen: grim is not run per item."""
        outs = CaptureLogicTest.OUTS
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 4480, 1440)
        frozen = capture.Frozen(pb, outs)
        c = capture.Capture(None, types.SimpleNamespace())
        picks = []
        c._pick = lambda title, items, action, then: picks.append(items)
        views = [{"role": "toplevel", "mapped": True, "title": "w", "output-id": 1,
                  "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}}]
        with mock.patch.object(capture, "outputs", return_value=outs), \
                mock.patch.object(capture.Frozen, "take", return_value=frozen) as take, \
                mock.patch.object(capture.subprocess, "run", side_effect=AssertionError("grim per item")), \
                mock.patch.object(capture, "_ipc", return_value=mock.Mock(call=lambda *_a: views)):
            for mode in ("display", "rec-display", "window", "rec-window"):
                c._run(mode, {})
        self.assertEqual(take.call_count, 4)
        self.assertEqual(len(picks), 4)
        for items in picks:
            self.assertTrue(all(it["texture"] is not None for it in items))
            tex = items[0]["texture"]
            self.assertLessEqual(tex.get_width(), sharepicker.THUMB_W)

    def test_clipboard_screenshot_reuses_one_runtime_file(self):
        """Screenshots to the clipboard reuse one file in the runtime dir (no pile-up in /tmp)."""
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
        frozen = capture.Frozen(pb, [])
        c = capture.Capture(None, types.SimpleNamespace())
        shots = []
        c.shot_taken = shots.append
        with mock.patch.object(capture.shutil, "which", side_effect=lambda t: "/usr/bin/grim" if t == "grim" else None), \
                mock.patch("sonata2.sounds.play"):
            c._shoot(None, {"shots_to": "clipboard"}, frozen=frozen)
            c._shoot(None, {"shots_to": "clipboard"}, frozen=frozen)
        self.assertEqual(shots[0], shots[1])
        self.assertTrue(shots[0].startswith(GLib.get_user_runtime_dir() or GLib.get_tmp_dir()))
        os.unlink(shots[0])


# -- animations of the main actions ---------------------------------------------------------------
class AnimationTests(unittest.TestCase):
    def test_popovers_open_with_css_animation(self):
        """Menus, menu bar panels and the Control Center open with the UI kit's popover animation."""
        tpl = ui.theme._templates["motion-open"][0]
        self.assertIn("popover > contents { animation: sonata-open", tpl)
        win = Gtk.Window()
        btn = Gtk.Button()
        win.set_child(btn)
        fake = types.SimpleNamespace()
        with mock.patch.object(topbar, "ControlCenter", return_value=Gtk.Box()):
            pop = topbar.Bar._control_center(fake, btn)
        self.assertIsInstance(pop, Gtk.Popover)
        pop.popdown()
        win.destroy()

    def test_notification_banner_slides_in_and_fades_out(self):
        """A banner slides in (Revealer, 250 ms) and cross-fades out."""
        n = N.Notifications.__new__(N.Notifications)
        n._banners, n.win = {}, N._BannerWindow(None)
        note = N.Note(1, "Chat", "", "Hi", "", [])
        n._banner(note)
        rev = n._banners[1][0]
        self.assertEqual(rev.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.assertGreater(rev.get_transition_duration(), 0)
        spin(20)
        self.assertTrue(rev.get_reveal_child())
        n._hide_banner(1)
        self.assertEqual(rev.get_transition_type(), Gtk.RevealerTransitionType.CROSSFADE)
        self.assertFalse(rev.get_reveal_child())
        n.win.destroy()

    def test_notification_center_slides(self):
        """The Notification Center slides in and out (Revealer)."""
        owner = types.SimpleNamespace(notes=[], listeners=[], card=None, clear=None)
        c = N._Center(None, owner)
        self.assertEqual(c.rev.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.assertGreater(c.rev.get_transition_duration(), 0)
        c.show_center()
        spin(20)
        self.assertTrue(c.rev.get_reveal_child())
        c.hide_center()
        self.assertFalse(c.rev.get_reveal_child())
        c.destroy()

    def test_spotlight_open_close_classes(self):
        """Spotlight drops in ("opening") and fades out ("closing")."""
        sp = types.SimpleNamespace(panel=Gtk.Box(), _close_src=0, get_visible=lambda: True)
        spotlight.Spotlight.close_spotlight(sp)
        self.assertTrue(sp.panel.has_css_class("closing"))
        GLib.source_remove(sp._close_src)
        self.assertIn(".sp-panel.opening { animation: sp-in", ui.theme._templates["spotlight"][0])

    def test_switcher_open_class_and_glide(self):
        """The switcher opens with the "opening" animation and its selection glides
        (Adw.TimedAnimation)."""
        from sonata2.shell import switcher
        panel = switcher.SwitcherPanel()
        a, b = Gtk.Box(width_request=40, height_request=40), Gtk.Box(width_request=40, height_request=40)
        panel.append(a)
        panel.append(b)
        win = Gtk.Window(child=panel)
        win.present()
        spin(300)
        panel.select(a, animate=False)
        panel.select(b, animate=True)
        self.assertIsInstance(panel._anim, Adw.TimedAnimation)
        win.destroy()
        self.assertIn("sw-panel.opening", " ".join(t[0] for t in ui.theme._templates.values()))

    def test_osd_crossfades(self):
        """The volume / brightness HUD cross-fades in."""
        w = osd.OSD(None)
        self.assertEqual(w.rev.get_transition_type(), Gtk.RevealerTransitionType.CROSSFADE)
        self.assertGreater(w.rev.get_transition_duration(), 0)
        w.show_level("volume", 30)
        self.assertTrue(w.rev.get_reveal_child())
        GLib.source_remove(w._src)
        w.destroy()

    def test_screenshot_thumbnail_slides(self):
        """The screenshot thumbnail slides in from the right and back out."""
        t = capture.Thumbnail(None)
        path = os.path.join(tempfile.mkdtemp(), "s.png")
        with open(path, "wb") as f:
            f.write(png_bytes(40, 30))
        t.show_shot(path)
        self.assertEqual(t.rev.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.assertTrue(t.rev.get_reveal_child())
        GLib.source_remove(t._src)
        t._hide()
        self.assertFalse(t.rev.get_reveal_child())
        t.destroy()

    def test_pickers_animate_in(self):
        """The share / capture picker and the clipboard picker panels animate in (CSS)."""
        css = " ".join(t[0] for t in ui.theme._templates.values())
        self.assertIn(".share-panel { animation: share-in", css)
        self.assertIn(".clip-panel { animation: clip-in", css)


if __name__ == "__main__":
    unittest.main()
