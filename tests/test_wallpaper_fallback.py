"""Vini: a friend's first login showed the bare gradient, not "Mountains" --
the picture taken over from his old desktop (KDE / GNOME's default) couldn't
be shown. Such a picture is never used: Sonata's default instead."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2 import config, prefs, wallpapers


class FallbackTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp()

    def test_missing_or_unreadable_gives_mountains(self):
        light, dark = wallpapers.default_uris()
        prefs.set_wallpaper("file:///usr/share/wallpapers/Gone/contents/images/1920x1080.png")
        self.assertEqual(prefs.wallpaper_uri(False), light)
        self.assertEqual(prefs.wallpaper_uri(True), dark)
        jxl = os.path.join(self.d, "adwaita-l.jxl")
        open(jxl, "wb").write(b"\xff\x0a not a picture any loader reads")
        prefs.set_wallpaper("file://" + jxl)
        self.assertEqual(prefs.wallpaper_uri(False), light)

    def test_a_real_picture_is_kept(self):
        mine = wallpapers.uri(wallpapers.CATALOG[4].light)
        prefs.set_wallpaper(mine)
        self.assertEqual(prefs.wallpaper_uri(False), mine)
        self.assertEqual(prefs.wallpaper_uri(True), mine)

    def test_first_session_takes_only_a_usable_picture(self):
        bad = "file:///usr/share/backgrounds/gnome/adwaita-l.jxl"
        with mock.patch.object(prefs, "_from_gsettings", side_effect=lambda s, k: {
                "picture-uri": bad, "picture-uri-dark": bad, "color-scheme": "prefer-dark"}.get(k)), \
                mock.patch.object(prefs, "_mirror"):
            prefs.session_env()
        data = prefs._load()
        self.assertNotIn(f"{prefs.BG}/picture-uri", data)
        self.assertEqual(prefs.get(prefs.BG, "picture-uri"), wallpapers.default_uris()[0])

    def test_every_reader_uses_it(self):
        import pathlib
        root = pathlib.Path(prefs.__file__).parent
        for f in ("shell/wallpaper.py", "shell/loginui.py", "settings/arrange.py"):
            src = (root / f).read_text()
            self.assertIn("prefs.wallpaper_uri(", src, f)
            self.assertNotIn('prefs.get(prefs.BG, "picture-uri', src, f)


if __name__ == "__main__":
    unittest.main()
