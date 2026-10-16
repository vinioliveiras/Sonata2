"""Vini: after deleting items, the Trash showed itself inside ("Wastebasket",
"“/” can't be deleted") until reopened -- the monitor reports the folder's
own changes too (the Trash's item count), and they were added as an item.
xvfb-run -a python3 -m unittest tests.test_folder_self_event"""
import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2.files import folder  # noqa: E402


def settle(ms=400):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FolderSelfEventTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        open(os.path.join(self.d, "a.txt"), "w").close()
        self.f = folder.Folder(lambda u: None, lambda u, e: None)
        self.f.load(Gio.File.new_for_path(self.d).get_uri())
        settle()

    def names(self):
        return sorted(self.f.store.get_item(i).get_name() for i in range(self.f.store.get_n_items()))

    def test_the_folder_itself_is_never_an_item(self):
        E = Gio.FileMonitorEvent
        me = Gio.File.new_for_path(self.d)
        for ev in (E.ATTRIBUTE_CHANGED, E.CHANGES_DONE_HINT, E.CREATED):
            self.f._changed(None, me, None, ev)
        settle()
        self.assertEqual(self.names(), ["a.txt"])

    def test_its_items_still_follow(self):
        E = Gio.FileMonitorEvent
        open(os.path.join(self.d, "b.txt"), "w").close()
        self.f._changed(None, Gio.File.new_for_path(os.path.join(self.d, "b.txt")), None, E.CREATED)
        settle()
        self.assertEqual(self.names(), ["a.txt", "b.txt"])
        self.f._changed(None, Gio.File.new_for_path(os.path.join(self.d, "a.txt")), None, E.DELETED)
        self.assertEqual(self.names(), ["b.txt"])

    def test_a_file_elsewhere_is_ignored(self):
        E = Gio.FileMonitorEvent
        other = tempfile.mkdtemp()
        open(os.path.join(other, "x.txt"), "w").close()
        self.f._changed(None, Gio.File.new_for_path(os.path.join(other, "x.txt")), None, E.CREATED)
        settle()
        self.assertEqual(self.names(), ["a.txt"])

    def test_renamed_into_the_folder(self):
        E = Gio.FileMonitorEvent
        os.rename(os.path.join(self.d, "a.txt"), os.path.join(self.d, "c.txt"))
        self.f._changed(None, Gio.File.new_for_path(os.path.join(self.d, "a.txt")),
                        Gio.File.new_for_path(os.path.join(self.d, "c.txt")), E.RENAMED)
        settle()
        self.assertEqual(self.names(), ["c.txt"])

    def test_trash_root_and_items(self):
        f = folder.Folder(lambda u: None, lambda u, e: None)
        f._watched = Gio.File.new_for_uri("trash:///")
        self.assertFalse(f._inside(Gio.File.new_for_uri("trash:///")))
        self.assertTrue(f._inside(Gio.File.new_for_uri("trash:///report.pdf")))


if __name__ == "__main__":
    unittest.main()
