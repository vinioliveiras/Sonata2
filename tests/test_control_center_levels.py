"""Vini: the Control Center's sound and brightness sliders moved after it
opened, and it opened with a lag. They now open at the last levels read,
and the live ones move them without setting them again (a DDC/CI monitor's
brightness took seconds on every open).

    xvfb-run -a python3 -m unittest tests.test_control_center_levels
"""
import os
import unittest
from unittest import mock


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class LevelsTest(unittest.TestCase):
    def test_shown_not_set(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from sonata2 import ui
        for style in ("menu", "module"):
            changed = []
            s = ui.controls.slider(50, changed.append, style=style)
            ui.controls.show_value(s, 80)
            self.assertEqual(round(s.get_value()), 80)
            self.assertEqual(changed, [], style)                  # nothing set again
            s.set_value(30)                                       # the user moving it still does
            self.assertEqual([round(v) for v in changed], [30], style)

    def test_open_at_the_last_levels_read(self):
        from sonata2.shell import topbar as T
        with open(T.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('known.get("bright", 50)', src)
        self.assertIn('_LEVELS.get("vol", 50)', src)
        self.assertIn('_LEVELS.get("mic", 50)', src)
        self.assertNotIn("self.bright.set_value(", src)
        self.assertNotIn("self.vol.set_value(", src)
        # sound doesn't wait for the display's brightness
        self.assertIn("system.run_async(system.brightness, self._fill_brightness, out)", src)

    def test_fill_remembers(self):
        from sonata2.shell import topbar as T
        holder = type("H", (), {})()
        holder.output = "DP-1"
        holder.bright, holder.vol, holder.mic = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.dict(T._LEVELS, clear=True), mock.patch.object(T.ui.controls, "show_value") as show:
            cls = next(c for c in vars(T).values() if isinstance(c, type) and hasattr(c, "_fill_sound"))
            cls._fill_sound(holder, ((35, False), (60, False)))
            cls._fill_brightness(holder, 70)
            self.assertEqual(T._LEVELS, {"vol": 35, "mic": 60, "DP-1": {"bright": 70}})
        self.assertEqual(show.call_count, 3)


if __name__ == "__main__":
    unittest.main()
