"""Volume feedback sound when a volume slider is let go (xvfb-run python3 -m
unittest tests.test_volume_feedback). Regression: only the volume keys played it."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

from sonata2 import sounds, ui  # noqa: E402


class VolumeFeedbackTest(unittest.TestCase):
    def test_release_events(self):
        E = Gdk.EventType
        self.assertTrue(ui.controls.is_release(E.BUTTON_RELEASE, 1))
        self.assertTrue(ui.controls.is_release(E.TOUCH_END))
        self.assertTrue(ui.controls.is_release(E.KEY_RELEASE, 0, Gdk.KEY_Right))
        self.assertFalse(ui.controls.is_release(E.BUTTON_RELEASE, 3))
        self.assertFalse(ui.controls.is_release(E.BUTTON_PRESS, 1))      # still dragging
        self.assertFalse(ui.controls.is_release(E.KEY_RELEASE, 0, Gdk.KEY_a))

    def test_play_soon(self):
        with mock.patch.object(sounds, "play") as play:
            sounds.play_soon("volume", ms=10)
            play.assert_not_called()
            end = GLib.get_monotonic_time() + 500_000
            while not play.called and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
            play.assert_called_once_with("volume")

    def test_volume_sliders_watched(self):
        ui.setup()
        from sonata2.shell import topbar
        sl = ui.controls.slider(50, None, style="module")
        topbar._volume_feedback(sl)
        self.assertTrue(hasattr(sl, "release_watch"))


if __name__ == "__main__":
    unittest.main()
