"""Vini: after adding a user, Settings stayed on Users & Groups whatever
section was picked (the new user not in it either): rebuilding the page in
place left a picture of it on top of everything. It fades away now.
xvfb-run -a python3 -m unittest tests.test_settings_reload_stuck"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.settings import app as S  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class ReloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_no_picture_left_on_top(self):
        win = S.Settings(None)
        win.set_default_size(900, 600)
        win.present()
        win.select("users")
        settle(500)
        win._reload_page("users")                     # what adding a user does when it's done
        settle(800)
        self.assertIsNone(win.fade._pic)               # the picture faded away
        win.select("gamecontrollers" if "gamecontrollers" in dict((s[0], s) for s in S.SECTIONS) else "sound")
        settle(300)
        self.assertIsNone(win.fade._pic)
        self.assertIs(win.content.get_visible_child(), win.pages[win.current])
        win.destroy()

    def test_switching_clears_a_stale_picture(self):
        win = S.Settings(None)
        win.set_default_size(900, 600)
        win.present()
        win.select("users")
        settle(500)
        win.fade.capture()                             # (a picture nobody played)
        win.select("sound")
        settle(800)
        self.assertIsNone(win.fade._pic)
        win.destroy()

    def test_switching_sections_stays_instant(self):
        """Vini: Settings' sections switch at once, no fade. A leftover
        picture was let go of by fading it out, which animated every
        section switch: it goes at once now."""
        win = S.Settings(None)
        win.set_default_size(900, 600)
        win.present()
        win.select("users")
        settle(500)
        win.fade.capture()
        win.select("sound")
        self.assertIsNone(win.fade._pic)               # gone already, not fading over the new page
        self.assertIsNone(win.fade._anim)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
