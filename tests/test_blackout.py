"""Vini: after the idle time the screen only dimmed and the pointer stayed on
it. Now it fades to black on every display, the pointer hidden, and any key
or move fades it out (IdleLock's dark())."""
import inspect
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import blackout as B  # noqa: E402


def spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class BlackoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def test_fades_in_and_out_without_a_pointer(self):
        B.show()
        self.assertTrue(B._WINDOWS)
        self.assertFalse(B.showing())                       # a transparent frame first: the fade shows
        spin(150)
        self.assertTrue(B.showing())
        for w in B._WINDOWS:
            self.assertEqual(w.get_cursor().get_name(), "none")
            self.assertTrue(w.black.has_css_class("lk-blackout"))
        B.show()                                            # again: nothing new
        n = len(B._WINDOWS)
        B.hide()
        self.assertFalse(B.showing())
        self.assertEqual(len(B._WINDOWS), n)                # still there while it fades out
        spin(B.FADE_OUT_MS + 150)
        self.assertEqual(B._WINDOWS, [])

    def test_shown_again_during_its_fade_out(self):
        B.show()
        spin(150)
        B.hide()
        spin(100)
        B.show()                                            # input stopped again right away
        spin(B.FADE_OUT_MS + 200)
        self.assertTrue(B.showing())                        # the old fade-out took nothing away
        B.hide()
        spin(B.FADE_OUT_MS + 150)
        self.assertEqual(B._WINDOWS, [])

    def test_idle_dark_uses_it(self):
        from sonata2.shell import idlelock
        src = inspect.getsource(idlelock.IdleLock._policy)
        self.assertIn("blackout.show() if on else blackout.hide()", src)


if __name__ == "__main__":
    unittest.main()
