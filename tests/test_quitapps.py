"""Restart / Shut Down / Log Out quit the apps first (Vini: Chrome asked to
restore its tabs after every restart -- it was killed with the session)."""
import signal
import unittest
from unittest import mock

from sonata2.shell import quitapps as Q


class Win:
    def __init__(self, app_id, pid):
        self.app_id, self.title, self.pid = app_id, app_id, pid


class FakeManager:
    def __init__(self, wins, refuses=()):
        self.toplevels = list(wins)
        self.refuses = set(refuses)
        self.closed = []

    def close(self, t):
        self.closed.append(t.app_id)
        if t.app_id not in self.refuses:
            self.toplevels.remove(t)


class QuitterTest(unittest.TestCase):
    def run_quit(self, wins, tray=(), refuses=(), ignore_term=()):
        mgr = FakeManager(wins, refuses)
        running = {w.pid for w in wins} | set(tray)
        killed, result = [], []

        def kill(pid, sig):
            killed.append((pid, sig))
            if pid not in ignore_term:
                running.discard(pid)

        def views():
            return [{"type": "toplevel", "pid": t.pid, "app-id": t.app_id} for t in mgr.toplevels]
        with mock.patch.object(Q, "_cmdline", return_value="/opt/app"):
            q = Q.Quitter("restart", mgr, lambda k: result.append(("done", k)),
                          lambda k, t: result.append(("cancel", t.app_id)), views=views,
                          tray=lambda: set(tray), alive=lambda p: p in running, kill=kill, timeout_ms=2000)
            while q.tick():
                q.left_ms -= Q.POLL_MS
                q.waited_ms += Q.POLL_MS
        return mgr, killed, result

    def test_windows_close_background_apps_get_sigterm_then_restart(self):
        mgr, killed, result = self.run_quit([Win("google-chrome", 100), Win("org.gnome.TextEditor", 200)],
                                            tray=[300])           # Spotify: only a tray icon
        self.assertEqual(sorted(mgr.closed), ["google-chrome", "org.gnome.TextEditor"])
        self.assertEqual(sorted(killed), [(100, signal.SIGTERM), (200, signal.SIGTERM), (300, signal.SIGTERM)])
        self.assertEqual(result, [("done", "restart")])

    def test_an_app_that_keeps_its_window_cancels(self):
        """'Save changes?': the restart stops, like on macOS."""
        _mgr, killed, result = self.run_quit([Win("google-chrome", 100), Win("libreoffice", 200)],
                                             refuses=["libreoffice"])
        self.assertEqual(result, [("cancel", "libreoffice")])
        self.assertNotIn((200, signal.SIGTERM), killed)         # never signalled while it asks

    def test_a_stuck_background_process_doesnt_block(self):
        _mgr, _killed, result = self.run_quit([], tray=[300], ignore_term=[300])
        self.assertEqual(result, [("done", "restart")])

    def test_never_sonata_itself_nor_xwayland(self):
        views = [{"type": "toplevel", "pid": 10, "app-id": "io.github.vinioliveiras.sonata2.files"},
                 {"type": "toplevel", "pid": 11, "app-id": "steam"},
                 {"type": "toplevel", "pid": 12, "app-id": "google-chrome"}]
        cmd = {11: "/usr/bin/Xwayland :0 -rootless", 12: "/opt/google/chrome/chrome"}
        with mock.patch.object(Q, "_cmdline", side_effect=lambda p: cmd.get(p, "")):
            self.assertEqual(Q.app_pids(views, {12}), {12})

    def test_power_menu_goes_through_it(self):
        import pathlib
        root = pathlib.Path(Q.__file__).resolve().parent.parent
        top = (root / "shell" / "topbar.py").read_text()
        self.assertIn("self._end_session(kind) if r == kind", top)
        self.assertIn("quitapps.end_session(", (root / "settings" / "app.py").read_text())


if __name__ == "__main__":
    unittest.main()
