"""Vini: the window buttons can go on the right (Settings > Appearance >
Window buttons); the left stays the default."""
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.ui import tokens  # noqa: E402
from sonata2.ui import window as W  # noqa: E402


def _classes(box):
    out, c = [], box.get_first_child()
    while c is not None:
        out.append([k for k in c.get_css_classes() if k.startswith("tl-")][0])
        c = c.get_next_sibling()
    return out


class ButtonsSideTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)

    def test_left_by_default(self):
        self.assertEqual(tokens.frame()["buttons_side"], "left")
        self.assertEqual(_classes(W.traffic_lights(lambda: 0, lambda: 0, lambda: 0)),
                         ["tl-close", "tl-min", "tl-zoom"])
        bar = W.titlebar(Gtk.Window(), "x").bar
        self.assertIsNotNone(bar.get_start_widget())

    def test_right(self):
        config.update("appearance", buttons_side="right")
        f = tokens.frame()
        self.assertEqual(f["buttons_side"], "right")
        self.assertEqual(tokens.button_layout(f), ":maximize,minimize,close")
        self.assertEqual(_classes(W.traffic_lights(lambda: 0, lambda: 0, lambda: 0)),
                         ["tl-zoom", "tl-min", "tl-close"])                  # close at the edge
        end = Gtk.Label(label="end")
        bar = W.titlebar(Gtk.Window(), "x", end=end).bar
        self.assertIsNone(bar.get_start_widget())
        both = bar.get_end_widget()
        self.assertIs(both.get_first_child(), end)                          # then the buttons
        self.assertIn("traffic", both.get_last_child().get_css_classes())

    def test_settings_applies_it_everywhere(self):
        from sonata2.settings import app
        config.update("appearance", buttons_side="right")
        calls, gs = [], []
        with mock.patch.object(app.system, "wayfire_set", lambda s, k, v: calls.append((s, k, v)) or True), \
                mock.patch("sonata2.prefs.set", lambda sch, k, v: gs.append((k, v))), \
                mock.patch("sonata2.titlebars.apply", lambda *a: None):
            app.apply_buttons_side()
        self.assertIn(("pixdecor", "button_layout", ":maximize,minimize,close"), calls)
        self.assertIn(("button-layout", ":maximize,minimize,close"), gs)
        self.assertIn("buttons_side", app.Settings.APPEARANCE_RESET)
        from sonata2 import icons
        self.assertEqual(icons.APPEARANCE_DEFAULTS["buttons_side"], "left")

    def test_files_puts_them_at_the_toolbars_end(self):
        import inspect
        from sonata2.files import window as FW
        src = inspect.getsource(FW.FilesWindow)
        self.assertIn('buttons_side() == "right"', src)
        self.assertIn("self._toolbar_bar.append(lights)", src)              # (live: test_buttons_live)


if __name__ == "__main__":
    unittest.main()
