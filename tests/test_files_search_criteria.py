"""Vini (Files vs Finder): search only matched names. Finder's criteria:
Name or Contents, a kind and a modification date."""
import inspect
import os
import tempfile
import time
import unittest

from gi.repository import GLib

from sonata2.files import search as S


def run(root, q, **kw):
    out, done = [], []
    S.Search().start(root, q, out.extend, done.append, **kw)
    end = time.time() + 5
    while not done and time.time() < end:
        GLib.MainContext.default().iteration(False)
    return sorted(i.get_name() for i in out)


class CriteriaTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for name, text in (("trip notes.txt", "Lisbon itinerary"), ("trip.jpg", ""), ("budget.txt", "trip money")):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(text)
        os.makedirs(os.path.join(self.d, "trip photos"))
        old = os.path.join(self.d, "trip old.txt")
        open(old, "w").close()
        os.utime(old, (time.time() - 60 * 86400,) * 2)

    def test_kinds(self):
        self.assertEqual(S.kind_of("a.jpg", False), "image")
        self.assertEqual(S.kind_of("a.tar.gz", False), "archive")
        self.assertEqual(S.kind_of("a.pdf", False), "document")
        self.assertEqual(S.kind_of("x", True), "folder")

    def test_name_only(self):
        self.assertEqual(run(self.d, "trip"), ["trip notes.txt", "trip old.txt", "trip photos", "trip.jpg"])

    def test_contents(self):
        self.assertIn("budget.txt", run(self.d, "trip", contents=True))
        self.assertEqual(run(self.d, "lisbon", contents=True), ["trip notes.txt"])

    def test_kind_and_date(self):
        self.assertEqual(run(self.d, "trip", kind="image"), ["trip.jpg"])
        self.assertEqual(run(self.d, "trip", kind="folder"), ["trip photos"])
        self.assertNotIn("trip old.txt", run(self.d, "trip", date="month"))
        self.assertIn("trip old.txt", run(self.d, "trip", date="any"))

    def test_since(self):
        now = time.time()
        self.assertEqual(S.since("any", now), 0)
        self.assertAlmostEqual(S.since("week", now), now - 7 * 86400)
        self.assertLessEqual(S.since("today", now), now)

    def test_bar_has_them(self):
        from sonata2.files import window
        src = inspect.getsource(window.FilesWindow._scope_bar)
        for k in ("Contents", "search_kind", "search_date"):
            self.assertIn(k, src)


if __name__ == "__main__":
    unittest.main()
