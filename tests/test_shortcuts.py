"""Shortcuts, like Windows (Vini): "Create Shortcut" in Files and on the
desktop, "Create Shortcut on Desktop" in Files, "Add to Desktop" for apps in
Launchpad and the Dock. A shortcut is a link named "x - Shortcut" with an
arrow on its icon; a shortcut to a shortcut leads to the original."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2.files import ops  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent / "sonata2"


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class MakeShortcutsTest(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.dest = tempfile.mkdtemp()
        self.file = os.path.join(self.src, "report.pdf")
        open(self.file, "w").close()
        self.folder = os.path.join(self.src, "Photos")
        os.mkdir(self.folder)

    def make(self, *paths, dest=None):
        return [f.get_path() for f in ops.make_shortcuts([Gio.File.new_for_path(p) for p in paths],
                                                         Gio.File.new_for_path(dest or self.dest))]

    def test_names_and_targets(self):
        a, b = self.make(self.file, self.folder)
        self.assertEqual(os.path.basename(a), "report.pdf - Shortcut")
        self.assertEqual(os.readlink(a), self.file)
        self.assertEqual(os.path.basename(b), "Photos - Shortcut")
        self.assertTrue(os.path.isdir(b))                                   # opens as the folder
        again, = self.make(self.file)
        self.assertEqual(os.path.basename(again), "report.pdf - Shortcut 2")

    def test_a_shortcut_to_a_shortcut_leads_to_the_original(self):
        first, = self.make(self.file)
        second, = self.make(first, dest=tempfile.mkdtemp())
        self.assertEqual(os.readlink(second), self.file)

    def test_an_apps_launcher_keeps_its_name(self):
        app = os.path.join(self.src, "org.example.App.desktop")
        with open(app, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Example\nExec=true\n")
        link, = self.make(app)
        self.assertEqual(os.path.basename(link), "org.example.App.desktop")
        self.assertEqual(ops.shortcut_name("a.desktop"), "a.desktop")
        self.assertEqual(ops.shortcut_name("a.txt"), "a.txt - Shortcut")

    def test_is_shortcut_reads_the_link(self):
        link, = self.make(self.file)
        info = Gio.File.new_for_path(link).query_info("standard::is-symlink", Gio.FileQueryInfoFlags.NONE, None)
        self.assertTrue(ops.is_shortcut(info))
        plain = Gio.File.new_for_path(self.file).query_info("standard::is-symlink", Gio.FileQueryInfoFlags.NONE,
                                                            None)
        self.assertFalse(ops.is_shortcut(plain))
        self.assertFalse(ops.is_shortcut(Gio.FileInfo()))                      # not asked: no arrow, no error

    def test_files_lists_links(self):
        from sonata2.files import folder
        self.assertIn("standard::is-symlink", folder.ATTRS)


class AddToDesktopTest(unittest.TestCase):
    def test_app_shortcut_on_the_desktop(self):
        from sonata2.shell import dock_menu
        desk = tempfile.mkdtemp()
        app = os.path.join(tempfile.mkdtemp(), "org.example.App.desktop")
        with open(app, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Example\nExec=true\n")
        with mock.patch("sonata2.shell.desktop.desktop_dir", return_value=Gio.File.new_for_path(desk)):
            made = dock_menu.add_to_desktop(app)
        self.assertEqual([f.get_basename() for f in made], ["org.example.App.desktop"])
        self.assertEqual(os.readlink(os.path.join(desk, "org.example.App.desktop")), app)
        self.assertEqual(dock_menu.add_to_desktop(""), [])

    def test_menus_offer_it(self):
        self.assertIn('Item("Add to Desktop", lambda: add_to_desktop(path))', (ROOT / "shell/launchpad.py").read_text())
        # the app's launcher (.desktop), never its program: that showed as a text file (Vini)
        self.assertIn('Item("Add to Desktop", lambda: add_to_desktop(app_filename(info)))',
                      (ROOT / "shell/dock_menu.py").read_text())
        files = (ROOT / "files/window.py").read_text()
        self.assertIn('Item("Create Shortcut", self.shortcut_selection', files)
        self.assertIn('Item("Create Shortcut on Desktop"', files)
        self.assertIn('Item("Create Shortcut", self.shortcut_selection)', (ROOT / "shell/desktop.py").read_text())


class DesktopBadgeTest(unittest.TestCase):
    def test_desktop_shortcut_shows_the_arrow(self):
        from sonata2 import config, ui
        from sonata2.shell import desktop as D
        ui.setup()
        d = tempfile.mkdtemp()
        open(os.path.join(d, "a.txt"), "w").close()
        config.save("desktop", {"positions": {}, "sort": "none"})
        with mock.patch.object(D, "desktop_dir", return_value=Gio.File.new_for_path(d)), \
                mock.patch.object(D, "_connected", return_value={"eDP-1"}):
            desk = D.Desktop(screen="eDP-1", main=True)
            desk.resized(1920, 1080)
            settle(400)
            desk.select([desk.items["a.txt"]])
            made = desk.shortcut_selection()
            self.assertEqual([f.get_basename() for f in made], ["a.txt - Shortcut"])
            settle(600)
            self.assertIn("a.txt - Shortcut", desk.items)
            self.assertTrue(desk.items["a.txt - Shortcut"].img.badge.get_visible())     # the arrow
            self.assertFalse(desk.items["a.txt"].img.badge.get_visible())


if __name__ == "__main__":
    unittest.main()
