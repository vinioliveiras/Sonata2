"""Reopen the apps after Restart / Shut Down / Log Out (Vini; macOS' "Reopen
windows when logging back in"): the confirmation has the box, ticked as
last time; ticked, the apps open now are kept before they quit and the next
session's Dock opens them once -- not the ones already open (Open at
Login). Unticked, an app stopping it, or Sleep: nothing kept.
Run: xvfb-run -a python3 -m unittest tests.test_reopen_at_login"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config, open_apps as O, ui  # noqa: E402
from sonata2.shell import quitapps as Q  # noqa: E402

KNOWN = {"firefox", "spotify", "discord", "org.gnome.Calculator"}


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def fake_lookup(i):
    i = O.bare(i)
    if i not in KNOWN:
        return None
    m = mock.Mock()
    m.get_display_name.return_value = i.capitalize()
    m.get_id.return_value = i + ".desktop"
    return m


class Top:
    def __init__(self, app_id):
        self.app_id = app_id


class ReopenTest(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch("sonata2.apps.lookup", side_effect=fake_lookup),
                  mock.patch("sonata2.apps.match_app_id", side_effect=lambda a: O.bare(a) if O.bare(a) in KNOWN else None),
                  mock.patch.object(Q, "Quitter"),
                  mock.patch.object(O, "_autostarted", return_value=set())]
        for p in self.p:
            p.start()
        config.save(O.CONFIG, {})
        for n in (O.FILE, O.REOPEN):
            try:
                os.remove(os.path.join(O._dir(), n))
            except OSError:
                pass

    def tearDown(self):
        for p in self.p:
            p.stop()

    def manager(self, *ids):
        return mock.Mock(toplevels=[Top(i) for i in ids])

    def test_ticked_by_default_and_remembered(self):
        self.assertTrue(O.reopen_wanted())
        O.set_reopen_wanted(False)
        self.assertFalse(O.reopen_wanted())

    def test_restart_keeps_the_open_apps_for_next_login(self):
        O._write(O.FILE, ["firefox"])                          # the Dock's note
        Q.end_session("restart", self.manager("firefox", "spotify", "sonata2-dock"))
        self.assertEqual(O.for_login(), ["firefox", "spotify"])

    def test_unticked_or_sleep_keeps_nothing(self):
        O._write(O.REOPEN, ["firefox"])                         # from an earlier try
        Q.end_session("shutdown", self.manager("spotify"), reopen=False)
        self.assertEqual(O.for_login(), [])
        Q.end_session("sleep", self.manager("spotify"))
        self.assertEqual(O.for_login(), [])

    def test_follows_the_boxes_last_choice(self):
        O.set_reopen_wanted(False)
        Q.end_session("logout", self.manager("firefox"))
        self.assertEqual(O.for_login(), [])
        O.set_reopen_wanted(True)
        Q.end_session("logout", self.manager("firefox"))
        self.assertEqual(O.for_login(), ["firefox"])

    def test_an_app_stopping_it_drops_the_list(self):
        Q.end_session("restart", self.manager("firefox"))
        on_cancel = Q.Quitter.call_args.args[3]
        with mock.patch.object(Q, "_cancelled"):
            on_cancel("restart", Top("firefox"))
        self.assertEqual(O.for_login(), [])

    def test_next_login_opens_them_once_but_not_open_ones(self):
        O.save_for_login(["firefox", "spotify", "discord"])
        started = []
        with mock.patch.object(O, "_autostarted", return_value={"discord.desktop"}):
            n = O.reopen_after_login(running={"spotify"}, launch=lambda i: started.append(i.get_id()))
        self.assertEqual(n, 1)
        settle(300)
        self.assertEqual(started, ["firefox.desktop"])
        self.assertEqual(O.reopen_after_login(launch=lambda i: started.append(i)), 0)   # used up
        self.assertEqual(len(started), 1)


class ConfirmBoxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_confirmation_has_the_box_ticked_as_last_time(self):
        from sonata2.shell.topbar import Bar as TopBar
        fake = mock.Mock()
        config.save(O.CONFIG, {"reopen": False})
        with mock.patch.object(ui.dialog, "alert") as alert:
            TopBar._confirm(fake, "restart")
        kw = alert.call_args.kwargs
        self.assertEqual(kw["check"], "Reopen windows when logging back in")
        self.assertFalse(kw["checked"])
        answered = alert.call_args.args[3]
        answered("restart", True)                              # ticked now
        fake._end_session.assert_called_once_with("restart", True)
        self.assertTrue(O.reopen_wanted())
        answered("cancel", False)                              # Cancel keeps the box's choice too
        self.assertFalse(O.reopen_wanted())
        self.assertEqual(fake._end_session.call_count, 1)

    def test_alert_box_can_start_ticked(self):
        got = []
        dlg = ui.dialog.alert("T", "b", [("cancel", "Cancel", ""), ("go", "Go", "default")],
                              lambda r, c: got.append((r, c)), check="Box", checked=True)
        settle(300)
        root = dlg.get_root() or dlg
        stack, box = [root], None
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.CheckButton):
                box = w
                break
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertTrue(box.get_active())
        ui.dialog._close(dlg)
        settle(500)
        self.assertEqual(got, [("cancel", True)])


class DockTest(unittest.TestCase):
    def test_dock_reopens_once_a_session(self):
        from sonata2.shell import dock as D
        src = open(D.__file__).read()
        init = src[src.index("class Dock(Gtk.Box):"):src.index("self.tiles = {}")]
        self.assertIn('if not _REOPEN["done"]', init)
        self.assertIn("self._reopen_last_session", init)

    def test_dock_skips_what_runs_and_bounces_its_tiles(self):
        from sonata2.shell import dock as D
        tile = object()
        fake = mock.Mock(windows={"spotify": []}, _starting={}, manager=None, tiles={"firefox": tile})
        with mock.patch.object(O, "reopen_after_login", return_value=1) as r:
            D.Dock._reopen_last_session(fake)
        running, launch = r.call_args.args
        self.assertEqual(running, {"spotify"})
        launch(mock.Mock(get_id=lambda: "firefox.desktop"))
        fake.launch.assert_called_once_with(tile)


if __name__ == "__main__":
    unittest.main()
