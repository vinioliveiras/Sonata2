"""Vini: an installer run with Faugus (Proton) showed as "steam_app_default"
in the Dock. Windows programs run with Proton outside Steam get their
window's title, or their launcher's shortcut (name and icon)."""
import inspect
import unittest
from unittest import mock

from sonata2 import steamgames as S


class FakeInfo:
    def __init__(self, name, cmd):
        self.name, self.cmd = name, cmd

    def get_display_name(self):
        return self.name

    def get_commandline(self):
        return self.cmd


class ProtonAppTest(unittest.TestCase):
    def test_title_when_no_shortcut(self):
        self.assertTrue(S.is_proton_app("steam_app_default"))
        self.assertIsNone(S.appid("steam_app_default"))
        with mock.patch("sonata2.apps._scan", {"x": FakeInfo("Firefox", "firefox %u")}):
            name, icon = S.shown("steam_app_default", "FL Studio 2026 Setup")
        self.assertEqual(name, "FL Studio 2026 Setup")
        self.assertIn("wine", icon.get_names())
        with mock.patch("sonata2.apps._scan", {"x": FakeInfo("Firefox", "firefox %u")}):
            self.assertEqual(S.shown("steam_app_default", "")[0], "Windows App")

    def test_faugus_shortcut(self):
        scan = {"fl": FakeInfo("FL Studio 2026", "faugus-launcher --game fl-studio-2026"),
                "fl2": FakeInfo("FL", "faugus-launcher --game fl"),
                "other": FakeInfo("FL Studio 2026", "flatpak run something")}
        with mock.patch("sonata2.apps._scan", scan), mock.patch("sonata2.icons.app_icon", lambda i: i.name):
            self.assertEqual(S.shown("steam_app_default", "FL Studio 2026 - untitled.flp"),
                             ("FL Studio 2026", "FL Studio 2026"))

    def test_dock_switcher_menubar(self):
        from sonata2.shell import dock, switcher, topbar
        self.assertIn("steamgames.is_proton_app(key)", inspect.getsource(dock.Dock._sync))
        self.assertIn("is_proton_app", inspect.getsource(switcher))
        self.assertIn("steamgames.shown(", inspect.getsource(topbar.Bar._active_changed))


if __name__ == "__main__":
    unittest.main()
