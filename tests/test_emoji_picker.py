"""Emoji and clipboard pickers (shell/emoji.py, clip_picker.py). Vini: they
close on a click outside and on Esc (the search field kept Esc for
itself); they sit in the middle of the screen, a little lower (the emoji
one was at the top left); an emoji is pasted, not typed as keys (WhatsApp
got other characters).
Run: xvfb-run -a python3 -m unittest tests.test_emoji_picker"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2.shell import clip_picker, layer  # noqa: E402
from sonata2.shell import emoji as E  # noqa: E402


def _click(win, at):
    """A press on the full-screen picker, `at` = the widget under it."""
    click = next(c for c in list(win.observe_controllers()) if isinstance(c, Gtk.GestureClick))
    with mock.patch.object(win, "pick", return_value=at):
        click.emit("pressed", 1, 5, 5)


class EmojiPickerCloseTest(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        self.app = Gtk.Application(application_id="io.github.test.emoji")
        self.app.register(None)
        with mock.patch.object(layer, "overlay_fullscreen", return_value=True):   # (a layer surface)
            self.win = E.EmojiPicker(self.app)
        self.win.set_visible(True)

    def tearDown(self):
        self.win.destroy()

    def test_click_outside_closes(self):
        _click(self.win, self.win)                       # the see-through part
        self.assertFalse(self.win.get_visible())

    def test_click_inside_stays(self):
        _click(self.win, self.win.search)
        self.assertTrue(self.win.get_visible())

    def test_middle_of_the_screen_a_little_lower(self):
        p = self.win.panel
        self.assertTrue(self.win.get_resizable())        # covers the screen (fixed size: top left)
        self.assertEqual((p.get_halign(), p.get_valign()), (Gtk.Align.CENTER, Gtk.Align.CENTER))
        self.assertEqual(p.get_margin_top(), 2 * layer.PICKER_DROP)

    def test_esc_from_the_search_field(self):
        self.win.search.emit("stop-search")
        self.assertFalse(self.win.get_visible())

    def test_esc_seen_before_the_field(self):
        ctrls = [c for c in list(self.win.observe_controllers()) if isinstance(c, Gtk.EventControllerKey)]
        self.assertTrue(any(c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE for c in ctrls))

    def test_emoji_is_pasted_not_typed(self):
        runs = []
        with mock.patch("shutil.which", return_value="/usr/bin/wtype"), \
                mock.patch.object(clip_picker, "_terminal_focused", return_value=False), \
                mock.patch("subprocess.Popen", lambda argv, **kw: runs.append(argv)), \
                mock.patch.object(E.config, "save"):
            self.win._pick("👨‍👩‍👧")
            ctx = GLib.MainContext.default()
            end = GLib.get_monotonic_time() + 600_000
            while not runs and GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        self.assertEqual(runs, [["wtype", "-M", "ctrl", "v", "-m", "ctrl"]])
        self.assertFalse(self.win.get_visible())

    def test_plain_window_without_layer_shell(self):
        win = E.EmojiPicker(self.app)                     # (no layer shell here)
        self.assertFalse(win.get_resizable())
        self.assertEqual(win.panel.get_margin_top(), 0)
        win.destroy()


class ClipboardPickerTest(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        self.app = Gtk.Application(application_id="io.github.test.clip")
        self.app.register(None)
        history = mock.Mock(items=[], listeners=[])
        with mock.patch.object(layer, "overlay_fullscreen", return_value=True):
            self.win = clip_picker.ClipboardPicker(self.app, history)
        self.win.set_visible(True)
        self.box = self.win.get_child()

    def tearDown(self):
        self.win.destroy()

    def test_click_outside_closes(self):
        _click(self.win, self.win)
        self.assertFalse(self.win.get_visible())

    def test_click_inside_stays(self):
        _click(self.win, self.win.search)
        self.assertTrue(self.win.get_visible())

    def test_middle_of_the_screen_a_little_lower(self):
        self.assertTrue(self.win.get_resizable())
        self.assertEqual((self.box.get_halign(), self.box.get_valign()), (Gtk.Align.CENTER, Gtk.Align.CENTER))
        self.assertEqual(self.box.get_margin_top(), 2 * layer.PICKER_DROP)

    def test_paste_keys(self):
        with mock.patch.object(clip_picker, "_terminal_focused", return_value=True):
            self.assertEqual(clip_picker.paste_keys(), ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"])
