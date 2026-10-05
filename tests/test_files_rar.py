"""Vini: RAR (and 7-Zip) archives open in Files like a .zip -- double-click
or Extract Here expands them next to themselves, with 7-Zip, unrar or
bsdtar (libarchive: always there on Arch / CachyOS)."""
import os
import stat
import tempfile
import unittest
from unittest import mock

from sonata2.files import packages as P


def _fake_tool(d, name, script):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\n" + script)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


class RarTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp()
        self.d = tempfile.mkdtemp()
        self.rar = os.path.join(self.d, "Fotos.rar")
        open(self.rar, "w").close()

    def which(self, names):
        return lambda t: os.path.join(self.bin, t) if t in names else None

    def test_kind_and_folder(self):
        self.assertEqual(P.kind("a/Fotos.RAR"), "archive")
        self.assertEqual(P.kind("x.7z"), "archive")
        self.assertEqual(P._extract_dir(self.rar), os.path.join(self.d, "Fotos"))
        self.assertEqual([i.label for i in P.menu_items(self.rar)], ["Extract Here"])

    def test_tool_order(self):
        with mock.patch.object(P.shutil, "which", self.which({"bsdtar", "unrar"})):
            self.assertEqual(os.path.basename(P.tool_command(self.rar, "/t")[0]), "unrar")
        with mock.patch.object(P.shutil, "which", self.which({"bsdtar"})):
            self.assertEqual(P.tool_command(self.rar, "/t")[1:], ["-xf", self.rar, "-C", "/t"])
        with mock.patch.object(P.shutil, "which", self.which({"7z", "bsdtar"})):
            argv = P.tool_command(os.path.join(self.d, "a.7z"), "/t")
            self.assertEqual(os.path.basename(argv[0]), "7z")
            self.assertIn("-o/t", argv)
        with mock.patch.object(P.shutil, "which", self.which({"unrar"})):
            self.assertIsNone(P.tool_command("a.7z", "/t"))          # unrar can't do 7z

    def test_extracts_and_reports(self):
        _fake_tool(self.bin, "bsdtar", 'touch "$4/inside.txt"\n')
        target = os.path.join(self.d, "Fotos")
        os.makedirs(target)
        with mock.patch.object(P.shutil, "which", self.which({"bsdtar"})):
            P._run_tool(self.rar, target)
        self.assertTrue(os.path.exists(os.path.join(target, "inside.txt")))
        _fake_tool(self.bin, "bsdtar", 'echo "Encrypted file is unsupported" >&2; exit 1\n')
        with mock.patch.object(P.shutil, "which", self.which({"bsdtar"})):
            with self.assertRaisesRegex(OSError, "password"):
                P._run_tool(self.rar, target)
        with mock.patch.object(P.shutil, "which", self.which(set())):
            with self.assertRaisesRegex(OSError, "7-Zip"):
                P._run_tool(self.rar, target)


if __name__ == "__main__":
    unittest.main()
