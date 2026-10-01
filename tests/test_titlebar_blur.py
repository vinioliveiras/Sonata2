"""Other apps' windows aren't blurred behind (python3 -m unittest tests.test_titlebar_blur).
Regression: every window was blurred on every frame of a move/resize (heavy;
the blur pass is also where the Radeon 680M hung)."""
import os
import unittest
from unittest import mock

from sonata2 import titlebars


class BlurTest(unittest.TestCase):
    def test_only_sonata_surfaces(self):
        self.assertNotIn("toplevel", titlebars.BLUR)
        self.assertIn('app_id contains "sonata2"', titlebars.BLUR)
        ini = open(os.path.join(os.path.dirname(__file__), "..", "config", "wayfire.ini")).read()
        self.assertIn("blur_by_default = " + titlebars.BLUR, ini)

    def test_apply_colors_sets_it(self):
        calls = {}
        with mock.patch("sonata2.backend.system.wayfire_set",
                        side_effect=lambda sec, key, val: calls.__setitem__((sec, key), val)):
            for dark in (False, True):
                titlebars.apply_colors(dark)
                self.assertEqual(calls[("blur", "blur_by_default")], titlebars.BLUR)


if __name__ == "__main__":
    unittest.main()
