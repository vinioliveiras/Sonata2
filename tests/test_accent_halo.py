"""Vini: with black as the accent colour in Dark Mode, accent icons and text
nearly vanished -- he wants black to stay black. A very dark accent keeps its
colour; in Dark Mode its text and icons get a light halo."""
import os
import re
import unittest

from sonata2.ui import tokens as T

ROOT = os.path.join(os.path.dirname(__file__), "..", "sonata2")


class AccentHaloTest(unittest.TestCase):
    def test_halo_only_when_too_dark(self):
        self.assertEqual(T.accent_tokens("#000000", dark=True)["accent"], "#000000")     # black stays black
        self.assertEqual(T.accent_tokens("#000000", dark=True)["accent_halo"], T.ACCENT_HALO)
        self.assertNotIn("accent_halo", T.accent_tokens("#000000", dark=False))         # light mode: readable
        self.assertNotIn("accent_halo", T.accent_tokens("orange", dark=True))
        self.assertNotIn("accent_halo", T.accent_tokens("graphite", dark=True))
        self.assertEqual(T.accent_tokens("blue", dark=True), {})

    def test_default_token(self):
        for theme in T.THEMES:
            for dark in (False, True):
                self.assertEqual(T.palette(dark, theme)["accent_halo"], "none")     # every theme has it

    def test_every_accent_text_rule_has_it(self):
        for base, _d, files in os.walk(ROOT):
            for f in files:
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    text = fh.read()
                for m in re.finditer(r"(?<![-\w])color: %\(accent\)s;([^}\n]*)", text):
                    self.assertIn("%(accent_halo)s", m.group(1), f"{f}: {m.group(0)[:60]}")


if __name__ == "__main__":
    unittest.main()
