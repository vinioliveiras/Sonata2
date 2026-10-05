"""Vini (Files vs Finder): no way to rename several items at once -- Finder's
"Rename N Items…" (Replace Text, Add Text, Format), one Undo for the batch."""
import inspect
import os
import tempfile
import time
import unittest

from gi.repository import Gio, GLib

from sonata2.files import batchrename as B
from sonata2.files import undo as U


class BatchRenameTest(unittest.TestCase):
    def test_rules(self):
        names = ["IMG_001.jpg", "IMG_002.jpg", "notes"]
        self.assertEqual(B.new_names(names, "replace", "IMG_", "Trip "), ["Trip 001.jpg", "Trip 002.jpg", "notes"])
        self.assertEqual(B.new_names(names[:1], "add", " v2", after=True), ["IMG_001 v2.jpg"])
        self.assertEqual(B.new_names(names[:1], "add", "Final ", after=False), ["Final IMG_001.jpg"])
        self.assertEqual(B.new_names(names[:2], "format", "Trip", start=9), ["Trip 09.jpg", "Trip 10.jpg"])
        self.assertEqual(B.new_names(["My Folder.d"], "add", "!", dirs=[True]), ["My Folder.d!"])   # folders whole

    def test_problems(self):
        self.assertTrue(B.problems(["a", "b"], ["x", "x"]))
        self.assertTrue(B.problems(["a"], ["x/y"]))
        self.assertEqual(B.problems(["a", "b"], ["x", "y"]), "")

    def test_rename_and_undo(self):
        U.history = U.History()
        d = tempfile.mkdtemp()
        for n in ("a.txt", "b.txt"):
            open(os.path.join(d, n), "w").close()
        files = [Gio.File.new_for_path(os.path.join(d, n)) for n in ("a.txt", "b.txt")]
        done = []
        B.rename(files, ["x 1.txt", "x 2.txt"], lambda: done.append(1))
        end = time.time() + 5
        while not done and time.time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(sorted(os.listdir(d)), ["x 1.txt", "x 2.txt"])
        U.history.take_undo().undo()
        self.assertEqual(sorted(os.listdir(d)), ["a.txt", "b.txt"])

    def test_wired(self):
        from sonata2.files import window
        self.assertIn("batchrename.dialog", inspect.getsource(window.FilesWindow.rename_selection))
        self.assertIn("Rename {n} Items…", inspect.getsource(window.FilesWindow._context_menu))


if __name__ == "__main__":
    unittest.main()
