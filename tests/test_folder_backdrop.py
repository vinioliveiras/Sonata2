"""An open folder in Apps / the Apps Menu (Vini): its background was too
see-through and the apps outside it too visible. Now the folder panel is
denser (nearly solid in the Apps Menu, where the window's glass can't blur
the grid under it) and the grid behind fades further and blurs.
Run: python3 -m unittest tests.test_folder_backdrop"""
import re
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from sonata2.ui import tokens  # noqa: E402
from sonata2.shell import launchpad as L, launchpad_window as LW  # noqa: E402


def _alpha(css: str) -> float:
    m = re.match(r"rgba\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)", css)
    return float(m.group(1)) if m else 1.0


def _source(mod) -> str:
    with open(mod.__file__) as f:
        return f.read()


class FolderBackdropTest(unittest.TestCase):
    def test_apps_outside_fade_far_and_blur(self):
        self.assertLessEqual(float(tokens.SHARED["folder_backdrop"]), 0.2)        # was 0.35 / 0.3
        self.assertTrue(tokens.SHARED["folder_backdrop_blur"].endswith("px"))
        for mod, sel in ((L, ".lp-col.dimmed"), (LW, ".lpw-content.dimmed")):
            rule = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", _source(mod)).group(1)
            self.assertIn("opacity: %(folder_backdrop)s", rule, mod.__name__)
            self.assertIn("filter: blur(%(folder_backdrop_blur)s)", rule, mod.__name__)

    def test_folder_panel_denser(self):
        # light glass in the light look, dark glass in the dark (Vini)
        self.assertNotIn("folder_panel", tokens.SHARED)
        light, dark = tokens.LIGHT["folder_panel"], tokens.DARK["folder_panel"]
        self.assertTrue(light.startswith("rgba(255, 255, 255"))
        self.assertFalse(dark.startswith("rgba(255, 255, 255"))
        for c in (light, dark):
            self.assertGreaterEqual(_alpha(c), 0.25)                                 # was 0.16
            self.assertLess(_alpha(c), 0.9)                                          # still glass
        rule = re.search(r"\.lpw-root \.lp-panel\s*\{([^}]*)\}", _source(LW)).group(1)
        # Vini (option B): the Apps Menu's folder is light glass like the full-screen one,
        # over the faded, blurred grid
        self.assertIn("background: %(folder_panel)s", rule)

    def test_fade_and_blur_animate(self):
        self.assertRegex(_source(L), r"\.lp-col \{ transition: opacity[^}]*filter")
        self.assertRegex(_source(LW), r"\.lpw-content \{ transition: opacity[^}]*filter")


if __name__ == "__main__":
    unittest.main()
