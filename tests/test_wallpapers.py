"""Sonata's own wallpapers (wallpapers.py, Settings > Wallpaper). Vini: a
cleaner default (mountains, a photo) and the other photos to pick from.
Run: python3 -m unittest tests.test_wallpapers"""
import os
import unittest

from sonata2 import prefs, wallpapers as W


class WallpapersTest(unittest.TestCase):
    def test_every_picture_and_thumb_ships(self):
        for w in W.CATALOG:
            for path in (w.light, w.dark, os.path.join(W.FOLDER, "thumbs", w.id + ".jpg")):
                self.assertTrue(os.path.exists(path), path)

    def test_default_is_mountains_light_and_dark(self):
        light, dark = W.default_uris()
        self.assertEqual(prefs.DEFAULTS[f"{prefs.BG}/picture-uri"], light)
        self.assertEqual(prefs.DEFAULTS[f"{prefs.BG}/picture-uri-dark"], dark)
        self.assertNotEqual(light, dark)                       # day and night
        self.assertEqual(W.current(light, dark), "mountains")

    def test_current(self):
        w = W.CATALOG[1]
        self.assertEqual(W.current(W.uri(w.light), ""), w.id)  # no dark key: the same picture
        self.assertEqual(W.current("file:///home/me/cat.jpg", "file:///home/me/cat.jpg"), "")

    def test_no_apple_names(self):
        for w in W.CATALOG:
            self.assertNotRegex(w.name.lower(), r"mac|ventura|sonoma|big sur|monterey|apple")
