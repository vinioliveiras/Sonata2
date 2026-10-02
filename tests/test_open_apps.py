"""Reopen the apps after a crash (sonata2/open_apps.py; Feedbacker's row;
the session starting again in tools/sonata-session). Vini: item 11.
Run: xvfb-run python3 -m unittest tests.test_open_apps"""
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import open_apps as O, ui  # noqa: E402
from sonata2.feedback import report  # noqa: E402

KNOWN = {"firefox", "spotify", "sonata2-launchpad", "org.gnome.Calculator"}


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def fake_lookup(i):
    if i not in KNOWN:
        return None
    m = mock.Mock()
    m.get_display_name.return_value = i.capitalize()
    m.get_id.return_value = i + ".desktop"
    return m


class OpenAppsTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch("sonata2.apps.lookup", side_effect=fake_lookup)
        self.p.start()
        for n in (O.FILE, O.BEFORE, report.CRASH):
            try:
                os.remove(os.path.join(O._dir(), n) if n != report.CRASH else os.path.join(report.log_dir(), n))
            except OSError:
                pass

    def tearDown(self):
        self.p.stop()

    def crash(self, when):
        os.makedirs(report.log_dir(), exist_ok=True)
        with open(os.path.join(report.log_dir(), report.CRASH), "w") as f:
            f.write(f"{int(when)} 134\n")

    def test_only_real_apps_once(self):
        O.save_now(["firefox", "?", "sonata2-launchpad", "firefox", "spotify"])
        self.assertEqual(O._read(O.FILE), ["firefox", "spotify"])

    def test_after_a_crash_the_old_list_is_offered(self):
        O.save_now(["firefox", "spotify"])
        old = time.time() - 60
        os.utime(os.path.join(O._dir(), O.FILE), (old, old))
        self.crash(time.time() - 30)
        O.save_now(["org.gnome.Calculator"])                 # the new session's Dock: keeps the old one first
        self.assertEqual(O.before_crash(), ["firefox", "spotify"])
        self.assertEqual(O._read(O.FILE), ["org.gnome.Calculator"])
        O.save_now(["firefox"])                              # later notes never replace it
        self.assertEqual(O.before_crash(), ["firefox", "spotify"])

    def test_feedbacker_first_also_finds_it(self):
        O.save_now(["spotify"])
        old = time.time() - 60
        os.utime(os.path.join(O._dir(), O.FILE), (old, old))
        self.crash(time.time() - 30)
        self.assertEqual(O.before_crash(), ["spotify"])      # before the Dock's first note

    def test_no_crash_nothing_offered(self):
        O.save_now(["firefox"])
        self.assertEqual(O.before_crash(), [])

    def test_reopen_starts_them_apart_and_uses_the_list_up(self):
        O._write(O.BEFORE, ["firefox", "spotify"])
        started = []
        n = O.reopen(["firefox", "spotify", "gone"], launch=lambda info: started.append(info.get_id()))
        self.assertEqual(n, 2)
        settle(400)
        self.assertEqual(started, ["firefox.desktop", "spotify.desktop"])
        self.assertFalse(os.path.exists(os.path.join(O._dir(), O.BEFORE)))

    def test_dock_notes_its_running_apps(self):
        src = open(os.path.join(os.path.dirname(O.__file__), "shell", "dock.py")).read()
        self.assertIn("open_apps.note(list(groups))", src[src.index("def _sync"):])


class FeedbackerRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_reopen_row(self):
        from sonata2.feedback import window
        grp = Adw.PreferencesGroup()
        win = mock.Mock()
        with mock.patch.object(O, "before_crash", return_value=["firefox", "spotify"]), \
                mock.patch.object(O, "names", return_value=["Firefox", "Spotify"]), \
                mock.patch.object(O, "reopen", return_value=2) as reopen:
            window.FeedbackWindow._reopen_row(win, grp)
            self.assertEqual(win.reopen_row.get_subtitle(), "Firefox, Spotify")
            win.reopen_button.emit("clicked")
        reopen.assert_called_once_with(["firefox", "spotify"])
        self.assertFalse(win.reopen_button.get_sensitive())

    def test_no_row_without_apps(self):
        from sonata2.feedback import window
        win = mock.Mock()
        with mock.patch.object(O, "before_crash", return_value=[]):
            window.FeedbackWindow._reopen_row(win, Adw.PreferencesGroup())
        self.assertIsNone(win.reopen_row)


if __name__ == "__main__":
    unittest.main()
