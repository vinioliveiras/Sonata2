"""Vini: after Empty Trash the Dock's Trash still showed full. A file in
~/.local/share/Trash/files without its .trashinfo is invisible in the Trash
(so Empty Trash never erased it) but the Dock counted it. Now the Dock counts
what the Trash shows, and Empty Trash clears such leftovers."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

from sonata2.files import ops  # noqa: E402


def trash(names, orphans=(), lonely_info=()):
    t = ops.home_trash()
    for d in ("files", "info"):
        os.makedirs(os.path.join(t, d), exist_ok=True)
    for n in names:
        open(os.path.join(t, "files", n), "w").close()
        with open(os.path.join(t, "info", n + ".trashinfo"), "w") as f:
            f.write("[Trash Info]\nPath=/x\nDeletionDate=2026-10-06T10:00:00\n")
    for n in orphans:
        os.makedirs(os.path.join(t, "files", n))
    for n in lonely_info:
        open(os.path.join(t, "info", n + ".trashinfo"), "w").close()
    return t


class TrashTest(unittest.TestCase):
    def setUp(self):
        os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

    def test_leftovers_dont_count(self):
        trash(["a.txt"], orphans=["ghost"], lonely_info=["gone"])
        self.assertEqual(ops.trash_items(), ["a.txt"])
        trash([], orphans=["ghost2"])
        os.remove(os.path.join(ops.home_trash(), "files", "a.txt"))
        self.assertEqual(ops.trash_items(), [])                # only leftovers: empty

    def test_clear_orphans_keeps_real_items(self):
        t = trash(["keep.txt"], orphans=["ghost"], lonely_info=["gone"])
        ops.clear_orphans()
        self.assertEqual(sorted(os.listdir(os.path.join(t, "files"))), ["keep.txt"])
        self.assertEqual(sorted(os.listdir(os.path.join(t, "info"))), ["keep.txt.trashinfo"])

    def test_dock_and_its_menu_count_the_same(self):
        trash([], orphans=["ghost"])
        from sonata2.shell import dock_menu
        self.assertEqual(dock_menu._trash_count(), 0)
        import inspect
        from sonata2.shell import dock
        self.assertIn("full = bool(ops.trash_items())", inspect.getsource(dock.Dock._update_trash)
                      if hasattr(dock, "Dock") else inspect.getsource(dock))

    def test_empty_trash_clears_leftovers(self):
        import inspect
        src = inspect.getsource(ops.empty_trash)
        self.assertLess(src.index("_delete_all(kids, report)"), src.index("clear_orphans()"))


if __name__ == "__main__":
    unittest.main()
