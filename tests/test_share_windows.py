"""Vini: the screen-share picker showed a generic icon for every window and
"WhatsApp (d4ff1290c45e...)" as its name. Windows get their own thumbnail
(grim -T, the toplevel identifier) and the app's icon; the name is the title."""
import unittest
from unittest import mock

from sonata2.shell import sharepicker as SP


class ParseTest(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(SP._parse("Window: WhatsApp (d4ff1290c45e)"), ("window", "WhatsApp", "d4ff1290c45e"))
        self.assertEqual(SP._parse("Window: ~ — fish — 80×24 (9ac6b3)"), ("window", "~ — fish — 80×24", "9ac6b3"))
        self.assertEqual(SP._parse("Window: Song (Live) (abc)"), ("window", "Song (Live)", "abc"))
        self.assertEqual(SP._parse("Window: abc"), ("window", "Window", "abc"))
        self.assertEqual(SP._parse("Monitor: HDMI-A-1 Acer KG241Y"), ("screen", "HDMI-A-1", "HDMI-A-1"))
        self.assertEqual(SP._parse("eDP-1"), ("screen", "eDP-1", "eDP-1"))

    def test_window_thumb_uses_toplevel_capture(self):
        with mock.patch.object(SP.shutil, "which", return_value="/usr/bin/grim"), \
                mock.patch.object(SP, "_grim_texture", return_value="tex") as g:
            self.assertEqual(SP._window_thumb("abc"), "tex")
        self.assertEqual(g.call_args.args[0][:2], ["-T", "abc"])
        self.assertIsNone(SP._window_thumb(None))

    def test_textures_keep_order(self):
        self.assertEqual(SP._textures([(str.upper, "a"), (str.upper, "b")]), ["A", "B"])
        self.assertEqual(SP._textures([]), [])


if __name__ == "__main__":
    unittest.main()
