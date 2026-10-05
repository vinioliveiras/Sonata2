"""Vini (Files vs Finder): Compress, Copy Path and Quick Actions (Rotate,
Convert) were missing."""
import inspect
import os
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from sonata2.files import actions as A  # noqa: E402


def wait(cond, s=5):
    end = time.time() + s
    while time.time() < end and not cond():
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def png(path, w=4, h=2, alpha=True):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alpha, 8, w, h)
    pb.fill(0xff000080)
    pb.savev(path, "png", [], [])


class ActionsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.undo = mock.patch("sonata2.files.undo.copied").start()
        self.addCleanup(mock.patch.stopall)

    def test_compress(self):
        os.makedirs(os.path.join(self.d, "Folder", "sub"))
        with open(os.path.join(self.d, "Folder", "sub", "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.d, "b.txt"), "w") as f:
            f.write("b")
        files = [Gio.File.new_for_path(os.path.join(self.d, n)) for n in ("Folder", "b.txt")]
        got = []
        A.compress(files, got.append)
        wait(lambda: got)
        self.assertEqual(got[0].get_basename(), "Archive.zip")
        names = zipfile.ZipFile(got[0].get_path()).namelist()
        self.assertIn("Folder/sub/a.txt", names)
        self.assertIn("b.txt", names)
        self.undo.assert_called_once()
        self.assertEqual(A.archive_name(files[:1]), "Folder.zip")

    def test_copy_path(self):
        f = [Gio.File.new_for_path("/home/vini/a b.txt"), Gio.File.new_for_path("/tmp/c")]
        self.assertEqual(A.paths_text(f), "/home/vini/a b.txt\n/tmp/c")

    def test_rotate(self):
        p = os.path.join(self.d, "p.png")
        png(p, 4, 2)
        done = []
        A.rotate([(Gio.File.new_for_path(p), "png")], True, lambda: done.append(1))
        wait(lambda: done)
        pb = GdkPixbuf.Pixbuf.new_from_file(p)
        self.assertEqual((pb.get_width(), pb.get_height()), (2, 4))

    def test_convert_to_jpeg(self):
        p = os.path.join(self.d, "p.png")
        png(p)
        done = []
        A.convert([Gio.File.new_for_path(p)], "jpeg", done.append)
        wait(lambda: done)
        self.assertTrue(os.path.exists(os.path.join(self.d, "p.jpg")))
        self.undo.assert_called_once()

    def test_in_the_menu(self):
        from sonata2.files import window
        src = inspect.getsource(window.FilesWindow._context_menu)
        for k in ("Compress", "Copy Path", "Quick Actions", "Rotate Left", "Convert to PNG"):
            self.assertIn(k, src)
        self.assertIn('"<Control><Alt>c"', inspect.getsource(window))


if __name__ == "__main__":
    unittest.main()
