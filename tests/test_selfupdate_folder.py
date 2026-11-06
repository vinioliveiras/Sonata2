"""The release tag names a folder that gets emptied first.

    python3 -m unittest tests.test_selfupdate_folder
"""
import os
import unittest

from sonata2.backend import selfupdate as S


class FolderNameTest(unittest.TestCase):
    def test_tag_emptied_a_folder_outside_the_cache(self):
        """A tag like "../.." (from GitHub or the command line) named the cache
        folder that is emptied before the download: one outside the cache."""
        for tag in ("../..", "..", ".", "", "a/../../b", "/etc"):
            name = S.folder_name(tag)
            self.assertNotIn("/", name)
            self.assertNotIn(name, ("", ".", ".."))
            base = os.path.realpath("/tmp/cache")
            self.assertEqual(os.path.dirname(os.path.realpath(os.path.join(base, name))), base)
        self.assertEqual(S.folder_name("v0.22.0-alpha"), "v0.22.0-alpha")
