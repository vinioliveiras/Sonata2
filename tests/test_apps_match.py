"""Windows matched to their app's desktop entry. Run:
python3 -m unittest tests.test_apps_match"""
import os
import tempfile
import unittest

_DATA = tempfile.mkdtemp()
os.makedirs(os.path.join(_DATA, "applications"))
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["XDG_DATA_DIRS"] = tempfile.mkdtemp()            # only these entries


_BIN = tempfile.mkdtemp()
os.environ["PATH"] = _BIN + os.pathsep + os.environ.get("PATH", "")


def _entry(did, name, exe, extra=""):
    path = os.path.join(_BIN, os.path.basename(exe))          # GIO lists only entries whose program exists
    with open(path, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    exe = path if exe.startswith("/") else exe
    with open(os.path.join(_DATA, "applications", did + ".desktop"), "w") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exe} %U\n{extra}")


_entry("spotify-launcher", "Spotify", "/usr/bin/spotify-launcher")
_entry("code-oss", "Code", "code-oss")
_entry("org.example.Thing", "Thing", "thing", "StartupWMClass=ThingWin\n")
_entry("foo-bin", "Foo (wrapper)", "foo-bin")
_entry("com.anthropic.Claude", "Claude", "claude-desktop", "StartupWMClass=com.anthropic.Claude\n")
_entry("foo", "Foo", "foo")

from sonata2 import apps  # noqa: E402


class MatchTest(unittest.TestCase):
    def setUp(self):
        apps.refresh()

    def test_launcher_entry_matches_its_window(self):
        """Vini: Spotify open while Sonata restarted lost its running dot and
        a click bounced on and on -- its window ("spotify") didn't match
        spotify-launcher.desktop."""
        self.assertEqual(apps.match_app_id("spotify"), "spotify-launcher")
        self.assertEqual(apps.match_app_id("Spotify"), "spotify-launcher")

    def test_claude_window_names(self):
        """Vini: the same happened with Claude: whatever id its window
        reports (class, program, short name), it's the pinned entry."""
        for app_id in ("com.anthropic.Claude", "claude-desktop", "Claude", "claude"):
            self.assertEqual(apps.match_app_id(app_id), "com.anthropic.Claude", app_id)

    def test_exact_names_still_win(self):
        self.assertEqual(apps.match_app_id("foo"), "foo")         # not foo-bin's loose name
        self.assertEqual(apps.match_app_id("ThingWin"), "org.example.Thing")
        self.assertEqual(apps.match_app_id("thing"), "org.example.Thing")
        self.assertEqual(apps.match_app_id("code-oss"), "code-oss")
        self.assertIsNone(apps.match_app_id("nothing-here"))


if __name__ == "__main__":
    unittest.main()
