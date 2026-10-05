"""Vini (Files vs Finder / Windows): the list's columns couldn't be chosen and
folders had no size. View > Show Columns (Date Created, Date Last Opened...)
and Calculate All Sizes."""
import inspect
import os
import tempfile
import time
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2.files import views as V  # noqa: E402
from sonata2.files.folder import ATTRS  # noqa: E402


def info_for(path):
    f = Gio.File.new_for_path(path)
    i = f.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
    i.set_attribute_object("sonata::file", f)
    return i


class ListColumnsTest(unittest.TestCase):
    def test_columns(self):
        lv = V.ListView(Gio.ListStore(item_type=Gio.FileInfo), lambda i: None)
        self.assertEqual(lv.shown_columns(), ["Date Modified", "Size", "Kind"])
        lv.show_columns(["Date Created", "Kind"])
        self.assertEqual(lv.shown_columns(), ["Date Created", "Kind"])
        self.assertIn("time::access", ATTRS)

    def test_folder_size(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "x"))
        with open(os.path.join(d, "x", "f"), "wb") as f:
            f.write(b"0" * 3000)
        info = info_for(d)
        self.assertEqual(V.size(info), "--")
        done = []
        V.count_dir(info, done.append)
        end = time.time() + 5
        while not done and time.time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(V.dir_size(info), 3000)
        self.assertNotEqual(V.size(info), "--")
        self.assertEqual(V._size_key(info), 3000)

    def test_dates(self):
        info = info_for(tempfile.mkdtemp())
        self.assertTrue(V.date(info, "time::access"))
        self.assertEqual(V.date(Gio.FileInfo(), "time::created"), "--")

    def test_menu(self):
        from sonata2.files import window
        src = inspect.getsource(window.FilesWindow._context_menu)
        self.assertIn("Show Columns", src)
        self.assertIn("Calculate All Sizes", src)


if __name__ == "__main__":
    unittest.main()
