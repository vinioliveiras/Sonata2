"""Vini: the menu bar named the WhatsApp web app (on Chrome) "Sonata2 Webapp
W6bf1bb01cf" -- the app_id index still pointed at an entry gone since (a web
app made again has a new id). A Chromium web app's window is found by
Sonata's own list of web apps; an index entry that is gone is rebuilt.
python3 -m unittest tests.test_webapp_menubar_name"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

from sonata2 import apps, webapps  # noqa: E402

WA = "chrome-web.whatsapp.com__-Default"


class NameTest(unittest.TestCase):
    def setUp(self):
        # (and the matches kept 10 s: one test's answer was the next one's)
        for cache in (apps._MISSES, apps._HITS):
            cache.clear()
            self.addCleanup(cache.clear)

    def test_chromium_web_app_found_by_its_window_class(self):
        entry = {"name": "WhatsApp", "url": "https://web.whatsapp.com/", "engine": "chromium"}
        with mock.patch.object(webapps, "apps", return_value={"w6bf1bb01cf": entry}), \
                mock.patch.object(webapps, "engine", return_value="chromium"), \
                mock.patch.object(apps, "lookup", side_effect=lambda d: d.startswith("sonata2-webapp-w6bf1bb01cf")):
            self.assertEqual(apps.match_app_id(WA), "sonata2-webapp-w6bf1bb01cf")

    def test_stale_index_built_again(self):
        built = [{WA.lower(): "sonata2-webapp-old"}, {WA.lower(): "sonata2-webapp-new"}]
        with mock.patch.object(webapps, "apps", return_value={}), \
                mock.patch.object(apps, "_INDEX", built[0]), \
                mock.patch.object(apps, "_build_index", side_effect=lambda: built[1]), \
                mock.patch.object(apps, "lookup", side_effect=lambda d: d == "sonata2-webapp-new"):
            self.assertEqual(apps.match_app_id(WA), "sonata2-webapp-new")

    def test_gone_everywhere_is_none(self):
        with mock.patch.object(webapps, "apps", return_value={}), \
                mock.patch.object(apps, "_INDEX", {WA.lower(): "sonata2-webapp-old"}), \
                mock.patch.object(apps, "_build_index", return_value={WA.lower(): "sonata2-webapp-old"}), \
                mock.patch.object(apps, "lookup", return_value=None), \
                mock.patch.object(apps, "scan", return_value={}):
            self.assertIsNone(apps.match_app_id(WA))


if __name__ == "__main__":
    unittest.main()
