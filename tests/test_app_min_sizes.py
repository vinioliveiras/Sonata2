"""Every Sonata app window has a smallest size (Vini: the music / video
player had none and broke; then: check the other apps). Preview, Terminal,
TextEdit, Disk Manager and Calendar had none either -- Preview shrank to
348 x 90, TextEdit to one line under its toolbar. Each window here is
opened, asked to be tiny, and must stay at least its minimum.
Run: dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_app_min_sizes"""
import os
import re
import shutil
import tempfile
import unittest

for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[v] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# app -> its main window's file (each sets a smallest size; Calculator: fixed size)
WINDOWS = {"files": "files/window.py", "settings": "settings/app.py", "activity": "activity/window.py",
           "clock": "clock/window.py", "notes": "notes/window.py", "assistant": "assistant/window.py",
           "feedback": "feedback/window.py", "preview": "preview/window.py", "terminal": "terminal/window.py",
           "textedit": "textedit/window.py", "diskutil": "diskutil/window.py", "calendar": "calendar/window.py",
           "camera": "camera/window.py", "webapps": "webapps/window.py", "videos": "videos/window.py"}


def settle(ms=600):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class SourceTest(unittest.TestCase):
    def test_every_app_window_sets_a_smallest_size(self):
        for app, rel in WINDOWS.items():
            with open(os.path.join(REPO, "sonata2", rel)) as f:
                src = f.read()
            self.assertRegex(src, re.compile(r"self\.set_size_request\(|set_size_request\(w or -1"), app)

    def test_calculator_is_fixed_size(self):
        with open(os.path.join(REPO, "sonata2", "calculator", "window.py")) as f:
            self.assertIn("resizable=False", f.read())


class OpenedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.minsizes")
        cls.app.register(None)
        cls.home = tempfile.mkdtemp()
        with open(os.path.join(cls.home, "a.txt"), "w") as f:
            f.write("hello")
        cls.pic = os.path.join(cls.home, "p.jpg")
        shutil.copy(os.path.join(REPO, "sonata2", "data", "wallpapers", "thumbs", "mountains.jpg"), cls.pic)

    def tiny(self, win, minimum):
        win.set_default_size(60, 60)
        win.present()
        settle()
        try:
            self.assertGreaterEqual(win.get_width(), minimum[0], type(win).__name__)
            self.assertGreaterEqual(win.get_height(), minimum[1], type(win).__name__)
        finally:
            win.destroy()
            settle(100)

    def test_preview(self):
        from sonata2.preview import window as W
        self.tiny(W.PreviewWindow(self.app, self.pic), W.MIN_SIZE)

    def test_terminal(self):
        from sonata2.terminal import window as W
        self.tiny(W.TerminalWindow(self.app, self.home), W.MIN_SIZE)

    def test_textedit(self):
        from sonata2.textedit import window as W
        self.tiny(W.TextEditWindow(self.app, os.path.join(self.home, "a.txt")), W.MIN_SIZE)

    def test_diskutil(self):
        from sonata2.diskutil import window as W
        self.tiny(W.DiskUtilityWindow(self.app), W.MIN_SIZE)

    def test_calendar(self):
        from sonata2.calendar import window as W
        self.tiny(W.CalendarWindow(self.app, tempfile.mkdtemp()), W.MIN_SIZE)


if __name__ == "__main__":
    unittest.main()
