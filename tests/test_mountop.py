"""Mount prompts in Sonata's alert (xvfb-run python3 -m unittest tests.test_mountop).
Regression: unlocking an encrypted disk showed GTK's own dialog."""
import os
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import mountop as M  # noqa: E402


class MountOpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def test_split(self):
        self.assertEqual(M.split_message("Enter a passphrase\nNeeded for USB\n"), ("Enter a passphrase",
                                                                                  "Needed for USB"))

    def test_password_handled(self):
        op = M.MountOperation(None)
        replies = []
        op.connect("reply", lambda _o, r: replies.append(r))
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            op.emit("ask-password", "Unlock disk\nEncrypted", "", "",
                    Gio.AskPasswordFlags.NEED_PASSWORD | Gio.AskPasswordFlags.SAVING_SUPPORTED)
            self.assertEqual(replies, [])                      # GIO's default "unhandled" stopped
            heading, body, responses, answered = alert.call_args[0][:4]
            self.assertEqual(heading, "Unlock disk")
            box = alert.return_value.set_extra_child.call_args[0][0]
            pw = box.get_first_child()
            pw.set_text("secret")
            answered("ok")
        self.assertEqual(replies, [Gio.MountOperationResult.HANDLED])
        self.assertEqual(op.get_password(), "secret")

    def test_cancel_aborts(self):
        op = M.MountOperation(None)
        replies = []
        op.connect("reply", lambda _o, r: replies.append(r))
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            op.emit("ask-password", "x", "", "", Gio.AskPasswordFlags.NEED_PASSWORD)
            alert.call_args[0][3]("cancel")
        self.assertEqual(replies, [Gio.MountOperationResult.ABORTED])

    def test_no_gtk_dialogs_left(self):
        root = os.path.join(os.path.dirname(M.__file__), "..")
        for dirpath, _d, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    self.assertNotIn("Gtk.MountOperation", open(os.path.join(dirpath, f)).read(), f)


if __name__ == "__main__":
    unittest.main()
