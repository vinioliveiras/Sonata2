"""Vini: closing an app's last window quits it (Steam stayed in the
background) -- Settings > Desktop & Windows, for every app."""
import signal
import unittest
from unittest import mock

from sonata2.shell import quitonclose as Q


class T:
    def __init__(self, app_id):
        self.app_id = app_id


class Mgr:
    def __init__(self):
        self.toplevels, self.listeners = [], []

    def change(self, *ids):
        self.toplevels = [T(i) for i in ids]
        for cb in self.listeners:
            cb()


class QuitOnCloseTest(unittest.TestCase):
    def make(self, views, enabled=True, parents=lambda w: set()):
        self.killed, self.timers = [], []
        m = Mgr()
        with mock.patch.object(Q.QuitOnClose, "key_of", staticmethod(lambda a: a)):
            q = Q.QuitOnClose(m, views=lambda: views, kill=lambda p, s: self.killed.append((p, s)),
                              alive=lambda p: True, enabled=lambda: enabled, parents=parents,
                              later=lambda ms, fn: self.timers.append(fn))
        q.key_of = lambda a: a
        return m, q

    def run_timers(self):
        while self.timers:
            self.timers.pop(0)()

    def test_last_window_closed_quits_the_app(self):
        views = [{"type": "toplevel", "pid": 500, "app-id": "steam"}]
        m, q = self.make(views)
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}):
            m.change("steam")
        views.clear()
        m.change()
        self.run_timers()
        self.assertEqual(self.killed, [(500, signal.SIGTERM)])

    def test_a_window_back_within_the_grace_keeps_it(self):
        """Steam swaps its sign-in window for the main one."""
        views = [{"type": "toplevel", "pid": 500}]
        m, q = self.make(views)
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}):
            m.change("steam")
            m.change()
            m.change("steam")
        self.run_timers()
        self.assertEqual(self.killed, [])

    def test_turned_off_nothing_quits(self):
        m, q = self.make([], enabled=False)
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}):
            m.change("steam")
        m.change()
        self.run_timers()
        self.assertEqual(self.killed, [])

    def test_a_game_it_started_keeps_it_running(self):
        views = [{"type": "toplevel", "pid": 900, "app-id": "steam_app_1"}]          # the game's window
        m, q = self.make(views, parents=lambda w: {500, 1} if w == 900 else set())
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}):
            m.change("steam", "steam_app_1")
        m.change("steam_app_1")
        self.run_timers()
        self.assertEqual(self.killed, [])

    def test_sonata_itself_is_never_watched(self):
        m, q = self.make([])
        m.change("io.github.vinioliveiras.sonata2.files")
        self.assertEqual(q.open, set())

    def test_setting_and_default(self):
        from sonata2.shell import dock
        self.assertTrue(dock.DEFAULTS["quit_on_close"])                 # on by default (Vini)
        src = open(Q.__file__.replace("shell/quitonclose.py", "settings/app.py")).read()
        self.assertIn('"Quit apps when their last window closes"', src)
        main = open(Q.__file__.replace("shell/quitonclose.py", "__main__.py")).read()
        self.assertIn("QuitOnClose(manager)", main)


if __name__ == "__main__":
    unittest.main()
