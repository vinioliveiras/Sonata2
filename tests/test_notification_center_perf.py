"""Vini: Notification Center (the menu bar's clock) opened laggy whenever
there were notifications. Every notification rebuilt all of its cards (up
to 40) and the calendar, so the next opening laid every one out again
before its first frame (~250 ms with 100 notifications). Now the column is
updated in place: a new note gets its card, the rest are kept.

    xvfb-run -a python3 -m unittest tests.test_notification_center_perf
"""
import datetime as dt
import os
import tempfile
import time
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import notifications as N  # noqa: E402


def spin(ms=0):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if GLib.get_monotonic_time() >= end:
            break


def note(i, **kw):
    return N.Note(i, "Chat", "", f"Title {i}", f"body {i}", [], "", **kw)


class OpensLaggyWithNotificationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.made = []
        owner = N.Notifications.__new__(N.Notifications)
        owner.notes, owner.listeners, owner._banners, owner._conn, owner.nc = [], [], {}, None, None
        real = N.Notifications.card

        def card(n, banner=False):
            self.made.append(n.id)
            return real(owner, n, banner)
        owner.card = card
        self.owner = owner
        self.c = N._Center(None, owner)
        self.upcoming = mock.patch.object(N, "_upcoming", wraps=N._upcoming)
        self.upcoming_calls = self.upcoming.start()

    def tearDown(self):
        self.upcoming.stop()
        self.c.destroy()

    def shown(self):
        return [nid for nid, _s in sorted(self.c._slots.items(), key=lambda kv: self.position(kv[1]))]

    def position(self, w):
        i, x = 0, self.c.col.get_first_child()
        while x is not None and x is not w:
            i, x = i + 1, x.get_next_sibling()
        return i

    def test_a_new_notification_builds_only_its_card(self):
        self.owner.notes = [note(i) for i in range(1, 31)]
        self.c._rebuild()
        self.assertEqual(len(self.made), 30)
        kept, cal = dict(self.c._slots), self.c._cal
        self.made.clear()
        self.owner.notes.append(note(31))
        self.c._rebuild()
        self.assertEqual(self.made, [31])                                  # not all 31 again
        self.assertTrue(all(self.c._slots[k] is w for k, w in kept.items()))
        self.assertIs(self.c._cal, cal)                                    # nor the calendar
        self.assertEqual(self.upcoming_calls.call_count, 1)                # (nor its files read)
        self.assertEqual(self.shown(), list(range(31, 0, -1)))             # newest first
        self.assertIs(self.c.col.get_last_child(), cal)

    def test_opening_after_a_notification_lays_out_one_card(self):
        """The real cost was the opening: the cards made while it was closed
        were laid out before its first frame."""
        self.owner.notes = [note(i) for i in range(1, 101)]
        self.c.show_center()
        spin(50)
        self.c.hide_center()
        spin(300)
        self.made.clear()
        self.owner.notes.append(note(101))
        self.c._changed()                                                  # closed: coalesced
        spin(1100)
        self.assertEqual(self.made, [101])
        self.c.show_center()                                               # nothing left to build
        spin(50)
        self.assertEqual(self.made, [101])
        self.assertEqual(len(self.c._slots), N.SHOWN)                      # the oldest shown dropped
        self.assertNotIn(101 - N.SHOWN, self.c._slots)
        self.c.hide_center()
        spin(300)
        self.c.show_center()                                               # unchanged: nothing at all
        spin(50)
        self.assertEqual(self.made, [101])

    def test_closed_and_replaced_notes(self):
        one, two, three = note(1), note(2), note(3)
        self.owner.notes = [one, two, three]
        self.c._rebuild()
        self.assertTrue(self.c._clear.get_visible())
        self.owner.notes = [one, three]
        self.c._rebuild()
        self.assertEqual(self.shown(), [3, 1])
        self.made.clear()
        self.owner.notes = [one, note(3, at=time.time() + 1)]              # replaced in place (same id)
        self.c._rebuild()
        self.assertEqual(self.made, [3])
        self.assertEqual(self.shown(), [3, 1])
        self.owner.notes = []
        self.c._rebuild()
        self.assertEqual(self.c._title.get_label(), N._("No Notifications"))
        self.assertFalse(self.c._clear.get_visible())
        self.assertEqual(self.c._slots, {})

    def test_times_are_those_of_the_opening(self):
        self.owner.notes = [note(1, at=time.time())]
        self.c._rebuild()
        when = self.c._slots[1].when
        self.assertEqual(when.get_label(), N._ago(time.time()))
        self.c._shown[1] = (self.c._shown[1][0], time.time() - 600)
        self.c._refresh_times()
        self.assertEqual(when.get_label(), N._("{n}m ago").format(n=10))

    def test_up_next_made_again_when_its_event_ends(self):
        self.c._rebuild()
        cal = self.c._cal
        self.c._cal_until = dt.datetime.now() - dt.timedelta(seconds=1)
        self.assertNotEqual(self.c._built_for, self.c._state_key())         # an opening updates it
        self.c._rebuild()
        self.assertIsNot(self.c._cal, cal)

    def test_center_slides_without_owner_cards(self):
        owner = types.SimpleNamespace(notes=[], listeners=[], card=None, clear=None)
        c = N._Center(None, owner)
        c._rebuild()
        self.assertEqual(c._title.get_label(), N._("No Notifications"))
        c.destroy()


if __name__ == "__main__":
    unittest.main()
