"""Launchpad UI tests on a virtual display (xvfb-run python3 -m unittest tests.test_launchpad).
Needs at least 3 installed apps; uses a temporary XDG_CONFIG_HOME."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2 import launchpad_model as M  # noqa: E402
from sonata2.shell import launchpad as L  # noqa: E402


def settle(ms=250):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class LaunchpadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        from sonata2 import config
        config.save("launchpad", {"pages": [], "hidden": []})       # each test from a fresh layout
        self.win = L.Launchpad(None)
        if len(self.win.model.all_apps()) < 3:
            self.skipTest("needs 3 installed apps")
        self.win.set_default_size(1280, 800)
        self.win.open_launchpad()
        settle(600)
        settle(600)                              # the grid fits the window (may re-layout once)
        self.grid = self.win.carousel.get_nth_page(0)

    def tearDown(self):
        self.win.destroy()

    def centre(self, index):
        w, h = self.grid.get_width(), self.grid.get_height()
        return ((index % M.COLS) + 0.5) * w / M.COLS, ((index // M.COLS) + 0.5) * h / M.ROWS

    def drag(self, item):
        self.win._drag = {"item": item, "widget": self.win._item_widget(item), "folder": None, "target": None}

    def test_reorder(self):
        first = self.win.model.pages[0][0]
        self.drag(first)
        x, y = self.centre(2)
        self.win.drag_over(self.grid, x + self.grid.get_width() / M.COLS * 0.4, y)   # beside the icon
        self.assertEqual(self.win.model.pages[0].index(first), 0)     # icons wait a moment...
        self.win._reorder_to(0, 2)                                     # ...then make way (REORDER_HOLD_MS)
        self.assertEqual(self.win.model.pages[0].index(first), 2)

    def test_edge_flips_page(self):
        if self.win.carousel.get_n_pages() < 2:
            self.skipTest("needs two pages")
        self.drag(self.win.model.pages[0][0])
        x = self.win.get_width() - 5                            # held at the right side
        self.win._edge_x = x
        self.win._edge_flip(x)
        settle(L.FLIP_HOLD_MS + 700)
        self.win._edge_x = None
        self.assertGreater(self.win.carousel.get_position(), 0.5)

    def test_make_folder(self):
        a, b = self.win.model.pages[0][0], self.win.model.pages[0][1]
        self.drag(b)
        self.win.drag_over(self.grid, *self.centre(0))
        settle(L.FOLDER_HOLD_MS + 500)          # slow software rendering in CI
        self.assertTrue(self.win._drag["target"].has_css_class("folder-target"))
        self.win.drag_drop(self.grid, *self.centre(0))
        item = self.win.model.pages[0][0]
        self.assertTrue(M.is_folder(item))
        self.assertEqual(item["apps"], [a, b])

    def test_search_and_escape(self):
        name = self.win.installed[self.win.model.all_apps()[0]].get_display_name()
        self.win.search.set_text(name[:3])
        settle(800)
        self.assertEqual(self.win.stack.get_visible_child_name(), "results")
        self.assertGreaterEqual(self.win.selected, 0)
        from gi.repository import Gdk
        self.win._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertEqual(self.win.search.get_text(), "")

    def _folder(self):
        a, b = self.win.model.pages[0][0], self.win.model.pages[0][1]
        self.win.model.make_folder(a, b, "Work")
        self.win.render()
        settle(200)
        folder = self.win.model.pages[0][0]
        self.win._open_folder(folder)
        settle(300)
        from gi.repository import Gtk
        title = self.win.folder_view[0].get_first_child()
        self.assertIsInstance(title, Gtk.EditableLabel)
        return folder, title

    def test_rename_shows_live_and_sticks(self):
        """Vini: the folder's name in the grid follows the typing (it used to
        keep the old name: the tile was reused as it was)."""
        folder, title = self._folder()
        tile = self.win._item_widget(folder)
        title.start_editing()
        settle(50)
        title.set_text("Games")
        self.assertEqual(tile.label.get_label(), "Games")                 # live
        title.stop_editing(True)
        settle(100)
        self.assertEqual(folder["folder"], "Games")
        self.assertEqual(self.win._item_widget(folder).label.get_label(), "Games")
        title.start_editing()
        title.set_text("Nope")
        title.stop_editing(False)                                          # Esc: the old name everywhere
        settle(100)
        self.assertEqual(self.win._item_widget(folder).label.get_label(), "Games")

    def test_name_centred_after_renaming(self):
        """Vini: the folder's name wasn't centred once edited (its label kept
        xalign 0 inside the entry's width)."""
        folder, title = self._folder()
        from gi.repository import Gtk

        def centre():
            ok, b = title.compute_bounds(title.get_parent())
            return round(b.get_x() + b.get_width() / 2)
        mid = round(title.get_parent().get_width() / 2)
        self.assertLessEqual(abs(centre() - mid), 1)
        title.start_editing()
        settle(50)
        title.set_text("A Much Longer Folder Name")
        title.stop_editing(True)
        settle(300)
        self.assertLessEqual(abs(centre() - mid), 1)
        labels = []

        def walk(w):
            while w is not None:
                if isinstance(w, Gtk.Label):
                    labels.append(w.get_xalign())
                walk(w.get_first_child())
                w = w.get_next_sibling()
        walk(title.get_first_child())
        self.assertEqual(set(labels), {0.5})

    def test_arrow_keys_move_in_the_name(self):
        """Vini: arrows went to the grid while renaming; they move the cursor now."""
        from gi.repository import Gdk
        folder, title = self._folder()
        title.start_editing()
        settle(100)
        self.assertIs(self.win._editing_title(), title)
        for k in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End):
            self.assertFalse(self.win._key(None, k, 0, 0))                # left to the text
        self.assertTrue(self.win._key(None, Gdk.KEY_Escape, 0, 0))         # Esc: stops renaming only
        self.assertFalse(title.get_editing())
        self.assertIsNotNone(self.win.folder_view)
        self.assertIsNone(self.win._editing_title())
        self.assertTrue(self.win._key(None, Gdk.KEY_Right, 0, 0))          # the grid's again

    def test_linked_folder_follows_both_ways(self):
        """Vini: a folder renamed in Launchpad is renamed in the Dock, and back."""
        import json
        from sonata2 import config
        folder, title = self._folder()
        folder["link"] = "L5"
        config.save("dock", {"folders": {"1": {"name": "Work", "apps": list(folder["apps"]), "link": "L5"}},
                             "pinned": ["folder:1"]})
        title.start_editing()
        title.set_text("Games")
        title.stop_editing(True)
        with open(os.path.join(config.CONFIG_DIR, "dock.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["folders"]["1"]["name"], "Games")
        from sonata2 import folder_link
        folder_link.to_launchpad({"1": {"name": "Tools", "apps": list(folder["apps"]), "link": "L5"}})
        self.win._config_changed()                                          # (config.watch calls it)
        settle(100)
        item = next(it for it in self.win.model.pages[0] if M.is_folder(it) and it.get("link") == "L5")
        self.assertEqual(item["folder"], "Tools")
        self.assertEqual(self.win._item_widget(item).label.get_label(), "Tools")
        config.save("dock", {})

    def test_toggle_hides(self):
        self.win.close_launchpad()
        settle(L.CLOSE_MS + 200)
        self.assertFalse(self.win.get_visible())


if __name__ == "__main__":
    unittest.main()
