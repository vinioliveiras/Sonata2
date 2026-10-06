"""Vini: locked, the screen stayed on. While locked the display turns off
after LOCKED_DPMS_S (Wayfire's idle, a full-screen app no longer keeping it
on; wlopm forcing it off past apps that hold it awake), and everything is
put back on unlock."""
import inspect
import types
import os
import tempfile
import unittest
from unittest import mock

from sonata2.shell import lockdisplay as LD


class LockDisplayTest(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        self.ini = os.path.join(self.run, "sonata2-wayfire.ini")
        with open(self.ini, "w") as f:
            f.write("[idle]\ndpms_timeout = 600\nscreensaver_timeout = -1\n")
        cfg = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run, "XDG_CONFIG_HOME": cfg})
        p.start()
        self.addCleanup(p.stop)

    def ini_text(self):
        with open(self.ini) as f:
            return f.read()

    def test_locked_then_unlocked(self):
        """Locked, Wayfire never powers the display off (the lock screen
        darkens it itself: powering it on again failed, Vini had to close the
        lid); your timeout comes back on unlock."""
        LD.locked()
        self.assertIn("dpms_timeout = -1", self.ini_text())
        self.assertEqual(LD.saved()["idle/dpms_timeout"], "600")
        self.assertEqual(LD.dark_seconds(), LD.LOCKED_DPMS_S)
        LD.locked()                                           # locked twice: the first values kept
        self.assertEqual(LD.saved()["idle/dpms_timeout"], "600")
        LD.unlocked()
        self.assertIn("dpms_timeout = 600", self.ini_text())
        self.assertEqual(LD.saved(), {})

    def test_a_shorter_timeout_stays(self):
        with open(self.ini, "w") as f:
            f.write("[idle]\ndpms_timeout = 15\n")
        LD.locked()
        self.assertEqual(LD.dark_seconds(), 15)                 # darker sooner, as you chose
        LD.unlocked()
        self.assertIn("dpms_timeout = 15", self.ini_text())

    def test_wired(self):
        from sonata2.shell import idlelock, lock
        src = inspect.getsource(lock.LockScreen)
        self.assertIn("self._dark(True)", src)
        self.assertIn("self._dark(False)", src)
        self.assertNotIn("displays(False)", src)              # never powered off while locked
        self.assertIn("lockdisplay.dim(True)", src)
        self.assertIn("lockdisplay.unlocked()", inspect.getsource(idlelock.IdleLock.__init__))

    def test_keyboard_goes_dark_with_the_lock(self):
        from sonata2.shell import idlelock
        ran = []
        with mock.patch.object(idlelock, "keyboard_light", return_value=True), \
                mock.patch.object(idlelock, "rgb_lights", return_value=False), \
                mock.patch.object(LD.subprocess, "Popen", lambda argv, **k: ran.append(argv[-1])):
            LD.lights(False)
            LD.lights(True)
        self.assertEqual(ran, [idlelock.serial(idlelock.KBD_OFF), idlelock.serial(idlelock.KBD_ON)])
        from sonata2.shell import lock
        self.assertIn("lockdisplay.lights(False)", inspect.getsource(lock.LockScreen._go_dark))

    def test_never_stays_never_when_locked(self):
        """Vini: the display set to never turn off went dark behind the lock."""
        with open(self.ini, "w") as f:
            f.write("[idle]\ndpms_timeout = -1\n")
        self.assertIsNone(LD.dark_seconds())
        LD.locked()
        self.assertIsNone(LD.dark_seconds())                 # (from what was saved at the lock)
        LD.unlocked()
        self.assertIn("dpms_timeout = -1", self.ini_text())

    def test_displays_without_wlopm(self):
        with mock.patch.object(LD.shutil, "which", return_value=None):
            self.assertFalse(LD.displays(False))

    def test_backlight_saved_once_and_back(self):
        import importlib
        import subprocess
        from sonata2.shell import idlelock as I
        d = tempfile.mkdtemp()
        level = os.path.join(d, "level")
        with open(level, "w") as f:
            f.write("120")
        fake = os.path.join(d, "brightnessctl")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nfor a; do l2="$l"; l="$a"; done\n'
                    f'if [ "$l" = g ]; then cat {level}; elif [ "$l2" = set ]; then echo "$l" > {level}; fi\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": d}):
            mod = importlib.reload(I)
            off, on = mod.BL_OFF, mod.BL_ON
        importlib.reload(I)
        for c in (off, off, on):
            subprocess.run(["sh", "-c", c], env=env)
        self.assertEqual(open(level).read().strip(), "120")


class LockWakeTest(unittest.TestCase):
    """Vini: the locked display went black and came back only by closing and
    opening the lid. Dark and back now follow the compositor's own input idle
    (any key or move), not only what the lock's windows see."""

    def lock(self, dark_after):
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        ls._idle_src, ls._off = 0, False
        return L, ls

    def fake_watch(self):
        class W:
            ok = input_idle = True
            def __init__(s):
                s.calls, s.watched, s.closed = [], None, False
            def watch(s, secs, idle, back):
                s.watched = (secs, idle, back)
                return 1
            def displays(s, on):
                s.calls.append(on)
                return True
            def close(s):
                s.closed = True
        return W()

    def test_input_idle_darkens_and_any_input_wakes(self):
        """Dark is black + backlight at zero, never the display powered off;
        any input anywhere brings it back."""
        L, ls = self.lock(30)
        w = self.fake_watch()
        black = types.SimpleNamespace(on=False)
        black.add_css_class = lambda c: setattr(black, "on", True)
        black.remove_css_class = lambda c: setattr(black, "on", False)
        ls.blackouts = [black]
        lights, dims = [], []
        with mock.patch.object(LD, "locked"), mock.patch.object(LD, "unlocked"), \
                mock.patch.object(LD, "dark_seconds", return_value=30), \
                mock.patch("sonata2.wl.idlewatch.IdleWatch", lambda: w), \
                mock.patch.object(LD, "lights", lights.append), mock.patch.object(LD, "dim", dims.append), \
                mock.patch.object(LD, "displays") as power:
            ls._dark(True)
            secs, idle, back = w.watched
            self.assertEqual(secs, 30)
            self.assertEqual(ls._idle_src, 0)                    # no GTK timer: the compositor counts
            idle()
            self.assertEqual((black.on, dims, lights), (True, [True], [False]))
            back()                                               # a key or a move, anywhere
            self.assertEqual((black.on, dims, lights), (False, [True, False], [False, True]))
            idle()
            ls._dark(False)                                      # unlocked while dark: lit again
            self.assertEqual((black.on, dims[-1]), (False, False))
            self.assertTrue(w.closed)
        power.assert_not_called()
        self.assertEqual(w.calls, [])                            # no output power changes at all

    def test_never_means_no_dark_at_all(self):
        L, ls = self.lock(None)
        with mock.patch.object(LD, "locked"), mock.patch.object(LD, "dark_seconds", return_value=None), \
                mock.patch("sonata2.wl.idlewatch.IdleWatch", side_effect=AssertionError("no watch")), \
                mock.patch.object(L.GLib, "timeout_add_seconds", side_effect=AssertionError("no timer")):
            ls._dark(True)
            ls._input()
        self.assertFalse(ls._off)


if __name__ == "__main__":
    unittest.main()
