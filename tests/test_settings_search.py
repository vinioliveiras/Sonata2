"""Vini: Settings' search only found sections (by their title and a few
keywords). Any text in Settings finds it now -- a group, a row, a switch's
label -- and picking a result opens that section scrolled to that setting,
which lights up.

    xvfb-run -a python3 -m unittest tests.test_settings_search
"""
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import i18n  # noqa: E402
from sonata2.settings import search  # noqa: E402


def spin(ms):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        ctx.iteration(False)


class IndexTest(unittest.TestCase):
    def test_search_only_found_sections(self):
        """A setting inside a section is found by its own text."""
        hits = search.find("night shift")
        self.assertTrue(hits)
        self.assertEqual((hits[0].part, hits[0].text), ("displays", "Night Shift"))   # its name first
        texts = [h.text for h in search.find("accent")]
        self.assertIn("Accent colour", texts)

    def test_every_section_has_its_texts(self):
        idx = search.index()
        for part in ("wifi", "sound", "displays", "appearance", "dock", "apps", "shortcuts", "language"):
            self.assertTrue(idx.get(part), part)

    def test_merged_part_opens_its_section(self):
        from sonata2.settings.app import section_of
        hit = next(h for h in search.find("wallpaper", section_of) if h.part == "wallpaper")
        self.assertEqual(hit.sid, "displays")                   # Wallpaper is inside Displays

    def test_found_in_portuguese(self):
        """The texts are searched as shown: in pt-BR / pt-PT too (and English still matches)."""
        with mock.patch.object(search, "_", side_effect=lambda t: {"Night Shift": "Turno da Noite"}.get(t, t)):
            texts = [h.text for h in search.find("turno")]
            self.assertIn("Turno da Noite", texts)
            self.assertIn("Turno da Noite", [h.text for h in search.find("night")])

    def test_no_noise(self):
        self.assertEqual(search.find(""), [])
        self.assertFalse(any(len(h.text.strip(" …")) < 3 for h in search.find("a")))


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gtk.Settings.get_default() is None:
            raise unittest.SkipTest("needs a display")
        Adw.init()
        from sonata2 import ui
        ui.setup()

    def setUp(self):
        from sonata2.settings import app as S
        self.app = Adw.Application(application_id=f"io.test.ssearch{os.getpid()}{int(time.time() * 1000) % 100000}")
        self.app.register(None)
        self.win = S.Settings(self.app)
        self.win.present()
        self.addCleanup(self.win.destroy)
        spin(200)

    def test_click_goes_to_the_setting(self):
        """Typing shows the settings found; picking one opens its section with
        that setting on screen, lit up."""
        self.win.search.set_text("night shift")
        spin(400)                                                # (search-changed waits a moment)
        self.assertTrue(self.win.results.get_visible())
        first = self.win.results.get_first_child()
        self.assertEqual(first.hit.text, "Night Shift")
        self.win.open_hit(first.hit)
        spin(800)
        self.assertEqual(self.win.current, "displays")
        found = getattr(self.win, "found", None)
        self.assertIsNotNone(found)
        self.assertTrue(found.has_css_class("st-found"))
        self.assertIsNotNone(search.locate(found, "Night Shift"))

    def test_results_cleared(self):
        self.win.search.set_text("accent")
        spin(400)
        self.assertTrue(self.win.results.get_visible())
        self.win.search.set_text("")
        spin(400)
        self.assertFalse(self.win.results.get_visible())
        self.assertIsNone(self.win.results.get_first_child())


if __name__ == "__main__":
    unittest.main()
