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
        w.set_view("list", save=False)
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
