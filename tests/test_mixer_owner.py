"""Whose sound stream (backend/mixer.py whose; shell/mixer_ui.py shown).
Vini: the sound mixer showed no icon for a Steam game.
Run: python3 -m unittest tests.test_mixer_owner"""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from sonata2.backend import mixer  # noqa: E402


def fake_proc(tree):
    """tree: {pid: (ppid, env dict)} -> a /proc-like folder."""
    root = tempfile.mkdtemp()
    for pid, (ppid, env) in tree.items():
        d = os.path.join(root, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "stat"), "w") as f:
            f.write(f"{pid} (some name) S {ppid} 0 0\n")
        with open(os.path.join(d, "environ"), "wb") as f:
            f.write(b"\0".join(f"{k}={v}".encode() for k, v in env.items()))
    return root


class WhoseTest(unittest.TestCase):
    def setUp(self):
        mixer._OWNERS.clear()

    def test_steam_game_by_its_environment(self):
        proc = fake_proc({500: (400, {"HOME": "/h"}), 400: (300, {"SteamAppId": "3219630"}), 300: (1, {})})
        self.assertEqual(mixer.whose(500, views=[], proc=proc), ("steam", "3219630"))

    def test_app_by_its_windows_process(self):
        proc = fake_proc({900: (800, {}), 800: (1, {})})                  # WebKitWebProcess -> the web app
        views = [{"pid": 800, "app-id": "io.github.vinioliveiras.sonata2.webapp.w0123456789"}]
        self.assertEqual(mixer.whose(900, views=views, proc=proc),
                         ("app", "io.github.vinioliveiras.sonata2.webapp.w0123456789"))

    def test_unknown_and_parse_keeps_the_pid(self):
        proc = fake_proc({70: (1, {})})
        self.assertIsNone(mixer.whose(70, views=[], proc=proc))
        s = mixer.parse('[{"index": 3, "volume": {}, "mute": false, "properties": '
                        '{"application.name": "WINE", "application.process.id": "4242"}}]')
        self.assertEqual(s[0].pid, 4242)


class ShownTest(unittest.TestCase):
    def test_game_name_and_icon_as_in_the_dock(self):
        from sonata2.shell import mixer_ui
        st = mixer.Stream(index=1, key="wine", name="WINE", icon="", volume=50, muted=False, pid=4242)
        with mock.patch("sonata2.apps.match_app_id", return_value=None), \
                mock.patch.object(mixer, "whose", return_value=("steam", "3219630")), \
                mock.patch("sonata2.steamgames.shown", return_value=("Ravage", "ICON")) as game:
            self.assertEqual(mixer_ui.shown(st), ("Ravage", "ICON"))
        game.assert_called_once_with("steam_app_3219630", "WINE")


if __name__ == "__main__":
    unittest.main()
