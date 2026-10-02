"""Folders linked between the Dock and Launchpad (sonata2/folder_link.py). Run:
python3 -m unittest tests.test_folder_link"""
import json
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import config, folder_link as L, launchpad_model as M  # noqa: E402


def raw(name):
    with open(os.path.join(config.CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.lp_folder = {"folder": "Work", "apps": ["a", "b"], "link": "L1"}
        config.save("launchpad", {"pages": [["x", self.lp_folder, "c", {"folder": "Other", "apps": ["d", "e"]}]],
                                  "hidden": ["h"]})
        self.dock = {"1": {"name": "Work", "apps": ["a", "b"], "link": "L1"},
                     "2": {"name": "Solo", "apps": ["d", "e"]}}
        config.save("dock", {"pinned": ["folder:1", "folder:2"], "folders": self.dock, "icon_size": 50})

    def lp_item(self, link="L1"):
        return next(it for it in raw("launchpad")["pages"][0] if isinstance(it, dict) and it.get("link") == link)

    def test_dock_rename_reaches_launchpad(self):
        self.dock["1"]["name"] = "Games"
        self.assertTrue(L.to_launchpad(self.dock))
        self.assertEqual(self.lp_item()["folder"], "Games")
        self.assertFalse(L.to_launchpad(self.dock))                         # nothing new: not written again

    def test_dock_app_in_and_out(self):
        del self.dock["2"]
        self.dock["1"]["apps"] = ["a", "c", "d"]                            # b out; c (top) and d (Other) in
        L.to_launchpad(self.dock)
        page = raw("launchpad")["pages"][0]
        self.assertEqual(self.lp_item()["apps"], ["a", "c", "d"])
        self.assertNotIn("c", page)                                         # left its own place
        other = next(it for it in page if isinstance(it, dict) and it["folder"] == "Other")
        self.assertEqual(other["apps"], ["e"])                              # (Launchpad dissolves it on load)
        self.assertEqual(page[-1], "b")                                     # taken out: at the end

    def test_hidden_apps_stay_hidden(self):
        self.dock["1"]["apps"] = ["a", "b", "h"]
        L.to_launchpad(self.dock)
        self.assertEqual(self.lp_item()["apps"], ["a", "b"])
        self.assertEqual(raw("launchpad")["hidden"], ["h"])

    def test_launchpad_changes_reach_dock(self):
        data = raw("launchpad")
        f = next(it for it in data["pages"][0] if isinstance(it, dict) and it.get("link") == "L1")
        f["folder"], f["apps"] = "Games", ["b", "a", "x"]
        self.assertTrue(L.to_dock(data))
        d = raw("dock")
        self.assertEqual(d["folders"]["1"], {"name": "Games", "apps": ["b", "a", "x"], "link": "L1"})
        self.assertEqual((d["pinned"], d["icon_size"]), (["folder:1", "folder:2"], 50))   # the rest as stored
        self.assertFalse(L.to_dock(data))

    def test_dock_keeps_its_hidden_apps(self):
        self.dock["1"]["apps"] = ["a", "b", "h"]
        config.update("dock", folders=self.dock)
        data = raw("launchpad")
        self.assertFalse(L.to_dock(data))                                   # h is hidden there, not removed
        self.assertEqual(raw("dock")["folders"]["1"]["apps"], ["a", "b", "h"])

    def test_old_folders_linked_by_name_and_apps(self):
        data = raw("launchpad")
        other = next(it for it in data["pages"][0] if isinstance(it, dict) and it["folder"] == "Other")
        other["folder"] = "Solo"
        config.save("launchpad", data)
        self.dock["2"]["name"] = "Solo"
        L.to_launchpad(self.dock)
        link = self.dock["2"].get("link")
        self.assertTrue(link)
        self.assertEqual(raw("dock")["folders"]["2"]["link"], link)
        self.assertEqual(self.lp_item(link)["apps"], ["d", "e"])

    def test_dock_made_folder_appears_in_launchpad(self):
        """Vini: a folder made in the Dock shows in Launchpad too (linked)."""
        self.dock["2"]["name"] = "Games"
        L.to_launchpad(self.dock)
        link = self.dock["2"]["link"]
        item = self.lp_item(link)
        self.assertEqual((item["folder"], item["apps"]), ("Games", ["d", "e"]))
        self.assertEqual(raw("dock")["folders"]["2"]["link"], link)          # linked from now on
        self.assertFalse(L.to_launchpad(self.dock))                          # added once

    def test_locked_dock_folder_stays_private(self):
        self.dock["2"]["locked"] = True
        L.to_launchpad(self.dock)
        self.assertNotIn("link", self.dock["2"])
        names = [it["folder"] for it in raw("launchpad")["pages"][0] if isinstance(it, dict)]
        self.assertEqual(names, ["Work", "Other"])


class ModelTest(unittest.TestCase):
    def test_link_kept_through_reconcile(self):
        m = M.Model({"pages": [[{"folder": "W", "apps": ["a", "b"], "link": "L1"}]]}, {"a": "A", "b": "B"})
        self.assertEqual(m.pages[0][0]["link"], "L1")
        self.assertEqual(m.to_json()["pages"][0][0]["link"], "L1")

    def test_drag_carries_the_link(self):
        f = M.decode_folder(M.encode_folder("W", ["a", "b"], "L1"))
        self.assertEqual(f, {"folder": "W", "apps": ["a", "b"], "link": "L1"})
        self.assertNotIn("link", M.decode_folder(M.encode_folder("W", ["a", "b"])))

    def test_dragged_back_replaces_its_copy(self):
        inst = {k: k.upper() for k in "abcx"}
        m = M.Model({"pages": [["x", {"folder": "W", "apps": ["a", "b", "c"], "link": "L1"}]]}, inst)
        m.add_folder("W2", ["a", "b"], 0, 0, link="L1")
        page = m.pages[0]
        folders = [it for it in page if M.is_folder(it)]
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0], {"folder": "W2", "apps": ["a", "b"], "link": "L1"})
        self.assertIn("c", page)                                            # its other app stays


if __name__ == "__main__":
    unittest.main()
