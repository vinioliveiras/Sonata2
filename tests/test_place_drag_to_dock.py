"""Vini: a folder in Files -- and the places in its sidebar (Favorites,
Locations) -- can be dragged onto the Dock, where it becomes a stack. A
place is offered as a link only, so the drops that copy or move (Files'
lists, the Desktop, the Dock's Trash) never copy or move a whole folder.
xvfb-run -a python3 -m unittest tests.test_place_drag_to_dock"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from sonata2.files import sidebar as SB  # noqa: E402
from sonata2.shell import dock_drop as DD  # noqa: E402

C, M, L = Gdk.DragAction.COPY, Gdk.DragAction.MOVE, Gdk.DragAction.LINK


def fake_drop(actions):
    d = mock.Mock()
    d.get_actions.return_value = actions
    return d


class PlaceDragTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        from sonata2 import ui
        ui.setup()

    def test_sidebar_places_could_not_be_dragged_to_the_dock(self):
        home = Gio.File.new_for_path(os.path.expanduser("~")).get_uri()
        content = SB.place_drag(home)
        self.assertIsNotNone(content)
        self.assertTrue(content.ref_formats().contain_mime_type("text/uri-list"))     # what the Dock reads
        for uri in ("trash:///", "recent:///", "file:///no/such/folder", "volume:abc"):
            self.assertIsNone(SB.place_folder(uri), uri)
        sb = SB.Sidebar(lambda _u: None, Gtk.Label())
        rows = [r for r in sb._rows.values() if SB.place_folder(r.uri) is not None]
        self.assertTrue(rows)
        for r in rows:                                      # a link only: never copied or moved
            self.assertEqual(r.place_drag.get_actions(), L)
        self.assertFalse(hasattr(sb._rows["trash:///"], "place_drag"))

    def test_dock_takes_a_link_only_folder_but_not_on_its_icons_or_trash(self):
        link_only = fake_drop(L)
        self.assertEqual(DD._action(link_only, C, link=True), L)       # the plate: a stack
        self.assertEqual(DD._action(link_only, C, link=False), 0)      # an app's icon
        self.assertEqual(DD._action(link_only, M, link=False), 0)      # the Trash: never binned
        self.assertEqual(DD._action(fake_drop(C | M), C, link=True), C)  # a folder from Files' list
        self.assertEqual(DD._action(fake_drop(C | M), 0, link=True), 0)

    def test_place_dropped_on_another_place_isnt_moved_into_it(self):
        t = mock.Mock()
        t.get_current_drop.return_value = fake_drop(L)
        self.assertTrue(SB._link_only(t))
        self.assertEqual(SB._pin_action(t), L)                         # pinning still works
        t.get_current_drop.return_value = fake_drop(C | M)
        self.assertFalse(SB._link_only(t))
        self.assertEqual(SB._pin_action(t), C)

    def test_pinned_place_dragged_moves(self):
        a, b, c = (Gio.File.new_for_path(tempfile.mkdtemp()).get_uri() for _ in range(3))
        SB.write_bookmarks([(a, ""), (b, "Bee"), (c, "")])
        SB.pin([c], before=a)
        self.assertEqual(SB.read_bookmarks(), [(c, ""), (a, ""), (b, "Bee")])
        SB.pin([b], before=c)                                           # its label kept
        self.assertEqual(SB.read_bookmarks()[0], (b, "Bee"))
        SB.pin([a], before=a)                                           # next to itself: stays
        self.assertEqual([u for u, _l in SB.read_bookmarks()], [b, c, a])


if __name__ == "__main__":
    unittest.main()
