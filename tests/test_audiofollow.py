"""Vini: headphones and their microphone (Bluetooth too) become the output
and input as soon as they're connected, like macOS; when they go, the
device used before comes back."""
import unittest

from sonata2.backend import audiofollow as A


class FollowTest(unittest.TestCase):
    def setUp(self):
        self.defaults = {"sink": "alsa_output.pci.analog-stereo", "source": "alsa_input.pci.analog-stereo"}
        self.nodes = {}
        self.on = True
        self.f = A.AudioFollow(info=lambda kind, i: self.nodes.get((kind, i)),
                               get_default=lambda kind: self.defaults[kind],
                               set_default=lambda kind, name: self.defaults.__setitem__(kind, name),
                               enabled=lambda: self.on)

    def plug(self, kind, i, name, **props):
        self.nodes[(kind, i)] = (name, props)
        self.f.event(f"Event 'new' on {kind} #{i}")

    def test_bluetooth_headset_output_and_mic(self):
        self.plug("sink", 80, "bluez_output.AA_BB.1", **{"device.api": "bluez5"})
        self.plug("source", 81, "bluez_input.AA_BB.0", **{"device.api": "bluez5"})
        self.assertEqual(self.defaults, {"sink": "bluez_output.AA_BB.1", "source": "bluez_input.AA_BB.0"})
        self.f.event("Event 'remove' on sink #80")
        self.f.event("Event 'remove' on source #81")
        self.assertEqual(self.defaults, {"sink": "alsa_output.pci.analog-stereo",
                                         "source": "alsa_input.pci.analog-stereo"})

    def test_usb_headset_and_what_is_left_alone(self):
        self.plug("sink", 90, "alsa_output.usb-Logi_H390.analog-stereo", **{"device.bus": "usb"})
        self.assertEqual(self.defaults["sink"], "alsa_output.usb-Logi_H390.analog-stereo")
        self.plug("source", 91, "alsa_output.usb-Logi_H390.analog-stereo.monitor", **{"device.bus": "usb"})
        self.plug("sink", 92, "alsa_output.pci-0000_01_00.1.hdmi-stereo", **{"device.bus": "pci"})
        self.plug("sink", 93, "sonata-eq-sink", **{"device.bus": "usb"})
        self.assertEqual(self.defaults["sink"], "alsa_output.usb-Logi_H390.analog-stereo")
        self.assertEqual(self.defaults["source"], "alsa_input.pci.analog-stereo")
        self.f.event("Event 'change' on sink #90")                 # other events: nothing
        self.f.event("Event 'remove' on sink #92")                 # not ours: nothing
        self.assertEqual(self.defaults["sink"], "alsa_output.usb-Logi_H390.analog-stereo")

    def test_two_in_a_row_go_back_one_at_a_time(self):
        self.plug("sink", 1, "bluez_output.A", **{"device.api": "bluez5"})
        self.plug("sink", 2, "alsa_output.usb-X", **{"device.bus": "usb"})
        self.f.event("Event 'remove' on sink #2")
        self.assertEqual(self.defaults["sink"], "bluez_output.A")           # the headphones still there
        self.f.event("Event 'remove' on sink #1")
        self.assertEqual(self.defaults["sink"], "alsa_output.pci.analog-stereo")

    def test_first_one_removed_first(self):
        self.plug("sink", 1, "bluez_output.A", **{"device.api": "bluez5"})
        self.plug("sink", 2, "alsa_output.usb-X", **{"device.bus": "usb"})
        self.f.event("Event 'remove' on sink #1")                          # not in use: nothing changes
        self.assertEqual(self.defaults["sink"], "alsa_output.usb-X")
        self.f.event("Event 'remove' on sink #2")
        self.assertEqual(self.defaults["sink"], "alsa_output.pci.analog-stereo")

    def test_your_own_choice_is_kept(self):
        self.plug("sink", 1, "bluez_output.A", **{"device.api": "bluez5"})
        self.defaults["sink"] = "alsa_output.pci.hdmi"                     # chosen in Sound meanwhile
        self.f.event("Event 'remove' on sink #1")
        self.assertEqual(self.defaults["sink"], "alsa_output.pci.hdmi")

    def test_off_in_settings(self):
        self.on = False
        self.plug("sink", 80, "bluez_output.AA", **{"device.api": "bluez5"})
        self.assertEqual(self.defaults["sink"], "alsa_output.pci.analog-stereo")

    def test_wired(self):
        import inspect
        from sonata2 import sounds
        from sonata2.settings import app
        from sonata2.shell import topbar
        self.assertTrue(sounds.DEFAULTS["follow_new_devices"])
        self.assertIn('_shared("audiofollow", audiofollow.start)', inspect.getsource(topbar))
        self.assertIn('"follow_new_devices"', inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
