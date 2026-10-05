"""Vini (Files vs Finder / Windows): no path bar, no way to type where to go.
A path bar at the bottom (View > Show/Hide Path Bar, Ctrl+Alt+P) and Go to
Folder (Ctrl+Shift+G, Ctrl+L)."""
import inspect
import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from sonata2.files import pathbar as P  # noqa: E402


class PathBarTest(unittest.TestCase):
    def test_crumbs_from_home(self):
        c = P.crumbs(Gio.File.new_for_path("/home/vini/Documents/Work").get_uri(), home="/home/vini")
        self.assertEqual([n for _u, n, _i in c], ["vini", "Documents", "Work"])
        self.assertEqual(c[0][2], "user-home-symbolic")
        self.assertTrue(c[-1][0].endswith("/home/vini/Documents/Work"))

    def test_crumbs_outside_home(self):
        c = P.crumbs("file:///etc/X11", home="/home/vini")
        self.assertEqual([n for _u, n, _i in c], ["Computer", "etc", "X11"])
        self.assertEqual(len(P.crumbs("trash:///", home="/home/vini")), 1)

    def test_bar(self):
        b = P.PathBar(lambda u: None)
        b.set_uri(Gio.File.new_for_path(os.path.expanduser("~")).get_uri())
        self.assertIsNotNone(b.box.get_first_child())

    def test_resolve(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "f.txt"), "w").close()
        self.assertEqual(P.resolve(d), (Gio.File.new_for_path(d).get_uri(), None))
        self.assertEqual(P.resolve(os.path.join(d, "f.txt")), (Gio.File.new_for_path(d).get_uri(), "f.txt"))
        self.assertEqual(P.resolve("f.txt", Gio.File.new_for_path(d).get_uri())[1], "f.txt")   # relative
        self.assertIsNotNone(P.resolve("~"))
        self.assertIsNone(P.resolve(os.path.join(d, "nope")))
        self.assertIsNone(P.resolve(""))

    def test_wired(self):
        from sonata2.files import window
        src = inspect.getsource(window)
        for k in ('"<Control><Shift>g"', '"<Control>l", self.edit_address', '"<Control><Alt>p"', "Show Path Bar", "self.pathbar.set_uri("):
            self.assertIn(k, src)
        self.assertTrue(window.DEFAULTS["path_bar"])


class AddressTest(unittest.TestCase):
    """Vini: Files had no way to type the address. Ctrl+L or a click on the
    path bar's empty part turns it into a field; Return goes there."""

    def test_edit_then_go(self):
        bar = P.PathBar(lambda u: None)
        d = tempfile.mkdtemp()
        bar.set_uri(Gio.File.new_for_path(d).get_uri())
        went = []
        bar.edit(d, went.append)
        self.assertIs(bar.stack.get_visible_child(), bar.entry)
        bar.entry.set_text("/tmp")
        bar.entry.emit("activate")
        self.assertEqual(went, ["/tmp"])
        self.assertIsNone(bar.entry)
        self.assertIs(bar.stack.get_visible_child(), bar.box)  # the folders are back

    def test_hidden_files_shown_by_default(self):
        from sonata2.files import window
        self.assertTrue(window.DEFAULTS["show_hidden"])
        src = inspect.getsource(window.FilesWindow)
        self.assertIn('config.update("files", show_hidden=', src)
        self.assertIn('"Show Hidden Files"', src)


if __name__ == "__main__":
    unittest.main()
