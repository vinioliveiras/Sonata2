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


if __name__ == "__main__":
    unittest.main()
