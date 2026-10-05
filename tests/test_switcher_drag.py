"""Vini: Alt+Tab while dragging a video to WhatsApp left the switcher open for
good (the drag holds the keyboard): without the keyboard after a moment, it
switches to the app picked and closes -- the drag can be dropped there."""
import unittest
from unittest import mock

from sonata2.shell import switcher as SW


class NoKeyboardTest(unittest.TestCase):
    def fake(self, active, waited_ms):
        sw = mock.Mock()
        sw.get_visible.return_value = True
        sw.panel.has_css_class.return_value = False
        sw.is_active.return_value = active
        sw._opened_at = 0
        sw.HELD = SW.Switcher.HELD
        sw._held_modifiers.return_value = SW.Switcher.HELD       # Alt still held (if it could tell)
        with mock.patch.object(SW.GLib, "get_monotonic_time", return_value=waited_ms * 1000):
            keep = SW.Switcher._watch_mods(sw)
        return keep, sw

    def test_switches_when_it_never_gets_the_keyboard(self):
        keep, sw = self.fake(active=False, waited_ms=SW.NO_KEYBOARD_MS + 10)
        self.assertFalse(keep)
        sw._switch.assert_called_once()

    def test_waits_a_moment_first_and_with_the_keyboard_follows_alt(self):
        keep, sw = self.fake(active=False, waited_ms=100)
        self.assertTrue(keep)
        sw._switch.assert_not_called()
        keep, sw = self.fake(active=True, waited_ms=SW.NO_KEYBOARD_MS + 10)    # Alt held: stays open
        self.assertTrue(keep)
        sw._switch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
