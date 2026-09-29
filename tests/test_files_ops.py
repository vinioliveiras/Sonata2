"""Files operations (xvfb): python3 -m unittest tests.test_files_ops"""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.files import ops  # noqa: E402


def spin(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class OpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = Gio.File.new_for_path(self.d)

    def p(self, *parts):
        return os.path.join(self.d, *parts)

    def test_names(self):
        open(self.p("a.txt"), "w").close()
        open(self.p("a copy.txt"), "w").close()
        self.assertEqual(ops.free_name(self.g, "a.txt"), "a copy 2.txt")
        self.assertEqual(ops.free_name(self.g, "b.tar.gz"), "b copy.tar.gz")
        self.assertEqual(ops.free_name(self.g, "untitled folder", True, "number"), "untitled folder")
        os.mkdir(self.p("untitled folder"))
        self.assertEqual(ops.free_name(self.g, "untitled folder", True, "number"), "untitled folder 2")
        self.assertEqual(ops.rename_selection("photo.jpeg", False), (0, 5))
        self.assertEqual(ops.rename_selection("my.folder", True), (0, 9))

    def test_new_folder_and_rename(self):
        made = []
        ops.new_folder(self.g, made.append, self.fail)
        self.assertTrue(spin(lambda: made))
        self.assertTrue(os.path.isdir(self.p("untitled folder")))
        renamed = []
        ops.rename(made[0], "Projects", renamed.append, self.fail)
        self.assertTrue(spin(lambda: renamed))
        self.assertTrue(os.path.isdir(self.p("Projects")))

    def test_copy_move_duplicate(self):
        os.makedirs(self.p("src", "sub"))
        with open(self.p("src", "sub", "f.bin"), "wb") as f:
            f.write(os.urandom(300_000))
        os.mkdir(self.p("dst"))
        done = []
        ops.Transfer([Gio.File.new_for_path(self.p("src"))], Gio.File.new_for_path(self.p("dst")),
                     on_done=lambda: done.append(1))
        self.assertTrue(spin(lambda: done))
        self.assertEqual(os.path.getsize(self.p("dst", "src", "sub", "f.bin")), 300_000)
        ops.Transfer([Gio.File.new_for_path(self.p("src"))], self.g, duplicate=True, on_done=lambda: done.append(2))
        self.assertTrue(spin(lambda: len(done) == 2))
        self.assertTrue(os.path.isfile(self.p("src copy", "sub", "f.bin")))
        os.mkdir(self.p("dst2"))
        ops.Transfer([Gio.File.new_for_path(self.p("src copy"))], Gio.File.new_for_path(self.p("dst2")), move=True,
                     on_done=lambda: done.append(3))
        self.assertTrue(spin(lambda: len(done) == 3))
        self.assertFalse(os.path.exists(self.p("src copy")))
        self.assertTrue(os.path.isfile(self.p("dst2", "src copy", "sub", "f.bin")))

    def test_clipboard_roundtrip(self):
        win = Gtk.Window()
        f = Gio.File.new_for_path(self.p("x"))
        ops.copy_to_clipboard(win, [f], cut=True)
        got = []
        ops.read_clipboard(win, lambda files, cut: got.append((files, cut)))
        self.assertTrue(spin(lambda: got))
        files, cut = got[0]
        self.assertTrue(cut)
        self.assertEqual([x.get_path() for x in files], [f.get_path()])


if __name__ == "__main__":
    unittest.main()
