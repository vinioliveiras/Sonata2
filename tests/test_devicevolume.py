"""Vini: "I keep the headset's mic at the maximum and it keeps resetting".
Each device keeps the volume set from Sonata; it is set again when the
device comes back (a Bluetooth headset reconnecting / switching profile
makes a new microphone)."""
import os
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


if __name__ == "__main__":
    unittest.main()
