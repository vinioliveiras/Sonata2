"""The Game Bar finished (Vini: "finish the whole Game Bar"): Audio (volumes,
what recordings take -- System, Microphone, Both as two tracks, None),
Performance (FPS, CPU, GPU, memory graphs), widgets moved by their title and
pinned over the game, and instant replay (the last 30 seconds).
Run: dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_gamebar_complete"""
import configparser
import os
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[v] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config, shortcuts, ui  # noqa: E402
from sonata2.shell import capture as C, gamebar as G, replay as R  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = {}


def _app():
    if "a" not in _APP:
        ui.setup()
        app = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.test.gbdone")
        app.register(None)
        _APP["a"] = app
    return _APP["a"]


def spin(ms):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


class Base(unittest.TestCase):
    def setUp(self):
        config.save(G.CONFIG, {})
        config.save("capture", {})
        self.rp = mock.Mock(running=False)
        self.rp.available.return_value = True
        self.cap = mock.Mock(recorder=None, pill=None, replay=self.rp)
        self.top = type("Top", (), {})()
        self.top.capture = self.cap
        self.runs = mock.patch("sonata2.backend.system.run_async",
                               side_effect=lambda fn, done=None, *a: done(fn(*a)) if done else fn(*a))
        self.runs.start()
        self.levels = mock.patch.multiple("sonata2.backend.system", volume=lambda: (40, False),
                                          input_volume=lambda: (70, False), set_volume=mock.DEFAULT,
                                          set_input_volume=mock.DEFAULT)
        self.mocked = self.levels.start()
        self.fo = mock.patch("sonata2.shell.capture.focused_output", return_value="DP-1")
        self.fo.start()
        self.bar = G.GameBar(_app(), self.top)
        self.bar.open()
        spin(200)

    def tearDown(self):
        self.bar._hide_pins()
        self.bar.destroy()
        for p in (self.runs, self.levels, self.fo):
            p.stop()


class AudioTests(Base):
    def test_volumes_read_on_open_and_set_when_moved(self):
        self.assertEqual(self.bar.vol.get_value(), 40)
        self.assertEqual(self.bar.mic.get_value(), 70)
        self.bar.vol.set_value(55)
        self.mocked["set_volume"].assert_called_with(55, False)
        self.bar.mic.set_value(20)
        self.mocked["set_input_volume"].assert_called_with(20, False)

    def test_what_recordings_take(self):
        """Both: system sound and microphone as two tracks (capture's option)."""
        self.assertEqual(set(self.bar.sound_choice.buttons), {"system", "mic", "both", "none"})
        self.bar.sound_choice.buttons["both"].emit("clicked")
        self.assertEqual(config.load("capture", C.DEFAULTS)["audio"], "both")
        self.assertTrue(self.bar.sound_choice.buttons["both"].has_css_class("on"))
        self.assertIn(self.bar.sound_choice.buttons["both"], G._focusables(self.bar.widgets["audio"]))


class MixerTests(unittest.TestCase):
    """Vini: the volume mixer in the Game Bar -- the Sound menu's per-app
    volumes (mixer_ui.AppMixer) in the Audio widget, kept up to date only
    while it's on screen."""

    def setUp(self):
        config.save(G.CONFIG, {})
        from sonata2.backend import mixer
        self.mixer = mixer
        self.p = [mock.patch.object(mixer, "available", return_value=True),
                  mock.patch.object(mixer, "streams", return_value=[]),
                  mock.patch("sonata2.backend.system.run_async",
                             side_effect=lambda fn, done=None, *a: done(fn(*a)) if done else fn(*a))]
        for p in self.p:
            p.start()
        self.service = mock.Mock(listeners=[])
        self.top = type("Top", (), {})()
        self.top.mixer = self.service
        self.bar = G.GameBar(_app(), self.top)

    def tearDown(self):
        self.bar.destroy()
        for p in self.p:
            p.stop()

    def test_mixer_in_the_audio_widget(self):
        from sonata2.shell.mixer_ui import AppMixer
        self.assertIsInstance(self.bar.app_mixer, AppMixer)
        self.assertTrue(self.bar.app_mixer.is_ancestor(self.bar.widgets["audio"]))

    def test_listens_only_while_on_screen(self):
        self.bar.open()
        spin(G.READ_AFTER_MS + 80)
        self.assertIn(self.bar._streams, self.service.listeners)
        self.bar.close_bar()
        spin(G._close_ms() + 120)
        self.assertNotIn(self.bar._streams, self.service.listeners)

    def test_pinned_audio_keeps_listening(self):
        self.bar.toggle_pin("audio")
        self.bar.open()
        spin(G.READ_AFTER_MS + 80)
        self.bar.close_bar()
        spin(G._close_ms() + 120)
        self.assertIn(self.bar._streams, self.service.listeners)
        self.bar._hide_pins()

    def test_rows_reachable_with_the_controller(self):
        from sonata2.shell import mixer_ui
        s = mock.Mock(key="spotify", volume=60, muted=False)
        with mock.patch.object(mixer_ui.mixer, "group", side_effect=lambda ss: ss), \
                mock.patch.object(mixer_ui, "shown", return_value=("Spotify", None)), \
                mock.patch.object(mixer_ui, "_gicon", return_value=mixer_ui.Gio.ThemedIcon.new("audio-x-generic")):
            self.bar._streams([s])
        row = self.bar.app_mixer.rows["spotify"]
        stops = G._focusables(self.bar.widgets["audio"])
        self.assertIn(row.slider, stops)
        self.assertIn(row.mute, stops)


class PerformanceTests(Base):
    def test_graphs_of_fps_cpu_gpu_memory(self):
        from sonata2.shell import statsui
        box = self.bar.widgets["performance"].body.get_first_child().get_first_child()    # (in its FixedWidth)
        grid = box.get_last_child().get_child()
        grid = grid.get_child() if isinstance(grid, Gtk.Viewport) else grid
        self.assertIsInstance(grid, Gtk.Grid)
        self.assertIsNone(self.bar.widgets["performance"].empty.get_parent())
        expected = [k for k in ("cpu", "ram") if k in statsui.KINDS]
        for k in expected:
            self.assertIn(k, self.bar.perf_kinds)
        self.assertTrue(all(k in statsui.KINDS for k in self.bar.perf_kinds))


class WidthTests(Base):
    def test_widgets_keep_their_width(self):
        """A long FPS hint made Performance twice as wide, over Capture: every
        widget keeps one width, its text wraps."""
        for wid, w in self.bar.widgets.items():
            self.assertLessEqual(w.get_width(), G.WIDGET_W + 1, wid)
        b = {k: w.compute_bounds(self.bar.root)[1] for k, w in self.bar.widgets.items()}
        self.assertLessEqual(b["audio"].get_x() + b["audio"].get_width(), b["capture"].get_x())
        self.assertLessEqual(b["capture"].get_x() + b["capture"].get_width(), b["performance"].get_x())


class NowPlayingTests(Base):
    def test_now_playing_widget_top_left(self):
        """Vini: the Now Playing widget too -- the menu bar's module (artwork,
        title, artist, previous / play-pause / next), at the top left."""
        w = self.bar.widgets["nowplaying"]
        self.assertIsNone(w.empty.get_parent())
        self.assertIn("nowplaying", self.bar.toggles)
        self.assertEqual((w.get_halign(), w.get_valign()), (Gtk.Align.START, Gtk.Align.START))
        names, stack = set(), [w]
        while stack:
            c = stack.pop()
            if isinstance(c, Gtk.Button):
                names.add(c.get_icon_name())
            k = c.get_first_child()
            while k is not None:
                stack.append(k)
                k = k.get_next_sibling()
        self.assertTrue({"media-skip-backward-symbolic", "media-skip-forward-symbolic"} <= names)

    def test_new_widget_shows_after_an_update(self):
        """A Game Bar saved before Now Playing existed: it shows the first
        time, and a choice made later (turned off) is kept."""
        config.save(G.CONFIG, {"widgets": ["audio", "capture"]})
        again = G.GameBar(_app(), self.top)
        try:
            self.assertTrue(again.widgets["nowplaying"].get_visible())
            self.assertFalse(again.widgets["performance"].get_visible())   # the user's own choice
            again.toggle_widget("nowplaying")
        finally:
            again.destroy()
        third = G.GameBar(_app(), self.top)
        try:
            self.assertFalse(third.widgets["nowplaying"].get_visible())
        finally:
            third.destroy()

    def test_follows_the_player_only_while_shown(self):
        from sonata2.shell import mpris
        p = mpris.players()
        n = len(p.listeners)
        self.bar.close_bar()
        spin(G._close_ms() + 150)
        self.assertLess(len(p.listeners), n)                       # unrealized: no more updates


class ControlsTests(Base):
    """Vini: Wi-Fi, Bluetooth, Airplane Mode, Do Not Disturb and the screen's
    brightness in the Game Bar -- Control Center's own (QuickControls)."""

    def test_controls_widget_top_right(self):
        from sonata2.shell.topbar import QuickControls
        w = self.bar.widgets["controls"]
        q = self.bar.quick
        self.assertIsInstance(q, QuickControls)
        self.assertEqual((w.get_halign(), w.get_valign()), (Gtk.Align.END, Gtk.Align.START))
        for t in (q.wifi, q.bt, q.plane, q.dnd):
            self.assertTrue(t.is_ancestor(w))
        for b in (q.plane.button, q.bright):
            b.set_sensitive(True)                                   # (no radios / backlight in the tests)
        stops = G._focusables(w)
        for b in (q.wifi.button, q.bt.button, q.plane.button, q.dnd.button, q.bright):
            self.assertIn(b, stops)                                 # the controller reaches them

    def test_read_again_at_each_opening_for_the_games_display(self):
        q = self.bar.quick
        with mock.patch.object(q, "read_quick") as read:
            self.bar.close_bar()
            spin(G._close_ms() + 120)
            self.bar.open()
            read.assert_not_called()                                # not while it opens (Vini: slow)
            spin(G.READ_AFTER_MS + 80)
        read.assert_called_once()
        self.assertEqual(q.output, "DP-1")                         # (focused_output: the game's)

    def test_brightness_and_radios_as_control_center(self):
        from sonata2.backend import system
        q = self.bar.quick
        with mock.patch.object(system, "run_latest") as latest:
            q.bright.set_value(30)
        self.assertEqual(latest.call_args.args[1:], (system.set_brightness, 30, q.output))
        q._fill_toggles((True, ("Home", 80, False), False, False))
        with mock.patch.object(system, "set_airplane_mode", return_value=True) as plane:
            q._set_airplane(True)
            spin(100)
        plane.assert_called_with(True)


class FpsLimitTests(Base):
    def test_control_centers_fps_limit_in_performance(self):
        """Vini: Control Center's FPS Limit in the Game Bar -- the same module
        (fpsmodule.FpsModule): its choices and the game's frame times."""
        from sonata2.shell.fpsmodule import FpsModule
        from sonata2 import fpslimit
        m = self.bar.fps_limit
        self.assertIsInstance(m, FpsModule)
        self.assertTrue(m.is_ancestor(self.bar.widgets["performance"]))
        self.assertEqual(set(m.buttons), set(fpslimit.CHOICES))
        stops = G._focusables(self.bar.widgets["performance"])
        self.assertTrue(all(b in stops for b in m.buttons.values()) or not m.seg.get_sensitive())
        with mock.patch.object(fpslimit, "installed", return_value=True), \
                mock.patch.object(fpslimit, "set") as chosen:
            m.update()
            self.assertIn(m.buttons["60"], G._focusables(self.bar.widgets["performance"]))
            m.buttons["60"].emit("clicked")
        chosen.assert_called()
        self.assertEqual(chosen.call_args.args[0], "60")

    def test_every_control_center_graph_under_it(self):
        """Vini: both GPUs, temperatures, everything Control Center has --
        in its order; FPS only once (the FPS Limit module)."""
        kinds = ("cpu", "gpu_amd", "gpu_nvidia", "vram_amd", "vram_nvidia", "temp_cpu", "temp_amd", "temp_nvidia",
                 "ram", "net", "fps")
        self.assertEqual(G.perf_kinds(kinds), list(kinds[:-1]))
        from sonata2.shell import statsui
        self.assertEqual(self.bar.perf_kinds, [k for k in statsui.KINDS if k != "fps"])

    def test_many_graphs_scroll_inside(self):
        box = self.bar.widgets["performance"].body.get_first_child().get_first_child()
        scroll = box.get_last_child()
        self.assertIsInstance(scroll, Gtk.ScrolledWindow)
        self.assertEqual(scroll.get_max_content_height(), G.PERF_MAX_H)


class MoveAndPinTests(Base):
    def test_moved_widget_stays_there_and_double_click_puts_it_back(self):
        w = self.bar.widgets["audio"]
        self.bar._save_pos("audio", (300, 200))
        w.place((300, 200))
        spin(100)
        ok, b = w.compute_bounds(self.bar.root)
        self.assertEqual((round(b.get_x()), round(b.get_y())), (300, 200))
        again = G.GameBar(_app(), self.top)
        try:
            self.assertEqual(again.widgets["audio"].get_margin_start(), 300)
        finally:
            again.destroy()
        self.bar.reset_position("audio")
        self.assertNotIn("audio", config.load(G.CONFIG, G.DEFAULTS)["positions"])
        self.assertEqual(w.get_halign(), Gtk.Align.START)          # its own corner again
        self.assertEqual(w.get_valign(), Gtk.Align.END)

    def test_drag_measured_on_the_game_bar_not_the_moving_title(self):
        """Vini: a dragged widget shook -- the gesture was on its title,
        which moved with it, so every offset was measured from a new place.
        Now it's the Game Bar's (still): the widget follows the pointer."""
        w = self.bar.widgets["capture"]
        self.assertIs(self.bar.drag.get_widget(), self.bar.root)
        self.assertFalse(any(isinstance(c, Gtk.GestureDrag) for c in list(w.head.observe_controllers())))
        ok, hb = w.head.compute_bounds(self.bar.root)
        ok, wb = w.compute_bounds(self.bar.root)
        px, py = hb.get_x() + 20, hb.get_y() + hb.get_height() / 2
        self.bar.drag.emit("drag-begin", px, py)
        for dx, dy in ((10, -5), (40, -60), (41, -61), (80, -120)):
            self.bar.drag.emit("drag-update", dx, dy)
            spin(60)                                               # (a layout pass, even on a busy machine)
            ok, nb = w.compute_bounds(self.bar.root)
            self.assertEqual((round(nb.get_x()), round(nb.get_y())),
                             (round(wb.get_x() + dx), round(wb.get_y() + dy)))       # exactly under the pointer
        self.bar.drag.emit("drag-end", 80, -120)
        self.assertEqual(config.load(G.CONFIG, G.DEFAULTS)["positions"]["capture"],
                         [round(wb.get_x() + 80), round(wb.get_y() - 120)])

    def test_drag_not_from_a_button_or_the_body(self):
        w = self.bar.widgets["audio"]
        ok, pb = w.pin_btn.compute_bounds(self.bar.root)
        self.bar.drag.emit("drag-begin", pb.get_x() + 4, pb.get_y() + 4)        # the pin
        self.assertNotIn("w", self.bar._drag_state)
        ok, bb = w.body.compute_bounds(self.bar.root)
        self.bar.drag.emit("drag-begin", bb.get_x() + 4, bb.get_y() + bb.get_height() - 4)
        self.assertNotIn("w", self.bar._drag_state)

    def test_pinned_widget_stays_over_the_game(self):
        self.bar.toggle_pin("performance")
        self.assertTrue(self.bar.widgets["performance"].pin_btn.has_css_class("on"))
        self.assertEqual(config.load(G.CONFIG, G.DEFAULTS)["pinned"], ["performance"])
        self.bar.close_bar()
        spin(G._close_ms() + 120)
        self.assertIn("performance", self.bar.pins)
        win = self.bar.pins["performance"]
        self.assertIs(win.get_child(), self.bar.widgets["performance"])
        self.assertNotIn("audio", self.bar.pins)                   # not pinned: gone with the bar
        self.bar.open()
        self.assertEqual(self.bar.pins, {})
        self.assertIs(self.bar.widgets["performance"].get_parent(), self.bar.root)
        self.bar.toggle_pin("performance")
        self.assertEqual(config.load(G.CONFIG, G.DEFAULTS)["pinned"], [])

    def test_pins_let_clicks_through(self):
        src = open(G.__file__).read()
        self.assertIn('self.connect("map", lambda *_a: layer.set_input_region(self, []))', src)
        from sonata2.ui import glass
        self.assertIn(f'app_id is "{G.PIN_NAMESPACE}"', glass.NEVER_BLUR)


class ReplayInBarTests(Base):
    def test_replay_on_off_and_save_after_the_bar_has_gone(self):
        self.bar.replay_btn.emit("clicked")
        self.rp.start.assert_called_once_with("DP-1")
        self.rp.running = True
        self.bar._sync_capture()
        self.assertTrue(self.bar.replay_btn.has_css_class("on"))
        self.assertTrue(self.bar.clip_btn.get_sensitive())
        self.bar.clip_btn.emit("clicked")
        self.assertFalse(self.bar.is_open())
        self.rp.save.assert_not_called()                          # the Game Bar not in the clip
        spin(G._close_ms() + 160)
        self.rp.save.assert_called_once()

    def test_without_wl_screenrec_it_says_so(self):
        self.rp.available.return_value = False
        self.bar._sync_capture()
        self.assertFalse(self.bar.replay_btn.get_sensitive())
        self.assertIn("wl-screenrec", self.bar.replay_btn.get_tooltip_text())
        self.assertFalse(self.bar.clip_btn.get_sensitive())


class FakeProc:
    def __init__(self, *a, **k):
        self.cmd = a[0] if a else None
        self.signals, self.code = [], None

    def poll(self):
        return self.code

    def send_signal(self, s):
        self.signals.append(s)
        if s == signal.SIGINT:
            self.code = 0

    def terminate(self):
        self.code = 0

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


class ReplayTests(unittest.TestCase):
    def test_command_keeps_the_last_seconds(self):
        cmd = R.command("/v/.replay.mp4", "DP-1", 30, "sink.monitor")
        self.assertEqual(cmd[cmd.index("wl-screenrec"):], ["wl-screenrec", "--history", "30", "-f", "/v/.replay.mp4",
                                                          "-o", "DP-1", "--audio", "--audio-device", "sink.monitor"])

    def test_save_flushes_finishes_keeps_and_starts_again(self):
        d = tempfile.mkdtemp()
        config.save("capture", {"movies_to": "other", "movies_dir": d})
        cap = mock.Mock()
        r = R.Replay(cap)
        procs = []

        def popen(cmd, **_k):
            p = FakeProc(cmd)
            procs.append(p)
            return p
        with mock.patch.object(R.shutil, "which", return_value="/usr/bin/wl-screenrec"), \
                mock.patch.object(R.subprocess, "Popen", side_effect=popen), \
                mock.patch("sonata2.shell.capture.audio_device", return_value=None):
            self.assertTrue(r.start("DP-1"))
            with open(r.path, "wb") as f:                          # what wl-screenrec writes on SIGUSR1
                f.write(b"clip")
            self.assertTrue(r.save())
            spin(R.FLUSH_MS + 400)
        first = procs[0]
        self.assertEqual(first.signals[:2], [signal.SIGUSR1, signal.SIGINT])
        clip = cap._saved.call_args.args[0]
        self.assertTrue(os.path.basename(clip).startswith("Replay "))
        self.assertTrue(os.path.exists(clip))
        self.assertEqual(len(procs), 2)                             # a new buffer at once
        self.assertTrue(r.running)
        r.stop()
        self.assertFalse(r.running)


class TwoTracksTests(unittest.TestCase):
    def test_commands(self):
        cmd = C.tracks_command("/m/.a.mka", "sys.monitor", "mic")
        self.assertEqual(cmd.count("-f"), 2)
        self.assertIn("sys.monitor", cmd)
        self.assertEqual(cmd[cmd.index("-map"):cmd.index("-map") + 4], ["-map", "0:a", "-map", "1:a"])
        mux = C.mux_command("/m/v.mp4", "/m/a.mka", "/m/out.mp4")
        self.assertIn("amix=inputs=2", " ".join(mux))
        self.assertEqual(mux.count("-map"), 4)                         # picture, mix, system, microphone

    def test_record_both_records_the_picture_silent_and_two_tracks(self):
        cap = C.Capture.__new__(C.Capture)
        cap.app, cap.bar, cap.pill, cap.recorder, cap._tracks, cap._replay = None, None, mock.Mock(), None, None, None
        d = tempfile.mkdtemp()
        cfg = dict(C.DEFAULTS, audio="both", movies_to="other", movies_dir=d)
        started = []
        with mock.patch.object(C.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(C, "audio_device", side_effect=lambda k: {"system": "s.monitor", "mic": "m"}[k]), \
                mock.patch.object(C.Capture, "_spawn", lambda self_: started.append(self_._rec["audio"]) or True), \
                mock.patch.object(C.subprocess, "Popen", side_effect=lambda cmd, **_k: FakeProc(cmd)):
            cap._record(None, cfg, "DP-1")
        self.assertEqual(started, [None])                          # the recorder: no sound of its own
        self.assertTrue(os.path.basename(cap.rec_path).startswith("."))
        self.assertIn("s.monitor", cap._tracks["proc"].cmd)
        self.assertIn("m", cap._tracks["proc"].cmd)
        self.assertTrue(cap._tracks["out"].endswith(".mp4") and not os.path.basename(cap._tracks["out"]).startswith("."))

    def test_without_a_microphone_both_is_the_system_sound(self):
        cap = C.Capture.__new__(C.Capture)
        cap.app, cap.bar, cap.pill, cap.recorder, cap._tracks, cap._replay = None, None, mock.Mock(), None, None, None
        started = []
        with mock.patch.object(C.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(C, "audio_device", side_effect=lambda k: "s.monitor" if k == "system" else None), \
                mock.patch.object(C.Capture, "_spawn", lambda self_: started.append(self_._rec["audio"]) or True):
            cap._record(None, dict(C.DEFAULTS, audio="both", movies_to="other", movies_dir=tempfile.mkdtemp()), "DP-1")
        self.assertEqual(started, ["s.monitor"])
        self.assertIsNone(cap._tracks)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_mux_gives_one_movie_with_two_tracks(self):
        d = tempfile.mkdtemp()
        v, a, out = (os.path.join(d, n) for n in (".v.mp4", ".a.mka", "out.mp4"))
        run = lambda *c: subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *c], check=True)  # noqa: E731
        run("-f", "lavfi", "-i", "testsrc=size=160x90:rate=10", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", v)
        run("-f", "lavfi", "-i", "sine=f=440", "-f", "lavfi", "-i", "sine=f=880", "-t", "1",
            "-map", "0:a", "-map", "1:a", "-c:a", "aac", a)
        cap = C.Capture.__new__(C.Capture)
        got = []
        cap._mux({"video": v, "audio": a, "out": out, "proc": None}, got.append)
        for _i in range(100):
            if got:
                break
            spin(100)
        self.assertEqual(got, [out])
        streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                                 capture_output=True, text=True).stdout.split()
        # the mix first (players play one track: Vini heard no microphone), then each apart
        self.assertEqual(streams.count("audio"), 3)
        self.assertEqual(streams.count("video"), 1)
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                               "stream_disposition=default:stream_tags=handler_name", "-of", "csv=p=0", out],
                              capture_output=True, text=True).stdout.splitlines()
        self.assertTrue(info[0].startswith("1"), info)                       # the mix: the default
        self.assertIn("System Sound + Microphone", info[0])
        self.assertTrue(info[1].startswith("0") and info[2].startswith("0"), info)

        def level(track, freq):
            r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", out, "-map", f"0:a:{track}",
                                "-af", f"bandpass=f={freq}:w=60,volumedetect", "-f", "null", "-"],
                               capture_output=True, text=True).stderr
            return float(r.split("max_volume:")[1].split("dB")[0])
        self.assertGreater(level(0, 440), -30)                                 # the mix: the system's tone...
        self.assertGreater(level(0, 880), -30)                                 # ...and the microphone's
        self.assertFalse(os.path.exists(v) or os.path.exists(a))   # the parts gone


class WiringTests(unittest.TestCase):
    def test_super_alt_g_saves_the_clip(self):
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(os.path.join(REPO, "config", "wayfire.ini"))
        self.assertEqual(ini["command"]["binding_replay_save"].strip(), "<alt> <super> KEY_G")
        self.assertIn("ActivateAction replay-save", ini["command"]["command_replay_save"])
        self.assertIn("replay_save", [s.id for s in shortcuts.SHORTCUTS])

    def test_toolbar_offers_both(self):
        self.assertIn('Item(_("Record System Sound and Microphone")', open(C.__file__).read())


if __name__ == "__main__":
    unittest.main()
