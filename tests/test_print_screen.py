"""Print Screen (Vini): the screen is captured at once and opened in the
editor (Preview's Markup) -- before it only opened the options toolbar and
nothing was captured until a click.
Run: python3 -m unittest tests.test_print_screen"""
import configparser
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402,F401

from sonata2 import shortcuts  # noqa: E402
from sonata2.shell import capture as C  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cap():
    cap = C.Capture.__new__(C.Capture)          # no windows: only the flow is tested
    cap.app, cap.thumb, cap.toolbar, cap.recorder = None, None, None, None
    return cap


class PrintScreenTests(unittest.TestCase):
    def test_print_key_captures_and_edits(self):
        """Print / SysRq run `screenshot edit`, not the toolbar."""
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(os.path.join(REPO, "config", "wayfire.ini"))
        self.assertIn("KEY_PRINT", ini["command"]["binding_capture_print"])
        self.assertEqual(ini["command"]["command_capture_print"].strip(), "sonata2 screenshot edit")

    def test_print_shoots_the_focused_display_without_toolbar_or_timer(self):
        cap = _cap()
        with mock.patch.object(C, "focused_output", return_value="HDMI-A-1"), \
                mock.patch.object(cap, "_shoot") as shoot, mock.patch.object(cap, "show_toolbar") as tb:
            cap._run("edit", {"timer": 5})
        tb.assert_not_called()
        geo, cfg, output = shoot.call_args.args[:3]
        self.assertIsNone(geo)
        self.assertEqual(output, "HDMI-A-1")
        self.assertEqual(cfg["timer"], 0)
        self.assertTrue(shoot.call_args.kwargs.get("edit"))

    def test_edited_shot_opens_markup_not_thumbnail(self):
        cap = _cap()
        with mock.patch.object(C, "open_shot") as opened, mock.patch.object(C, "Thumbnail") as thumb:
            cap.shot_taken("/tmp/x.png", edit=True)
        opened.assert_called_once_with("/tmp/x.png")
        thumb.assert_not_called()

    def test_plain_screenshot_still_shows_thumbnail(self):
        cap = _cap()
        with mock.patch.object(C, "open_shot") as opened, mock.patch.object(C, "Thumbnail") as thumb:
            cap.shot_taken("/tmp/x.png")
        opened.assert_not_called()
        thumb.return_value.show_shot.assert_called_once_with("/tmp/x.png")

    def test_open_shot_runs_preview_markup(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as f, \
                mock.patch.object(C.GLib, "spawn_async") as spawn:
            self.assertTrue(C.open_shot(f.name))
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[-3:], ["preview", "--markup", f.name])

    def test_listed_in_keyboard_shortcuts(self):
        self.assertIn("capture_print", [s.id for s in shortcuts.SHORTCUTS])


if __name__ == "__main__":
    unittest.main()
