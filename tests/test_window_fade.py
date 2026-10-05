"""Vini: the open / close / maximize / minimize animations were too heavy;
windows fade instead (lighter), and Settings can still choose the genie."""
import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WindowFadeTest(unittest.TestCase):
    def ini(self):
        c = configparser.ConfigParser(interpolation=None, strict=False)
        c.read(ROOT / "config" / "wayfire.ini")
        return c

    def test_fade_by_default(self):
        c = self.ini()
        self.assertEqual(c["animate"]["open_animation"], "fade")
        self.assertEqual(c["animate"]["close_animation"], "fade")
        self.assertEqual(c["animate"]["minimize_animation"], "fade")
        self.assertEqual(c["grid"]["type"], "crossfade")             # maximize / restore
        self.assertLessEqual(int(c["grid"]["duration"].rstrip("ms")), 200)
        from sonata2.shell import dock
        self.assertEqual(dock.DEFAULTS["minimize_effect"], "fade")

    def test_settings_choices(self):
        from sonata2.settings.app import minimize_animation
        self.assertEqual(minimize_animation("fade"), "fade")
        self.assertEqual(minimize_animation("genie"), "squeezimize")
        self.assertEqual(minimize_animation("scale"), "zoom")


if __name__ == "__main__":
    unittest.main()
