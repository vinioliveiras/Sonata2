"""Vini: Spotify's level went back every time -- he set it in Spotify, and
Sonata kept putting back the old saved one. Levels changed outside Sonata
are learned; right after a stream starts, the saved level wins again."""
import unittest
from unittest import mock

from sonata2.backend import mixer as M


def st(index, key, vol, muted=False):
    return M.Stream(index=index, key=key, name=key, icon="", volume=vol, muted=muted)


class LearnTest(unittest.TestCase):
    def test_outside_changes_learned(self):
        data = {"volumes": {"spotify": 100, "chrome": 51}, "muted": {}}
        ss = [st(1, "spotify", 35), st(2, "chrome", 51), st(3, "chrome", 51), st(4, "discord", 80, True)]
        self.assertEqual(M.learn(ss, data), {"spotify": (35, False), "discord": (80, True)})

    def test_fresh_streams_and_disagreeing_apps_left(self):
        data = {"volumes": {"spotify": 100}, "muted": {}}
        self.assertEqual(M.learn([st(1, "spotify", 35)], data, skip={1}), {})        # its start: ours wins
        self.assertEqual(M.learn([st(2, "chrome", 20), st(3, "chrome", 70)], data), {})  # two tabs, two levels

    def test_change_events_schedule_one_learn(self):
        svc = M.MixerService.__new__(M.MixerService)
        svc.listeners, svc._new, svc._src, svc.proc, svc._fresh, svc._learn_src = [], set(), 0, None, {}, 0
        with mock.patch.object(M.GLib, "timeout_add", return_value=7) as ta:
            svc._line("Event 'change' on sink-input #42")
            svc._line("Event 'change' on sink-input #42")
        self.assertEqual([c.args[0] for c in ta.call_args_list].count(M.LEARN_MS), 1)
        saved = []
        with mock.patch.object(M, "streams", return_value=[st(42, "spotify", 30)]), \
                mock.patch.object(M, "saved", return_value={"volumes": {"spotify": 100}, "muted": {}}), \
                mock.patch.object(M, "remember", side_effect=lambda *a: saved.append(a)), \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: fn()):
            svc._learn()
        self.assertEqual(saved, [("spotify", 30, False)])

    def test_new_streams_get_the_saved_level_again(self):
        """Spotify sets its own level just after starting: rechecked a few times."""
        svc = M.MixerService.__new__(M.MixerService)
        svc.listeners, svc._new, svc._src, svc.proc = [], {5}, 0, None
        with mock.patch.object(M.GLib, "timeout_add") as ta, mock.patch("sonata2.backend.system.run_async"):
            svc._changed()
        self.assertEqual(sorted(c.args[0] for c in ta.call_args_list), sorted(M.RECHECK_MS))


if __name__ == "__main__":
    unittest.main()
