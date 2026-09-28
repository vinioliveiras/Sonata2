"""Files: Finder-style formatting and folder loading (xvfb):
python3 -m unittest tests.test_files"""
import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2.files import folder, views  # noqa: E402


def info(size, directory=False):
    i = Gio.FileInfo()
    i.set_size(size)
    i.set_file_type(Gio.FileType.DIRECTORY if directory else Gio.FileType.REGULAR)
    return i


class FilesTest(unittest.TestCase):
    def test_size(self):
        self.assertEqual([views.size(info(n)) for n in (0, 653, 12_400, 1_430_000, 2_000_000_000)],
                         ["Zero bytes", "653 bytes", "12 KB", "1.4 MB", "2.0 GB"])
        self.assertEqual(views.size(info(0, True)), "--")

    def test_load_sorted_and_live(self):
        d = tempfile.mkdtemp()
        for n in ("photo-10.jpg", "photo-2.jpg", "B", "a", ".hidden"):
            open(os.path.join(d, n), "w").close()
        loop = GLib.MainLoop()
        f = folder.Folder(lambda _u: loop.quit(), lambda _u, e: self.fail(e.message))
        f.load(Gio.File.new_for_path(d).get_uri())
        GLib.timeout_add(3000, loop.quit)
        loop.run()
        names = lambda: [f.store.get_item(i).get_name() for i in range(f.store.get_n_items())]  # noqa: E731
        self.assertEqual(names(), ["a", "B", "photo-2.jpg", "photo-10.jpg"])
        open(os.path.join(d, "c"), "w").close()              # live insert keeps the order
        end = GLib.get_monotonic_time() + 3_000_000
        while "c" not in names() and GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(names(), ["a", "B", "c", "photo-2.jpg", "photo-10.jpg"])


if __name__ == "__main__":
    unittest.main()
