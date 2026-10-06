"""Vini: Spotify opened together with Chrome started, but its window was
created and never shown; clicking its Dock icon again only woke that stuck
copy. Clicked again after a launch that never showed a window, the Dock
stops that launch (its systemd scope) and opens the app afresh."""
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from sonata2 import appscope  # noqa: E402
from sonata2.shell import dock as D  # noqa: E402

SCOPE_HAS_WINDOW = D.scope_has_window      # (patched in the launch tests)


class FakeInfo:
    def __init__(self):
        self.launches = 0

    def get_id(self):
        return "spotify-launcher.desktop"

    def launch(self, _uris, _ctx):
        self.launches += 1


def fake_dock():
    later = []
    d = types.SimpleNamespace(_starting={}, windows={}, launch_feedback=lambda t: None)
    d.launch = lambda tile: D.Dock.launch(d, tile)
    tile = types.SimpleNamespace(key="spotify-launcher", info=FakeInfo(), _stop_bounce=lambda: None,
                                 get_display=lambda: types.SimpleNamespace(get_app_launch_context=lambda: None))
    return d, tile, later


class StuckLaunchTest(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.with_window = set()
        self.later = []
        self.stopped = []
        for p in (mock.patch.object(D.time, "monotonic", lambda: self.clock[0]),
                  mock.patch.object(D.GLib, "timeout_add", lambda ms, fn: self.later.append(fn)),
                  mock.patch.object(appscope, "_bus", lambda: object()),
                  mock.patch.object(D, "scope_has_window", lambda unit: unit in self.with_window),
                  mock.patch.object(appscope, "_manager", lambda _b, m, params, sig=None:
                                    self.stopped.append(params.unpack()[0]))):
            p.start()
            self.addCleanup(p.stop)
        appscope.launched.clear()

    def test_stuck_launch_is_stopped_and_opened_again(self):
        d, tile, _ = fake_dock()
        d.launch(tile)
        self.assertEqual(tile.info.launches, 1)
        appscope.launched["spotify-launcher.desktop"] = appscope.unit_name("spotify-launcher.desktop", 3524)
        self.clock[0] += 1
        d.launch(tile)                                   # too soon: just opening (slow app)
        self.assertEqual(self.stopped, [])
        self.assertEqual(tile.info.launches, 2)
        self.clock[0] += D.STUCK_S + 1
        d.launch(tile)                                   # no window since: stuck
        self.assertEqual(self.stopped, ["app-sonata2-spotify\\x2dlauncher-3524.scope"])
        self.assertEqual(tile.info.launches, 2)          # opened again once it's stopped
        self.later.pop()()
        self.assertEqual(tile.info.launches, 3)

    def test_running_app_is_never_stopped(self):
        d, tile, _ = fake_dock()
        d.launch(tile)
        appscope.launched["spotify-launcher.desktop"] = "x.scope"
        d._starting.pop("spotify-launcher")              # its window showed (Dock's update)
        self.clock[0] += 60
        d.launch(tile)                                   # (closed to the tray, say): opened normally
        self.assertEqual(self.stopped, [])
        self.assertEqual(tile.info.launches, 2)

    def test_window_under_another_icon_is_not_stuck(self):
        """Review: an app whose window groups under another icon (its app id
        names another entry) was killed on the next click."""
        d, tile, _ = fake_dock()
        d.launch(tile)
        appscope.launched["spotify-launcher.desktop"] = "x.scope"
        self.with_window.add("x.scope")                  # a window of that launch, under another icon
        self.clock[0] += D.STUCK_S + 1
        d.launch(tile)
        self.assertEqual(self.stopped, [])
        self.assertEqual(tile.info.launches, 2)

    def test_long_ago_launch_is_not_stuck(self):
        """Review: the launch time never expired -- a slow app clicked much later was stopped."""
        d, tile, _ = fake_dock()
        d.launch(tile)
        appscope.launched["spotify-launcher.desktop"] = "x.scope"
        self.clock[0] += D.STUCK_MAX_S + 5
        d.launch(tile)
        self.assertEqual(self.stopped, [])

    def test_scope_window_lookup(self):
        import os
        import tempfile
        d = tempfile.mkdtemp()
        real_open = open

        def fake_open(path, *a, **k):
            if path == "/proc/4242/cgroup":
                return real_open(os.path.join(d, "cg"), *a, **k)
            raise OSError
        with real_open(os.path.join(d, "cg"), "w") as f:
            f.write("0::/user.slice/sonata-apps.slice/app-sonata2-spotify-3524.scope\n")
        ipc = mock.Mock()
        ipc.return_value.call.return_value = [{"pid": 4242}, {"pid": "x"}, {"pid": 7}]
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", ipc), mock.patch("builtins.open", fake_open):
            self.assertTrue(SCOPE_HAS_WINDOW("app-sonata2-spotify-3524.scope"))
            self.assertFalse(SCOPE_HAS_WINDOW("app-sonata2-other-1.scope"))
        self.assertFalse(SCOPE_HAS_WINDOW(None))

    def test_move_remembers_the_scope(self):
        with mock.patch.object(appscope, "raise_oom_score", lambda pid: True):
            appscope.move(3524, "spotify-launcher.desktop")
        self.assertEqual(appscope.launched["spotify-launcher.desktop"],
                         "app-sonata2-spotify\\x2dlauncher-3524.scope")


if __name__ == "__main__":
    unittest.main()
