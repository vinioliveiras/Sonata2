"""Vini: renaming an item in icons resized the grid and its items, and
adding a tag grew the cells. The name's two lines and the tags' row are
always kept: a cell's size never changes."""
import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2.files import tags  # noqa: E402
from sonata2.files import views as V  # noqa: E402
from sonata2.files.folder import ATTRS  # noqa: E402


def _info(path):
    f = Gio.File.new_for_path(path)
    i = f.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
    i.set_attribute_object("sonata::file", f)
    return i


def _size(w):
    return w.measure(Gtk.Orientation.HORIZONTAL, -1)[1], w.measure(Gtk.Orientation.VERTICAL, -1)[1]


class IconCellTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.txt")
        open(p, "w").close()
        self.info = _info(p)
        store = Gio.ListStore(item_type=Gio.FileInfo)
        store.append(self.info)
        self.iv = V.IconsView(store, lambda i: None)

    def cell(self):
        holder = type("Item", (), {})()
        holder.set_child = lambda c: setattr(holder, "child", c)
        holder.get_child = lambda: holder.child
        holder.get_item = lambda: self.info
        self.iv._setup(None, holder)
        self.iv._bind(None, holder)
        return holder.child

    def test_tags_and_rename_keep_the_size(self):
        cell = self.cell()
        before = _size(cell)
        tags.show(cell.tags, ["Red", "Blue", "Green"])
        self.assertEqual(_size(cell), before)
        V._inline_rename(cell, self.info, lambda *a: None)
        self.assertEqual(_size(cell), before)
        entry = cell.over.get_last_child()
        self.assertIsInstance(entry, Gtk.Entry)
        entry.emit("activate")                       # done: the name back
        GLib.MainContext.default().iteration(False)
        self.assertEqual(cell.lbl.get_opacity(), 1)
        self.assertEqual(_size(cell), before)

    def test_reserved_dots_row(self):
        box = tags.dots_box(reserve=True)
        tags.show(box, [])
        self.assertTrue(box.get_visible())
        plain = tags.dots_box()
        tags.show(plain, [])
        self.assertFalse(plain.get_visible())        # list / columns: no room taken


if __name__ == "__main__":
    unittest.main()
