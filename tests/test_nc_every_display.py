"""Vini: on the second display the clock opened a bare calendar; it must
open the same Notification Center as the main display's (with the
notifications), on the display whose clock was clicked."""
import pathlib
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402,F401

from sonata2.shell import notifications as N  # noqa: E402
from sonata2.shell import topbar  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


class EveryDisplayTest(unittest.TestCase):
    def test_secondary_bars_share_the_main_center(self):
        src = (ROOT / "sonata2" / "__main__.py").read_text()
        part = src[src.index("def run_topbar"):src.index("others = monitors.each(create, destroy)")]
        self.assertIn('w.bar.notifications = getattr(win.bar, "notifications", None)', part)

    def test_clock_opens_it_on_its_display(self):
        bar = mock.Mock(spec=["notifications", "monitor"])
        bar.monitor = "HDMI"
        topbar.Bar._calendar(bar, None)
        bar.notifications.toggle_center.assert_called_once_with("HDMI")

    def test_center_moves_to_that_display(self):
        nc = mock.Mock()
        nc.get_visible.return_value = False
        owner = mock.Mock(nc=nc, _banners={})
        N.Notifications.toggle_center(owner, "HDMI")
        nc.show_center.assert_called_once_with("HDMI")
        nc.get_visible.return_value = True                  # open: the clock closes it
        N.Notifications.toggle_center(owner, "eDP")
        nc.hide_center.assert_called_once()

    def test_show_center_sets_the_monitor_once(self):
        ls = mock.Mock()
        center = mock.Mock(spec=["monitor", "_built_for", "_state_key", "_rebuild", "present", "rev"])
        center.monitor = None
        center._built_for = center._state_key.return_value = "k"
        with mock.patch.object(N.layer, "layer_shell", return_value=ls), \
                mock.patch.object(N.GLib, "idle_add"):
            N._Center.show_center(center, "HDMI")
            N._Center.show_center(center, "HDMI")
            N._Center.show_center(center)                      # no display named: where it was
        ls.set_monitor.assert_called_once_with(center, "HDMI")
        self.assertEqual(center.present.call_count, 3)


if __name__ == "__main__":
    unittest.main()
