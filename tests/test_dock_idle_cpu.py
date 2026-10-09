"""Regression tests: the Dock idle / steady-state work and leaks.
- a window's title changing (browser tabs, terminals) re-ran the whole sync:
  regroup, dots, relayout, Wayfire IPC + set_rectangle for every window;
- an app_id without a .desktop (Steam games, Wine) rebuilt the app index;
- a Steam game's icon was globbed / re-stamped on every window event;
- a magnification step resized icons whose size didn't change;
- DockWindow's registrations outlived it (each display hotplug leaked a Dock);
- a downloading folder's stack re-listed on the main loop every 300 ms.
Run: PYTHONPATH=.:tests xvfb-run -a python3.12 -m unittest test_dock_idle_cpu"""
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

for _var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_var] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2 import apps, steamgames  # noqa: E402
from sonata2.shell import dock as D, dock_stack  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


def bare_cfg(**over):
    return dict(D.DEFAULTS, pinned=[], stacks=[], recent=[], folders={}, **over)


def win(app_id, title, states=frozenset()):
    return SimpleNamespace(app_id=app_id, title=title, states=states, minimized=False, activated=False)


class TitleOnlySyncTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        self.mgr = SimpleNamespace(toplevels=[], listeners=[], available=True)
        self.dock = D.Dock(bare_cfg(), None)
        self.dock.manager = self.mgr
        self.rects = []
        p = [mock.patch.object(D.apps, "match_app_id", lambda a: a),
             mock.patch.object(D.Dock, "_update_rectangles_bg",
                               lambda s: (self.rects.append(1), False)[1]),
             mock.patch.object(D.windowapps, "describe",
                               lambda key, title="": (title or key, Gio.ThemedIcon.new("x"), True))]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def tearDown(self):
        self.dock.detach()

    def run_sync(self):
        self.rects.clear()
        self.dock._sync()
        settle(30)

    def test_title_change_skips_regroup_and_rectangles(self):
        a = win("game-x", "Level 1")
        self.mgr.toplevels = [a]
        self.run_sync()
        self.assertEqual(self.rects, [1])
        tile = self.dock.tiles["game-x"]
        self.assertEqual(tile.name, "Level 1")
        a.title = "Level 2"                                   # only the title changed
        with mock.patch.object(self.dock, "_relayout") as relayout, \
                mock.patch.object(D.DockTile, "set_running") as running:
            self.run_sync()
        relayout.assert_not_called()
        running.assert_not_called()
        self.assertEqual(self.rects, [])                      # no Wayfire IPC
        self.assertEqual(tile.name, "Level 2")                # the dynamic name still follows

    def test_title_badge_still_follows(self):
        a = win("game-x", "Inbox")
        self.mgr.toplevels = [a]
        self.run_sync()
        with mock.patch.object(D.badges, "counted_title", lambda *_a: True), \
                mock.patch.object(D.badges, "title_count", lambda titles: 3 if "(3) Inbox" in titles else 0):
            a.title = "(3) Inbox"
            self.run_sync()
        self.assertEqual(self.dock.title_counts.get("game-x"), 3)

    def test_new_window_or_state_runs_the_full_sync(self):
        self.mgr.toplevels = [win("game-x", "A")]
        self.run_sync()
        self.mgr.toplevels.append(win("game-y", "B"))
        self.run_sync()
        self.assertIn("game-y", self.dock.tiles)
        self.assertEqual(self.rects, [1])
        self.mgr.toplevels[0].states = frozenset({2})         # activated
        self.run_sync()
        self.assertEqual(self.rects, [1])

    def test_unplaced_title_asks_wayfire_again(self):
        """A window Wayfire's list didn't know by its title (several Docks):
        its next title is worth one more look."""
        self.mgr.toplevels = [win("game-x", "A")]
        self.run_sync()
        self.dock._rects_unsure = True
        self.mgr.toplevels[0].title = "B"
        self.run_sync()
        self.assertEqual(self.rects, [1])


class NegativeMatchCacheTest(unittest.TestCase):
    def setUp(self):
        apps.refresh()
        self.addCleanup(apps.refresh)

    def test_unknown_app_id_doesnt_rebuild_the_index(self):
        with mock.patch.object(apps.Gio.AppInfo, "get_all", return_value=[]) as get_all, \
                mock.patch.object(apps, "lookup", return_value=None):
            for _ in range(5):
                self.assertIsNone(apps.match_app_id("steam_app_123"))
            with mock.patch.object(apps.time, "monotonic", return_value=apps.time.monotonic() + 30):
                self.assertIsNone(apps.match_app_id("steam_app_123"))   # past INDEX_RETRY_S
            self.assertEqual(get_all.call_count, 1)
            apps.refresh()                                   # apps installed: asked again
            self.assertIsNone(apps.match_app_id("steam_app_123"))
            self.assertEqual(get_all.call_count, 2)

    def test_scan_forgets_misses(self):
        with mock.patch.object(apps, "_match", return_value=None) as match:
            apps.match_app_id("wine")
            apps.match_app_id("wine")
            self.assertEqual(match.call_count, 1)
            with mock.patch.object(apps, "app_dirs", return_value=[]):
                apps.scan()
            apps.match_app_id("wine")
            self.assertEqual(match.call_count, 2)


class SteamIconCacheTest(unittest.TestCase):
    def test_icon_looked_up_once_a_minute(self):
        steamgames._icons.clear()
        self.addCleanup(steamgames._icons.clear)
        with mock.patch.object(steamgames, "icon_path", return_value=None) as path, \
                mock.patch.object(steamgames, "name", return_value="Game"):
            for _ in range(5):
                name, icon = steamgames.shown("steam_app_42")
            self.assertEqual(name, "Game")
            self.assertEqual(path.call_count, 1)
            later = steamgames.time.monotonic() + steamgames.ICON_RETRY_S + 1
            with mock.patch.object(steamgames.time, "monotonic", return_value=later):
                steamgames.shown("steam_app_42")
            self.assertEqual(path.call_count, 2)


class MagnificationResizeTest(unittest.TestCase):
    def test_same_size_queues_no_resize(self):
        icon = D.DockIcon(Gio.ThemedIcon.new("x"), 48)
        with mock.patch.object(icon, "queue_resize") as qr:
            icon.set_size(48.2)
            icon.set_size(47.8)
            qr.assert_not_called()
            icon.set_size(49)
            qr.assert_called_once()


class DockWindowLeakTest(unittest.TestCase):
    def test_destroy_undoes_registrations(self):
        from sonata2 import config, sandbox, ui
        app = Gtk.Application(application_id="org.sonata.leaktest", flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        menus, boxes = len(ui.menu.on_closed), len(sandbox.listeners)
        w = D.DockWindow(app, bare_cfg())
        self.assertEqual(len(ui.menu.on_closed), menus + 1)
        self.assertEqual(len(sandbox.listeners), boxes + 1)
        mons = [w._cfg_mon, w._icons_mon, w._lock_mon]
        dock = w.dock
        w.destroy()
        self.assertEqual(len(ui.menu.on_closed), menus)
        self.assertEqual(len(sandbox.listeners), boxes)
        self.assertTrue(all(m.is_cancelled() for m in mons))
        self.assertNotIn(dock, D._DOCKS)
        del config


class StackRefreshTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        self.dock = D.Dock(bare_cfg(), None)
        self.addCleanup(self.dock.detach)

    def test_debounce_is_a_second(self):
        self.assertGreaterEqual(dock_stack.REFRESH_MS, 1000)

    def test_same_icon_not_set_again(self):
        tile = SimpleNamespace(gicon=Gio.ThemedIcon.new("folder"), set_gicon=mock.Mock())
        dock_stack._set_icon(tile, Gio.ThemedIcon.new("folder"))
        tile.set_gicon.assert_not_called()
        dock_stack._set_icon(tile, Gio.ThemedIcon.new("folder-download"))
        tile.set_gicon.assert_called_once()

    def test_listing_off_the_main_thread(self):
        folder = tempfile.mkdtemp()
        open(os.path.join(folder, "a.txt"), "w").close()
        self.dock.stacks.add(folder)
        tile = self.dock.stacks.tiles()[-1]
        tile.spec["display"] = "stack"
        seen = []
        real = dock_stack._items
        with mock.patch.object(dock_stack, "_items",
                               lambda *a: (seen.append(threading.current_thread()), real(*a))[1]), \
                mock.patch.object(tile, "set_gicon") as set_gicon:
            self.dock.stacks.refresh_icon_bg(tile)
            self.dock.stacks.refresh_icon_bg(tile)            # busy: once more after
            for _ in range(50):
                settle(20)
                if not getattr(tile, "stack_busy", False) and not tile.stack_src:
                    break
        self.assertTrue(seen)
        self.assertNotIn(threading.main_thread(), seen)
        self.assertLessEqual(set_gicon.call_count, 1)          # the same icon: set once at most


if __name__ == "__main__":
    unittest.main()
