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


class CalendarWidthTest(unittest.TestCase):
    def test_calendar_shrank_when_notifications_were_cleared(self):
        """Vini: clearing the notifications, the calendar at the top got a
        little smaller. The cards' column made the window wider than the
        calendar, which stretched to it; without cards it went back to its
        own width. It keeps its width, with cards or without."""
        from gi.repository import Gtk
        Adw.init()
        ui.setup()
        owner = N.Notifications.__new__(N.Notifications)
        owner.notes, owner.listeners, owner._banners, owner._conn, owner.nc = [], [], {}, None, None
        c = N._Center(None, owner)
        try:
            owner.notes = [note(i) for i in range(1, 6)]
            c._rebuild()
            widths = []
            for notes in (owner.notes, []):
                owner.notes = notes
                c._rebuild()
                w = c.rev.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
                h = c.rev.measure(Gtk.Orientation.VERTICAL, w)[1]
                c.rev.allocate(w, h, -1, None)
                widths.append(c._cal.get_width())
            self.assertEqual(widths[0], widths[1])
            self.assertEqual(widths[0], N.BANNER_W + 6)
        finally:
            c.destroy()


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
        self.assertIs(self.c.top.get_last_child(), cal)

    def test_laggy_with_many_notifications_blurred_every_frame(self):
        """Vini: Notification Center lagged with many notifications -- the
        compositor blurred behind its whole column again on every frame it
        scrolled or slid. It's never blurred now (glass.NEVER_BLUR); its
        panels draw a picture of the display blurred once (frost.py), each
        its own slice, and go solid without one."""
        from sonata2.ui import glass
        from sonata2.shell import frost
        self.assertIn('app_id is "sonata2-notification-center"', glass.NEVER_BLUR)
        self.assertIsInstance(self.c.col, N._FrostBox)
        self.assertIsInstance(self.c.top, N._FrostBox)
        self.owner.notes = [note(i) for i in range(1, 41)]
        self.c._rebuild()
        panels = list(self.c.col._panels())
        self.assertEqual(len(panels), 40)                                  # each card draws its own glass
        self.assertTrue(all(w.has_css_class("nt-card") for w, _k in panels))
        self.assertEqual([w for w, _k in self.c.top._panels()][0].has_css_class("nc-widget"), True)
        with mock.patch.object(frost, "capture", return_value=False), \
                mock.patch.object(N.layer, "layer_shell", return_value=object()):
            self.c._frost()
        self.assertTrue(self.c.has_css_class("no-frost"))                  # no picture: solid
        got = {}
        with mock.patch.object(frost, "capture", side_effect=lambda out, done, r: (got.setdefault("done", done), True)[1]), \
                mock.patch.object(N.layer, "layer_shell", return_value=object()), \
                mock.patch.object(self.c, "get_visible", return_value=True):
            self.c._frost()
            tex = object()
            got["done"](tex)
        self.assertIs(self.c.frost_tex, tex)
        self.assertFalse(self.c.has_css_class("no-frost"))

    def test_calendar_scrolled_away_under_notifications(self):
        """Vini: with many notifications the calendar was at the end of the
        column, scrolled out of sight. It stays fixed at the top; only the
        notifications scroll, under it."""
        self.owner.notes = [note(i) for i in range(1, 41)]
        self.c._rebuild()
        cal = self.c._cal
        self.assertIs(cal.get_parent(), self.c.top)
        scroller = self.c.col.get_ancestor(N.Gtk.ScrolledWindow)
        self.assertIsNotNone(scroller)
        self.assertIsNone(cal.get_ancestor(N.Gtk.ScrolledWindow))          # not in what scrolls
        self.assertIs(self.c.top.get_next_sibling(), scroller)              # above the notifications

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
