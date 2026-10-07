"""Vini: "I keep the headset's mic at the maximum and it keeps resetting".
Each device keeps the volume set from Sonata; it is set again when the
device comes back (a Bluetooth headset reconnecting / switching profile
makes a new microphone)."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import config  # noqa: E402
from sonata2.backend import audiofollow, devicevolume as DV, system  # noqa: E402


class DeviceVolumeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(config, "CONFIG_DIR", self.dir)
        self.p.start()
        DV._pending.clear()

    def tearDown(self):
        self.p.stop()

    def test_remembered_per_device(self):
        DV.remember("source", 100)
        DV.remember("sink", 40)
        DV.flush(lambda kind: {"source": "bluez_input.AA", "sink": "alsa_output.speaker"}[kind])
        self.assertEqual(DV.wanted("source", "bluez_input.AA"), 100)
        self.assertEqual(DV.wanted("sink", "alsa_output.speaker"), 40)
        self.assertIsNone(DV.wanted("source", "alsa_input.laptop"))
        DV.remember("source", 30)                          # another mic in use now
        DV.flush(lambda kind: "alsa_input.laptop")
        self.assertEqual(DV.wanted("source", "bluez_input.AA"), 100)       # the headset's kept
        self.assertEqual(DV.wanted("source", "alsa_input.laptop"), 30)

    def test_dragging_writes_once(self):
        with mock.patch.object(DV.threading, "Timer") as T:
            for v in (10, 20, 30):
                DV.remember("sink", v)
        self.assertEqual(T.call_count, 3)
        self.assertEqual(T.return_value.cancel.call_count, 2)               # earlier ones called off
        self.assertEqual(DV._pending, {"sink": 30})

    def test_not_for_monitors_or_the_equalizer(self):
        for name in ("alsa_output.x.monitor", "sonata-eq-sink", ""):
            DV.remember("sink", 50)
            DV.flush(lambda kind: name)
            self.assertIsNone(DV.wanted("sink", name))

    def test_restored_when_it_comes_back(self):
        DV.remember("source", 100)
        DV.flush(lambda kind: "bluez_input.AA")
        calls = []
        self.assertTrue(DV.restore("source", "bluez_input.AA", wait=0,
                                   set_volume=lambda *a: calls.append(a)))
        self.assertEqual(calls, [("source", "bluez_input.AA", 100)])
        self.assertFalse(DV.restore("source", "other", wait=0, set_volume=lambda *a: calls.append(a)))

    def test_new_device_event_restores(self):
        seen = []
        f = audiofollow.AudioFollow(info=lambda kind, i: ("bluez_input.AA", {"device.bus": "bluetooth"}),
                                    get_default=lambda kind: "", set_default=lambda *a: None,
                                    enabled=lambda: False, restore=lambda *a: seen.append(a))
        f.event("Event 'new' on source #77")
        self.assertEqual(seen, [("source", "bluez_input.AA")])             # even with following off
        f.event("Event 'change' on source #77")
        self.assertEqual(len(seen), 1)

    def test_sonata_volume_calls_remember(self):
        with mock.patch.object(system, "_run", return_value=(0, "")), \
                mock.patch.object(DV, "remember") as rem:
            system.set_input_volume(100)
            system.set_volume(35)
            system.set_volume(None, True)                                   # mute only: nothing
            system.set_volume(20, node="55")                                # a given node: not "in use"
        self.assertEqual(rem.call_args_list, [mock.call("source", 100), mock.call("sink", 35)])


class LockTest(unittest.TestCase):
    """Settings > Sound > "Don't let apps change the volume": a volume
    nobody set from Sonata goes back (Vini: apps turning the mic down)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(config, "CONFIG_DIR", self.dir)
        self.p.start()
        DV._pending.clear()
        DV._set_at.clear()
        config.update("sounds", lock_volumes=True)
        data = DV.saved()
        data["source"]["bluez_input.AA"] = 100
        config.save(DV.NAME, data)
        self.calls = []

    def tearDown(self):
        self.p.stop()

    def keep(self, pct, name="bluez_input.AA", now=1000.0):
        return DV.keep("source", 5, state=lambda k, i: (name, pct),
                       set_volume=lambda *a: self.calls.append(a), now=now)

    def test_an_app_turning_it_down_is_put_back(self):
        self.assertTrue(self.keep(62))
        self.assertEqual(self.calls, [("source", "bluez_input.AA", 100)])
        self.assertFalse(self.keep(100, now=1001.0))              # our own put-back: nothing more

    def test_off_by_default_and_when_off(self):
        from sonata2 import sounds
        self.assertFalse(sounds.DEFAULTS["lock_volumes"])
        config.update("sounds", lock_volumes=False)
        self.assertFalse(self.keep(10))
        self.assertEqual(self.calls, [])

    def test_sonata_own_changes_are_not_fought(self):
        DV._set_at["source"] = 999.5                              # set from Sonata half a second ago
        self.assertFalse(self.keep(50))
        DV._set_at.clear()
        DV._pending["source"] = 50                                # still being dragged
        self.assertFalse(self.keep(50))
        self.assertEqual(self.calls, [])

    def test_first_seen_volume_is_its_own(self):
        self.assertFalse(self.keep(70, name="alsa_input.laptop"))
        self.assertEqual(DV.wanted("source", "alsa_input.laptop"), 70)
        self.assertTrue(self.keep(30, name="alsa_input.laptop", now=1002.0))
        self.assertEqual(self.calls[-1], ("source", "alsa_input.laptop", 70))

    def test_turning_it_on_keeps_the_current_volumes(self):
        DV.lock_defaults(get=lambda k: {"sink": "alsa_output.spk", "source": "bluez_input.AA"}[k],
                         volume=lambda k: (55, False) if k == "sink" else (20, False))
        self.assertEqual(DV.wanted("sink", "alsa_output.spk"), 55)
        self.assertEqual(DV.wanted("source", "bluez_input.AA"), 100)     # the one you set stays

    def test_change_events_reach_it(self):
        seen = []
        f = audiofollow.AudioFollow(info=lambda *a: None, get_default=lambda k: "", set_default=lambda *a: None,
                                    enabled=lambda: True, restore=lambda *a: None, keep=lambda *a: seen.append(a))
        f.event("Event 'change' on source #5")
        self.assertEqual(seen, [("source", 5)])

    def test_settings_switch(self):
        src = (pathlib.Path(__file__).resolve().parent.parent / "sonata2" / "settings" / "app.py").read_text()
        self.assertIn('switch_row("Don\'t let apps change the volume"', src)
        self.assertIn("devicevolume.lock_defaults", src)


if __name__ == "__main__":
    unittest.main()
