"""Launchpad layout logic (no display needed): python3 -m unittest tests.test_launchpad_model"""
import unittest

from sonata2 import launchpad_model as M


def installed(n):
    return {f"app{i:03d}": f"App {i:03d}" for i in range(n)}


class ModelTest(unittest.TestCase):
    def test_new_apps_fill_pages(self):
        m = M.Model({}, installed(80))
        self.assertEqual([len(p) for p in m.pages], [35, 35, 10])
        self.assertEqual(m.pages[0][0], "app000")

    def test_reconcile_drops_missing_and_dissolves(self):
        data = {"pages": [["gone", {"folder": "F", "apps": ["app001", "gone2"]}, "app002"]]}
        m = M.Model(data, installed(3))
        self.assertEqual(m.pages, [["app001", "app002", "app000"]])

    def test_overflow_cascades(self):
        m = M.Model({}, installed(35))
        m.pages.append(["x"])
        m.installed["x"] = "X"
        m.move("x", 0, 0)
        self.assertEqual(m.pages[0][0], "x")
        self.assertEqual(len(m.pages[0]), 35)
        self.assertEqual(m.pages[1], ["app034"])

    def test_make_and_dissolve_folder(self):
        m = M.Model({}, installed(4))
        f = m.make_folder("app000", "app003", "Games")
        self.assertEqual(m.pages[0][0], {"folder": "Games", "apps": ["app000", "app003"]})
        self.assertNotIn("app003", m.pages[0][1:])
        m.make_folder(f, "app001", "x")
        self.assertEqual(f["apps"], ["app000", "app003", "app001"])
        m.take_out_of_folder(f, "app001", 0, 5)
        m.take_out_of_folder(f, "app003", 0, 5)
        self.assertEqual(m.pages[0][0], "app000")     # 1 app left -> plain app again
        self.assertCountEqual(m.all_apps(), list(installed(4)))

    def test_hide(self):
        m = M.Model({}, installed(3))
        m.hide("app001")
        self.assertNotIn("app001", m.all_apps())
        m2 = M.Model(m.to_json(), installed(3))       # stays hidden after reload
        self.assertNotIn("app001", m2.all_apps())

    def test_folder_name(self):
        self.assertEqual(M.folder_name(["Game", "Network"], ["Game"]), "Games")
        self.assertEqual(M.folder_name(["Utility"], ["Office"]), "Utilities")
        self.assertEqual(M.folder_name([], []), "Untitled Folder")

    def test_search_ranking(self):
        meta = {"a": ("Firefox", "web browser"), "b": ("GNOME Web", "browser"),
                "c": ("Files", "file manager"), "d": ("Terminal", "")}
        self.assertEqual(M.search(meta, "fi"), ["c", "a"])
        self.assertEqual(M.search(meta, "web"), ["b", "a"])
        self.assertEqual(M.search(meta, "browser"), ["a", "b"])
        self.assertEqual(M.search(meta, " "), [])


if __name__ == "__main__":
    unittest.main()
