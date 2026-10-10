"""Vini: an app uninstalled by dragging it from Files' Applications to the
Trash stayed in the list -- it follows the apps' folders now."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2 import apps  # noqa: E402
from sonata2.files import folder as F  # noqa: E402

ENTRY = "[Desktop Entry]\nType=Application\nName={}\nExec=true\n"


def wait(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class AppsLiveTest(unittest.TestCase):
    def test_uninstalled_app_leaves_and_new_one_comes(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(apps, "app_dirs", return_value=[d]):
            for n in ("alpha", "beta"):
                with open(os.path.join(d, n + ".desktop"), "w") as f:
                    f.write(ENTRY.format(n.title()))
            loads = []
            fo = F.Folder(lambda uri: loads.append(uri), lambda uri, e: None)

            def names():
                return sorted(fo.store.get_item(i).get_name() for i in range(fo.store.get_n_items()))
            fo.load(F.APPS)
            self.assertTrue(wait(lambda: names() == ["alpha.desktop", "beta.desktop"]), names())
            os.unlink(os.path.join(d, "beta.desktop"))                 # uninstalled
            self.assertTrue(wait(lambda: names() == ["alpha.desktop"]), names())
            with open(os.path.join(d, "gamma.desktop"), "w") as f:
                f.write(ENTRY.format("Gamma"))
            self.assertTrue(wait(lambda: "gamma.desktop" in names()), names())
            fo.load(GLib.get_home_dir() and "file://" + d)             # another folder: no longer watched
            self.assertEqual(fo._app_monitors, [])


if __name__ == "__main__":
    unittest.main()
