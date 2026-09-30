"""Waits after wrong passwords (sonata2/throttle.py) and the field that
counts them down (loginui.WaitGuard): login screen and lock screen.
Run: xvfb-run -a python3 -m unittest tests.test_throttle"""
import os
import tempfile
import unittest

os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2 import throttle  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ThrottleTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sub", "waits.json")
        self.clock = Clock()
        self.t = throttle.Throttle(self.path, self.clock)

    def test_free_tries_then_growing_waits(self):
        for _ in range(throttle.FREE_TRIES):
            self.assertEqual(self.t.failed("vini"), 0)
            self.assertEqual(self.t.wait_left("vini"), 0)
        self.assertEqual(self.t.failed("vini"), 60)
        self.assertEqual(self.t.wait_left("vini"), 60)
        self.clock.t += 30
        self.assertEqual(self.t.wait_left("vini"), 30)
        self.clock.t += 30
        self.assertEqual(self.t.wait_left("vini"), 0)
        self.assertEqual(self.t.failed("vini"), 300)
        self.assertEqual(self.t.failed("vini"), 900)
        self.assertEqual(self.t.failed("vini"), 3600)
        self.assertEqual(self.t.failed("vini"), 3600)        # the last step repeats

    def test_per_user_and_reset_on_success(self):
        for _ in range(throttle.FREE_TRIES + 1):
            self.t.failed("a")
        self.assertGreater(self.t.wait_left("a"), 0)
        self.assertEqual(self.t.wait_left("b"), 0)
        self.t.succeeded("a")
        self.assertEqual(self.t.wait_left("a"), 0)
        self.assertEqual(self.t.failed("a"), 0)             # the count starts over

    def test_survives_a_restart(self):
        for _ in range(throttle.FREE_TRIES + 1):
            self.t.failed("vini")
        again = throttle.Throttle(self.path, self.clock)
        self.assertEqual(again.wait_left("vini"), 60)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")

    def test_unwritable_place_does_not_crash(self):
        t = throttle.Throttle("/proc/nope/waits.json", self.clock)
        self.assertEqual(t.failed("x"), 0)

    def test_describe(self):
        self.assertEqual(throttle.describe(299), "Try again in 4:59")
        self.assertEqual(throttle.describe(1), "Try again in 1 second")


class GuardTest(unittest.TestCase):
    def test_field_waits_and_comes_back(self):
        from sonata2.shell.loginui import WaitGuard
        Gtk.init()
        clock = Clock()
        t = throttle.Throttle(os.path.join(tempfile.mkdtemp(), "w.json"), clock)
        win = Gtk.Window()
        box = Gtk.Box()
        entry, hint = Gtk.PasswordEntry(), Gtk.Label()
        box.append(entry)
        box.append(hint)
        win.set_child(box)
        win.present()
        g = WaitGuard(entry, hint, t)
        for _ in range(throttle.FREE_TRIES):
            g.failed("u")
        self.assertTrue(entry.get_sensitive())
        g.failed("u")
        self.assertFalse(entry.get_sensitive())
        self.assertEqual(hint.get_label(), "Try again in 1:00")
        self.assertTrue(g.blocked("u"))
        clock.t += 61
        g._tick()
        self.assertTrue(entry.get_sensitive())
        self.assertEqual(hint.get_label(), "")
        self.assertFalse(g.blocked("u"))
        win.destroy()


if __name__ == "__main__":
    unittest.main()
