"""Sonata's own desktop settings and the portal's view of them
(python3 -m unittest tests.test_prefs)."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["PATH"] = "/nonexistent"          # no gsettings: nothing may depend on GNOME

from sonata2 import config, prefs  # noqa: E402

config.CONFIG_DIR = os.path.join(os.environ["XDG_CONFIG_HOME"], "sonata2")


class PrefsTest(unittest.TestCase):
    def test_defaults_and_set(self):
        self.assertEqual(prefs.get(prefs.I, "color-scheme"), "default")
        prefs.set(prefs.I, "color-scheme", "'prefer-dark'")
        self.assertEqual(prefs.get(prefs.I, "color-scheme"), "prefer-dark")
        prefs.set(prefs.P, "old-files-age", "uint32 30")
        self.assertEqual(prefs.typed(prefs.P, "old-files-age"), ("i", 30))
        self.assertEqual(prefs.typed(prefs.I, "enable-animations"), ("b", True))

    def test_portal_values(self):
        from sonata2 import portal
        prefs.set(prefs.I, "color-scheme", "prefer-dark")
        vals = portal.settings_values()
        self.assertEqual(vals["org.freedesktop.appearance"]["color-scheme"].unpack(), 1)
        self.assertEqual(vals[prefs.I]["cursor-size"].unpack(), 24)
        self.assertTrue(portal._matches("org.gnome.desktop.interface", ["org.gnome.*"]))
        self.assertFalse(portal._matches("org.gnome.desktop.interface", ["org.freedesktop.appearance"]))


if __name__ == "__main__":
    unittest.main()
