"""Vini (Files vs Finder): Get Info couldn't change anything. Finder's
"Open with:" (Change All…) and Sharing & Permissions (you, the group,
everyone: Read & Write / Read only / No Access; only the owner)."""
import os
import stat
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from sonata2.files import quicklook as Q  # noqa: E402
from sonata2.files.folder import ATTRS  # noqa: E402


class PermTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(Q.perm_level(0o644, 6), 0)
        self.assertEqual(Q.perm_level(0o644, 3), 1)
        self.assertEqual(Q.perm_level(0o640, 0), 2)

    def test_set(self):
        self.assertEqual(Q.set_level(0o644, 0, 2, False), 0o640)            # everyone: no access
        self.assertEqual(Q.set_level(0o755, 3, 0, True), 0o775)             # a folder's group: read & write
        self.assertEqual(Q.set_level(0o700, 0, 1, True), 0o705)             # a folder readable: also x
        self.assertEqual(Q.set_level(0o755, 6, 1, False), 0o555)            # a program stays runnable
        self.assertEqual(Q.set_level(0o644, 6, 0, False), 0o644)

    def test_window(self):
        Adw.init()
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.txt")
        with open(p, "w") as f:
            f.write("x")
        f = Gio.File.new_for_path(p)
        info = f.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        info.set_attribute_object("sonata::file", f)
        w = Q.GetInfo(None, info)
        self.assertEqual(set(w.perm_picks), {"owner", "group", "other"})
        w._set_perm(p, 0, 2, False)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode) & 0o7, 0)
        w.close()


if __name__ == "__main__":
    unittest.main()
