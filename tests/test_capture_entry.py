"""Screenshot in Launchpad / the Apps Menu (Vini: it had no entry there).
Run: python3 -m unittest tests.test_capture_entry"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402,F401


class CaptureEntryTest(unittest.TestCase):
    def entry(self):
        from sonata2.shell import capture
        with mock.patch("sonata2.apps.write_desktop_file", side_effect=lambda name, text: (name, text)):
            return capture.capture_desktop_file("sonata2")

    def test_opens_the_capture_toolbar(self):
        name, text = self.entry()
        self.assertEqual(name, "sonata2-screenshot.desktop")
        self.assertIn("Exec=sonata2 screenshot toolbar\n", text)      # the same as Super+Shift+5
        self.assertIn("Name=Screenshot\n", text)
        self.assertNotIn("NoDisplay", text)

    def test_in_the_utilities_tab(self):
        from sonata2.shell import launchpad_window as LW
        _n, text = self.entry()
        cats = next(ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("Categories="))
        info = mock.Mock(get_categories=lambda: cats, get_string=lambda k: cats if k == "Categories" else "")
        self.assertEqual(LW.category_of(info), "utilities")

    def test_written_when_the_dock_starts(self):
        from sonata2 import __main__ as main
        src = open(main.__file__).read()
        run_dock = src[src.index("def run_dock"):src.index("cfg = dock.load_config()")]
        self.assertIn("capture_desktop_file(self_command())", run_dock)


if __name__ == "__main__":
    unittest.main()
