"""Vini: with two keyboards on different layouts (the laptop's Portuguese,
the HyperX's US International -- Settings > Keyboard, a layout each) the
menu bar showed one input control, for Input Sources, which neither used:
"PT" while typing US, and switching there changed nothing. Now there is a
control per layout the keyboards type, each switching its keyboards only."""
import os
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import topbar as T  # noqa: E402

LAPTOP, ITE, MOUSE, HYPERX = ("AT Translated Set 2 keyboard", "ITE5570:00 048D:8051",
                              "HP, Inc HyperX Pulsefire Haste 2 Keyboard", "HP, Inc HyperX Alloy Origins 65")
VINI = {LAPTOP: "pt", ITE: "us(intl)", MOUSE: "us(intl)", HYPERX: "us(intl)"}


class GroupsTest(unittest.TestCase):
    def test_by_layout(self):
        kbs = [MOUSE, HYPERX, ITE, LAPTOP]
        self.assertEqual(T.keyboard_groups(kbs, VINI), [("us(intl)", [MOUSE, HYPERX, ITE]), ("pt", [LAPTOP])])
        self.assertEqual(T.keyboard_groups(kbs, {}), [(None, kbs)])                 # all on Input Sources
        self.assertEqual(T.keyboard_groups([LAPTOP, HYPERX], {HYPERX: "us"}),
                         [(None, [LAPTOP]), ("us", [HYPERX])])                      # Input Sources first
        self.assertEqual(T.keyboard_groups([], VINI), [])


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class ControlsTest(unittest.TestCase):
    def setUp(self):
        from sonata2 import ui
        ui.setup()
        self.layouts = mock.patch.object(T.Bar, "_layouts", return_value=["pt", "us(intl)"])
        self.layouts.start()
        self.bar = T.Bar(None)

    def tearDown(self):
        self.bar.stop()
        self.layouts.stop()

    def controls(self):
        return [b for b in [self.bar.input_btn] + self.bar._input_extra if b.get_visible()]

    def badge(self, b):
        return b.get_child().get_last_child().get_label()

    def test_two_keyboards_had_one_control_for_neither(self):
        self.bar._show_keyboards(T.keyboard_groups([LAPTOP, ITE, MOUSE, HYPERX], VINI))
        shown = self.controls()
        self.assertEqual(sorted(self.badge(b) for b in shown), ["PT", "US"])
        us = next(b for b in shown if self.badge(b) == "US")
        self.assertIn(HYPERX, us.get_tooltip_text())
        self.assertNotIn(LAPTOP, us.get_tooltip_text())
        self.assertIs(us.get_parent(), self.bar.input_btn.get_parent())                # side by side
        with mock.patch.object(T.system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))), \
                mock.patch.object(T.system, "set_keyboard_device_layout", return_value=True) as set_one, \
                mock.patch.object(T.Bar, "_read_keyboards"):
            self.bar._use_keyboard_layout(us.kbd_group[1], "pt")
        self.assertEqual(sorted(c.args[0] for c in set_one.call_args_list), sorted([ITE, MOUSE, HYPERX]))
        self.assertTrue(all(c.args[1] == "pt" for c in set_one.call_args_list))       # the laptop untouched

    def test_all_on_input_sources_as_before(self):
        self.bar._show_keyboards(T.keyboard_groups([LAPTOP, HYPERX], {}))
        self.assertEqual([self.badge(b) for b in self.controls()], ["PT"])
        self.assertIsNone(self.bar.input_btn.kbd_group[0])
        self.bar._show_keyboards(T.keyboard_groups([LAPTOP, ITE, MOUSE, HYPERX], VINI))
        self.bar._show_keyboards(T.keyboard_groups([LAPTOP], {}))                     # the others unplugged
        self.assertEqual(len(self.controls()), 1)
        self.assertEqual(self.bar._input_extra, [])


if __name__ == "__main__":
    unittest.main()
