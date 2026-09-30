"""Calendar: iCalendar round trip, recurrences, layout, store, alerts and the
window (xvfb-run python3 -m unittest tests.test_calendar)."""
import datetime as dt
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

from sonata2.calendar import alerts, ics, model  # noqa: E402

D = dt.datetime


def starts(ev, a, b):
    return [o.start for o in ics.occurrences(ev, a, b)]


class IcsTest(unittest.TestCase):
    def test_round_trip(self):
        evs = [
            ics.Event(uid="a", summary="Lunch; with, Ana\\x", start=D(2026, 9, 30, 12, 30), end=D(2026, 9, 30, 13, 30),
                      location="Café Central", description="Line one\nLine two " + "é" * 60, alarm=15),
            ics.Event(uid="b", summary="Holiday", start=D(2026, 10, 5), end=D(2026, 10, 8), all_day=True,
                      freq="YEARLY", count=3, alarm=0),
            ics.Event(uid="c", summary="Standup", start=D(2026, 9, 1, 9), end=D(2026, 9, 1, 9, 15), freq="WEEKLY",
                      interval=2, until=D(2026, 12, 31, 23, 59, 59), exdates=[D(2026, 9, 15, 9)], alarm=1440),
        ]
        for e in evs:
            e.calendar = "home"
        text = ics.serialize(evs, "Home", "blue", "#007aff")
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split("\r\n")))
        info, back = ics.parse(text, "home")
        self.assertEqual(info, {"name": "Home", "color": "blue", "hex": "#007aff"})
        self.assertEqual(back, evs)

    def test_foreign_file(self):
        text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x@y\nSUMMARY:Flight\nDTSTART:20260310T080000Z\n"
                "DURATION:PT2H30M\nRRULE:FREQ=DAILY;UNTIL=20260312\nBEGIN:VALARM\nTRIGGER:-PT1H\nEND:VALARM\n"
                "END:VEVENT\nBEGIN:VEVENT\nUID:z\nSUMMARY:Zoned\nDTSTART;TZID=Europe/Berlin:20260701T100000\n"
                "DTEND;TZID=Europe/Berlin:20260701T110000\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:No uid\n"
                "DTSTART;VALUE=DATE:20260101\nEND:VEVENT\nEND:VCALENDAR\n")
        _info, evs = ics.parse(text)
        f, z, n = evs
        local = D(2026, 3, 10, 8, tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
        self.assertEqual(f.start, local)
        self.assertEqual(f.duration, dt.timedelta(hours=2, minutes=30))
        self.assertEqual(f.alarm, 60)
        self.assertEqual(len(starts(f, D(2026, 1, 1), D(2027, 1, 1))), 3)      # UNTIL a date: that whole day
        self.assertEqual(z.duration, dt.timedelta(hours=1))
        self.assertTrue(n.all_day and n.uid and n.end == D(2026, 1, 2))


class RecurrenceTest(unittest.TestCase):
    def test_monthly_31st(self):
        ev = ics.Event(uid="m", start=D(2026, 1, 31, 10), end=D(2026, 1, 31, 11), freq="MONTHLY", count=5)
        self.assertEqual(starts(ev, D(2026, 1, 1), D(2028, 1, 1)),
                         [D(2026, 1, 31, 10), D(2026, 3, 31, 10), D(2026, 5, 31, 10), D(2026, 7, 31, 10),
                          D(2026, 8, 31, 10)])

    def test_until_and_count(self):
        ev = ics.Event(uid="d", start=D(2026, 9, 1, 9), end=D(2026, 9, 1, 10), freq="DAILY",
                       until=D(2026, 9, 5, 9))
        self.assertEqual(len(starts(ev, D(2026, 1, 1), D(2027, 1, 1))), 5)
        ev.until, ev.count = None, 3
        self.assertEqual(starts(ev, D(2026, 9, 2), D(2027, 1, 1)), [D(2026, 9, 2, 9), D(2026, 9, 3, 9)])
        ev.count, ev.exdates = 3, [D(2026, 9, 2, 9)]                 # an excluded date still counts
        self.assertEqual(starts(ev, D(2026, 1, 1), D(2027, 1, 1)), [D(2026, 9, 1, 9), D(2026, 9, 3, 9)])

    def test_yearly_leap_day_and_weekly_interval(self):
        ev = ics.Event(uid="y", start=D(2024, 2, 29), end=D(2024, 3, 1), all_day=True, freq="YEARLY")
        self.assertEqual(starts(ev, D(2024, 1, 1), D(2033, 1, 1)), [D(2024, 2, 29), D(2028, 2, 29), D(2032, 2, 29)])
        ev = ics.Event(uid="w", start=D(2026, 9, 1, 9), end=D(2026, 9, 1, 10), freq="WEEKLY", interval=2)
        self.assertEqual(starts(ev, D(2026, 9, 1), D(2026, 10, 1)),
                         [D(2026, 9, 1, 9), D(2026, 9, 15, 9), D(2026, 9, 29, 9)])

    def test_far_range_matches_walk(self):
        """Ranges far from the first occurrence skip ahead: same result."""
        for freq in ics.FREQS:
            ev = ics.Event(uid=freq, start=D(2001, 1, 31, 23), end=D(2001, 2, 1, 1), freq=freq, interval=3)
            a, b = D(2026, 1, 1), D(2027, 1, 1)
            walked, n = [], 0
            while True:
                s = ics._nth(ev, n)
                n += 1
                if s is None:
                    continue
                if s >= b:
                    break
                if s + ev.duration > a:
                    walked.append(s)
            self.assertEqual(starts(ev, a, b), walked, freq)

    def test_overlapping_range(self):
        ev = ics.Event(uid="o", start=D(2026, 9, 29, 23), end=D(2026, 9, 30, 1))
        self.assertEqual(len(starts(ev, D(2026, 9, 30), D(2026, 10, 1))), 1)       # started the day before
        self.assertEqual(starts(ev, D(2026, 9, 30, 1), D(2026, 10, 1)), [])


class LayoutTest(unittest.TestCase):
    def test_columns(self):
        n = iter(range(100))
        e = lambda h, m, h2, m2: ics.Occurrence(ics.Event(uid=str(next(n))), D(2026, 9, 30, h, m),  # noqa: E731
                                                D(2026, 9, 30, h2, m2))
        a, b, c, d = e(9, 0, 10, 0), e(9, 30, 10, 30), e(10, 0, 11, 0), e(12, 0, 13, 0)
        cols = model.columns([d, c, b, a])
        self.assertEqual(cols[a], (0, 2))
        self.assertEqual(cols[b], (1, 2))
        self.assertEqual(cols[c], (0, 2))
        self.assertEqual(cols[d], (0, 1))
        x, y, z = e(14, 0, 16, 0), e(14, 0, 15, 0), e(14, 30, 15, 30)
        self.assertEqual(sorted(model.columns([x, y, z]).values()), [(0, 3), (1, 3), (2, 3)])

    def test_lanes(self):
        ev = lambda uid, a, b: ics.Occurrence(ics.Event(uid=uid, all_day=True), D(2026, 9, 1) + dt.timedelta(a - 1),  # noqa
                                              D(2026, 9, 1) + dt.timedelta(b - 1))
        long, one, two = ev("l", 28, 31), ev("o", 27, 28), ev("t", 29, 30)
        out = {o.event.uid: (s, e, lane) for o, s, e, lane in model.lanes([one, two, long], dt.date(2026, 9, 27), 7)}
        self.assertEqual(out, {"o": (0, 0, 0), "l": (1, 3, 0), "t": (2, 2, 1)})

    def test_month_grid(self):
        g = model.month_grid(2026, 9, 6)                       # Sunday first
        self.assertEqual((len(g), g[0], g[-1]), (42, dt.date(2026, 8, 30), dt.date(2026, 10, 10)))
        self.assertEqual(model.month_grid(2026, 9, 0)[0], dt.date(2026, 8, 31))     # Monday first
        self.assertEqual(model.month_grid(2026, 2, 6)[0], dt.date(2026, 2, 1))       # starts on a Sunday
        self.assertTrue(all(d.weekday() == 6 for d in model.month_grid(2027, 5, 6)[::7]))
        self.assertIn(model.locale_first_weekday(), range(7))
        self.assertEqual(model.week_start(dt.date(2026, 9, 30), 6), dt.date(2026, 9, 27))


class StoreTest(unittest.TestCase):
    def test_store(self):
        folder = tempfile.mkdtemp()
        st = model.Store(folder)
        st.load()
        self.assertEqual([(c.name, c.color) for c in st.calendars], [("Home", "blue"), ("Work", "red")])
        ev = ics.Event(uid="u1", summary="Dentist", start=D(2026, 10, 2, 11), end=D(2026, 10, 2, 12),
                       calendar="home")
        st.put(ev)
        moved = ev.copy()
        moved.calendar = "work"
        st.put(moved)
        st.update_calendar("work", name="Office", color="purple")
        n = st.import_text("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:i1\nSUMMARY:Imported\nDTSTART:20261001T100000\n"
                           "END:VEVENT\nEND:VCALENDAR\n", "home")
        self.assertEqual(n, 1)
        again = model.Store(folder)
        again.load()
        self.assertEqual(again.events["u1"].calendar, "work")
        self.assertEqual(again.calendar("work").name, "Office")
        self.assertEqual(again.calendar("work").color, "purple")
        self.assertIn("i1", again.events)
        again.delete_calendar("work")
        self.assertFalse(os.path.exists(os.path.join(folder, "work.ics")))
        self.assertEqual(model.color_from_hex("#34C759"), "green")


class AlertsTest(unittest.TestCase):
    def test_due(self):
        ev = ics.Event(uid="a", summary="Call", start=D(2026, 9, 30, 10), end=D(2026, 9, 30, 11), alarm=15,
                       freq="DAILY")
        quiet = ics.Event(uid="q", start=D(2026, 9, 30, 10), end=D(2026, 9, 30, 11))
        got = alerts.due([ev, quiet], D(2026, 9, 30, 9, 40), D(2026, 10, 1, 9, 45))
        self.assertEqual([t for t, _o in got], [D(2026, 9, 30, 9, 45), D(2026, 10, 1, 9, 45)])
        self.assertEqual(alerts.due([ev], D(2026, 9, 30, 9, 45), D(2026, 9, 30, 9, 50)), [])


class WindowTest(unittest.TestCase):
    def test_window(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib
        from sonata2 import ui
        from sonata2.calendar.window import CalendarWindow, parse_time

        def settle(ms=200):
            end = GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
        self.assertEqual(parse_time("9:30 pm"), dt.time(21, 30))
        self.assertEqual(parse_time("0930"), dt.time(9, 30))
        self.assertIsNone(parse_time("25:00"))
        Adw.init()
        ui.setup()
        app = Adw.Application(application_id="io.test.calendar")
        app.register(None)
        folder = tempfile.mkdtemp()
        win = CalendarWindow(app, folder=folder)
        win.store.async_writes = False
        win.present()
        settle()
        for v in ("day", "week", "month", "year"):
            win.set_view(v)
            settle(60)
            self.assertEqual(win.stack.get_visible_child_name(), v)
        win.set_view("week")
        win.go(dt.date(2026, 9, 30))
        start = D(2026, 9, 30, 10)
        win.create(start, start + dt.timedelta(hours=1), False)
        uid = win.selected[0]
        self.assertEqual(win.store.events[uid].summary, "New Event")
        occ = win._selected_occ
        win.move(occ, dt.timedelta(days=1, minutes=30), dt.timedelta(days=1, minutes=90))
        ev = win.store.events[uid]
        self.assertEqual((ev.start, ev.end), (D(2026, 10, 1, 10, 30), D(2026, 10, 1, 12, 30)))
        win.undo()
        self.assertEqual(win.store.events[uid].start, start)
        win.redo()
        self.assertEqual(win.store.events[uid].start, D(2026, 10, 1, 10, 30))
        win.delete_selected()
        self.assertNotIn(uid, win.store.events)
        win.undo()
        self.assertIn(uid, win.store.events)
        # the editor: a new title and a daily repeat, applied when it closes
        occ = ics.Occurrence(win.store.events[uid], D(2026, 10, 1, 10, 30), D(2026, 10, 1, 12, 30))
        grid = win.views["week"].grid
        r = grid.rect(200, 10.5 * 48, 100, 96)
        win.views["week"].scroll_to_hour(9, animate=False)
        win.edit(occ, grid, r)
        ed = win.editor
        settle(100)
        ed.title.set_text("Review")
        ed.repeat.set_selected(1)
        ed.pop.popdown()
        settle(100)
        ev = win.store.events[uid]
        self.assertEqual((ev.summary, ev.freq), ("Review", "DAILY"))
        self.assertEqual(len(win.occurrences(D(2026, 10, 1), D(2026, 10, 8))), 7)
        # the week view lays them out; month / year paint them
        win.refresh()
        self.assertTrue(win.views["week"].segments)
        for v in ("month", "year", "day"):
            win.set_view(v)
            settle(60)
        win.step(1)
        self.assertEqual(win.date, dt.date(2026, 10, 1))
        win.search.set_text("review")
        settle(400)                                   # the search field waits for typing to pause
        self.assertIsNotNone(win.results.get_row_at_index(0))
        # hidden calendar: its events leave the views
        win.set_visible_calendar(ev.calendar, False)
        self.assertEqual(win.occurrences(D(2026, 10, 1), D(2026, 10, 8)), [])
        win.destroy()
        settle(50)
        self.assertTrue(os.path.exists(os.path.join(folder, "home.ics")))

    def test_interactions(self):
        """Double-click creates, drags move and resize, Delete and ⌘Z."""
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, GLib
        from sonata2 import ui
        from sonata2.calendar.window import CalendarWindow
        from sonata2.calendar.views import HOUR_H

        def settle(ms=200):
            end = GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
        Adw.init()
        ui.setup()
        app = Adw.Application(application_id="io.test.calendar2")
        app.register(None)
        win = CalendarWindow(app, folder=tempfile.mkdtemp())
        win.store.async_writes = False
        win.set_default_size(1000, 700)
        win.present()
        win.set_view("week")
        win.go(dt.date(2026, 9, 30))
        settle(300)
        grid = win.views["week"].grid
        cw = (grid.get_width() - 56) / 7
        x = 56 + 3 * cw + cw / 2                     # Wednesday 30
        grid._pressed(1, 2, x, 10 * HOUR_H + 5)      # double-click at 10:05 -> 10:00-11:00
        settle(100)
        if win.editor:
            win.editor.pop.popdown()
            settle(100)
        uid = win.selected[0]
        ev = win.store.events[uid]
        self.assertEqual((ev.start, ev.end), (D(2026, 9, 30, 10), D(2026, 9, 30, 11)))
        grid.queue_draw()
        settle(100)
        grid._drag_begin(None, x, 10.5 * HOUR_H)           # move: down 1 hour, one day right
        grid._drag_update(None, cw, HOUR_H)
        grid._drag_end(None, cw, HOUR_H)
        ev = win.store.events[uid]
        self.assertEqual((ev.start, ev.end), (D(2026, 10, 1, 11), D(2026, 10, 1, 12)))
        settle(100)
        x2 = x + cw
        grid._drag_begin(None, x2, 12 * HOUR_H - 2)        # resize: the bottom edge down 30 minutes
        grid._drag_update(None, 0, HOUR_H / 2)
        grid._drag_end(None, 0, HOUR_H / 2)
        self.assertEqual(win.store.events[uid].end, D(2026, 10, 1, 12, 30))
        # month: drag to the next week
        win.set_view("month")
        settle(150)
        month = win.views["month"]
        hx, hy, hw, hh, _o = next(h for h in month.hits if h[4].event.uid == uid)
        ch = (month.get_height() - 26) / 6
        month._drag_begin(None, hx + 5, hy + 5)
        month._drag_update(None, 0, ch)
        month._drag_end(None, 0, ch)
        self.assertEqual(win.store.events[uid].start, D(2026, 10, 8, 11))
        # Delete key, then ⌘Z
        win._key(None, Gdk.KEY_Delete, 0, 0)
        self.assertNotIn(uid, win.store.events)
        win._key(None, Gdk.KEY_z, 0, Gdk.ModifierType.CONTROL_MASK)
        self.assertIn(uid, win.store.events)
        win._key(None, Gdk.KEY_1, 0, Gdk.ModifierType.SUPER_MASK)
        self.assertEqual(win.view, "day")
        win._key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(win.date, dt.date(2026, 10, 1))
        win.destroy()
        settle(50)


if __name__ == "__main__":
    unittest.main()
