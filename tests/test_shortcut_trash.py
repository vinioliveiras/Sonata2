"""Vini: a Desktop shortcut to an app, deleted (dragged to the Dock's
Trash), asked to uninstall the app. Only an installed app's own entry (in
an applications folder) is uninstalled; a shortcut just goes to the Trash.
And the shortcut's badge: a smaller arrow with a short shaft, centred."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from sonata2.shell import dock_drop  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def entry(folder, name="org.example.App.desktop"):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Example\nExec=true\n")
    return Gio.File.new_for_path(p)


class ShortcutTrashTest(unittest.TestCase):
    def test_installed_vs_shortcut(self):
        base = tempfile.mkdtemp()
        app = entry(os.path.join(base, "share", "applications"))
        desk = os.path.join(base, "Desktop")
        os.makedirs(desk)
        link = os.path.join(desk, "org.example.App.desktop")
        os.symlink(app.get_path(), link)
        self.assertTrue(dock_drop._installed(app))
        self.assertFalse(dock_drop._installed(Gio.File.new_for_path(link)))        # a shortcut
        self.assertFalse(dock_drop._installed(entry(os.path.join(base, "Documents"))))

    def test_drop_on_trash_never_uninstalls_a_shortcut(self):
        src = (ROOT / "sonata2" / "shell" / "dock_drop.py").read_text()
        part = src[src.index("def attach_trash"):src.index("def _uninstall_apps")]
        self.assertIn("all(_installed(f) for f in dropped)", part)
        self.assertNotIn("all(_is_app(f) for f in dropped)", part)
        self.assertIn("files = [f for f in dropped if not _installed(f)]", part)   # installed ones never trashed

    def test_add_to_desktop_links_the_launcher(self):
        menu = (ROOT / "sonata2" / "shell" / "dock_menu.py").read_text()
        self.assertIn("add_to_desktop(app_filename(info))", menu)

    def test_badge(self):
        svg = (ROOT / "sonata2" / "data" / "icons" / "Sonata" / "actions" / "symbolic" /
               "sonata-shortcut-arrow-symbolic.svg").read_text()
        self.assertIn('fill-rule="evenodd"', svg)
        views = (ROOT / "sonata2" / "files" / "views.py").read_text()
        self.assertIn('icon_name="sonata-shortcut-arrow-symbolic"', views)
        self.assertIn("-gtk-icon-size: 11px; min-width: 14px", views)


if __name__ == "__main__":
    unittest.main()
