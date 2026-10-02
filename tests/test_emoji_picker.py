"""Emoji picker (shell/emoji.py). Vini: it must close on a click outside it
and on Esc (the search field kept Esc for itself).
Run: xvfb-run -a python3 -m unittest tests.test_emoji_picker"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import emoji as E  # noqa: E402


class EmojiPickerCloseTest(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        self.app = Gtk.Application(application_id="io.github.test.emoji")
        self.app.register(None)
        self.win = E.EmojiPicker(self.app)
        self.win.set_visible(True)

    def tearDown(self):
        self.win.destroy()

    def test_click_outside_closes(self):
        with mock.patch.object(self.win, "pick", return_value=self.win):        # the see-through part
            self.win._clicked(None, 1, 5, 5)
        self.assertFalse(self.win.get_visible())

    def test_click_inside_stays(self):
        with mock.patch.object(self.win, "pick", return_value=self.win.search):
            self.win._clicked(None, 1, 200, 40)
        self.assertTrue(self.win.get_visible())

    def test_esc_from_the_search_field(self):
        self.win.search.emit("stop-search")
        self.assertFalse(self.win.get_visible())

    def test_esc_seen_before_the_field(self):
        ctrls = [c for c in list(self.win.observe_controllers()) if isinstance(c, Gtk.EventControllerKey)]
        self.assertTrue(any(c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE for c in ctrls))
