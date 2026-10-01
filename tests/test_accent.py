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
        self.assertIn("accent_hex(accent)", inspect.getsource(portal))


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
        c = Gdk.RGBA()
        c.parse("#12ab34")
        row.custom_accent.set_rgba(c)
        settle(50)
        self.assertEqual(config.load("appearance", {"accent": "blue"})["accent"], "#12ab34")
        self.assertTrue(row.custom_accent.has_css_class("selected"))

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


if __name__ == "__main__":
    unittest.main()
