"""Vini: Steam's menu in the menu bar (Library, Store...) opened nothing --
Steam ignores the clicks it gets, however they're sent. Its items open
with Steam's own steam:// links instead; other apps' menus are unchanged."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2.shell import tray  # noqa: E402


def layout():
    kid = lambda i, label: GLib.Variant("(ia{sv}av)", (i, {"label": GLib.Variant("s", label)}, []))
    return (0, {}, [kid(1, "_Library"), kid(2, "Store"), kid(3, "Half-Life 2"), kid(4, "Exit Steam")])


class SteamTrayTest(unittest.TestCase):
    def test_links(self):
        self.assertEqual(tray.steam_link("Library"), "steam://open/games")
        self.assertEqual(tray.steam_link("Biblioteca"), "steam://open/games")
        self.assertEqual(tray.steam_link("Configurações"), "steam://open/settings")
        self.assertEqual(tray.steam_link("Exit Steam"), "-shutdown")
        self.assertIsNone(tray.steam_link("Half-Life 2"))                 # a recent game: Steam's own Event

    def menu(self, steam):
        m = tray.DBusMenu.__new__(tray.DBusMenu)
        m.steam, m.labels, m.calls = steam, {}, []
        m._call = lambda method, args, *a: m.calls.append((method, args.unpack()[:2]))
        m.sections(layout())
        return m

    def test_steam_items_open_with_links(self):
        m = self.menu(True)
        with mock.patch.object(tray, "run_steam", return_value=True) as run:
            m.event(1)
            m.event(4)
            m.event(3)
        self.assertEqual([c.args[0] for c in run.call_args_list], ["steam://open/games", "-shutdown"])
        self.assertEqual(m.calls, [("Event", (3, "clicked"))])            # the game: the usual click

    def test_other_apps_unchanged(self):
        m = self.menu(False)
        with mock.patch.object(tray, "run_steam") as run:
            m.event(1)
        run.assert_not_called()
        self.assertEqual(m.calls, [("Event", (1, "clicked"))])


def renumbered(base, label3="Half-Life 2"):
    """Steam's menu again, rebuilt with new ids (as on every AboutToShow / "opened")."""
    kid = lambda i, label: GLib.Variant("(ia{sv}av)", (i, {"label": GLib.Variant("s", label)}, []))
    return (0, {}, [kid(base + 1, "_Library"), kid(base + 2, "Store"), kid(base + 3, label3),
                    kid(base + 4, "Exit Steam")])


class SteamMenuLoopTest(unittest.TestCase):
    """Vini: clicking Steam's menu did nothing and froze the menu bar --
    each redraw sent AboutToShow, Steam answered with a rebuilt menu and
    LayoutUpdated, and the open menu's rows were swapped without end."""

    def menu(self):
        m = tray.DBusMenu.__new__(tray.DBusMenu)
        m.steam, m.labels, m.calls, m.replies = True, {}, [], []
        m.pop = mock.Mock(get_visible=lambda: True)

        def call(method, args, rtype=None, cb=None):
            m.calls.append((method, args.unpack()[:2]))
            if method == "GetLayout" and cb:
                cb(GLib.Variant("(u(ia{sv}av))", (1, m.replies.pop(0))) if m.replies else None)
        m._call = call
        m.sections(layout())
        m.open = (m.pop, None)
        return m

    def flush(self, m):
        if m._pending:
            GLib.source_remove(m._pending)
            m._refetch()

    def test_redraw_sends_no_about_to_show(self):
        m = self.menu()
        m.replies = [renumbered(100)]
        for _ in range(5):                               # a burst: one read
            m._signal(None, None, None, None, "LayoutUpdated")
        self.flush(m)
        self.assertEqual([c[0] for c in m.calls], ["GetLayout"])

    def test_renumbered_menu_keeps_rows_and_clicks_reach_steam(self):
        m = self.menu()
        m.replies = [renumbered(100)]
        m._signal(None, None, None, None, "LayoutUpdated")
        self.flush(m)
        m.pop.set_menu_model.assert_not_called()         # the same menu: rows untouched
        m.calls.clear()
        m.event(3)                                       # the game, shown with id 3
        self.assertEqual(m.calls, [("Event", (103, "clicked"))])
        self.assertIsNone(m.open)                        # closing: no more redraws
        m._signal(None, None, None, None, "LayoutUpdated")
        self.assertFalse(m._pending)

    def test_changed_menu_is_redrawn(self):
        m = self.menu()
        m.replies = [renumbered(100, "Portal 2")]
        m._signal(None, None, None, None, "LayoutUpdated")
        self.flush(m)
        m.pop.set_menu_model.assert_called_once()
        self.assertEqual(m.labels[103], "Portal 2")

    def test_link_brings_steam_forward(self):
        with mock.patch("shutil.which", return_value="/usr/bin/steam"), \
                mock.patch("subprocess.Popen"), mock.patch.object(tray, "front_steam") as front:
            self.assertTrue(tray.run_steam("steam://open/games"))
            self.assertTrue(tray.run_steam("-shutdown"))
        self.assertEqual(front.call_count, 1)

    def test_link_really_starts_steam(self):
        """Vini's log: GLib.spawn_async(..., *_TO_DEV_NULL) raised AssertionError
        in PyGObject on Python 3.14 -- the click died there. A real program runs now."""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "steam")
            out = os.path.join(d, "ran")
            with open(fake, "w") as f:
                f.write(f'#!/bin/sh\necho "$1" > {out}\n')
            os.chmod(fake, 0o755)
            with mock.patch("shutil.which", return_value=fake), mock.patch.object(tray, "front_steam"):
                self.assertTrue(tray.run_steam("steam://open/games"))
            end = GLib.get_monotonic_time() + 3_000_000
            while not os.path.exists(out) and GLib.get_monotonic_time() < end:
                GLib.usleep(20000)
            self.assertEqual(open(out).read().strip(), "steam://open/games")
        import inspect
        self.assertNotIn("GLib.spawn_async(", inspect.getsource(tray.run_steam))

    def test_front_steam_polls_until_shown(self):
        from sonata2.shell import notifications
        seen = []
        with mock.patch.object(notifications, "bring_forward", side_effect=lambda *a: seen.append(a) or len(seen) >= 3), \
                mock.patch.object(tray, "FRONT_EVERY_MS", 1):
            tray.front_steam()
            end = GLib.get_monotonic_time() + 2_000_000
            while len(seen) < 3 and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
            for _ in range(50):
                GLib.MainContext.default().iteration(False)
        self.assertEqual(seen, [("steam", "Steam")] * 3)


if __name__ == "__main__":
    unittest.main()
