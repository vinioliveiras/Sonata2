"""Vini: Settings > About > Backup -- Sonata's settings (and its apps'
data) exported to one file and imported back."""
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from sonata2 import backup, config, userdata


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.data, self.out = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
        self.p = [mock.patch.object(config, "CONFIG_DIR", self.cfg),
                  mock.patch.object(userdata, "root", return_value=self.data),
                  mock.patch.object(backup, "_trash_all", side_effect=self.trash)]
        for p in self.p:
            p.start()
        self.trashed = []
        self.write(self.cfg, "appearance.json", '{"accent": "pink"}')
        self.write(self.cfg, "dock.json", '{"pinned": ["firefox"]}')
        self.write(self.cfg, "wayfire.ini", "[core]")                       # the session's: never in it
        self.write(os.path.join(self.data, "notes"), "n.json", "[1]")

    def tearDown(self):
        for p in self.p:
            p.stop()

    def trash(self, root, skip=()):
        for n in os.listdir(root):
            if n not in skip:
                self.trashed.append(n)
                p = os.path.join(root, n)
                (os.remove if os.path.isfile(p) else __import__("shutil").rmtree)(p)

    @staticmethod
    def write(folder, name, text):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "w") as f:
            f.write(text)

    def test_export_and_restore(self):
        path = os.path.join(self.out, backup.default_name())
        self.assertTrue(path.endswith(".sonata-backup"))
        n = backup.export(path)
        self.assertEqual(n, 3)
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        self.assertEqual(names, {"sonata-backup.json", "config/appearance.json", "config/dock.json",
                                 "data/notes/n.json"})
        self.assertIsNotNone(backup.last_export())
        m = backup.read_manifest(path)
        self.assertTrue(m["data"])
        # changed since: the backup's come back, the session file stays
        self.write(self.cfg, "appearance.json", '{"accent": "blue"}')
        self.write(self.cfg, "extra.json", "{}")
        self.assertTrue(backup.restore(path))
        self.assertEqual(json.load(open(os.path.join(self.cfg, "appearance.json")))["accent"], "pink")
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "extra.json")))       # yours went to the Trash
        self.assertIn("extra.json", self.trashed)
        self.assertTrue(os.path.exists(os.path.join(self.cfg, "wayfire.ini")))
        self.assertTrue(os.path.exists(os.path.join(self.data, "notes", "n.json")))

    def test_settings_only(self):
        path = os.path.join(self.out, "s.sonata-backup")
        backup.export(path, data=False)
        with zipfile.ZipFile(path) as z:
            self.assertFalse(any(n.startswith("data/") for n in z.namelist()))
        self.trashed.clear()
        backup.restore(path)
        self.assertNotIn("notes", self.trashed)                 # no data in it: yours untouched

    def test_not_a_backup_and_unsafe_names(self):
        bad = os.path.join(self.out, "x.sonata-backup")
        with open(bad, "w") as f:
            f.write("nope")
        self.assertIsNone(backup.read_manifest(bad))
        self.assertFalse(backup.restore(bad))
        evil = os.path.join(self.out, "evil.sonata-backup")
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("sonata-backup.json", json.dumps({"format": 1}))
            z.writestr("config/../../escaped.json", "{}")
            z.writestr("config/wayfire.ini", "[evil]")
        self.assertTrue(backup.restore(evil))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.cfg), "escaped.json")))
        self.assertEqual(open(os.path.join(self.cfg, "wayfire.ini")).read(), "[core]")

    def test_settings_page_has_it(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "sonata2", "settings", "app.py")).read()
        self.assertIn("backup = self._backup_group()", src)
        self.assertIn("return [hero, specs, shell, backup, reset]", src)
        self.assertIn('self.ask_restart("session", "The imported settings")', src)


if __name__ == "__main__":
    unittest.main()
