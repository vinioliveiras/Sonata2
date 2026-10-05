"""Vini (Files vs Finder): no Undo. Ctrl+Z / Ctrl+Shift+Z undo and redo a
rename, a move, a copy, a new folder and Move to Trash."""
import inspect
import os
import tempfile
import unittest

from gi.repository import Gio

from sonata2.files import undo as U


def gf(*p):
    return Gio.File.new_for_path(os.path.join(*p))


class UndoTest(unittest.TestCase):
    def setUp(self):
        U.history = U.History()
        self.d = tempfile.mkdtemp()

    def run_undo(self):
        a = U.history.take_undo()
        a.undo()
        return a

    def run_redo(self):
        a = U.history.take_redo()
        a.redo()
        return a

    def test_rename(self):
        open(os.path.join(self.d, "a.txt"), "w").close()
        new = gf(self.d, "a.txt").set_display_name("b.txt", None)
        U.renamed(new, "a.txt")
        self.run_undo()
        self.assertTrue(os.path.exists(os.path.join(self.d, "a.txt")))
        self.run_redo()
        self.assertTrue(os.path.exists(os.path.join(self.d, "b.txt")))

    def test_move(self):
        os.makedirs(os.path.join(self.d, "sub"))
        open(os.path.join(self.d, "f"), "w").close()
        gf(self.d, "f").move(gf(self.d, "sub", "f"), Gio.FileCopyFlags.NONE, None, None, None)
        U.moved([(gf(self.d, "f"), gf(self.d, "sub", "f"))])
        self.run_undo()
        self.assertTrue(os.path.exists(os.path.join(self.d, "f")))
        self.run_redo()
        self.assertTrue(os.path.exists(os.path.join(self.d, "sub", "f")))

    def test_new_folder_removed_while_empty(self):
        gf(self.d, "untitled folder").make_directory(None)
        U.new_folder(gf(self.d, "untitled folder"))
        self.run_undo()
        self.assertFalse(os.path.exists(os.path.join(self.d, "untitled folder")))

    def test_redo_cleared_by_a_new_action(self):
        U.history.push(U.Action("a", lambda: None, lambda: None))
        U.history.take_undo()
        self.assertTrue(U.history.can_redo())
        U.history.push(U.Action("b", lambda: None))
        self.assertFalse(U.history.can_redo())

    def test_limit(self):
        for i in range(U.LIMIT + 10):
            U.history.push(U.Action(str(i), lambda: None))
        self.assertEqual(len(U.history.done), U.LIMIT)

    def test_wired(self):
        from sonata2.files import ops, window
        src = inspect.getsource(window)
        for k in ('"<Control>z"', '"<Control><Shift>z"', "undo.renamed", "undo.new_folder", "undo.trashed"):
            self.assertIn(k, src)
        self.assertIn("undo.moved", inspect.getsource(ops.Transfer))
        self.assertIn("undo.copied", inspect.getsource(ops.Transfer))


if __name__ == "__main__":
    unittest.main()
