"""Memory review: Search's file index held up to 60k (name lower, path,
is_dir) tuples (~15 MB) for the session. Now compact (spotlight._Names);
the results must stay exactly what the tuple loop gave, and as fast.
Run: xvfb-run -a python3 -m unittest tests.test_spotlight_index"""
import os
import random
import tempfile
import time
import unittest
from unittest import mock

from sonata2.shell import spotlight as S


def old_entries(home):
    """The index as the tuple version built it."""
    out = []
    base_depth = home.rstrip("/").count("/")
    for root, dirs, files in os.walk(home, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        if root.count("/") - base_depth >= S.INDEX_DEPTH:
            dirs[:] = []
        for d in dirs:
            out.append((d.lower(), os.path.join(root, d), True))
        for f in files:
            if not f.startswith("."):
                out.append((f.lower(), os.path.join(root, f), False))
        if len(out) > S.MAX_FILES:
            break
    return out


def old_search(entries, q):
    q = q.lower()
    pre, sub = [], []
    for name, path, d in entries:
        if name.startswith(q):
            pre.append((len(name), path, d))
        elif q in name:
            sub.append((len(name), path, d))
        if len(pre) > 200:
            break
    pre.sort()
    sub.sort()
    return [(p, d) for _n, p, d in pre + sub]


def make_home(n_files=3000):
    home = tempfile.mkdtemp()
    rnd = random.Random(7)
    words = ["Report", "photo", "a", "ab", "Notes", "budget", "İstanbul", "ÉTÉ", "x", "main", "readme", "Ab"]
    dirs = [home]
    for i in range(n_files):
        parent = rnd.choice(dirs)
        name = "".join(rnd.choice(words) for _ in range(rnd.randint(1, 3))) + str(rnd.randint(0, 99))
        path = os.path.join(parent, name)
        if os.path.exists(path):
            continue
        if rnd.random() < 0.12 and parent.count("/") - home.count("/") < 7:
            os.mkdir(path)
            dirs.append(path)
        else:
            open(path + rnd.choice(["", ".txt", ".png"]), "w").close()
    os.mkdir(os.path.join(home, ".hidden"))
    open(os.path.join(home, ".hidden", "report.txt"), "w").close()
    os.mkdir(os.path.join(home, "node_modules"))
    return home


class SpotlightIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = make_home()
        cls.index = S.Index()
        with mock.patch.object(S.GLib, "get_home_dir", return_value=cls.home):
            cls.index._build()
        cls.old = old_entries(cls.home)

    def test_same_entries(self):
        self.assertEqual(len(self.index), len(self.old))
        self.assertEqual([self.index.data.entry(i) for i in range(len(self.index))],
                         [(p, d) for _n, p, d in self.old])

    def test_same_results(self):
        for q in ("", "a", "ab", "AB", "report", "port", "photo1", "istanbul", "i̇stanbul", "été", "1",
                  "notes", "zzz", "txt", ".png", "b", "readme9"):
            self.assertEqual(self.index.search(q), old_search(self.old, q), q)

    def test_empty_and_odd(self):
        self.assertEqual(S.Index().search("abc"), [])
        self.assertEqual(self.index.search("a\nb"), [])

    def test_smaller_and_not_slower(self):
        from tracemalloc import start, stop, take_snapshot
        start()
        idx = S.Index()
        with mock.patch.object(S.GLib, "get_home_dir", return_value=self.home):
            idx._build()
        new = sum(st.size for st in take_snapshot().statistics("filename"))
        stop()
        start()
        old = old_entries(self.home)
        before = sum(st.size for st in take_snapshot().statistics("filename"))
        stop()
        self.assertLess(new, before * 0.6)
        t0 = time.perf_counter()
        for q in ("re", "port", "a", "budget"):
            old_search(old, q)
        t_old = time.perf_counter() - t0
        t0 = time.perf_counter()
        for q in ("re", "port", "a", "budget"):
            idx.search(q)
        self.assertLess(time.perf_counter() - t0, t_old * 1.5 + 0.005)


if __name__ == "__main__":
    unittest.main()
