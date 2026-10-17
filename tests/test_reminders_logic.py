"""Reminders' pure logic: the due string a reminder keeps, how it reads in a
row ("Today, 14:00"), when it turns red (overdue), the Scheduled list's day
headings and which reminders an alert sweep picks. All with fixed dates, so
nothing depends on when the test runs.
Run: xvfb-run -a python3 -m unittest tests.test_reminders_logic"""
import datetime
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
# the strings below are English ones: not the runner's language
os.environ["LC_ALL"] = os.environ["LANG"] = "C.UTF-8"
os.environ.pop("LANGUAGE", None)
os.environ.pop("LC_MESSAGES", None)

from sonata2.notes import reminders as R  # noqa: E402
from sonata2.notes import store as S  # noqa: E402

TODAY = datetime.date(2026, 10, 10)
NOON = datetime.datetime(2026, 10, 10, 12, 0)


class DueStringTest(unittest.TestCase):
    def test_make_due_round_trips(self):
        """make_due writes what due_datetime reads back: all day without a
        time (alerting at 9:00, like macOS), timed with zero-padded hours."""
        d = datetime.date(2026, 3, 7)
        self.assertEqual(S.make_due(d), "2026-03-07")
        self.assertEqual(S.make_due(d, 8, 5), "2026-03-07T08:05")
        self.assertIsNone(S.make_due(None, 9))
        self.assertEqual(S.due_datetime({"due": S.make_due(d)}), datetime.datetime(2026, 3, 7, S.ALL_DAY_HOUR, 0))
        self.assertEqual(S.due_datetime({"due": S.make_due(d, 23, 59)}), datetime.datetime(2026, 3, 7, 23, 59))
        self.assertFalse(S.has_time({"due": S.make_due(d)}))
        self.assertTrue(S.has_time({"due": S.make_due(d, 0, 0)}))     # midnight is still a time

    def test_damaged_due_is_no_date(self):
        """reminders.json is hand-editable and synced: a bad `due` must read as
        "no date" (never raise in the list, the alert sweep or the menu bar)."""
        for due in ("", None, "tomorrow", "2026-13-01", "2026-02-30", "2026-10-10T25:00", "2026-10-10 14:00"):
            r = {"due": due}
            self.assertIsNone(S.due_datetime(r), due)
            self.assertIsNone(S.due_date(r), due)
            self.assertFalse(R.is_overdue(r, NOON), due)
            self.assertEqual(R.due_text(r, TODAY), "", due)
        self.assertIsNone(S.due_datetime({}))


class DueTextTest(unittest.TestCase):
    def test_near_days_by_name(self):
        """Yesterday / Today / Tomorrow by name, with the time when it has one."""
        self.assertEqual(R.due_text({"due": "2026-10-10T14:00"}, TODAY), "Today, 14:00")
        self.assertEqual(R.due_text({"due": "2026-10-11"}, TODAY), "Tomorrow")
        self.assertEqual(R.due_text({"due": "2026-10-09T08:30"}, TODAY), "Yesterday, 08:30")

    def test_other_days_as_a_date(self):
        """Further away: the locale's date (never a day name that could be
        mistaken for this week's), the time after a comma."""
        text = R.due_text({"due": "2026-12-25T09:30"}, TODAY)
        self.assertTrue(text.endswith(", 09:30"), text)
        self.assertIn("25", text)
        self.assertNotIn("Today", R.due_text({"due": "2026-10-12"}, TODAY))
        self.assertNotIn(",", R.due_text({"due": "2026-10-12"}, TODAY))     # all day: no time part

    def test_across_a_month_and_year_end(self):
        """Tomorrow is counted in days, not by the day of the month."""
        self.assertEqual(R.due_text({"due": "2027-01-01"}, datetime.date(2026, 12, 31)), "Tomorrow")
        self.assertEqual(R.due_text({"due": "2026-10-31T10:00"}, datetime.date(2026, 11, 1)), "Yesterday, 10:00")


class OverdueTest(unittest.TestCase):
    def test_timed_reminder_overdue_after_its_minute(self):
        r = {"due": "2026-10-10T11:59"}
        self.assertTrue(R.is_overdue(r, NOON))
        self.assertFalse(R.is_overdue({"due": "2026-10-10T12:00"}, NOON))     # due now: not late yet
        self.assertFalse(R.is_overdue({"due": "2026-10-10T12:01"}, NOON))

    def test_all_day_reminder_overdue_only_the_next_day(self):
        """An all-day reminder alerts at 9:00 but isn't late (red) until its
        day is over: at noon today it is still on time."""
        self.assertFalse(R.is_overdue({"due": "2026-10-10"}, NOON))
        self.assertFalse(R.is_overdue({"due": "2026-10-10"}, datetime.datetime(2026, 10, 10, 23, 59)))
        self.assertTrue(R.is_overdue({"due": "2026-10-09"}, NOON))

    def test_completed_is_never_overdue(self):
        self.assertFalse(R.is_overdue({"due": "2020-01-01", "completed": True}, NOON))


class DayHeadingTest(unittest.TestCase):
    def test_headings(self):
        """Scheduled groups every past day under one "Overdue", then Today,
        Tomorrow and a weekday-and-date heading for later days."""
        self.assertEqual(R.day_heading(datetime.date(2026, 9, 1), TODAY), "Overdue")
        self.assertEqual(R.day_heading(datetime.date(2026, 10, 9), TODAY), "Overdue")
        self.assertEqual(R.day_heading(TODAY, TODAY), "Today")
        self.assertEqual(R.day_heading(datetime.date(2026, 10, 11), TODAY), "Tomorrow")
        later = R.day_heading(datetime.date(2026, 10, 14), TODAY)
        self.assertIn("Wednesday", later)
        self.assertIn("14", later)
        self.assertIn("October", later)


class AlertSweepTest(unittest.TestCase):
    def setUp(self):
        self.st = S.Store(tempfile.mkdtemp())

    def test_due_between_is_half_open(self):
        """(start, end]: a reminder due exactly at the end of one sweep is
        caught by it and not again by the next one (no double alert); one due
        at the start was the previous sweep's."""
        a = self.st.new_reminder(title="a", due="2026-10-10T12:00")
        b = self.st.new_reminder(title="b", due="2026-10-10T12:05")
        self.st.new_reminder(title="undated")
        first = self.st.due_between(datetime.datetime(2026, 10, 10, 11, 55), NOON)
        second = self.st.due_between(NOON, datetime.datetime(2026, 10, 10, 12, 5))
        self.assertEqual([r["id"] for r in first], [a["id"]])
        self.assertEqual([r["id"] for r in second], [b["id"]])

    def test_all_day_alerts_at_nine(self):
        r = self.st.new_reminder(title="all day", due="2026-10-10")
        self.assertEqual(self.st.due_between(datetime.datetime(2026, 10, 10, 8, 0),
                                             datetime.datetime(2026, 10, 10, 8, 59)), [])
        self.assertEqual(self.st.due_between(datetime.datetime(2026, 10, 10, 8, 59),
                                             datetime.datetime(2026, 10, 10, 9, 0)), [r])

    def test_completed_ones_never_alert(self):
        r = self.st.new_reminder(title="done", due="2026-10-10T12:00")
        self.st.update_reminder(r, completed=True)
        self.assertEqual(self.st.due_between(datetime.datetime(2026, 10, 10, 11), NOON), [])
        self.assertIsNone(self.st.next_due_after(datetime.datetime(2026, 10, 10, 11)))

    def test_next_due_after(self):
        """The alert timer sleeps until the next open due time, skipping
        undated, past and damaged ones."""
        self.st.new_reminder(title="past", due="2026-10-10T08:00")
        self.st.new_reminder(title="bad", due="soon")
        self.st.new_reminder(title="later", due="2026-10-12")
        self.st.new_reminder(title="next", due="2026-10-10T18:30")
        self.assertEqual(self.st.next_due_after(NOON), datetime.datetime(2026, 10, 10, 18, 30))

    def test_completing_keeps_the_first_completion_time(self):
        """Re-completing a done reminder keeps when it was first done (the
        Completed list's order); reopening clears it."""
        r = self.st.new_reminder(title="x")
        self.st.update_reminder(r, completed=True)
        first = r["completed_at"]
        self.assertIsNotNone(first)
        self.st.update_reminder(r, completed=True)
        self.assertEqual(r["completed_at"], first)
        self.st.update_reminder(r, completed=False)
        self.assertIsNone(r["completed_at"])


if __name__ == "__main__":
    unittest.main()
