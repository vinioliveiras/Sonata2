"""The menu bar burned CPU with nothing playing: five `pactl subscribe`
readers in one process, the equalizer syncing (pactl + pw-dump + pw-cli on
the main loop) on every sink event -- its own set-param included, a loop --
and the mixer re-reading the config every time a playing stream changed."""
import os
import tempfile
import time
import unittest
from unittest import mock

from gi.repository import GLib

from sonata2.backend import audiofollow, equalizer as EQ, mixer as M, pactl_watch


def _spin(ms):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


class SharedReaderTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp()
        self.count = os.path.join(self.bin, "count")
        with open(os.path.join(self.bin, "pactl"), "w") as f:
            f.write(f"#!/bin/sh\necho x >> {self.count}\nprintf \"Event 'change' on sink #1\\n\"\nsleep 30\n")
        os.chmod(os.path.join(self.bin, "pactl"), 0o755)
        self.env = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _starts(self):
        try:
            with open(self.count) as f:
                return len(f.read().split())
        except OSError:
            return 0

    def test_one_pactl_for_every_watcher(self):
        a, b = [], []
        wa, wb = pactl_watch.watch(a.append), pactl_watch.watch(b.append)
        try:
            _spin(500)
            self.assertEqual(self._starts(), 1)
            self.assertEqual(a, ["Event 'change' on sink #1"])
            self.assertEqual(b, a)
            wa.stop()
            self.assertIsNotNone(pactl_watch._HUB.reader)      # b still listens
        finally:
            wa.kill()
            wb.kill()
        self.assertIsNone(pactl_watch._HUB.reader)             # the last one gone: pactl ends

    def test_a_failing_watcher_does_not_starve_the_others(self):
        got = []
        w1 = pactl_watch.watch(lambda t: 1 / 0)
        w2 = pactl_watch.watch(got.append)
        try:
            _spin(500)
        finally:
            w1.stop()
            w2.stop()
        self.assertEqual(got, ["Event 'change' on sink #1"])

    def test_audiofollow_uses_the_shared_reader(self):
        seen = []
        with mock.patch.object(audiofollow.AudioFollow, "event", lambda self, line: seen.append(line)), \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: fn(*a)):
            f = audiofollow.start()
            try:
                _spin(500)
            finally:
                f.proc.stop()
        self.assertEqual(seen, ["Event 'change' on sink #1"])
        self.assertEqual(self._starts(), 1)


class EqualizerQuietTest(unittest.TestCase):
    def _eq(self):
        eq = EQ.Equalizer()
        eq.proc = mock.Mock(pid=4242)
        eq.proc.poll.return_value = None
        eq.chains = ("alsa_out",)
        return eq

    def test_own_filter_change_events_are_ignored(self):
        eq = self._eq()
        eq._own = frozenset({57})
        self.assertTrue(eq.own_event("Event 'change' on sink #57"))
        self.assertFalse(eq.own_event("Event 'remove' on sink #57"))    # a dead chain is still noticed
        self.assertFalse(eq.own_event("Event 'change' on sink #3"))
        self.assertFalse(eq.own_event("Event 'change' on sink-input #57"))

    def test_sink_state_finds_our_filters(self):
        out = ('[{"index": 3, "name": "alsa_out", "active_port": "spk"},'
               ' {"index": 57, "name": "sonata-eq.alsa_out"}]')
        with mock.patch.object(EQ, "_run", return_value=(0, out)):
            self.assertEqual(EQ._sink_state(), ([("alsa_out", "spk")], frozenset({57})))

    def test_same_gains_are_not_set_again(self):
        eq = self._eq()
        on = {"on": True, "preset": "Rock", "gains": [1.0] * 10}
        runs = []
        with mock.patch("sonata2.backend.system.run_async",
                        side_effect=lambda fn, cb=None, *a: (runs.append(fn), cb and cb(fn(*a)))), \
                mock.patch.object(EQ, "_set_gains", return_value=True) as sg:
            for _ in range(3):
                eq._decide([("alsa_out", "spk")], frozenset(), {"alsa_out|spk": on})
            self.assertEqual(sg.call_count, 1)
            on2 = dict(on, gains=[2.0] * 10)                     # moved in Settings: set again
            eq._decide([("alsa_out", "spk")], frozenset(), {"alsa_out|spk": on2})
            self.assertEqual(sg.call_count, 2)

    def test_nothing_runs_with_the_equalizer_off(self):
        eq = self._eq()
        eq.proc, eq.chains = None, ()
        with mock.patch("sonata2.backend.system.run_async") as ra, \
                mock.patch.object(EQ, "_node_ids") as ids:
            eq._decide([("alsa_out", "spk")], frozenset(), {"alsa_out|spk": {"on": False, "gains": [0.0] * 10}})
        ra.assert_not_called()
        ids.assert_not_called()

    def test_sync_reads_in_a_thread_one_at_a_time(self):
        eq = self._eq()
        with mock.patch("sonata2.backend.system.run_async") as ra:
            eq.sync()
            eq.sync()
        ra.assert_called_once()
        self.assertIs(ra.call_args.args[0], EQ._read_state)
        self.assertTrue(eq._again)


def st(index, key, vol, muted=False):
    return M.Stream(index=index, key=key, name=key, icon="", volume=vol, muted=muted)


class MixerLearnQuietTest(unittest.TestCase):
    def test_config_read_only_when_a_level_changed(self):
        svc = M.MixerService.__new__(M.MixerService)
        svc._fresh, svc._learn_src = {}, 0
        levels = [[st(1, "spotify", 30)]]
        with mock.patch.object(M, "streams", side_effect=lambda: levels[0]), \
                mock.patch.object(M, "saved", return_value={"volumes": {"spotify": 30}, "muted": {}}) as sv, \
                mock.patch.object(M, "remember"), \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: fn()):
            svc._learn()
            self.assertEqual(sv.call_count, 1)                  # first look
            svc._learn()
            svc._learn()                                         # title/cork changes: same level
            self.assertEqual(sv.call_count, 1)
            levels[0] = [st(1, "spotify", 50)]
            svc._learn()
            self.assertEqual(sv.call_count, 2)


if __name__ == "__main__":
    unittest.main()
