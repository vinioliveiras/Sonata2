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



class DockFolderTransferTest(unittest.TestCase):
    """Folders dragged between Launchpad and the Dock travel as text."""

    def test_round_trip(self):
        t = M.encode_folder("Games & \u00e9 \"x\"", ["a.desktop", "b"])
        self.assertTrue(t.startswith(M.FOLDER_SCHEME))
        self.assertNotIn(" ", t)                              # one URI line in a uri-list
        self.assertEqual(M.decode_folder(t), {"folder": "Games & \u00e9 \"x\"", "apps": ["a.desktop", "b"]})

    def test_decode_rejects(self):
        for bad in (None, "", "org.app.desktop", "file:///x", M.FOLDER_SCHEME + "nope",
                    M.FOLDER_SCHEME + "%5B1%5D", M.encode_folder("x", []).replace("apps", "nope")):
            self.assertIsNone(M.decode_folder(bad), bad)

    def test_add_folder_gathers_apps(self):
        m = M.Model({"pages": [["app000", {"folder": "F", "apps": ["app001", "app002", "app003"]}, "app004"]]},
                    installed(6))
        item = m.add_folder("Work", ["app000", "app002", "gone", "app000"], 0, 1)
        self.assertEqual(item, {"folder": "Work", "apps": ["app000", "app002"]})
        flat = m.pages[0]
        self.assertIn(item, flat)
        self.assertEqual(sorted(m.all_apps()), [f"app{i:03d}" for i in range(6)])     # nothing lost or doubled
        self.assertEqual(m.find("app001")[2]["apps"], ["app001", "app003"])

    def test_add_folder_single_and_none(self):
        m = M.Model({}, installed(3))
        self.assertEqual(m.add_folder("X", ["app001", "gone"], 0, 0), "app001")
        self.assertEqual(m.pages[0][0], "app001")
        self.assertIsNone(m.add_folder("X", ["gone"], 0, 0))
        m.hidden.append("app002")
        self.assertEqual(m.add_folder("X", ["app002", "app000"], 0, 0), "app000")      # hidden stays hidden


if __name__ == "__main__":
    unittest.main()


class GridRepackTest(unittest.TestCase):
    """Vini: the fifth row of the Apps page stayed empty -- the pages were
    filled for a 7x4 grid, and the 7x5 grid (the default) changed nothing."""

    def test_pages_remember_their_grid(self):
        from sonata2 import launchpad_model as M
        old = (M.COLS, M.ROWS)
        try:
            M.set_grid(7, 5)
            names = {f"a{i:02}": f"A{i:02}" for i in range(52)}
            ids = sorted(names)
            m = M.Model({"pages": [ids[:28], ids[28:]]}, names)
            self.assertEqual(m.grid, ())                         # an old file: size unknown
            m.repack()
            self.assertEqual([len(p) for p in m.pages], [35, 17])
            self.assertEqual(m.to_json()["grid"], [7, 5])
            again = M.Model(m.to_json(), names)
            self.assertEqual(again.grid, (7, 5))                 # kept: no repack next time
        finally:
            M.set_grid(*old)

    def test_launchpad_repacks_when_the_grid_differs(self):
        import pathlib
        from sonata2 import launchpad_model as M
        self.assertIn("grid", M.DEFAULTS)                        # config.load keeps it
        src = (pathlib.Path(__file__).resolve().parent.parent / "sonata2" / "shell" / "launchpad.py").read_text()
        self.assertIn("if M.set_grid(cols, rows) or self.model.grid != (M.COLS, M.ROWS):", src)
        self.assertNotIn('config.load("launchpad", {"pages": [], "hidden": []})', src)
