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


class LockWaitTest(unittest.TestCase):
    """Vini: on waking, the password was asked 3 times -- swayidle -w waited
    for `sonata2 lock` until unlock, so the idle timeout / before-sleep lock
    requests that came meanwhile each locked again after it."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        p = mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": self.dir})
        p.start()
        self.addCleanup(p.stop)

    def test_swayidle_runs_the_returning_command(self):
        self.assertEqual(I.LOCK, "sonata2 lock-wait")
        self.assertIn(I.LOCK, I.command({"lock_after": 0, "lock_before_sleep": True}, 300))

    def test_marker_follows_the_lock(self):
        self.assertFalse(I.is_locked())
        I.mark_locked(True)
        self.assertTrue(I.is_locked())
        I.mark_locked(False)
        self.assertFalse(I.is_locked())
        I.mark_locked(False)                                   # twice: no error

    def test_stale_marker_from_a_crashed_lock(self):
        import os
        with open(I.marker(), "w") as f:
            f.write("999999999")
        self.assertFalse(I.is_locked())
        os.unlink(I.marker())

    def test_returns_once_locked(self):
        def started(*_a, **_k):
            I.mark_locked(True)                                # the lock screen engaged
        with mock.patch.object(I.subprocess, "Popen", side_effect=started) as pop:
            self.assertEqual(I.lock_and_wait(["sonata2", "lock"], timeout=2), 0)
        self.assertTrue(pop.call_args.kwargs["start_new_session"])   # not swayidle's child to wait on

    def test_already_locked_starts_nothing(self):
        I.mark_locked(True)
        with mock.patch.object(I.subprocess, "Popen") as pop:
            self.assertEqual(I.lock_and_wait(["sonata2", "lock"]), 0)
        pop.assert_not_called()

    def test_never_locks_returns_failure(self):
        with mock.patch.object(I.subprocess, "Popen"):
            self.assertEqual(I.lock_and_wait(["sonata2", "lock"], timeout=0.2), 1)


if __name__ == "__main__":
    unittest.main()


class LockByDefaultTest(unittest.TestCase):
    """Vini: the session never locked on its own -- automatic locking was off
    by default. Now on: when the display turns off, and before sleep."""

    def test_defaults(self):
        from sonata2.shell import idlelock as I
        self.assertEqual(I.DEFAULTS["lock_after"], 0)
        self.assertTrue(I.DEFAULTS["lock_before_sleep"])
        cmd = I.command(I.DEFAULTS, 600)
        self.assertIn("before-sleep", cmd)
        self.assertEqual(cmd[cmd.index(I.LOCK) - 1], "600")
