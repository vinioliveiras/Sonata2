"""Vini: deleting, moving or adding items made the grid jump. The items
still there slide to their new place, the new ones fade in."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402


def spin(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class ReflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.reflowtest")
        cls.app.register(None)

    def test_slide_and_fade(self):
        from sonata2.files.window import FilesWindow
        d = tempfile.mkdtemp()
        for i in range(10):
            open(os.path.join(d, f"f{i:02d}.txt"), "w").close()
        w = FilesWindow(self.app, Gio.File.new_for_path(d).get_uri())
        w.set_default_size(800, 500)
        w.present()
        w.set_view("icons")
        v = w.views["icons"]
        self.assertTrue(spin(lambda: len(v._cells) == 10))
        spin(lambda: False, 300)
        os.unlink(os.path.join(d, "f01.txt"))
        open(os.path.join(d, "f05b.txt"), "w").close()
        moved, faded = set(), set()

        def seen():
            for info, box in v._cells.items():
                if getattr(box, "_glide", (0, 0)) != (0, 0):
                    moved.add(info.get_name())
                if box.get_opacity() < 1:
                    faded.add(info.get_name())
            return "f02.txt" in moved and "f05b.txt" in faded
        self.assertTrue(spin(seen), (moved, faded))
        self.assertNotIn("f07.txt", moved)                  # its place didn't change: it stays put
        self.assertTrue(spin(lambda: all(getattr(b, "_glide", (0, 0)) == (0, 0) and b.get_opacity() == 1
                                         for b in v._cells.values())))
        w.destroy()


if __name__ == "__main__":
    unittest.main()
