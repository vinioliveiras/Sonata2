"""Settings > About > Reset (sonata2/factory_reset.py). Run:
python3 -m unittest tests.test_factory_reset"""
import json
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

from sonata2 import config, factory_reset as R, userdata  # noqa: E402


def write(path, text="{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ResetTest(unittest.TestCase):
    def setUp(self):
        c = config.CONFIG_DIR
        for n in ("wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1", "appearance.json", "topbar.json",
                  "launchpad.json", "security.json", "activity-history.json", "wayfire-overrides.ini",
                  "compositor-display-gpu"):
            write(os.path.join(c, n))
        config.save("dock", {"pinned": ["a.desktop", "folder:f1"], "folders": {"f1": {"name": "W", "apps": []}},
                             "icon_size": 90})
        self.cache = os.path.join(os.environ["XDG_CACHE_HOME"], "sonata2")
        write(os.path.join(self.cache, "app-icons", "x.png"), "x")
        write(os.path.join(self.cache, "dock.log"), "log")
        write(os.path.join(userdata.root(), "notes", "n.json"))

    def exists(self, n):
        return os.path.exists(os.path.join(config.CONFIG_DIR, n))

    def test_settings_only(self):
        R.settings_only()
        for n in ("appearance.json", "topbar.json", "wayfire-overrides.ini", "compositor-display-gpu"):
            self.assertFalse(self.exists(n), n)
        for n in ("launchpad.json", "security.json", "activity-history.json"):    # what you made
            self.assertTrue(self.exists(n), n)
        for n in ("wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1"):  # the session's
            self.assertTrue(self.exists(n), n)
        with open(os.path.join(config.CONFIG_DIR, "dock.json"), encoding="utf-8") as f:
            dock = json.load(f)
        self.assertEqual(dock["pinned"], ["a.desktop", "folder:f1"])            # Dock's apps and folders
        self.assertIn("f1", dock["folders"])
        self.assertNotIn("icon_size", dock)                                     # its options: defaults
        self.assertTrue(os.path.exists(os.path.join(self.cache, "app-icons", "x.png")))
        self.assertTrue(os.path.isdir(userdata.root()))

    def test_everything(self):
        from unittest import mock
        trashed = []
        with mock.patch.object(R.Gio.File, "new_for_path") as f:
            f.return_value.trash.side_effect = lambda _c: trashed.append(f.call_args[0][0])
            R.everything()
        for n in ("appearance.json", "dock.json", "launchpad.json", "security.json", "wayfire-overrides.ini"):
            self.assertFalse(self.exists(n), n)
        for n in ("wayfire.ini", "dconf-profile", "fonts.conf", ".defaults-v1"):  # the next login needs them
            self.assertTrue(self.exists(n), n)
        self.assertFalse(os.path.exists(os.path.join(self.cache, "app-icons")))
        self.assertTrue(os.path.exists(os.path.join(self.cache, "dock.log")))
        self.assertEqual(trashed, [userdata.root()])                            # to the Trash, never deleted


if __name__ == "__main__":
    unittest.main()
