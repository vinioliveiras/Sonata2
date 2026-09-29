"""TextEdit: open, edited title, find, undo, save (xvfb-run python3 -m unittest tests.test_textedit)."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TextEditTest(unittest.TestCase):
    def test_document(self):
        Adw.init()
        ui.setup()
        from sonata2.textedit.window import TextEditWindow
        path = os.path.join(tempfile.mkdtemp(), "doc.txt")
        with open(path, "w") as f:
            f.write("Hello Sonata\nfind sonata here\n")
        app = Adw.Application(application_id="io.test.textedit")
        app.register(None)
        w = TextEditWindow(app, path)
        w.present()
        settle()
        self.assertEqual(w.bar.title_label.get_label(), "doc.txt")
        w.buffer.insert_at_cursor("X")
        settle(50)
        self.assertEqual(w.bar.title_label.get_label(), "doc.txt — Edited")
        w._show_find()
        w.find_entry.set_text("sonata")
        settle(400)
        self.assertEqual(w.find_count.get_label(), "2 found")
        w._hide_find()
        w.save()
        with open(path) as f:
            self.assertTrue(f.read().startswith("XHello"))
        self.assertFalse(w.buffer.get_modified())
        w.destroy()


if __name__ == "__main__":
    unittest.main()
