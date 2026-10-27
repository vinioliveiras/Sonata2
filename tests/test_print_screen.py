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

    def _print(self, cfg, views=(), outs=({"id": 1, "name": "HDMI-A-1", "geometry": {}},)):
        cap = _cap()
        ipc = mock.Mock(call=mock.Mock(return_value=list(views)))
        with mock.patch.object(C, "focused_output", return_value="HDMI-A-1"), \
                mock.patch.object(C, "outputs", return_value=list(outs)), \
                mock.patch.object(C, "_ipc", return_value=ipc), \
                mock.patch.object(C.Frozen, "take", return_value=None), \
                mock.patch.object(C, "_slurp_async") as slurp, \
                mock.patch.object(C.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(cap, "_shoot") as shoot, mock.patch.object(cap, "_pick") as pick, \
                mock.patch.object(cap, "show_toolbar") as tb:
            cap._run("edit", cfg)
            ctx = C.GLib.MainContext.default()
            for _i in range(20):                       # (the portion's slurp starts on the next turn)
                ctx.iteration(False)
        tb.assert_not_called()
        pick.assert_not_called()                       # never a list to choose from: it's "direct"
        self.cap = cap
        return shoot, slurp

    def test_print_shoots_the_focused_display_without_toolbar_or_timer(self):
        shoot, _s = self._print({"timer": 5, "mode": "display"})
        geo, cfg, output = shoot.call_args.args[:3]
        self.assertIsNone(geo)
        self.assertEqual(output, "HDMI-A-1")
        self.assertEqual(cfg["timer"], 0)
        self.assertTrue(shoot.call_args.kwargs.get("edit"))

    def test_print_remembers_the_toolbars_last_mode(self):
        """Vini: Print uses what was used last -- Capture a Window: the window in front."""
        views = [{"app-id": "old", "last-focus-timestamp": 1, "output-id": 1,
                  "geometry": {"x": 0, "y": 0, "width": 100, "height": 100}},
                 {"app-id": "front", "last-focus-timestamp": 9, "output-id": 1,
                  "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}}]
        outs = [{"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
        shoot, _s = self._print({"mode": "window"}, views, outs)
        self.assertEqual(shoot.call_args.args[0], "10,20 300x200")
        self.assertTrue(shoot.call_args.kwargs.get("edit"))

    def test_print_selection_mode_draws_a_portion(self):
        shoot, slurp = self._print({"mode": "area"})
        slurp.assert_called_once()
        with mock.patch.object(C.GLib, "timeout_add", side_effect=lambda _ms, f: f()), \
                mock.patch.object(self.cap, "_shoot") as shoot:
            slurp.call_args.args[1]("1,2 30x40")       # the portion drawn
        self.assertEqual(shoot.call_args.args[0], "1,2 30x40")
        self.assertTrue(shoot.call_args.kwargs.get("edit"))

    def test_print_after_recording_mode_takes_a_still(self):
        self.assertEqual(C.print_mode("rec-window"), "window")
        self.assertEqual(C.print_mode("rec-display"), "display")
        self.assertEqual(C.print_mode("rec-area"), "area")
        self.assertEqual(C.print_mode("???"), "display")
        shoot, _s = self._print({"mode": "rec-display"})
        self.assertTrue(shoot.called)
        self.assertTrue(shoot.call_args.kwargs.get("edit"))

    def test_print_keeps_folder_and_clipboard_choices(self):
        shoot, _s = self._print({"mode": "display", "shots_to": "clipboard", "shots_copy": True})
        cfg = shoot.call_args.args[1]
        self.assertEqual((cfg["shots_to"], cfg["shots_copy"]), ("clipboard", True))

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
