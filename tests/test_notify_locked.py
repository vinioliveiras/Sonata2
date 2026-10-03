"""Apps in a locked folder show no notifications (Vini).
Run: xvfb-run -a python3 -m unittest tests.test_notify_locked"""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from sonata2 import config  # noqa: E402
from sonata2.shell import notifications as N  # noqa: E402


def write(name, data):
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(os.path.join(config.CONFIG_DIR, name + ".json"), "w") as f:
        json.dump(data, f)
    st = os.stat(os.path.join(config.CONFIG_DIR, name + ".json"))
    os.utime(os.path.join(config.CONFIG_DIR, name + ".json"), (st.st_atime, st.st_mtime + 5))


class LockedFolderNotificationsTest(unittest.TestCase):
    def setUp(self):
        N._LOCKED["stamp"] = None
        write("dock", {"folders": {"f1": {"name": "Private", "apps": ["org.telegram.desktop"], "locked": True},
                                   "f2": {"name": "Open", "apps": ["discord"]}}})
        write("launchpad", {"pages": [], "hidden": ["whatsapp-web"]})

    def test_by_desktop_entry(self):
        with mock.patch.object(N.apps, "lookup", return_value=None):
            self.assertTrue(N.locked("org.telegram.desktop", ""))
            self.assertTrue(N.locked("org.telegram.desktop.desktop", ""))
            self.assertTrue(N.locked("whatsapp-web", ""))              # Apps' hidden (locked) folder
            self.assertFalse(N.locked("discord", "Discord"))           # an unlocked folder

    def test_by_name(self):
        info = mock.Mock()
        info.get_name.return_value = "Telegram Desktop"
        with mock.patch.object(N.apps, "lookup", side_effect=lambda i: info if "telegram" in i else None):
            self.assertTrue(N.locked("", "Telegram Desktop"))

    def test_daemon_drops_them(self):
        src = open(N.__file__).read()
        body = src[src.index("    def notify(self, app_name"):src.index("    def close(self")]
        self.assertLess(body.index("if locked(n.desktop, n.app)"), body.index("self._banner(n)"))
