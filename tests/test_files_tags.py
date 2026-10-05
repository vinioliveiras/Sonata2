"""Vini (Files vs Finder): no tags. Colour tags in the file's xattr
(user.xdg.tags), dots next to names, a row of colours in the right-click
menu, the sidebar's Tags listing what has each one."""
import inspect
import os
import tempfile
import time
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2.files import folder, tags  # noqa: E402


def _xattrs_work(d):
    p = os.path.join(d, ".probe")
    open(p, "w").close()
    try:
        os.setxattr(p, tags.XATTR, b"x")
        return True
    except OSError:
        return False
    finally:
        os.unlink(p)


class TagsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(dir=os.path.expanduser("~"))
        if not _xattrs_work(self.d):
            self.skipTest("no user xattrs here")
        self.idx = os.path.join(self.d, "index.json")
        p = mock.patch.object(tags, "index_path", return_value=self.idx)
        p.start()
        self.addCleanup(p.stop)

    def file(self, name):
        p = os.path.join(self.d, name)
        open(p, "w").close()
        return p

    def test_parse(self):
        self.assertEqual(tags.parse("Red, Work,,Red"), ["Red", "Work"])
        self.assertEqual(tags.parse(b""), [])
        self.assertEqual(tags.colors(["Work", "Blue", "Red"]), [("Red", "#ff3b30"), ("Blue", "#007aff")])

    def test_toggle_keeps_other_tags_and_is_listed(self):
        a, b = self.file("a"), self.file("b")
        os.setxattr(a, tags.XATTR, "Trabalho é".encode())
        self.assertEqual(tags.toggle([a, b], "Red", True), [])
        self.assertEqual(tags.read(a), ["Trabalho é", "Red"])
        self.assertEqual(tags.state([a, b], "Red"), "all")
        tags.toggle([b], "Red", False)
        self.assertEqual(tags.state([a, b], "Red"), "some")
        self.assertFalse(os.listxattr(b))                    # none left: the attribute goes
        self.assertEqual(tags.tagged("Red"), [a])

    def test_gio_listing_reads_them(self):
        a = self.file("a")
        tags.write(a, ["Green", "Trabalho é"])
        info = Gio.File.new_for_path(a).query_info(folder.ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        self.assertEqual(tags.of_info(info), ["Green", "Trabalho é"])
        self.assertEqual(tags.of_info(Gio.FileInfo()), [])

    def test_index_follows_moves_and_drops_the_gone(self):
        os.makedirs(os.path.join(self.d, "dir"))
        a = self.file("dir/a")
        tags.write(a, ["Blue"])
        new = os.path.join(self.d, "moved")
        os.rename(os.path.join(self.d, "dir"), new)
        tags.moved([(Gio.File.new_for_path(os.path.join(self.d, "dir")), Gio.File.new_for_path(new))])
        self.assertEqual(tags.tagged("Blue"), [os.path.join(new, "a")])
        os.unlink(os.path.join(new, "a"))
        self.assertEqual(tags.tagged("Blue"), [])

    def test_sidebar_locations(self):
        u = tags.uri("Red")
        self.assertIn(u, folder.VIRTUAL)
        self.assertIn(folder.RECENTS, folder.VIRTUAL)
        self.assertNotIn("file:///", folder.VIRTUAL)
        self.assertEqual(folder.display_name(u), "Red")
        self.assertEqual(tags.tag_of(u), "Red")
        a = self.file("a")
        tags.write(a, ["Red"])
        got = []
        f = folder.Folder(got.append, lambda *_e: got.append(None))
        f.load(u)
        end = time.time() + 5
        while not got and time.time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(got, [u])
        self.assertEqual([f.store.get_item(i).get_name() for i in range(f.store.get_n_items())], ["a"])


class TagsUiTest(unittest.TestCase):
    def test_dots(self):
        box = tags.dots_box()
        tags.show(box, ["Work"])
        self.assertFalse(box.get_visible())
        tags.show(box, ["Blue", "Red"])
        self.assertTrue(box.get_visible())
        kids, c = [], box.get_first_child()
        while c is not None:
            kids.append(c.get_css_classes()[-1])
            c = c.get_next_sibling()
        self.assertEqual(kids, ["fs-tag-red", "fs-tag-blue"])

    def test_menu_row(self):
        from sonata2.ui import menu
        picked = []
        win = Gtk.Window()
        anchor = Gtk.Label(label="x")
        win.set_child(anchor)
        pop = menu.popup(anchor, [[menu.Item("Tags", widget=lambda p: tags.menu_row(
            p, {"Red": "all"}, lambda t, on: picked.append((t, on))))]])
        self.assertIsNotNone(pop)
        row = tags.menu_row(pop, {"Red": "all", "Blue": "some"}, lambda *a: None)
        n, c = 0, row.get_first_child()
        while c is not None:
            n += 1
            c = c.get_next_sibling()
        self.assertEqual(n, len(tags.COLORS))
        pop.popdown()
        win.destroy()

    def test_wiring(self):
        from sonata2.files import ops, sidebar, views, window
        self.assertIn("tags.menu_row", inspect.getsource(window.FilesWindow._context_menu))
        self.assertIn("tags.moved", inspect.getsource(ops))
        self.assertIn('self._head("Tags")', inspect.getsource(sidebar.Sidebar.rebuild))
        self.assertEqual(inspect.getsource(views).count("tags.show("), 3)


if __name__ == "__main__":
    unittest.main()
