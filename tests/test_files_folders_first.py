"""Vini: folders always come first in Files, whatever the sort (name, date,
size...) and its direction -- in every view."""
import os
import tempfile
import time
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2.files import views as V  # noqa: E402
from sonata2.files.folder import Folder  # noqa: E402


def _names(model):
    return [model.get_item(i).get_name() for i in range(model.get_n_items())]


class FoldersFirstTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        for n in ("a.txt", "zeta", "B.txt"):
            open(os.path.join(d, n), "w").close()
        for n in ("y", "c"):
            os.mkdir(os.path.join(d, n))
        got = []
        self.folder = Folder(got.append, lambda *_e: got.append(None))
        self.folder.load(Gio.File.new_for_path(d).get_uri())
        end = time.time() + 5
        while not got and time.time() < end:
            GLib.MainContext.default().iteration(False)

    def test_store_and_name_order(self):
        self.assertEqual(_names(self.folder.store), ["c", "y", "a.txt", "B.txt", "zeta"])

    def test_icons_every_sort(self):
        iv = V.IconsView(self.folder.store, lambda i: None)
        for title in V.SORTS:
            for desc in (False, True):
                iv.set_sort(title, desc)
                self.assertEqual(sorted(_names(iv.model)[:2]), ["c", "y"], (title, desc))
        iv.set_sort("Name", True)
        self.assertEqual(_names(iv.model), ["y", "c", "zeta", "B.txt", "a.txt"])

    def test_list_descending(self):
        lv = V.ListView(self.folder.store, lambda i: None)
        lv.set_sort("Name", True)
        self.assertEqual(_names(lv.sorted), ["y", "c", "zeta", "B.txt", "a.txt"])
        lv.set_sort("Size", False)
        self.assertEqual(sorted(_names(lv.sorted)[:2]), ["c", "y"])


if __name__ == "__main__":
    unittest.main()


class ShortNameTest(unittest.TestCase):
    """Vini: a long name made the right-click menu very wide."""

    def test_cut_keeps_extension(self):
        import inspect
        from sonata2.files import folder, window
        s = folder.short_name("Um video muito muito grande do whatsapp de 2024.mp4")
        self.assertTrue(s.endswith("….mp4"))
        self.assertLessEqual(len(s), folder.MENU_NAME_MAX)
        self.assertEqual(folder.short_name("a.mp4"), "a.mp4")
        self.assertTrue(folder.short_name("x" * 60).endswith("…"))
        self.assertIn("folder.short_name(", inspect.getsource(window.FilesWindow._context_menu))
