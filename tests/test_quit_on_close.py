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


class TreeTest(unittest.TestCase):
    def test_root_stops_at_what_launched_it(self):
        t = {10: (1, "systemd", "/usr/lib/systemd/systemd --user"), 20: (10, "chrome", "chrome"),
             21: (20, "chrome", "chrome --type=gpu")}
        self.assertEqual(Q.app_root(21, t), 20)
        self.assertEqual(Q.subtree(20, t), [20, 21])
        t[20] = (30, "chrome", "chrome")                      # started from a terminal's shell
        t[30] = (40, "fish", "fish")
        t[40] = (1, "python3", "python3 -m sonata2 terminal")
        self.assertEqual(Q.app_root(21, t), 20)                 # the shell (and terminal) stay


class QuitOnCloseTest(unittest.TestCase):
    # dock (sonata2) -> steam.sh -> steam -> steamwebhelper (the window) ; steam -> game
    TABLE = {100: (1, "python3", "python3 -m sonata2 dock"), 400: (100, "steam.sh", "bash steam.sh"),
             450: (400, "steam", "steam"), 500: (450, "steamwebhelper", "steamwebhelper"),
             900: (450, "game.exe", "game.exe")}

    def make(self, views, enabled=True, table=None, launched=lambda k: 0.0, started=lambda p: 0.0):
        self.killed, self.timers = [], []
        m = Mgr()
        with mock.patch.object(Q.QuitOnClose, "key_of", staticmethod(lambda a: a)):
            q = Q.QuitOnClose(m, views=lambda: views, kill=lambda p, s: self.killed.append((p, s)),
                              alive=lambda p: True, enabled=lambda: enabled,
                              table=lambda: dict(table if table is not None else {
                                  k: v for k, v in self.TABLE.items() if k != 900}),
                              launched=launched, started=started, now=lambda: 100.0,
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
        # the whole app, not just its window's process: Steam restarted its web
        # helper (the window's process) and the window came back (Vini)
        self.assertEqual(sorted(p for p, _s in self.killed), [400, 450, 500])
        self.assertNotIn(100, [p for p, _s in self.killed])                    # never Sonata

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

    def test_opened_again_right_after_is_kept(self):
        """Vini: apps didn't open -- closed and opened again, the pending quit
        ended the app coming back."""
        views = [{"type": "toplevel", "pid": 500}]
        m, q = self.make(views, launched=lambda k: 101.0)             # Sonata launched it after the close
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}), mock.patch.object(Q, "log"):
            m.change("steam")
            views.clear()
            m.change()
            self.run_timers()
        self.assertEqual(self.killed, [])
        m, q = self.make([{"type": "toplevel", "pid": 500}], started=lambda p: 150.0 if p == 450 else 0.0)
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}), mock.patch.object(Q, "log"):
            m.change("steam")
            m.change()
            self.run_timers()
        self.assertEqual(self.killed, [])                             # a process newer than the close

    def test_process_found_once_the_compositor_lists_it(self):
        """The Claude app stayed: its window wasn't listed yet when it appeared."""
        views = []
        m, q = self.make(views)
        with mock.patch.object(Q.quitapps, "app_pids", side_effect=lambda v, key: {500} if v else set()):
            m.change("steam")
            self.assertFalse(q.pids.get("steam"))
            views.append({"type": "toplevel", "pid": 500})
            m.change("steam")                                         # e.g. a title change
            self.assertEqual(q.pids["steam"], {500})

    def test_turned_off_nothing_quits(self):
        m, q = self.make([], enabled=False)
        with mock.patch.object(Q.quitapps, "app_pids", return_value={500}):
            m.change("steam")
        m.change()
        self.run_timers()
        self.assertEqual(self.killed, [])

    def test_a_game_it_started_keeps_it_running(self):
        views = [{"type": "toplevel", "pid": 900, "app-id": "steam_app_1"}]          # the game's window
        m, q = self.make(views, table=self.TABLE)
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
