"""Vini: windows grew and shrank (zoom) when they opened and closed: they
fade instead (lighter). Minimize keeps the genie, maximize Wayfire's own
cross-fade (he wanted those as they were)."""
import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WindowFadeTest(unittest.TestCase):
    def test_open_close_fade_minimize_genie(self):
        c = configparser.ConfigParser(interpolation=None, strict=False)
        c.read(ROOT / "config" / "wayfire.ini")
        self.assertEqual(c["animate"]["open_animation"], "fade")
        self.assertEqual(c["animate"]["close_animation"], "fade")
        self.assertEqual(c["animate"]["minimize_animation"], "squeezimize")
        self.assertNotIn("type", c["grid"])                          # maximize: as it was
        from sonata2.shell import dock
        self.assertEqual(dock.DEFAULTS["minimize_effect"], "genie")
        from sonata2.settings.app import minimize_animation
        self.assertEqual(minimize_animation("genie"), "squeezimize")
        self.assertEqual(minimize_animation("scale"), "zoom")


if __name__ == "__main__":
    unittest.main()
