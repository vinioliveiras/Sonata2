"""Vini: locked, the screen stayed on. While locked the display turns off
after LOCKED_DPMS_S (Wayfire's idle, a full-screen app no longer keeping it
on; wlopm forcing it off past apps that hold it awake), and everything is
put back on unlock."""
import inspect
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
        LD.locked()
        self.assertIn(f"dpms_timeout = {LD.LOCKED_DPMS_S}", self.ini_text())
        self.assertIn("disable_on_fullscreen = false", self.ini_text())
        self.assertEqual(LD.saved()["idle/dpms_timeout"], "600")
        LD.locked()                                           # locked twice: the first values kept
        self.assertEqual(LD.saved()["idle/dpms_timeout"], "600")
        LD.unlocked()
        self.assertIn("dpms_timeout = 600", self.ini_text())
        self.assertNotIn("disable_on_fullscreen", self.ini_text())
        self.assertEqual(LD.saved(), {})

    def test_a_shorter_timeout_stays(self):
        with open(self.ini, "w") as f:
            f.write("[idle]\ndpms_timeout = 15\n")
        LD.locked()
        self.assertIn("dpms_timeout = 15", self.ini_text())
        LD.unlocked()

    def test_wired(self):
        from sonata2.shell import idlelock, lock
        src = inspect.getsource(lock.LockScreen)
        self.assertIn("self._dark(True)", src)
        self.assertIn("self._dark(False)", src)
        self.assertIn("lockdisplay.displays(False)", src)
        self.assertIn("lockdisplay.unlocked()", inspect.getsource(idlelock.IdleLock.__init__))

    def test_displays_without_wlopm(self):
        with mock.patch.object(LD.shutil, "which", return_value=None):
            self.assertFalse(LD.displays(False))


if __name__ == "__main__":
    unittest.main()
