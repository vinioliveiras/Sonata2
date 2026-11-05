"""Launchpad and Files open apps watched like the Dock (Vini): no window
in time -> killed and opened again, 3 times, then a notification."""
import inspect
import unittest
from unittest import mock

from sonata2 import launchwatch as W


class Info:
    def __init__(self, did="steam.desktop"):
        self.did, self.launches = did, 0

    def get_id(self):
        return self.did

    def get_display_name(self):
        return "Steam"

    def get_icon(self):
        return None


class HasWindowTest(unittest.TestCase):
    def test_by_app_id(self):
        with mock.patch("sonata2.apps.match_app_id", side_effect=lambda a: {"Steam": "steam"}.get(a)):
            self.assertTrue(W.has_window(Info(), [{"app-id": "steam", "type": "toplevel"}]))
            self.assertTrue(W.has_window(Info(), [{"app-id": "Steam", "type": "toplevel"}]))
            self.assertFalse(W.has_window(Info(), [{"app-id": "firefox", "type": "toplevel"}]))
            self.assertFalse(W.has_window(Info(), [{"app-id": "steam", "type": "background"}]))

    def test_unknown_means_open(self):
        """Wayfire's list can't be read: never kill on a guess."""
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", side_effect=OSError):
            self.assertTrue(W.has_window(Info()))


class OpenTest(unittest.TestCase):
    def drive(self, info, windows_from=None):
        timers, later = [], []
        n = {"launch": 0, "check": 0, "first": True}

        def launch():
            n["launch"] += 1

        def has(_info, views=None, unit=None):
            if n["first"]:                                      # before the first launch
                n["first"] = False
                return False
            n["check"] += 1
            return windows_from is not None and n["check"] >= windows_from
        with mock.patch.object(W, "has_window", side_effect=has), \
                mock.patch.object(W.GLib, "timeout_add_seconds", side_effect=lambda s, f: timers.append((s, f))), \
                mock.patch.object(W.GLib, "timeout_add", side_effect=lambda ms, f: later.append(f)), \
                mock.patch("sonata2.appscope.kill") as kill, mock.patch("sonata2.notify.send") as send:
            W.open(info, launch)
            while timers or later:
                if timers:
                    s, f = timers.pop(0)
                    self.assertEqual(s, 1)                      # looked at every second
                    if f():
                        timers.insert(0, (s, f))
                while later:
                    later.pop(0)()
        return n, kill, send

    def test_three_retries_then_notified(self):
        n, kill, send = self.drive(Info())
        self.assertEqual(n["launch"], W.RETRIES + 1)
        self.assertEqual(kill.call_count, W.RETRIES + 1)
        send.assert_called_once()

    def test_stops_when_a_window_comes(self):
        # none for the first launch's whole wait (and its last look), one at
        # the second launch's first second
        n, kill, send = self.drive(Info(), windows_from=W.wait_s(Info()) + 2)
        self.assertEqual(n["launch"], 2)
        send.assert_not_called()

    def test_assistant_reopened_after_closing_it(self):
        """Vini: the Assistant kept opening again after he closed it -- its
        window came at once, but it was looked for only after 30 s, when he
        had closed it. A window seen during the wait ends the watch."""
        seen = {"n": 0}

        def has(_info, views=None, unit=None):
            seen["n"] += 1
            return seen["n"] == 3                               # shown in the 2nd second, closed after
        timers = []
        with mock.patch.object(W, "has_window", side_effect=has), \
                mock.patch.object(W.GLib, "timeout_add_seconds", side_effect=lambda s, f: timers.append(f)), \
                mock.patch("sonata2.appscope.kill") as kill:
            W.open(Info(), lambda: None)
            for _ in range(60):
                if not timers or not timers[0]():
                    break
        kill.assert_not_called()
        self.assertEqual(W.tick(False, 30, 30), "check")
        self.assertEqual(W.tick(True, 1, 30), "opened")

    def test_already_open_not_watched(self):
        with mock.patch.object(W, "has_window", return_value=True), \
                mock.patch.object(W.GLib, "timeout_add_seconds") as t:
            W.open(Info(), lambda: None)
        t.assert_not_called()

    def test_cannot_start_notifies(self):
        from gi.repository import GLib

        def boom():
            raise GLib.Error("nope")
        with mock.patch.object(W, "has_window", return_value=False), mock.patch("sonata2.notify.send") as send:
            self.assertFalse(W.open(Info(), boom))
        send.assert_called_once()

    def test_used_by_launchpad_and_files(self):
        from sonata2.files import window
        from sonata2.shell import launchpad
        self.assertIn("launchwatch.open(info", inspect.getsource(launchpad.Launchpad.activate_item))
        src = inspect.getsource(window)
        self.assertGreaterEqual(src.count("launchwatch.open(app"), 3)


if __name__ == "__main__":
    unittest.main()
