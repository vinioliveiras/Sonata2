"""Vini: Bazaar (the Flatpak store) should wear the App Store icon -- the
Sonata theme (looked up first) links its names to MacTahoe's softwarecenter."""
import os
import unittest

ICONS = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "icons")


class BazaarIconTest(unittest.TestCase):
    def test_app_store_icon(self):
        store = os.path.realpath(os.path.join(ICONS, "Sonata-MacTahoe", "apps", "scalable", "softwarecenter.svg"))
        self.assertTrue(os.path.isfile(store))
        for name in ("io.github.kolunmi.Bazaar.svg", "Bazaar.svg"):
            path = os.path.join(ICONS, "Sonata", "apps", "scalable", name)
            self.assertTrue(os.path.islink(path), name)
            self.assertFalse(os.path.isabs(os.readlink(path)))           # relative: works wherever installed
            self.assertEqual(os.path.realpath(path), store)


if __name__ == "__main__":
    unittest.main()
