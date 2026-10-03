"""Task Manager: a Steam game's processes are that game (Vini: Halloween's
showed as "Red Dead Redemption" -- a launcher in another game's entry).
Run: python3 -m unittest tests.test_tm_steam"""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from sonata2.activity import procfs, window as W  # noqa: E402


class SteamProcessTest(unittest.TestCase):
    def test_appid_from_environ(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(b"HOME=/home/x\0SteamAppId=3219630\0WINEPREFIX=/p\0")
        self.assertEqual(procfs.steam_appid(d), "3219630")
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(b"SteamAppId=0\0HOME=/x\0")
        self.assertEqual(procfs.steam_appid(d), "")
        self.assertEqual(procfs.steam_appid(os.path.join(d, "gone")), "")

    def test_game_named_by_its_app_id(self):
        p = mock.Mock(cmdline="Z:\\Halloween.exe", exe="wine64-preloader", comm="Halloween.exe",
                      steam="3219630")
        p.name = "Halloween.exe"
        idx = {"wine64-preloader": ("RDR-ICON", "Red Dead Redemption")}
        with mock.patch("sonata2.steamgames.shown", return_value=("Halloween: The Video Game", "ICON")):
            key, icon, name = W.app_for(p, idx)
        self.assertEqual((key, name), ("steam_app_3219630", "Halloween: The Video Game"))

    def test_wine_is_no_app(self):
        p = mock.Mock(cmdline="wineserver", exe="wineserver", comm="wineserver", steam="")
        p.name = "wineserver"
        key, _icon, _name = W.app_for(p, {"wineserver": ("X", "Red Dead Redemption")})
        self.assertIsNone(key)


class IconIndexTest(unittest.TestCase):
    """Vini: Steam itself showed as "Red Dead Redemption" -- the game's
    shortcut runs `steam steam://rungameid/...`, and its executable's name
    ("steam") was taken for the game."""

    def test_game_shortcut_doesnt_own_steam(self):
        def app(did, name, cmd, exe):
            i = mock.Mock()
            i.get_icon.return_value = name.upper()
            i.get_display_name.return_value = name
            i.get_id.return_value = did + ".desktop"
            i.get_startup_wm_class.return_value = None
            i.get_executable.return_value = exe
            i.get_commandline.return_value = cmd
            return i
        rdr = app("Red Dead Redemption", "Red Dead Redemption", "steam steam://rungameid/2668510", "steam")
        steam = app("steam", "Steam", "/usr/bin/steam %U", "/usr/bin/steam")
        with mock.patch.object(W.Gio.AppInfo, "get_all", return_value=[rdr, steam]):
            idx = W.icon_index()
        self.assertEqual(idx["steam"][1], "Steam")
