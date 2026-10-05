"""Vini: window sizes were lost after a restart -- they were only saved when
a window closed, and a restart ends apps without closing their windows.
Now a resize is saved shortly after it settles."""
import inspect
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config, winsize  # noqa: E402
from sonata2.ui import window as W  # noqa: E402


def spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class SizeKeptWhileOpenTest(unittest.TestCase):
    def test_saved_after_a_resize_without_closing(self):
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()), \
                mock.patch.object(W, "SAVE_SIZE_MS", 50):
            win = Gtk.Window()
            W.remember_size(win, "testapp", 700, 500)
            win.present()
            spin(200)
            win.set_default_size(900, 640)               # what a resize does to GTK's default size
            spin(400)
            self.assertEqual(winsize.saved("testapp")["width"], 900)
            self.assertEqual(winsize.saved("testapp")["height"], 640)
            # (killed now, as by a restart: nothing more to save -- it's kept already)
            win.destroy()

    def test_web_apps_save_while_open(self):
        from sonata2 import webapps
        src = inspect.getsource(webapps)
        self.assertIn("winsize.save(size_key(app), *size)", src)


if __name__ == "__main__":
    unittest.main()
