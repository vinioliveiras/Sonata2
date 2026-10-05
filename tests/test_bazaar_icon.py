"""Vini: Bazaar (the Flatpak store) should wear the App Store icon -- the
Sonata theme (looked up first) carries MacTahoe's softwarecenter under its
names (copies: a symlink didn't survive every checkout / install)."""
import os
import unittest

ICONS = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "icons")


class BazaarIconTest(unittest.TestCase):
    def test_app_store_icon(self):
        store = os.path.realpath(os.path.join(ICONS, "Sonata-MacTahoe", "apps", "scalable", "softwarecenter.svg"))
        self.assertTrue(os.path.isfile(store))
        for name in ("io.github.kolunmi.Bazaar.svg", "Bazaar.svg"):
            path = os.path.join(ICONS, "Sonata", "apps", "scalable", name)
            with open(path, "rb") as a, open(store, "rb") as b:
                self.assertEqual(a.read(), b.read(), name)


if __name__ == "__main__":
    unittest.main()
