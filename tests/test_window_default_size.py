"""Vini: Sonata's windows opened too small once their size was reset (Files
~820 x 520 on 1080p); a document window's first size is about two thirds
of the display (~1260 x 700 on 1080p), never bigger than the display.
xvfb-run -a python3 -m unittest tests.test_window_default_size"""
import inspect
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.ui import window as W  # noqa: E402


class DefaultSizeTest(unittest.TestCase):
    def test_files_opened_too_small_after_a_reset(self):
        w, h = W.roomy_size(920, 560, (1920, 1080))
        self.assertTrue(1240 <= w <= 1290 and 680 <= h <= 720, (w, h))
        self.assertEqual(W.roomy_size(1400, 900, (1920, 1080)), (1400, 900))       # never smaller than its own
        self.assertEqual(W.roomy_size(920, 560, None), (920, 560))
        fw, fh = W.fit_size(*W.roomy_size(920, 560, (1280, 720)), (1280, 720))     # a small laptop: still fits
        self.assertLessEqual(fh, 720 - W.WORK_MARGIN_H)

    def test_first_open_is_roomy_a_kept_size_wins(self):
        Gtk.init()
        win = Gtk.Window()
        with mock.patch("sonata2.winsize.saved", return_value=None), mock.patch("sonata2.winsize.save"), \
                mock.patch.object(W, "screen_size", return_value=(1920, 1080)):
            W.remember_size(win, "t-roomy", 920, 560, roomy=True)
        self.assertGreater(win.get_default_size()[0], 1200)
        win2 = Gtk.Window()
        kept = {"width": 700, "height": 500, "maximized": False}
        with mock.patch("sonata2.winsize.saved", return_value=kept), mock.patch("sonata2.winsize.save"), \
                mock.patch.object(W, "screen_size", return_value=(1920, 1080)):
            W.remember_size(win2, "t-roomy", 920, 560, roomy=True)
        self.assertEqual(win2.get_default_size(), (700, 500))

    def test_document_windows_ask_for_it(self):
        from sonata2.files.window import FilesWindow
        from sonata2.settings.app import Settings
        for cls in (FilesWindow, Settings):
            self.assertIn("roomy=True", inspect.getsource(cls))


if __name__ == "__main__":
    unittest.main()
