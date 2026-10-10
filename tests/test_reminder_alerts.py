"""Reminder alerts with Notes closed (Vini: "reminders don't notify"): the
menu bar's ReminderWatch reads reminders.json read-only, notifies each due
reminder once (shared ledger with the Notes window), and doesn't flood old
overdue ones at login."""
import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from sonata2.notes import notifier as N  # noqa: E402
from sonata2.notes import store as S  # noqa: E402

NOW = datetime.datetime(2026, 10, 10, 12, 0)


def stamp(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M")


class ReminderWatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "reminders.json")
        self.write([
            {"id": "old", "title": "Last week", "due": stamp(NOW - datetime.timedelta(days=7))},
            {"id": "recent", "title": "Two minutes ago", "due": stamp(NOW - datetime.timedelta(minutes=2))},
            {"id": "soon", "title": "Soon", "notes": "milk", "due": stamp(NOW + datetime.timedelta(minutes=10))},
            {"id": "done", "title": "Done", "due": stamp(NOW + datetime.timedelta(minutes=5)), "completed": True},
        ])
        self.notified = []
        p = mock.patch.object(N.Notifier, "notify", lambda n, t, b: (n.sent.append((t, b)),
                                                                    self.notified.append(t)))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, reminders):
        with open(self.path, "w") as f:
            json.dump({"lists": [{"id": "reminders", "name": "Reminders", "color": "blue"}],
                       "reminders": reminders}, f)

    def test_notifies_with_notes_closed_once_no_storm(self):
        w = N.ReminderWatch(self.dir, now=NOW)
        self.addCleanup(w.stop)
        self.assertTrue(w.notifier.timer)                          # aimed at "recent" (catch-up)
        w.notifier.check(NOW)
        self.assertEqual(self.notified, ["Two minutes ago"])      # not last week's
        w.notifier.check(NOW + datetime.timedelta(minutes=11))
        self.assertEqual(self.notified, ["Two minutes ago", "Soon"])
        self.assertEqual(w.notifier.sent[-1], ("Soon", "milk"))
        # the Notes window's own Notifier (open at the same time) doesn't repeat them
        other = N.Notifier(S.Store(self.dir), now=NOW)
        self.addCleanup(other.stop)
        other.check(NOW + datetime.timedelta(minutes=12))
        self.assertEqual(self.notified, ["Two minutes ago", "Soon"])
        # a restart (login again) doesn't repeat either
        again = N.ReminderWatch(self.dir, now=NOW + datetime.timedelta(minutes=12))
        self.addCleanup(again.stop)
        again.notifier.check(NOW + datetime.timedelta(minutes=12))
        self.assertEqual(len(self.notified), 2)

    def test_reloads_on_save_and_never_writes(self):
        w = N.ReminderWatch(self.dir, now=NOW)
        self.addCleanup(w.stop)
        before = os.path.getmtime(self.path)
        self.write([{"id": "new", "title": "Added in Notes", "due": stamp(NOW + datetime.timedelta(minutes=1))}])
        w.reload()
        self.assertEqual([r["id"] for r in w.store.reminders], ["new"])
        w.notifier.check(NOW + datetime.timedelta(minutes=2))
        self.assertIn("Added in Notes", self.notified)
        with open(self.path, "w") as f:
            f.write("{broken")                                      # mid-write / damaged
        w.reload()
        self.assertEqual([r["id"] for r in w.store.reminders], ["new"])       # keeps what it had
        self.assertTrue(os.path.exists(self.path))                 # never renamed aside by the menu bar
        self.assertGreaterEqual(os.path.getmtime(self.path), before)

    def test_rescheduled_reminder_notifies_again(self):
        w = N.ReminderWatch(self.dir, now=NOW)
        self.addCleanup(w.stop)
        w.notifier.check(NOW + datetime.timedelta(minutes=11))
        self.write([{"id": "soon", "title": "Soon", "due": stamp(NOW + datetime.timedelta(minutes=30))}])
        w.reload()
        w.notifier.check(NOW + datetime.timedelta(minutes=31))
        self.assertEqual(self.notified.count("Soon"), 2)

    def test_notification_carries_reminders_identity(self):
        calls = []
        bus = mock.Mock()
        bus.call.side_effect = lambda *a: calls.append(a)
        with mock.patch.object(N.Gio, "bus_get", lambda _t, _c, cb: cb(None, None)), \
                mock.patch.object(N.Gio, "bus_get_finish", lambda _r: bus):
            mock.patch.stopall()
            n = N.Notifier(S.Store(self.dir), now=NOW)
            self.addCleanup(n.stop)
            N.Notifier.notify(n, "T", "B")
        args = calls[0][4].unpack()
        self.assertEqual(args[0], "Reminders")
        self.assertEqual(args[6]["desktop-entry"], N.DESKTOP_ENTRY)


if __name__ == "__main__":
    GLib.set_prgname("test")
    unittest.main()
