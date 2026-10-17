"""Volume per app (backend/mixer.py, shell/mixer_ui.py). Run:
xvfb-run python3 -m unittest tests.test_mixer"""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.backend import mixer as M  # noqa: E402


def item(index, name, binary, pct="65%", mute=False, role=None, app_id=None, icon=None, sink=1):
    props = {"application.name": name, "application.process.binary": binary}
    if role:
        props["media.role"] = role
    if app_id:
        props["application.id"] = app_id
    if icon:
        props["application.icon_name"] = icon
    return {"index": index, "mute": mute, "corked": False, "properties": props, "sink": sink,
            "volume": {"front-left": {"value_percent": pct}, "front-right": {"value_percent": pct}}}


PACTL = json.dumps([
    item(10, "Firefox", "firefox", "40%", icon="firefox"),
    item(11, "Firefox", "firefox", "40%"),                       # a second tab playing
    item(12, "Spotify", "spotify", "80%", mute=True, app_id="com.spotify.Client", sink=2),
    item(13, "pw-play", "pw-play", "100%"),                     # Sonata's own sound
    item(14, "Chat", "chat", "100%", role="event"),             # a ding
])


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class BackendTest(unittest.TestCase):
    def setUp(self):
        config.save(M.NAME, {})

    def test_parse(self):
        ss = M.parse(PACTL)
        self.assertEqual([s.index for s in ss], [10, 11, 12])     # own players and events left out
        self.assertEqual(ss[0].key, "firefox")
        self.assertEqual(ss[0].volume, 40)
        self.assertEqual(ss[2].key, "com.spotify.client")          # the app id first
        self.assertTrue(ss[2].muted)
        self.assertEqual(M.parse("not json"), [])
        self.assertEqual(M.parse("{}"), [])

    def test_group_one_row_per_app(self):
        self.assertEqual([s.key for s in M.group(M.parse(PACTL))], ["firefox", "com.spotify.client"])

    def test_new_stream_line(self):
        self.assertEqual(M.new_stream_index("Event 'new' on sink-input #42"), 42)
        self.assertIsNone(M.new_stream_index("Event 'change' on sink-input #42"))
        self.assertIsNone(M.new_stream_index("Event 'new' on sink #1"))

    def test_set_level_saves_and_sets_every_stream(self):
        calls = []
        with mock.patch.object(M, "_run", side_effect=lambda a, timeout=4: (calls.append(a), (0, ""))[1]):
            self.assertTrue(M.set_level("firefox", 25, None, M.parse(PACTL)))
        self.assertEqual(calls, [["pactl", "set-sink-input-volume", "10", "25%"],
                                 ["pactl", "set-sink-input-volume", "11", "25%"]])
        self.assertEqual(M.saved()["volumes"], {"firefox": 25})      # kept for next time
        with mock.patch.object(M, "_run", return_value=(0, "")):
            M.set_level("firefox", 500)
        self.assertEqual(M.saved()["volumes"]["firefox"], M.MAX_PERCENT)

    def test_restore_on_a_new_stream(self):
        M.remember("firefox", 25)
        M.remember("com.spotify.client", muted=False)
        ss = M.parse(PACTL)
        calls = []
        with mock.patch.object(M, "_run", side_effect=lambda a, timeout=4: (calls.append(a), (0, ""))[1]):
            self.assertTrue(M.restore(ss[0]))
            self.assertTrue(M.restore(ss[2]))                       # unmuted, as saved
            M.remember("firefox", 40)
            self.assertFalse(M.restore(ss[0]))                      # already right: nothing to do
        self.assertEqual(calls, [["pactl", "set-sink-input-volume", "10", "25%"],
                                 ["pactl", "set-sink-input-mute", "12", "0"]])

    def test_stream_knows_its_output(self):
        ss = M.parse(PACTL)
        self.assertEqual([s.sink for s in ss], [1, 1, 2])
        self.assertEqual(M.parse(json.dumps([{**item(1, "A", "a"), "sink": None}]))[0].sink, -1)

    def test_saved_is_a_known_config(self):
        M.remember("x", 10, True)
        self.assertEqual(config.load(M.NAME, M.DEFAULTS), {"volumes": {"x": 10}, "muted": {"x": True}})


SINKS = json.dumps([
    {"index": 1, "name": "alsa_output.pci", "description": "Built-in Audio Analog Stereo"},
    {"index": 2, "name": "bluez_output.AA", "description": "AirPods Pro"},
    {"index": 3, "name": "alsa_output.hdmi", "description": "HDMI"},              # unplugged: not in Output
    {"index": 4, "name": "sonata-eq.alsa_output.pci", "description": "Equalizer"},
    {"index": 5, "name": "alsa_output.usb", "description": "USB Audio"},          # two ports: its own name
])


def devices():
    from sonata2.backend.system import AudioDevice
    return [AudioDevice("alsa_output.pci|analog-output-speaker", "Speakers", True),
            AudioDevice("bluez_output.AA|", "AirPods Pro", False),
            AudioDevice("alsa_output.usb|line", "Line Out", False),
            AudioDevice("alsa_output.usb|headphones", "Headphones", False)]


class PerAppOutputTest(unittest.TestCase):
    """Vini: each app's slider gets a button, like the Sound module's output
    one, that sends that app alone to another output."""

    def test_outputs_as_in_the_output_list(self):
        outs = M.outputs_from(SINKS, devices())
        self.assertEqual([(o.index, o.name, o.label) for o in outs],
                         [(1, "alsa_output.pci", "Speakers"), (2, "bluez_output.AA", "AirPods Pro"),
                          (5, "alsa_output.usb", "USB Audio")])
        self.assertEqual(M.outputs_from("not json", devices()), [])

    def test_current_output(self):
        outs = M.outputs_from(SINKS, devices())
        ss = M.parse(PACTL)
        self.assertEqual(M.current_output(ss[2], outs, SINKS).label, "AirPods Pro")
        ss[0].sink = 4                                   # through the equalizer: its device's
        self.assertEqual(M.current_output(ss[0], outs, SINKS).name, "alsa_output.pci")
        ss[0].sink = 99
        self.assertIsNone(M.current_output(ss[0], outs, SINKS))

    def test_move_sends_every_stream_of_the_app(self):
        calls = []
        with mock.patch.object(M, "_run", side_effect=lambda a, timeout=4: (calls.append(a), (0, ""))[1]):
            self.assertTrue(M.move("firefox", "bluez_output.AA", M.parse(PACTL)))
        self.assertEqual(calls, [["pactl", "move-sink-input", "10", "bluez_output.AA"],
                                 ["pactl", "move-sink-input", "11", "bluez_output.AA"]])

    def test_choices_read_once(self):
        with mock.patch.object(M, "_run", return_value=(0, SINKS)), \
                mock.patch("sonata2.backend.system.audio_outputs", return_value=devices()):
            outs, cur = M.choices(M.parse(PACTL)[2])
        self.assertEqual(len(outs), 3)
        self.assertEqual(cur.name, "bluez_output.AA")
        with mock.patch.object(M, "_run", return_value=(1, "")):
            self.assertEqual(M.choices(M.parse(PACTL)[2]), ([], None))


class ServiceTest(unittest.TestCase):
    """Performance: stream events with no new stream and no open menu run nothing."""

    def service(self):
        svc = M.MixerService.__new__(M.MixerService)
        svc.listeners, svc._new, svc._src, svc.proc = [], set(), 0, None
        return svc

    def test_idle_events_spawn_nothing(self):
        svc = self.service()
        with mock.patch.object(M, "streams") as st, mock.patch("sonata2.backend.system.run_async") as ra:
            svc._changed()
        ra.assert_not_called()
        st.assert_not_called()

    def test_new_stream_restored_and_menu_told(self):
        svc = self.service()
        got = []
        svc.listeners.append(got.append)
        svc._new = {10, 12}
        ss = M.parse(PACTL)
        with mock.patch.object(M, "streams", return_value=ss) as st, \
                mock.patch.object(M, "restore", return_value=True) as rs, \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: cb(fn())):
            svc._changed()
        self.assertEqual([c.args[0].index for c in rs.call_args_list], [10, 12])   # each new one
        self.assertEqual(st.call_count, 2)                      # read again after restoring
        self.assertEqual(got, [ss])


class MixerUiTest(unittest.TestCase):
    def test_icon_never_breaks_the_menu(self):
        with mock.patch.object(self.U.apps, "match_app_id", side_effect=TypeError("GI")):
            self.assertIsNotNone(self.U._gicon(M.parse(PACTL)[0]))

    @classmethod
    def setUpClass(cls):
        Gtk.init()
        from sonata2.shell import mixer_ui
        cls.U = mixer_ui

    def setUp(self):
        from gi.repository import Gio
        p = mock.patch.object(self.U, "_gicon", return_value=Gio.ThemedIcon.new("audio-x-generic"))
        p.start()
        self.addCleanup(p.stop)
        self.applied = []
        self.box = self.U.AppMixer(apply=lambda *a: self.applied.append(a[:3]))
        self.win = Gtk.Window(child=self.box)
        self.win.present()
        settle(50)

    def tearDown(self):
        self.win.destroy()

    def test_row_looks_like_the_sound_slider(self):
        """Vini: the big white slider of the Sound menu, the app's icon and
        name above it (symmetrical with the main one)."""
        from sonata2.ui.controls import ModuleSlider
        self.box.set_streams(M.parse(PACTL))
        row = self.box.rows["firefox"]
        self.assertIsInstance(row.slider, ModuleSlider)
        self.assertEqual(row.box.get_orientation(), Gtk.Orientation.VERTICAL)
        title, under = row.box.get_first_child(), row.box.get_last_child()
        self.assertIs(title.get_first_child(), row.mute)          # icon, then name, on top
        capsule = under.get_first_child()
        self.assertTrue(capsule.has_css_class("cc-slider-box"))   # the capsule under them,
        self.assertIs(under.get_last_child(), row.output)         # the output button at its right

    def test_output_button_picks_for_that_app(self):
        picked = []
        box = self.U.AppMixer(apply=lambda *a: None, pick=lambda b, key, ss: picked.append((b, key, len(ss))))
        box.set_streams(M.parse(PACTL))
        row = box.rows["com.spotify.client"]
        row.output.emit("clicked")
        self.assertEqual(picked, [(row.output, "com.spotify.client", 3)])


    def test_picker_lists_outputs_and_moves_the_app(self):
        from sonata2.shell import topbar
        ss = M.parse(PACTL)
        outs = [M.Output(1, "alsa_output.pci", "Speakers"), M.Output(2, "bluez_output.AA", "AirPods Pro")]
        seen = {}

        def menu(btn, title, list_fn, set_fn):
            seen["devs"] = [(d.name, d.default) for d in list_fn()]
            seen["set"] = set_fn
        with mock.patch.object(topbar, "_device_menu", side_effect=menu), \
                mock.patch.object(M, "choices", return_value=(outs, outs[1])):
            self.U.AppMixer._pick_menu(None, "com.spotify.client", ss)
        self.assertEqual(seen["devs"], [("Speakers", False), ("AirPods Pro", True)])
        with mock.patch.object(M, "move") as move:
            seen["set"]("alsa_output.pci")
        move.assert_called_once_with("com.spotify.client", "alsa_output.pci", ss)

    def test_rows_follow_the_streams(self):
        self.assertTrue(self.box.empty.get_visible())
        ss = M.parse(PACTL)
        self.box.set_streams(ss)
        settle(self.U.SLIDE_MS + 100)
        self.assertEqual(list(self.box.rows), ["firefox", "com.spotify.client"])
        self.assertFalse(self.box.empty.get_visible())
        ff = self.box.rows["firefox"]
        self.assertTrue(ff.get_child_revealed())                  # slid in
        self.assertTrue(self.box.rows["com.spotify.client"].muted)
        self.box.set_streams(ss[2:])                               # Firefox stopped
        self.assertNotIn("firefox", self.box.rows)
        settle(self.U.SLIDE_MS + 150)
        self.assertIsNone(ff.get_parent())                         # slid out, then removed
        self.box.set_streams(ss[2:])                               # same again: the row stays
        self.assertEqual(list(self.box.rows), ["com.spotify.client"])

    def test_slider_applies_after_it_stops(self):
        self.box.set_streams(M.parse(PACTL))
        row = self.box.rows["firefox"]
        for v in (50, 55, 60):
            row.slider.set_value(v)
        self.assertEqual(self.applied, [])
        settle(self.U.APPLY_MS + 200)
        self.assertEqual(self.applied, [("firefox", 60, None)])   # one call, the last level

    def test_outside_updates_dont_echo(self):
        ss = M.parse(PACTL)
        self.box.set_streams(ss)
        ss[0].volume = 90
        self.box.set_streams(ss)
        settle(self.U.APPLY_MS + 200)
        self.assertEqual(self.box.rows["firefox"].slider.get_value(), 90)
        self.assertEqual(self.applied, [])                         # PipeWire's own change: not sent back

    def test_mute_and_unmute_by_moving(self):
        self.box.set_streams(M.parse(PACTL))
        sp = self.box.rows["com.spotify.client"]
        sp.toggle_mute()
        self.assertEqual(self.applied[-1], ("com.spotify.client", None, False))
        sp.toggle_mute()
        self.assertTrue(sp.muted)
        sp.slider.set_value(30)
        settle(self.U.APPLY_MS + 200)
        self.assertEqual(self.applied[-1], ("com.spotify.client", 30, False))   # moving unmutes
        self.assertFalse(sp.muted)


if __name__ == "__main__":
    unittest.main()


class VolumeMixerAlignedWithSoundSlidersTest(unittest.TestCase):
    """Vini: the Volume Mixer's app sliders started further in and ended
    earlier than the Sound module's, with no output button. They line up
    now (same left edge, same width) with the button at their right, in
    Control Center and in the menu bar's Sound menu."""

    @classmethod
    def setUpClass(cls):
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from sonata2 import ui
        ui.theme.setup()                 # the real CSS: paddings are what this is about

    def setUp(self):
        import types
        from sonata2.backend import system
        from sonata2.shell import topbar as T
        self.T = T
        ss = [M.Stream(index=10, key="google-chrome", name="Google Chrome", icon="", volume=70, muted=False,
                       sink=1)]
        self.service = types.SimpleNamespace(listeners=[])
        for p in (mock.patch.object(M, "available", return_value=True),
                  mock.patch.object(M, "streams", return_value=ss),
                  mock.patch.object(system, "run_async",
                                    side_effect=lambda fn, cb=None, *a: cb and cb(fn(*a))),
                  mock.patch.object(system, "volume", return_value=(60, False)),
                  mock.patch.object(system, "audio_outputs", return_value=devices()),
                  mock.patch.object(T, "cached")):
            p.start()
            self.addCleanup(p.stop)
        self.wins = []
        self.addCleanup(lambda: [w.destroy() for w in self.wins])

    def bounds(self, w, ref):
        ok, b = w.compute_bounds(ref)
        self.assertTrue(ok)
        return round(b.get_x()), round(b.get_width())

    def test_control_center(self):
        import types
        config.save("controlcenter", {"modules": ["sound", "mixer"]})
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None, mixer=self.service)
        cc = self.T.ControlCenter(bar)
        win = Gtk.Window(child=cc)
        self.wins.append(win)
        win.present()
        settle(400)
        row = cc.app_mixer.rows["google-chrome"]
        self.assertEqual(self.bounds(row.slider, cc), self.bounds(cc.vol, cc))
        self.assertEqual(self.bounds(row.slider, cc), self.bounds(cc.mic, cc))
        sound_btn = cc.vol.get_parent().get_next_sibling()           # the Sound module's output button
        self.assertEqual(self.bounds(row.output, cc), self.bounds(sound_btn, cc))

    def test_sound_menu(self):
        import types
        from sonata2.ui.controls import ModuleSlider
        bar = types.SimpleNamespace(mixer=self.service, _set_volume=lambda v: None)
        bar._prefs_row = types.MethodType(self.T.Bar._prefs_row, bar)
        btn = Gtk.Button(label="x", halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        win = Gtk.Window(child=btn, default_width=400, default_height=600)
        self.wins.append(win)
        win.present()
        settle(50)
        pop = self.T.Bar._sound_panel(bar, btn)
        pop.popup()
        settle(400)
        row = bar.app_mixer.rows["google-chrome"]

        def walk(w):
            yield w
            c = w.get_first_child()
            while c is not None:
                yield from walk(c)
                c = c.get_next_sibling()
        main = next(s for s in walk(pop.get_child()) if isinstance(s, ModuleSlider))
        # Vini: "the same layout as Control Center's mixer": the output
        # button at the slider's right, both on one line under the name
        under = row.slider.get_parent()
        while under is not None and row.output.get_parent() is not under:
            under = under.get_parent()
        self.assertIsNotNone(under)
        self.assertIs(under.get_last_child(), row.output)
        x, w = self.bounds(row.output, pop)
        sx, sw = self.bounds(main, pop)
        rx, _rw = self.bounds(row.slider, pop)
        self.assertEqual(rx, sx)                          # the sliders start together
        self.assertEqual(x + w, sx + sw)                  # the button ends where the main slider ends
        pop.popdown()


class SystemSoundsTest(unittest.TestCase):
    """Vini: changing the volume flashed a row in the mixer for the feedback
    sound -- system sounds never show there."""

    def streams(self, *props):
        import json as _json
        from sonata2.backend import mixer as M
        return M.parse(_json.dumps([{"index": i, "volume": {}, "mute": False, "properties": p}
                                    for i, p in enumerate(props)]), webapp=None)

    def test_left_out(self):
        got = self.streams(
            {"application.name": "pw-play", "media.name": "audio-volume-change.oga"},   # no binary
            {"application.name": "Sonata", "media.role": "Event", "application.process.binary": "pw-cat"},
            {"application.name": "Firefox", "media.role": "event"},
            {"application.name": "GNOME Settings", "event.id": "bell-window-system"},
            {"application.name": "Some App", "media.category": "Event"},
            {"application.name": "Spotify", "application.process.binary": "spotify"})
        self.assertEqual([s.name for s in got], ["Spotify"])

    def test_apps_named_like_sonata_stay(self):
        """Review: any name starting with "sonata" was hidden -- a web app the
        user named "Sonata Radio", or Sonatype."""
        got = self.streams({"application.name": "Sonata Radio", "application.process.binary": "WebKitWebProcess"},
                           {"application.name": "Sonatype", "application.process.binary": "java"},
                           {"application.name": "Sonata", "application.process.binary": "paplay"})
        self.assertEqual(sorted(s.name for s in got), ["Sonata Radio", "Sonatype"])

    def test_player_marks_sonatas_sounds(self):
        from unittest import mock
        from sonata2 import sounds
        with mock.patch("shutil.which", lambda n: "/usr/bin/" + n):
            cmd = sounds._player("/x.oga")
        self.assertEqual(cmd[:3], ["pw-play", "--media-role", "Event"])
        self.assertIn('application.name = "Sonata"', cmd[4])
