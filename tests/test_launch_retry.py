"""Vini: an app sometimes fails to open (Steam above all). No window this
long after a launch: stopped and opened again, up to LAUNCH_RETRIES times;
then a notification says it couldn't open."""
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2.shell import dock as D  # noqa: E402


class FakeInfo:
    def __init__(self, fail=False):
        self.launches, self.fail = 0, fail

    def get_id(self):
        return "steam.desktop"

    def get_display_name(self):
        return "Steam"

    def get_icon(self):
        return None

    def launch(self, _uris, _ctx):
        if self.fail:
            raise GLib.Error("no such program")
        self.launches += 1


def fake(fail=False):
    d = types.SimpleNamespace(_starting={}, windows={}, _retries={}, launch_feedback=lambda t: None)
    d.launch = lambda tile: D.Dock.launch(d, tile)
    d._watch_launch = lambda tile: D.Dock._watch_launch(d, tile)
    d._launch_failed = lambda tile, why: D.Dock._launch_failed(d, tile, why)
    tile = types.SimpleNamespace(key="steam", name="Steam", info=FakeInfo(fail), stopped=0,
                                 get_display=lambda: types.SimpleNamespace(get_app_launch_context=lambda: None))
    tile._stop_bounce = lambda: setattr(tile, "stopped", tile.stopped + 1)
    return d, tile


class KillTest(unittest.TestCase):
    def test_kills_the_apps_own_processes(self):
        import os
        import tempfile
        from sonata2 import appscope

        class Info:
            def get_commandline(self):
                return "/usr/bin/steam %U"

            def get_id(self):
                return "steam.desktop"
        self.assertEqual(appscope.process_names(Info()), {"steam", "steamwebhelper", "steam.sh"})
        with tempfile.TemporaryDirectory() as proc:
            for pid, comm in ((111, "steamwebhelper"), (222, "firefox"), (333, "steam")):
                os.makedirs(os.path.join(proc, str(pid)))
                with open(os.path.join(proc, str(pid), "comm"), "w") as f:
                    f.write(comm + "\n")
            killed = []
            with mock.patch.object(appscope.os, "kill", side_effect=lambda p, s: killed.append(p)), \
                    mock.patch.object(appscope, "_bus", return_value=None):
                appscope.kill("steam.desktop", Info(), proc=proc)
        self.assertEqual(sorted(killed), [111, 333])

    def test_never_a_shell_by_name(self):
        from sonata2 import appscope

        class Info:
            def get_commandline(self):
                return "sh -c 'foo'"

            def get_id(self):
                return "foo.desktop"
        self.assertEqual(appscope.process_names(Info()), set())


class LaunchRetryTest(unittest.TestCase):
    def run_launch(self, d, tile, windows_after=None):
        checks, relaunch = [], []
        with mock.patch.object(D.GLib, "timeout_add_seconds", side_effect=lambda s, f: checks.append((s, f))), \
                mock.patch.object(D.GLib, "timeout_add", side_effect=lambda ms, f: relaunch.append(f)), \
                mock.patch.object(D, "scope_has_window", return_value=False), \
                mock.patch("sonata2.appscope.kill") as stop, \
                mock.patch("sonata2.notify.send") as send:
            d.launch(tile)
            rounds = 0
            while checks:
                s, f = checks.pop(0)
                self.assertEqual(s, D.LAUNCH_WATCH_SLOW_S["steam"])     # Steam updates itself first
                rounds += 1
                if windows_after is not None and rounds == windows_after:
                    d.windows["steam"] = [object()]
                f()
                while relaunch:
                    relaunch.pop(0)()
        return stop, send, rounds

    def test_retried_then_notified(self):
        d, tile = fake()
        stop, send, rounds = self.run_launch(d, tile)
        self.assertEqual(tile.info.launches, D.LAUNCH_RETRIES + 1)
        self.assertEqual(rounds, D.LAUNCH_RETRIES + 1)
        self.assertEqual(stop.call_count, D.LAUNCH_RETRIES + 1)          # killed before each retry, and at the end
        self.assertIs(stop.call_args.args[1], tile.info)
        send.assert_called_once()
        self.assertIn("Steam", send.call_args.args[0])
        self.assertEqual(d._retries, {})

    def test_window_came_no_more_retries(self):
        d, tile = fake()
        stop, send, _rounds = self.run_launch(d, tile, windows_after=2)
        self.assertEqual(tile.info.launches, 2)
        send.assert_not_called()

    def test_launch_error_notifies_at_once(self):
        d, tile = fake(fail=True)
        with mock.patch("sonata2.notify.send") as send, \
                mock.patch.object(D.GLib, "timeout_add_seconds") as watch:
            d.launch(tile)
        send.assert_called_once()
        watch.assert_not_called()

    def test_others_wait_less(self):
        self.assertLess(D.LAUNCH_WATCH_S, D.LAUNCH_WATCH_SLOW_S["steam"])


if __name__ == "__main__":
    unittest.main()
