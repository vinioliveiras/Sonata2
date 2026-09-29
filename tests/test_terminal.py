"""Terminal: the shell starts, the title follows it, a running program
counts as busy (xvfb-run python3 -m unittest tests.test_terminal; skipped
without VTE for GTK 4)."""
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
from sonata2.terminal import window as T  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipIf(T.Vte is None, "no VTE for GTK 4")
class TerminalTest(unittest.TestCase):
    def test_shell(self):
        Adw.init()
        ui.setup()
        os.environ["SHELL"] = "/bin/sh"
        T.user_shell = lambda: "/bin/sh"
        app = Adw.Application(application_id="io.test.terminal")
        app.register(None)
        w = T.TerminalWindow(app, "/tmp")
        w.present()
        settle(1500)
        self.assertIsNotNone(w.pid)
        w.term.feed_child(b"sleep 5\n")
        settle(1500)
        self.assertEqual(w._busy(), "sleep")
        self.assertIn("sleep", w.bar.title_label.get_label())
        w._really_close()


if __name__ == "__main__":
    unittest.main()
