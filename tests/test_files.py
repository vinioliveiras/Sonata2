"""Files: Finder-style formatting and folder loading (xvfb):
python3 -m unittest tests.test_files"""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

from sonata2.files import folder, views  # noqa: E402


def spin(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


def info(size, directory=False):
    i = Gio.FileInfo()
    i.set_size(size)
    i.set_file_type(Gio.FileType.DIRECTORY if directory else Gio.FileType.REGULAR)
    return i


class FolderPrefsTest(unittest.TestCase):
    def test_only_what_you_chose_and_bounded(self):
        from unittest import mock
        from sonata2.files import folderprefs as P
        self.assertEqual(P.get("file:///nowhere"), {})
        P.remember("file:///x", sort=("Kind", True))
        self.assertEqual(P.get("file:///x"), {"sort": ("Kind", True)})
        P.remember("file:///x", view="list")
        self.assertEqual(P.get("file:///x"), {"view": "list", "sort": ("Kind", True)})
        with mock.patch.object(P, "MAX", 2):
            P.remember("file:///y", view="icons")
            P.remember("file:///z", view="icons")
            self.assertEqual(P.get("file:///x"), {})              # the oldest went
            self.assertEqual(P.get("file:///z"), {"view": "icons"})


class FilesTest(unittest.TestCase):
    def test_size(self):
        self.assertEqual([views.size(info(n)) for n in (0, 653, 12_400, 1_430_000, 2_000_000_000)],
                         ["Zero bytes", "653 bytes", "12 KB", "1.4 MB", "2.0 GB"])
        self.assertEqual(views.size(info(0, True)), "--")

    def test_load_sorted_and_live(self):
        d = tempfile.mkdtemp()
        for n in ("photo-10.jpg", "photo-2.jpg", "B", "a", ".hidden"):
            open(os.path.join(d, n), "w").close()
        loop = GLib.MainLoop()
        f = folder.Folder(lambda _u: loop.quit(), lambda _u, e: self.fail(e.message))
        f.load(Gio.File.new_for_path(d).get_uri())
        GLib.timeout_add(3000, loop.quit)
        loop.run()
        names = lambda: [f.store.get_item(i).get_name() for i in range(f.store.get_n_items())]  # noqa: E731
        self.assertEqual(names(), ["a", "B", "photo-2.jpg", "photo-10.jpg"])
        open(os.path.join(d, "c"), "w").close()              # live insert keeps the order
        end = GLib.get_monotonic_time() + 3_000_000
        while "c" not in names() and GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(names(), ["a", "B", "c", "photo-2.jpg", "photo-10.jpg"])



class TabsTest(unittest.TestCase):
    """Finder tabs: each tab keeps its folder, history and view mode."""

    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.filestest")
        cls.app.register(None)

    def setUp(self):
        from sonata2.files.window import FilesWindow
        self.d = tempfile.mkdtemp()
        for n in ("A", "B"):
            os.mkdir(os.path.join(self.d, n))
        open(os.path.join(self.d, "f.txt"), "w").close()
        self.uri = lambda *p: Gio.File.new_for_path(os.path.join(self.d, *p)).get_uri()
        self.win = FilesWindow(self.app, self.uri())
        self.win.present()
        self.assertTrue(spin(lambda: self.win.folder.store.get_n_items() == 3))

    def tearDown(self):
        self.win.destroy()

    def test_tabs_keep_their_own_state(self):
        w = self.win
        self.assertFalse(w.strip.get_reveal_child())            # one tab: no strip
        w.go(self.uri("A"))
        first = w.tab
        second = w.new_tab()                                    # same folder, in front
        self.assertIs(w.tab, second)
        self.assertEqual(second.uri, self.uri("A"))
        self.assertTrue(w.strip.get_reveal_child())
        w.set_view("list")                                      # your choice: B opens in it too (the default)
        w.go(self.uri("B"))
        self.assertTrue(spin(lambda: w.get_title() == "B"))
        w.select_tab(first)
        self.assertEqual((w.get_title(), w.tab.view_id, w.history), ("A", "icons", [self.uri(), self.uri("A")]))
        self.assertTrue(w.view_buttons["icons"].get_active())
        self.assertTrue(w.back.get_sensitive())
        w.cycle_tab(1)
        self.assertIs(w.tab, second)
        self.assertEqual((w.tab.view_id, w.history), ("list", [self.uri("A"), self.uri("B")]))
        w.move_tab(second, 0)
        self.assertEqual(w.tabs, [second, first])
        self.assertEqual(w.strip.buttons, [second.button, first.button])
        w.close_tab()                                           # the front one; its neighbour comes forward
        self.assertEqual(w.tabs, [first])
        self.assertIs(w.tab, first)
        self.assertFalse(w.strip.get_reveal_child())

    def test_view_remembered_per_folder(self):
        """Each folder opens in the view you chose there (Finder)."""
        w = self.win
        w.go(self.uri("A"))
        w.set_view("list")
        w.go(self.uri("B"))
        w.set_view("icons")
        w.go(self.uri("A"))
        self.assertEqual(w.tab.view_id, "list")
        w.go(self.uri("B"))
        self.assertEqual(w.tab.view_id, "icons")
        w.go_back()
        self.assertEqual(w.tab.view_id, "list")
        tab = w.new_tab(self.uri("A"), select=False)            # a tab behind gets it too
        self.assertEqual(tab.view_id, "list")

    def test_sort_remembered_per_folder(self):
        from gi.repository import Gtk
        w = self.win
        w.go(self.uri("A"))
        lv = w.views["list"]
        cols = lv.view.get_columns()
        size = next(cols.get_item(i) for i in range(cols.get_n_items()) if cols.get_item(i).get_title() == "Size")
        lv.view.sort_by_column(size, Gtk.SortType.DESCENDING)    # a header click
        w.go(self.uri("B"))
        self.assertEqual(lv.sort_state(), ("Name", False))      # never sorted here: by name
        w.go(self.uri("A"))
        self.assertEqual(lv.sort_state(), ("Size", True))

    def test_hidden_files_per_folder(self):
        """Vini: an eye in the toolbar shows this folder's hidden files; off by
        default, remembered for the folder."""
        w = self.win
        open(os.path.join(self.d, ".secret"), "w").close()
        names = lambda: {w.folder.store.get_item(i).get_name() for i in range(w.folder.store.get_n_items())}
        spin(lambda: False, 300)
        self.assertNotIn(".secret", names())
        self.assertFalse(w.hidden_btn.get_active())
        w.hidden_btn.set_active(True)                              # the eye
        self.assertTrue(spin(lambda: ".secret" in names()))
        w.go(self.uri("A"))
        self.assertTrue(spin(lambda: w.folder.uri == self.uri("A")))
        self.assertFalse(w.hidden_btn.get_active())                # another folder: off
        w.go_back()
        self.assertTrue(spin(lambda: ".secret" in names()))      # remembered here
        self.assertTrue(w.hidden_btn.get_active())

    def test_rename_the_folder_from_its_title(self):
        """Vini: a click on the folder's name in the toolbar renames it."""
        w = self.win
        w.go(self.uri("A"))
        self.assertTrue(spin(lambda: w.folder.uri == self.uri("A")))
        w.rename_folder()
        entry = w._title_entry
        self.assertIsNotNone(entry)
        entry.set_text("Renamed")
        entry.emit("activate")
        self.assertTrue(spin(lambda: w.folder.uri == self.uri("Renamed")))
        self.assertTrue(os.path.isdir(os.path.join(self.d, "Renamed")))
        self.assertEqual(w.history, [self.uri(), self.uri("Renamed")])      # back still works
        self.assertTrue(w.title.get_visible())
        w.go(folder.RECENTS)
        self.assertTrue(spin(lambda: w.folder.uri == folder.RECENTS))
        w.rename_folder()                                                  # not a folder you can rename
        self.assertIsNone(getattr(w, "_title_entry", None))

    def test_sort_by_in_icons(self):
        """View > Sort By works in icons too, and is the folder's one order (list included)."""
        w = self.win
        open(os.path.join(self.d, "big.txt"), "w").write("x" * 5000)
        self.assertTrue(spin(lambda: w.folder.store.get_n_items() == 4))
        icons = w.views["icons"]
        names = lambda: [icons.model.get_item(i).get_name() for i in range(icons.model.get_n_items())]
        self.assertEqual(names(), ["A", "B", "big.txt", "f.txt"])
        w.sort_by("Size")                  # biggest first, after the folders (Vini: folders always first)
        self.assertEqual(names()[:3], ["A", "B", "big.txt"])
        self.assertEqual(w.views["list"].sort_state(), ("Size", True))
        w.go(self.uri("A"))
        self.assertEqual(icons.sort_state(), ("Name", False))
        w.go_back()
        self.assertEqual(icons.sort_state(), ("Size", True))
        self.assertEqual(names()[2], "big.txt")

    def test_keys_and_background_tab(self):
        w = self.win
        info = next(w.folder.store.get_item(i) for i in range(3) if w.folder.store.get_item(i).get_name() == "A")
        w.open_in_new_tab(info, select=False)                   # middle-click: behind
        self.assertEqual(len(w.tabs), 2)
        self.assertEqual(w.tab.uri, self.uri())
        self.assertTrue(spin(lambda: w.tabs[1].button.label.get_label() == "A"))
        C, S = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK
        self.assertTrue(w._tab_keys(None, Gdk.KEY_Tab, 0, C))
        self.assertIs(w.tab, w.tabs[1])
        self.assertTrue(w._tab_keys(None, Gdk.KEY_braceleft, 0, C | S))
        self.assertIs(w.tab, w.tabs[0])
        self.assertTrue(w._tab_keys(None, Gdk.KEY_t, 0, Gdk.ModifierType.SUPER_MASK))
        self.assertEqual(len(w.tabs), 3)
        self.assertTrue(w._tab_keys(None, Gdk.KEY_w, 0, C))
        self.assertEqual(len(w.tabs), 2)

    def test_press_on_item_drags_not_rubberband(self):
        """A press anywhere on an item is the item (drag); only empty space
        starts the rubber band."""
        from gi.repository import Graphene
        w = self.win
        v = w.view
        self.assertTrue(spin(lambda: len(v._cells) == 3 and all(b.get_width() for b in v._cells.values())))
        spin(lambda: False, 300)                  # laid out and drawn
        info, box = next(iter(v._cells.items()))
        ok, pt = box.compute_point(v.widget, Graphene.Point().init(box.get_width() / 2, 10))
        self.assertTrue(ok)
        self.assertIs(v.info_at(v.widget, pt.x, pt.y), info)
        self.assertIsNotNone(v._drag_prepare(v.widget, pt.x, pt.y))
        self.assertIsNone(v.info_at(v.widget, v.widget.get_width() - 5, v.widget.get_height() - 5))
        self.assertIsNone(v._drag_prepare(v.widget, v.widget.get_width() - 5, v.widget.get_height() - 5))
        v._drag_end()

    def test_drop_in_same_folder_is_silent(self):
        from sonata2.files import ops
        w = self.win
        started = []
        real = ops.Transfer
        ops.Transfer = lambda *a, **k: started.append(a)
        try:
            f = Gio.File.new_for_path(os.path.join(self.d, "f.txt"))
            a = Gio.File.new_for_path(os.path.join(self.d, "A"))
            here = Gio.File.new_for_uri(w.location())
            self.assertFalse(w.drop([f], here))                 # its own folder
            self.assertFalse(w.drop([f], here, copy=True))      # even with Ctrl: no copy
            self.assertFalse(w.drop([a], a))                    # onto itself
            self.assertFalse(w.drop([a, f], a))                 # selection onto one of its folders
            self.assertEqual(started, [])
            self.assertTrue(w.drop([f], a))                     # a real move
            self.assertEqual(len(started), 1)
        finally:
            ops.Transfer = real


if __name__ == "__main__":
    unittest.main()
