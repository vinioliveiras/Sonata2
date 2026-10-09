"""Vini: an installer run with Faugus (Proton) showed as "steam_app_default"
in the Dock -- and it must work for any app, not only Steam. A window whose
app_id has no desktop entry is named by windowapps, the same way in the
Dock, the app switcher and the menu bar, following its title when that is
where its name comes from."""
import inspect
import unittest
from unittest import mock

from sonata2 import windowapps as W


class FakeInfo:
    def __init__(self, name, cmd):
        self.name, self.cmd = name, cmd

    def get_display_name(self):
        return self.name

    def get_commandline(self):
        return self.cmd


class Win:
    def __init__(self, title, activated=False):
        self.title, self.activated = title, activated


NO_APPS = {"x": FakeInfo("Firefox", "firefox %u")}


class WindowAppsTest(unittest.TestCase):
    def test_windows_program_named_by_its_title(self):
        with mock.patch("sonata2.apps._scan", NO_APPS):
            name, icon, dynamic = W.describe("steam_app_default", "FL Studio 2026 Setup")
            self.assertEqual(name, "FL Studio 2026 Setup")
            self.assertTrue(dynamic)                              # follows the title
            self.assertIn("wine", icon.get_names())
            self.assertEqual(W.describe("steam_app_default", "")[0], "Windows App")
            self.assertEqual(W.describe("wine", "Notepad++")[0], "Notepad++")
            self.assertEqual(W.describe("FL64.exe", "")[0], "FL64")

    def test_launcher_shortcut(self):
        scan = {"fl": FakeInfo("FL Studio 2026", "faugus-launcher --game fl-studio-2026"),
                "fl2": FakeInfo("FL", "lutris lutris:rungame/fl"),
                "other": FakeInfo("FL Studio 2026", "flatpak run something")}
        with mock.patch("sonata2.apps._scan", scan), mock.patch("sonata2.icons.app_icon", lambda i: i.name):
            self.assertEqual(W.describe("steam_app_default", "FL Studio 2026 - untitled.flp"),
                             ("FL Studio 2026", "FL Studio 2026", False))

    def test_any_other_app(self):
        self.assertEqual(W.describe("org.example.CoolApp", "doc.txt")[0], "CoolApp")
        self.assertEqual(W.describe("my-tool", "")[0], "My Tool")
        name, icon, dynamic = W.describe("my-tool", "")
        self.assertIn("my-tool", icon.get_names())
        self.assertFalse(dynamic)

    def test_window_title(self):
        self.assertEqual(W.window_title([Win("a"), Win("b", True)]), "b")
        self.assertEqual(W.window_title([Win(""), Win("c")]), "c")
        self.assertEqual(W.window_title([]), "")

    def test_one_answer_everywhere(self):
        from sonata2.shell import dock, switcher, topbar
        src = inspect.getsource(dock.Dock._sync)
        self.assertIn("windowapps.describe(", src)
        self.assertIn("_sync_titles(", src)
        self.assertIn("tile.label.set_text(name)", inspect.getsource(dock.Dock._sync_titles))   # renamed with its window
        self.assertIn("windowapps.describe(", inspect.getsource(switcher))
        self.assertIn("windowapps.describe(", inspect.getsource(topbar.Bar._active_changed))


if __name__ == "__main__":
    unittest.main()
