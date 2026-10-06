"""Vini: turned on and left at the login screen, the display never went
dark. The login screen now goes dark after the time chosen in Settings >
Battery (shared by the session, like the main display), the same way the
session does (idlelock.darken); any key or move brings it back."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import config, displaysleep  # noqa: E402
from sonata2.shell import idlelock as I  # noqa: E402
from sonata2.shell import monitors as M  # noqa: E402


class SharedTimeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.user = GLib.get_user_name()
        os.makedirs(os.path.join(self.root, self.user))
        for target, name, value in ((M, "GREETER", self.root), (config, "CONFIG_DIR", tempfile.mkdtemp())):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_the_session_shares_its_time(self):
        config.save(displaysleep.NAME, {"off_after": 300})
        M.share_with_login_screen()
        self.assertEqual(M.login_off_after(["nobody", self.user]), 300)
        self.assertEqual(M.login_off_after(["nobody"]), displaysleep.DEFAULT)     # nothing shared
        self.assertEqual(M.login_main([self.user]), config.load("displays", M.DEFAULTS)["main"] or "")

    def test_changing_it_in_settings_shares_it(self):
        with mock.patch.object(displaysleep.wfconfig, "wayfire_get", return_value="-1"):
            displaysleep.set_seconds(-1)                                        # never
        self.assertEqual(M.login_off_after([self.user]), -1)

    def test_a_bad_value_is_the_default(self):
        with open(os.path.join(self.root, self.user, "displays.json"), "w") as f:
            f.write('{"off_after": true}')
        self.assertEqual(M.login_off_after([self.user]), displaysleep.DEFAULT)


class DarkenTest(unittest.TestCase):
    def run_darken(self, on, laptop):
        watch = mock.Mock()
        with mock.patch("sonata2.shell.lockdisplay.lights") as lights, \
                mock.patch("sonata2.shell.lockdisplay.has_backlight", return_value=laptop), \
                mock.patch("sonata2.shell.lockdisplay.dim") as dim, \
                mock.patch("sonata2.shell.blackout.show") as show, mock.patch("sonata2.shell.blackout.hide") as hide:
            I.darken(watch, on)
        return watch, lights, dim, show, hide

    def test_laptop(self):
        watch, lights, dim, show, hide = self.run_darken(True, laptop=True)
        lights.assert_called_once_with(False)
        show.assert_called_once()
        dim.assert_called_once_with(True)                  # the backlight, never the panel's power
        only = watch.displays.call_args.kwargs["only"]     # plugged-in monitors really go off
        self.assertEqual((only("eDP-1"), only("HDMI-A-1")), (False, True))
        watch, lights, dim, show, hide = self.run_darken(False, laptop=True)
        hide.assert_called_once()
        dim.assert_called_once_with(False)

    def test_desktop(self):
        watch, _l, dim, _s, _h = self.run_darken(True, laptop=False)
        watch.displays.assert_called_once_with(False)
        dim.assert_not_called()


class GreeterSleepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def sleep(self, seconds, ok=True):
        from sonata2.shell import greeter as G
        g = G.Greeter.__new__(G.Greeter)
        g.fake = False
        w = mock.Mock(ok=ok)
        with mock.patch("sonata2.wl.idlewatch.IdleWatch", return_value=w):
            return g._sleep(seconds), w

    def test_goes_dark_and_back(self):
        got, w = self.sleep(300)
        self.assertIs(got, w)
        seconds, on_idle, on_back = w.watch.call_args[0]
        self.assertEqual(seconds, 300)
        with mock.patch.object(I, "darken") as darken:
            on_idle()
            on_back()
        self.assertEqual(darken.call_args_list, [mock.call(w, True), mock.call(w, False)])

    def test_never_or_no_compositor(self):
        got, w = self.sleep(-1)
        self.assertIsNone(got)
        w.watch.assert_not_called()
        got, w = self.sleep(300, ok=False)
        self.assertIsNone(got)
        w.close.assert_called_once()

    def test_the_greeter_starts_it_with_the_users_time(self):
        import inspect
        from sonata2.shell import greeter as G
        init = inspect.getsource(G.Greeter.__init__)
        self.assertIn("self._sleep(displays.login_off_after(names))", init)


if __name__ == "__main__":
    unittest.main()
