"""Vini: a heavy folder sometimes takes a while to open: Finder's spinner
and "Loading…" then -- never for a quick one, gone once it is loaded.
xvfb-run -a python3 -m unittest tests.test_files_loading"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class LoadingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.filesloading")
        cls.app.register(None)

    def setUp(self):
        from sonata2.files.window import FilesWindow
        self.d = tempfile.mkdtemp()
        self.w = FilesWindow(self.app, Gio.File.new_for_path(self.d).get_uri())
        self.w.present()
        settle(400)

    def tearDown(self):
        self.w.destroy()

    def test_quick_folder_never_shows_it(self):
        tab = self.w.tab
        sub = os.path.join(self.d, "quick")
        os.mkdir(sub)
        shown = []
        tab.loading_box.connect("notify::visible", lambda b, _p: shown.append(b.get_visible()))
        self.w.go(Gio.File.new_for_path(sub).get_uri())
        settle(tab.LOADING_MS + 300)
        self.assertNotIn(True, shown)

    def test_slow_folder_shows_it_until_loaded(self):
        from sonata2.files import folder
        tab = self.w.tab
        sub = os.path.join(self.d, "slow")
        os.mkdir(sub)
        held = {}
        real = folder.Folder._fill

        def slow_fill(f, uri, items, keep_order=False):        # the folder answers late
            held["go"] = lambda: real(f, uri, items, keep_order)
        with mock.patch.object(folder.Folder, "_fill", slow_fill):
            self.w.go(Gio.File.new_for_path(sub).get_uri())
            settle(tab.LOADING_MS + 300)
            self.assertTrue(tab.loading_box.get_visible())
            self.assertTrue(tab.loading_box.get_first_child().spinning)
            held["go"]()
        self.assertFalse(tab.loading_box.get_visible())
        self.assertFalse(tab.loading_box.get_first_child().spinning)

    def test_failed_folder_hides_it(self):
        tab = self.w.tab
        tab.loading(True)
        settle(tab.LOADING_MS + 200)
        self.assertTrue(tab.loading_box.get_visible())
        with mock.patch.object(ui.dialog, "alert"):
            self.w._tab_failed(tab, "file:///nope", GLib.Error("x"))
        self.assertFalse(tab.loading_box.get_visible())


if __name__ == "__main__":
    unittest.main()
