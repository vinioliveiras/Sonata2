"""Vini: the Dock's notification badges only came back with a new
notification -- the menu bar (which keeps them) started empty after a
restart and published that. Its notifications are kept in the runtime
folder and read back at start.

    python3 -m unittest tests.test_badges_after_restart
"""
import os
import tempfile
import unittest
from unittest import mock

from sonata2.shell import notifications as N


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run})
        patch.start()
        self.addCleanup(patch.stop)

    def test_kept_and_read_back(self):
        notes = [N.Note(3, "WhatsApp", "", "Ana", "oi", [("default", "Open")], desktop="whatsapp"),
                 N.Note(4, "Discord", "", "Bia", "hey", [])]
        N.save_history(notes)
        back = N.load_history()
        self.assertEqual([(n.id, n.app, n.desktop) for n in back], [(3, "WhatsApp", "whatsapp"), (4, "Discord", "")])
        self.assertEqual(back[0].actions, [("default", "Open")])        # tuples again
        self.assertTrue(os.path.isfile(os.path.join(self.run, N.HISTORY)))

    def test_nothing_or_damaged_is_empty(self):
        self.assertEqual(N.load_history(), [])
        with open(os.path.join(self.run, N.HISTORY), "w") as f:
            f.write("{not json")
        self.assertEqual(N.load_history(), [])

    def test_start_publishes_what_was_kept(self):
        src = open(N.__file__).read()
        self.assertIn("self.notes = load_history()", src)
        self.assertIn("badges.publish(self.notes, os.getpid())", src)
        self.assertNotIn("badges.publish([], os.getpid())", src)


if __name__ == "__main__":
    unittest.main()
