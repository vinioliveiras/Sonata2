"""Vini: the menu bar stopped responding. A menu opened over another one
never showed (GTK: "Tried to map a grabbing popup with a non-top most
parent") but stayed open, holding the bar's clicks. It is closed again."""
import time
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2.ui import menu  # noqa: E402


def run(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


class NeverShownTest(unittest.TestCase):
    def setUp(self):
        self.pop = mock.Mock()
        menu.OPEN.add(self.pop)

    def tearDown(self):
        menu.OPEN.discard(self.pop)

    def test_closed_when_it_never_shows(self):
        self.pop.get_visible.return_value, self.pop.get_mapped.return_value = True, False
        menu.watch_shown(self.pop)
        run(menu.SHOWN_WITHIN_MS + 150)
        self.pop.popdown.assert_called_once()

    def test_left_alone_when_shown_or_closed(self):
        self.pop.get_visible.return_value, self.pop.get_mapped.return_value = True, True
        menu.watch_shown(self.pop)
        run(menu.SHOWN_WITHIN_MS + 150)
        self.pop.popdown.assert_not_called()
        menu.OPEN.discard(self.pop)                       # closed meanwhile
        self.pop.get_mapped.return_value = False
        menu.watch_shown(self.pop)
        run(menu.SHOWN_WITHIN_MS + 150)
        self.pop.popdown.assert_not_called()

    def test_every_menu_and_panel_is_watched(self):
        import inspect
        from sonata2.ui import panel
        self.assertIn("watch_shown(pop)", inspect.getsource(menu.popup))
        self.assertIn("menu.watch_shown(pop)", inspect.getsource(panel.popup))


if __name__ == "__main__":
    unittest.main()
