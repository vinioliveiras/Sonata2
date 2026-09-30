"""User folders without user-dirs.dirs (sonata2/userdirs.py): the Camera
saved into ~/Camera because GLib answered the home folder.
Run: python3 -m unittest tests.test_userdirs"""
import os
import tempfile
import unittest

from gi.repository import GLib

from sonata2 import userdirs as D

P = GLib.UserDirectory.DIRECTORY_PICTURES


class UserDirsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._lang = os.environ.get("LANG")

    def tearDown(self):
        if self._lang is None:
            os.environ.pop("LANG", None)
        else:
            os.environ["LANG"] = self._lang

    def test_existing_folder_in_any_language(self):
        os.mkdir(os.path.join(self.home, "Imagens"))
        os.environ["LANG"] = "en_US.UTF-8"
        self.assertEqual(D.special(P, home=self.home), os.path.join(self.home, "Imagens"))

    def test_session_language_first(self):
        for n in ("Pictures", "Imagens"):
            os.mkdir(os.path.join(self.home, n))
        os.environ["LANG"] = "pt_BR.UTF-8"
        self.assertEqual(D.special(P, home=self.home), os.path.join(self.home, "Imagens"))

    def test_created_in_the_session_language_never_home(self):
        os.environ["LANG"] = "pt_BR.UTF-8"
        self.assertIsNone(D.special(P, home=self.home))
        path = D.special(P, create=True, home=self.home)
        self.assertEqual(path, os.path.join(self.home, "Imagens"))
        self.assertTrue(os.path.isdir(path))
        self.assertNotEqual(os.path.realpath(path), os.path.realpath(self.home))


if __name__ == "__main__":
    unittest.main()
