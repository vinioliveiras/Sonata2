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
        # the laptop's keyboard, a USB one, and a Caps Lock light (not a backlight)
        self.devs = {"asus::kbd_backlight": "3", "usb-0003::kbd_backlight": "2", "input3::capslock": "1"}
        for name, v in self.devs.items():
            self.set(name, v)
        fake = os.path.join(self.d, "brightnessctl")
        with open(fake, "w") as f:      # -l -m: the list; -d NAME g: its level; -d NAME set N
            f.write('#!/bin/sh\ndev=""; prev=""; last=""; list=""\n'
                    'for a; do [ "$prev" = -d ] && dev="$a"; [ "$a" = -l ] && list=1; prev2="$prev"; prev="$a"; '
                    'last2="$last"; last="$a"; done\n'
                    f'if [ -n "$list" ]; then for f in {self.d}/lvl-*; do n=${{f##*/lvl-}}; '
                    'echo "$n,leds,$(cat "$f"),100%,3"; done; exit 0; fi\n'
                    f'case "$dev" in "*::kbd_backlight") dev=asus::kbd_backlight;; esac\n'
                    f'if [ "$last" = g ]; then cat "{self.d}/lvl-$dev"; '
                    f'elif [ "$last2" = set ]; then echo "$last" > "{self.d}/lvl-$dev"; fi\n')
        os.chmod(fake, 0o755)
        self.env = dict(os.environ, PATH=self.d + ":" + os.environ["PATH"], XDG_CACHE_HOME=self.d)
        self.level = os.path.join(self.d, "lvl-asus::kbd_backlight")

    def set(self, name, v):
        import os
        with open(os.path.join(self.d, "lvl-" + name), "w") as f:
            f.write(v)

    def get(self, name):
        import os
        return open(os.path.join(self.d, "lvl-" + name)).read().strip()

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

    def test_every_keyboard_goes_dark_and_comes_back(self):
        """Vini: USB keyboards stayed lit, only the laptop's went dark."""
        self.run_cmd("KBD_OFF")
        self.assertEqual((self.get("asus::kbd_backlight"), self.get("usb-0003::kbd_backlight")), ("0", "0"))
        self.assertEqual(self.get("input3::capslock"), "1")          # not a backlight: left alone
        self.run_cmd("KBD_ON")
        self.assertEqual((self.get("asus::kbd_backlight"), self.get("usb-0003::kbd_backlight")), ("3", "2"))

    def test_level_saved_by_an_older_sonata_comes_back(self):
        import os
        os.makedirs(os.path.join(self.d, "sonata2"), exist_ok=True)
        with open(os.path.join(self.d, "sonata2", "lights-before-dark"), "w") as f:
            f.write("3")
        self.set("asus::kbd_backlight", "0")
        self.run_cmd("KBD_ON")
        self.assertEqual(self.get("asus::kbd_backlight"), "3")

    def test_session_start_gives_lights_back(self):
        import inspect
        src = inspect.getsource(I.IdleLock.__init__)
        self.assertIn("lockdisplay.lights(True)", src)


class NoRgbOptionTest(unittest.TestCase):
    """Vini: the "Turn RGB lights off with the screen" option removed."""
    def test_gone(self):
        import importlib.util
        import pathlib
        self.assertIsNone(importlib.util.find_spec("sonata2.rgblights"))
        self.assertNotIn("rgb_dark", I.DEFAULTS)
        root = pathlib.Path(__file__).resolve().parent.parent
        self.assertNotIn("RGB lights", (root / "sonata2" / "settings" / "app.py").read_text())
        self.assertIn(" openrgb ", (root / "install.sh").read_text())      # the OpenRGB app itself stays (Vini)


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
