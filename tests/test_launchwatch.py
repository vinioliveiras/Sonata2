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
                    self.assertEqual(s, W.wait_s(info))
                    f()
                while later:
                    later.pop(0)()
        return n, kill, send

    def test_three_retries_then_notified(self):
        n, kill, send = self.drive(Info())
        self.assertEqual(n["launch"], W.RETRIES + 1)
        self.assertEqual(kill.call_count, W.RETRIES + 1)
        send.assert_called_once()

    def test_stops_when_a_window_comes(self):
        n, kill, send = self.drive(Info(), windows_from=2)
        self.assertEqual(n["launch"], 2)
        send.assert_not_called()

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
