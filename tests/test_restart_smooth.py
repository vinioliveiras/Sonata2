"""Restart Sonata, smoothly (Vini): the old Dock and menu bar slide away,
the new ones slide in once ready, the wallpaper never blinks."""
import os
import signal
import tempfile
import unittest
from unittest import mock

from gi.repository import GLib

from sonata2 import __main__ as M
from sonata2.shell import intro


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class RestartTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        p = mock.patch.object(intro, "RESTART_MARK", os.path.join(d, "restarting"))
        p.start()
        self.addCleanup(p.stop)

    def test_choreography(self):
        events = []
        pids = {"keep dock": [11], "keep topbar": [12], "keep wallpaper": [13],
                "sonata2 dock": [21], "sonata2 topbar": [22], "sonata2 wallpaper": [23]}

        def pgrep(pattern):
            for k, v in pids.items():
                if pattern.startswith("-m " + k.replace("sonata2 ", "sonata2 ") if k.startswith("sonata2") else
                                      "-m sonata2 " + k):
                    return v
            return []
        with mock.patch.object(M, "_pids", side_effect=pgrep), \
                mock.patch.object(M, "_signal", side_effect=lambda p, s: events.append(("kill", p, s))), \
                mock.patch("subprocess.run"), mock.patch.object(M, "_reload_wayfire_config"), \
                mock.patch("subprocess.Popen", side_effect=lambda a, **k: events.append(
                    ("start", a[-1] if a[-1] != "--background" else a[-2], os.path.exists(intro.RESTART_MARK)))), \
                mock.patch("time.sleep", side_effect=lambda s: events.append(("sleep", s))):
            M.restart(["wallpaper", "dock", "topbar"])
        kills = [e for e in events if e[0] == "kill"]
        self.assertIn(("kill", 21, signal.SIGUSR1), kills)            # the old Dock and bar slide away first
        self.assertIn(("kill", 22, signal.SIGUSR1), kills)
        i_slide = events.index(("kill", 21, signal.SIGUSR1))
        i_term = events.index(("kill", 21, signal.SIGTERM))
        self.assertTrue(any(e[0] == "sleep" for e in events[i_slide:i_term]))   # ...and get time to
        starts = [e for e in events if e[0] == "start"]
        self.assertTrue(all(e[2] for e in starts))                    # the new ones start out of sight
        i_wall_start = events.index(next(e for e in starts if e[1] == "wallpaper"))
        i_wall_kill = events.index(("kill", 23, signal.SIGTERM))
        self.assertGreater(i_wall_kill, i_wall_start)                 # old wallpaper goes after the new one is up
        self.assertFalse(os.path.exists(intro.RESTART_MARK))          # and the marker is gone at the end

    def test_new_component_slides_in_soon_after_ready(self):
        open(intro.RESTART_MARK, "w").close()
        hits = []
        with mock.patch.object(intro, "MARK", intro.RESTART_MARK + ".none"):
            self.assertTrue(intro.entering())
            intro.wait(lambda: hits.append(1))
            settle(intro.ARRIVE_MS // 2)
            self.assertEqual(hits, [])
            settle(intro.ARRIVE_MS)
        self.assertEqual(hits, [1])

    def test_dock_and_bar_leave_on_the_signal(self):
        import inspect
        from sonata2.shell import dock, topbar
        self.assertIn("intro.leave_on_signal(lambda: self._slide(True))", inspect.getsource(dock))
        self.assertIn("intro.leave_on_signal(lambda: self._slide(True))", inspect.getsource(topbar))
        got = []
        intro._leaving.clear()
        intro.leave_on_signal(lambda: got.append(1))
        os.kill(os.getpid(), signal.SIGUSR1)
        settle(100)
        self.assertEqual(got, [1])


if __name__ == "__main__":
    unittest.main()
