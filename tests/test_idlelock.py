"""swayidle command: lock and keyboard backlight (python3 -m unittest tests.test_idlelock)."""
import unittest
from unittest import mock

from sonata2.shell import idlelock as I


class KeyboardLightTest(unittest.TestCase):
    """Vini: the laptop's keyboard stayed unlit. The idle timeout and the
    lock screen both turned it off; the second "off" saved the dark level
    over the real one, and a restart while dark lost the saved level."""

    def setUp(self):
        import os
        import tempfile
        self.d = tempfile.mkdtemp()
        self.level = os.path.join(self.d, "level")
        with open(self.level, "w") as f:
            f.write("3")
        fake = os.path.join(self.d, "brightnessctl")
        with open(fake, "w") as f:      # g: the level; set N: the new level
            f.write('#!/bin/sh\nfor a; do last2="$last"; last="$a"; done\n'
                    f'if [ "$last" = g ]; then cat {self.level}; '
                    f'elif [ "$last2" = set ]; then echo "$last" > {self.level}; fi\n')
        os.chmod(fake, 0o755)
        self.env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"], XDG_CACHE_HOME=self.d)

    def run_cmd(self, which):
        import importlib
        import subprocess
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": self.d}):
            mod = importlib.reload(I)
        try:
            subprocess.run(["sh", "-c", getattr(mod, which)], env=self.env, check=False)
        finally:
            importlib.reload(I)

    def now(self):
        return open(self.level).read().strip()

    def test_two_offs_then_on_gives_the_light_back(self):
        self.run_cmd("KBD_OFF")
        self.assertEqual(self.now(), "0")
        self.run_cmd("KBD_OFF")                             # the lock screen too
        self.run_cmd("KBD_ON")
        self.assertEqual(self.now(), "3")

    def test_restart_while_dark_restores_at_the_next_session(self):
        self.run_cmd("KBD_OFF")
        with open(self.level, "w") as f:                  # the restart: still 0
            f.write("0")
        self.run_cmd("KBD_ON")                              # the next session's start (IdleLock)
        self.assertEqual(self.now(), "3")
        self.run_cmd("KBD_ON")                              # nothing saved any more: left as it is
        self.assertEqual(self.now(), "3")

    def test_on_never_lights_a_keyboard_you_turned_off(self):
        with open(self.level, "w") as f:
            f.write("0")
        self.run_cmd("KBD_OFF")
        self.run_cmd("KBD_ON")
        self.assertEqual(self.now(), "0")

    def test_session_start_gives_lights_back(self):
        import inspect
        src = inspect.getsource(I.IdleLock.__init__)
        self.assertIn("lockdisplay.lights(True)", src)


class RgbLightTest(unittest.TestCase):
    """Review: RGB_OFF wrote its mark into ~/.cache/sonata2 without making
    the folder (a fresh profile never restored the lights); and the full
    swayidle form (sh -c '<quoted>' &) must run as written."""

    def test_off_off_on_with_no_cache_folder(self):
        import importlib
        import os
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        cache = os.path.join(d, "new cache")                  # (doesn't exist yet; a space in it)
        log = os.path.join(d, "log")
        fake = os.path.join(d, "openrgb")
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\necho "$*" >> "{log}"\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": cache}):
            mod = importlib.reload(I)
            off, on = mod.RGB_OFF, mod.RGB_ON
            cmd = mod.command({"lock_after": -1}, 300, kbd=False, rgb=True)
        importlib.reload(I)
        for c in (off, off, on):
            subprocess.run(["sh", "-c", c], env=env, check=False)
        lines = open(log).read().split("\n")
        self.assertEqual(sum("--save-profile" in ln for ln in lines), 1)    # saved once
        self.assertEqual(sum(ln.startswith("--profile") for ln in lines), 1)  # restored
        self.assertFalse(os.path.exists(os.path.join(cache, "sonata2", "rgb-before-dark")))
        for c in (cmd[4], cmd[6]):                         # swayidle's own form
            self.assertEqual(subprocess.run(["sh", "-n", "-c", c]).returncode, 0, c)


class LightsOrderTest(unittest.TestCase):
    """Vini: after the lock, the keyboard went dark and never came back.
    OpenRGB takes seconds to save the colours; waking meanwhile ran "back on"
    before "off" had made its mark, then "off" finished. Each command waits
    for the one before it now."""

    def test_on_waits_for_a_slow_off(self):
        import importlib
        import os
        import shutil
        import subprocess
        import tempfile
        import time
        if not shutil.which("flock"):
            self.skipTest("no flock here")
        d = tempfile.mkdtemp()
        log = os.path.join(d, "log")
        fake = os.path.join(d, "openrgb")
        with open(fake, "w") as f:      # saving takes a while, like the real one
            f.write(f'#!/bin/sh\ncase "$*" in *save-profile*) sleep 1;; esac\necho "$*" >> "{log}"\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": os.path.join(d, "cache")}):
            mod = importlib.reload(I)
            off, on = mod.serial(mod.RGB_OFF), mod.serial(mod.RGB_ON)
        importlib.reload(I)
        p1 = subprocess.Popen(["sh", "-c", off], env=env)
        time.sleep(0.2)                                        # woken while it still saves
        p2 = subprocess.Popen(["sh", "-c", on], env=env)
        p1.wait(10)
        p2.wait(10)
        lines = [ln for ln in open(log).read().split("\n") if ln]
        self.assertEqual(lines[-1], f"--profile {I.RGB_PROFILE}")   # the colours back, last
        self.assertFalse(os.path.exists(os.path.join(d, "cache", "sonata2", "rgb-before-dark")))


class CommandTest(unittest.TestCase):
    def test_keyboard_light_follows_the_display(self):
        cmd = I.command({"lock_after": -1}, 300, kbd=True)
        self.assertEqual(cmd, ["swayidle", "-w", "timeout", "300", I.serial(I.KBD_OFF), "resume",
                               I.serial(I.KBD_ON)])
        self.assertIn("set 0", I.KBD_OFF)                 # level saved before turning off
        self.assertIn("$(cat ", I.KBD_ON)                 # and restored on resume (KeyboardLightTest)

    def test_no_keyboard_light(self):
        self.assertEqual(I.command({"lock_after": -1}, 300, kbd=False), [])
        self.assertEqual(I.command({"lock_after": -1}, 0, kbd=True), [])     # display never sleeps

    def test_lock_kept(self):
        cmd = I.command({"lock_after": 5, "lock_before_sleep": True}, 300, kbd=True)
        self.assertEqual(cmd[-5:], ["timeout", "305", I.LOCK, "before-sleep", I.LOCK])
        self.assertIn(I.serial(I.KBD_OFF), cmd)

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
