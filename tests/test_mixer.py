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


def item(index, name, binary, pct="65%", mute=False, role=None, app_id=None, icon=None):
    props = {"application.name": name, "application.process.binary": binary}
    if role:
        props["media.role"] = role
    if app_id:
        props["application.id"] = app_id
    if icon:
        props["application.icon_name"] = icon
    return {"index": index, "mute": mute, "corked": False, "properties": props,
            "volume": {"front-left": {"value_percent": pct}, "front-right": {"value_percent": pct}}}


PACTL = json.dumps([
    item(10, "Firefox", "firefox", "40%", icon="firefox"),
    item(11, "Firefox", "firefox", "40%"),                       # a second tab playing
    item(12, "Spotify", "spotify", "80%", mute=True, app_id="com.spotify.Client"),
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

    def test_saved_is_a_known_config(self):
        M.remember("x", 10, True)
        self.assertEqual(config.load(M.NAME, M.DEFAULTS), {"volumes": {"x": 10}, "muted": {"x": True}})


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
        title, capsule = row.box.get_first_child(), row.box.get_last_child()
        self.assertIs(title.get_first_child(), row.mute)          # icon, then name, on top
        self.assertTrue(capsule.has_css_class("cc-slider-box"))   # the capsule under them

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
