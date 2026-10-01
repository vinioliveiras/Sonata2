"""swayidle command: lock and keyboard backlight (python3 -m unittest tests.test_idlelock)."""
import unittest
from unittest import mock

from sonata2.shell import idlelock as I


class CommandTest(unittest.TestCase):
    def test_keyboard_light_follows_the_display(self):
        cmd = I.command({"lock_after": -1}, 300, kbd=True)
        self.assertEqual(cmd, ["swayidle", "-w", "timeout", "300", I.KBD_OFF, "resume", I.KBD_ON])
        self.assertIn("-s set 0", I.KBD_OFF)              # level saved before turning off
        self.assertIn(" -r", I.KBD_ON)                    # and restored on resume

    def test_no_keyboard_light(self):
        self.assertEqual(I.command({"lock_after": -1}, 300, kbd=False), [])
        self.assertEqual(I.command({"lock_after": -1}, 0, kbd=True), [])     # display never sleeps

    def test_lock_kept(self):
        cmd = I.command({"lock_after": 5, "lock_before_sleep": True}, 300, kbd=True)
        self.assertEqual(cmd[-5:], ["timeout", "305", I.LOCK, "before-sleep", I.LOCK])
        self.assertIn(I.KBD_OFF, cmd)

    def test_rgb_devices_go_dark(self):
        """A USB HyperX keyboard stayed lit: OpenRGB turns every RGB device off and back."""
        cmd = I.command({"lock_after": -1}, 300, kbd=False, rgb=True)
        self.assertEqual(cmd[2:4], ["timeout", "300"])
        self.assertIn("--save-profile " + I.RGB_PROFILE, cmd[4])
        self.assertIn("--mode off", cmd[4])
        self.assertEqual(cmd[5], "resume")
        self.assertIn("--profile " + I.RGB_PROFILE, cmd[6])
        self.assertEqual(I.command({"lock_after": -1}, 0, rgb=True), [])

    def test_detection(self):
        with mock.patch("glob.glob", return_value=["/sys/class/leds/asus::kbd_backlight"]), \
                mock.patch("shutil.which", return_value="/usr/bin/brightnessctl"):
            self.assertTrue(I.keyboard_light())
        with mock.patch("glob.glob", return_value=[]):
            self.assertFalse(I.keyboard_light())


if __name__ == "__main__":
    unittest.main()
