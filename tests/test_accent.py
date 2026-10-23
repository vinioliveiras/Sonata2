"""Custom accent colour and Settings' section colours. Run:
xvfb-run python3 -m unittest tests.test_accent"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.ui import tokens as T  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TokensTest(unittest.TestCase):
    def test_custom_accent(self):
        self.assertEqual(T.custom_accent("#FF8800"), "#ff8800")
        self.assertEqual(T.custom_accent("#f80"), "#ff8800")
        for bad in ("blue", "#zzz", "#12345", "", None, 3):
            self.assertIsNone(T.custom_accent(bad))
        t = T.accent_tokens("#ff8800", False)
        self.assertEqual(t["accent"], "#ff8800")
        self.assertNotEqual(t["accent_selected"], "#ff8800")            # darker for selected rows (light)
        self.assertEqual(T.accent_tokens("#ff8800", True)["accent_selected"], "#ff8800")
        self.assertEqual(T.accent_tokens("blue", False), {})              # the default: unchanged
        self.assertEqual(T.accent_hex("nope"), T.ACCENTS["blue"][0])
        self.assertEqual(T.accent_hex("green", True), T.ACCENTS["green"][1])

    def test_portal_sends_the_custom_colour(self):
        import inspect
        from sonata2 import portal
        self.assertIn("tokens.accent_hex(look[\"accent\"]", inspect.getsource(portal))


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        from sonata2.settings import app as st
        cls.st = st

    def setUp(self):
        config.save("appearance", {})
        self.open()

    def open(self):
        self.w = self.st.Settings(None, "appearance")
        self.w.present()
        self.w.select("appearance", from_sidebar=True)
        settle(200)

    def tearDown(self):
        self.w.destroy()

    def _row(self):
        from tests.test_regressions import rows_of
        return next(r for r in rows_of(self.w.pages["appearance"], Adw.ActionRow)
                    if r.get_title() == "Accent colour")

    def test_pick_any_colour(self):
        row = self._row()
        row.custom_accent.emit("clicked")
        pop = row.custom_accent.picker
        settle(50)
        pop.hex.set_text("#12ab34")
        pop.select_button.emit("clicked")
        settle(50)
        self.assertEqual(config.load("appearance", {"accent": "blue"})["accent"], "#12ab34")
        self.assertTrue(row.custom_accent.has_css_class("selected"))
        self.assertEqual(row.custom_accent.swatch.color, "#12ab34")

    def test_own_picker_not_gtks(self):
        """Vini: the GTK colour dialog's buttons and header came in another
        theme -- the accent picker is Sonata's own (ui.colorpicker)."""
        import inspect
        src = inspect.getsource(self.st.Settings._accent_row)
        self.assertNotIn("ColorDialog", src)
        self.assertIn("ui.colorpicker", src)

    def test_named_dot_unselects_custom(self):
        config.save("appearance", {"accent": "#12ab34"})
        self.w.destroy()
        self.open()
        row = self._row()
        self.assertTrue(row.custom_accent.has_css_class("selected"))
        from tests.test_regressions import rows_of
        green = next(b for b in rows_of(row, Gtk.Button) if b.has_css_class("green"))
        green.emit("clicked")
        self.assertFalse(row.custom_accent.has_css_class("selected"))
        self.assertEqual(config.load("appearance", {"accent": "blue"})["accent"], "green")

    def test_section_colours_varied_and_styled(self):
        import inspect
        colours = [s[3] for s in self.st.SECTIONS]
        grey = sum(c in ("gray", "graphite") for c in colours)
        self.assertLessEqual(grey, 5)                                     # Vini: not mostly grey...
        greys = {s[0] for s in self.st.SECTIONS if s[3] in ("gray", "graphite")}
        self.assertEqual(greys, {"keyboard", "mouse", "printers", "launchpad", "about"})   # ...but these (macOS)
        src = inspect.getsource(self.st)
        for c in set(colours):
            self.assertIn(f".st-badge.{c} {{", src)                       # every colour has its CSS


class PickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        from sonata2 import ui
        ui.setup()
        cls.C = ui.colorpicker

    def test_hex(self):
        self.assertEqual(self.C.parse_hex("ABC"), "#aabbcc")
        self.assertEqual(self.C.parse_hex(" #12AB34 "), "#12ab34")
        for bad in ("", "#12", "zzzzzz", None):
            self.assertIsNone(self.C.parse_hex(bad))

    def test_hsv_round_trip(self):
        for c in ("#ff9f0a", "#30d158", "#000000", "#ffffff", "#418cd8"):
            self.assertEqual(self.C.to_hex(*self.C.to_hsv(c)), c)

    def _open(self, colour, got):
        w = Gtk.Window()
        anchor = Gtk.Button()
        w.set_child(anchor)
        w.present()
        pop = self.C.popup(anchor, colour, got.append)
        settle(50)
        return w, pop

    def test_popup_uses_the_kit(self):
        got = []
        w, pop = self._open("#ff9f0a", got)
        self.assertTrue(pop.has_css_class("sonata-panel"))                 # Sonata's panel, not a dialog
        self.assertTrue(pop.select_button.has_css_class("sonata-button"))
        self.assertTrue(pop.select_button.has_css_class("default"))
        self.assertIsInstance(pop.brightness, ui_controls().ModuleSlider)  # the kit's slider
        pop.hex.set_text("#30d158")
        settle(400)                                                     # the knob glides there
        self.assertEqual(self.C.to_hex(pop.wheel.h, pop.wheel.s, pop.wheel.v), "#30d158")
        pop.hex.set_text("nonsense")                                    # ignored until it's a colour
        self.assertEqual(pop.state["color"], "#30d158")
        pop.select_button.emit("clicked")
        self.assertEqual(got, ["#30d158"])
        w.destroy()

    def test_wheel_picks_hue_and_saturation(self):
        """Vini: a circle you move a knob in (macOS): red on the right,
        green up-left, white in the middle; brightness apart."""
        got = []
        w, pop = self._open("#ffffff", got)
        wh = pop.wheel
        cx, cy, r = wh.get_width() / 2, wh.get_height() / 2, wh.get_width() / 2
        wh._at(cx + r, cy)                                              # the right edge
        self.assertEqual(pop.state["color"], "#ff0000")
        self.assertEqual(pop.hex.get_text(), "#ff0000")
        h, s = wh.hs_at(*wh.point_of(1 / 3, 1.0))                       # green
        self.assertAlmostEqual(h, 1 / 3, places=3)
        self.assertLess(wh.point_of(1 / 3, 1)[0], cx)                   # ...on the left
        self.assertLess(wh.point_of(1 / 3, 1)[1], cy)                   # ...and up
        self.assertEqual(wh.hs_at(cx + 5 * r, cy)[1], 1.0)              # outside: the edge
        pop.brightness.set_value(50)
        self.assertEqual(pop.state["color"], "#800000")
        self.assertEqual(pop.wheel.h, 0.0)                              # the hue kept
        pop.select_button.emit("clicked")
        self.assertEqual(got, ["#800000"])
        w.destroy()

    def test_wheel_draws(self):
        got = []
        w, pop = self._open("#418cd8", got)
        snap = Gtk.Snapshot()
        pop.wheel.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())
        snap = Gtk.Snapshot()
        pop.brightness.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())
        w.destroy()


def ui_controls():
    from sonata2.ui import controls
    return controls


if __name__ == "__main__":
    unittest.main()
