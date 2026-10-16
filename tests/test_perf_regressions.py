"""Vini felt Sonata got slower. What was found, kept from coming back:

- every sound stream's events (sink-input) read the volume again, many a second;
- no DDC/CI display found was asked again on every Control Center opening
  (`ddcutil detect`: seconds);
- quit-on-close listed every window on every title change for an app whose
  process it never found.

    python3 -m unittest tests.test_perf_regressions
"""
import unittest
from unittest import mock

from sonata2.backend import system as S
from sonata2.shell import quitonclose as Q


class AudioEventsTest(unittest.TestCase):
    def test_only_devices(self):
        self.assertTrue(S._AUDIO_EVENT.search("Event 'change' on sink #56"))
        self.assertTrue(S._AUDIO_EVENT.search("Event 'new' on source #3"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on sink-input #812"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on source-output #9"))


class DdcDetectTest(unittest.TestCase):
    def test_none_found_is_kept_too(self):
        with mock.patch.dict(S._detected, {"at": 0.0, "displays": []}), \
                mock.patch.object(S, "_run", return_value=(0, "Invalid display\n")) as run:
            self.assertEqual(S.ddc_displays(), [])
            self.assertEqual(S.ddc_displays(), [])
        run.assert_called_once()


class QuitOnCloseRetryTest(unittest.TestCase):
    def test_unknown_process_asked_again_only_now_and_then(self):
        class Mgr:
            toplevels, listeners = [], []
        m = Mgr()
        m.toplevels = [type("T", (), {"app_id": "flatpak.app"})()]
        clock = [100.0]
        views = mock.Mock(return_value=[])
        with mock.patch.object(Q.QuitOnClose, "key_of", staticmethod(lambda a: a)), \
                mock.patch.object(Q.quitapps, "app_pids", return_value=set()):
            q = Q.QuitOnClose(m, views=views, enabled=lambda: True, later=lambda ms, fn: None,
                              now=lambda: clock[0])
            clock[0] += Q.RETRY_S                                 # (its first moments: asked freely)
            for _ in range(20):                                   # title changes
                q.changed()
            self.assertEqual(views.call_count, 2)
            clock[0] += Q.RETRY_S
            q.changed()
            self.assertEqual(views.call_count, 3)


if __name__ == "__main__":
    unittest.main()
