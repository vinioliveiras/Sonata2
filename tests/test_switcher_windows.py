"""Vini: Alt+Tab stays as it is, with one icon per window for an app that
has several (like Windows); picking one brings that window alone forward.
One press still goes to the previous app.
python3 -m unittest tests.test_switcher_windows"""
import types
import unittest
from unittest import mock

from sonata2.shell import switcher as SW


def win(app, title, at, minimized=False):
    return types.SimpleNamespace(app_id=app, title=title, focused_at=at, minimized=minimized)


class EntriesTest(unittest.TestCase):
    def setUp(self):
        self.code1, self.code2 = win("code", "a.py", 30), win("code", "b.py", 10)
        self.term = win("term", "~", 20)
        self.groups = {"code": [self.code2, self.code1], "term": [self.term]}

    def test_one_entry_per_window_last_used_first(self):
        keys, wins = SW.Switcher._entries(None, ["code", "term"], self.groups)
        self.assertEqual(keys, ["code", "code", "term"])
        self.assertEqual(wins, [self.code1, self.code2, None])       # a lone window: the app as before

    def sw(self):
        sw = SW.Switcher.__new__(SW.Switcher)
        sw.groups = self.groups
        sw.keys, sw.wins = SW.Switcher._entries(None, ["code", "term"], self.groups)
        sw.manager = mock.Mock()
        sw.get_visible = lambda: True
        sw.panel = mock.Mock()
        sw.panel.has_css_class.return_value = False
        sw._mark = lambda *a: None
        sw._close = lambda: None
        return sw

    def test_first_press_goes_to_the_previous_app(self):
        sw = self.sw()
        sw._open = lambda: None
        sw.get_visible = lambda: False                 # (opening)
        SW.Switcher.step(sw, 1)
        self.assertEqual(sw.keys[sw.index], "term")    # past the app in front's other window

    def test_picking_a_window_brings_it_alone(self):
        sw = self.sw()
        sw.index = 1                                   # code's other window
        SW.Switcher._switch(sw)
        sw.manager.activate.assert_called_once_with(self.code2)

    def test_picking_a_lone_app_as_before(self):
        sw = self.sw()
        sw.index = 2
        SW.Switcher._switch(sw)
        sw.manager.activate.assert_called_once_with(self.term)

    def test_opening_shows_an_icon_per_window(self):
        """What Alt+Tab opens on: Code's two windows, two icons named after
        them; the terminal's one window, one icon (the app)."""
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        sw = SW.Switcher.__new__(SW.Switcher)
        sw.manager = types.SimpleNamespace(toplevels=[self.code2, self.term, self.code1])
        sw.mru = ["code", "term"]
        sw.panel = Gtk.Box()
        sw.present = lambda: None
        with mock.patch.object(SW.GLib, "timeout_add", return_value=1), \
                mock.patch.object(SW.apps, "match_app_id", side_effect=lambda a: a):
            SW.Switcher._open(sw)
        self.assertEqual(sw.keys, ["code", "code", "term"])
        self.assertEqual(sw.wins, [self.code1, self.code2, None])
        self.assertEqual([b.name for b in sw.items[:2]], ["a.py", "b.py"])      # which window
        self.assertEqual(len(sw.items), 3)

    def test_only_one_app_with_two_windows(self):
        sw = self.sw()
        sw.groups = {"code": [self.code1, self.code2]}
        sw.keys, sw.wins = SW.Switcher._entries(None, ["code"], sw.groups)
        sw._open = lambda: None
        sw.get_visible = lambda: False
        SW.Switcher.step(sw, 1)
        self.assertIs(sw.wins[sw.index], self.code2)   # the other window


if __name__ == "__main__":
    unittest.main()
