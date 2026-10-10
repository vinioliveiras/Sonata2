"""Vini: with a dark accent (black) in Dark Mode, accent icons and text
nearly vanished; then they got a light halo -- now no halo: they (and the
progress bars) use a lighter tone of the accent, automatically. The accent
itself stays as chosen where it fills (selected rows, buttons)."""
import os
import re
import unittest

from sonata2.ui import tokens as T

ROOT = os.path.join(os.path.dirname(__file__), "..", "sonata2")


class AccentInkTest(unittest.TestCase):
    def test_dark_accent_gets_a_lighter_ink(self):
        t = T.accent_tokens("#000000", dark=True)
        self.assertEqual(t["accent"], "#000000")                       # black stays black where it fills
        self.assertGreaterEqual(T.contrast(t["accent_ink"], T.DARK_BG), T.INK_MIN)
        self.assertLess(T.contrast(t["accent_ink"], T.DARK_BG), T.INK_MIN + 0.5)   # just enough: still dark-ish
        navy = T.accent_tokens("#1a2a6c", dark=True)["accent_ink"]
        r, g, b = (int(navy[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(b, r)                                       # the same hue, lighter
        self.assertNotIn("accent_halo", t)

    def test_readable_accents_unchanged(self):
        for name in ("orange", "graphite", "#ff3b30"):
            t = T.accent_tokens(name, dark=True)
            self.assertEqual(t["accent_ink"], t["accent"], name)
        self.assertEqual(T.accent_tokens("#000000", dark=False)["accent_ink"], "#000000")   # light mode: readable
        self.assertEqual(T.accent_tokens("blue", dark=True), {})

    def test_default_token(self):
        for theme in T.THEMES:
            for dark in (False, True):
                p = T.palette(dark, theme)
                self.assertEqual(p["accent_ink"], p["accent"])

    def test_no_halo_and_ink_everywhere(self):
        for base, _d, files in os.walk(ROOT):
            for f in files:
                if not f.endswith(".py") or f == "tokens.py":
                    continue
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertNotIn("%(accent_halo)s", text, f)
                self.assertIsNone(re.search(r"(?<![-\w])color: %\(accent\)s;", text), f)

    def test_bars_use_the_ink(self):
        with open(os.path.join(ROOT, "ui", "progress.py"), encoding="utf-8") as fh:
            self.assertIn("background: %(accent_ink)s", fh.read())
        with open(os.path.join(ROOT, "ui", "controls.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("scale.sonata-slider highlight { border-radius: 99px; background: %(accent_ink)s", src)


if __name__ == "__main__":
    unittest.main()
