"""Review tests for the document apps (Calendar, Notes, TextEdit): pure logic
only -- iCalendar parsing/recurrence/time zones, the calendar store, notes
markup round trips and storage, TextEdit's encodings and session buffers
(xvfb-run python3 -m unittest tests.test_review_docs)."""
import datetime as dt
import json
import os
import random
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2.calendar import alerts, ics, model  # noqa: E402
from sonata2.notes import markup  # noqa: E402
from sonata2.notes import store as S  # noqa: E402
from sonata2.textedit import document, search, session  # noqa: E402

D = dt.datetime


def starts(ev, a, b):
    return [o.start for o in ics.occurrences(ev, a, b)]


class _TZ:
    """Run a block with the process' local time zone set to `name`."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = self.name
        time.tzset()

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        time.tzset()


# -- calendar: ics ------------------------------------------------------------------------------
class IcsParseTest(unittest.TestCase):
    def test_folded_lines_and_escapes(self):
        """Folded continuation lines (space or tab) and \\n \\, \\; escapes decode correctly."""
        text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20261001T090000\r\n"
                "SUMMARY:Long sum\r\n mary\\, with\\; escapes\r\nDESCRIPTION:one\\ntwo\\Nthree\r\n\tfour\r\n"
                "END:VEVENT\r\nEND:VCALENDAR\r\n")
        _info, (ev,) = ics.parse(text)
        self.assertEqual(ev.summary, "Long summary, with; escapes")
        self.assertEqual(ev.description, "one\ntwo\nthreefour")
        self.assertEqual(ev.end, ev.start)                        # no DTEND/DURATION: zero length

    def test_quoted_param_with_colon(self):
        """A quoted parameter value containing ':' does not split the value early."""
        name, params, value = ics._split('ATTENDEE;CN="Doe: John";ROLE=REQ:mailto:j@x.org')
        self.assertEqual((name, params["CN"], params["ROLE"], value), ("ATTENDEE", "Doe: John", "REQ", "mailto:j@x.org"))

    def test_malformed_property_keeps_event(self):
        """A bad DTEND is skipped instead of dropping the event; DTEND before DTSTART is clamped."""
        text = ("BEGIN:VEVENT\nUID:a\nDTSTART:20261001T100000\nDTEND:garbage\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:b\nDTSTART:20261001T100000\nDTEND:20261001T090000\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:c\nSUMMARY:no start\nEND:VEVENT\n")
        _info, evs = ics.parse(text)
        self.assertEqual([e.uid for e in evs], ["a", "b"])        # no DTSTART: dropped
        self.assertTrue(all(e.end == e.start for e in evs))

    def test_alarm_triggers(self):
        """Only relative-to-start triggers become alerts; after-start triggers clamp to 0."""
        def alarm(trigger):
            text = f"BEGIN:VEVENT\nUID:x\nDTSTART:20261001T100000\nBEGIN:VALARM\n{trigger}\nEND:VALARM\nEND:VEVENT\n"
            return ics.parse(text)[1][0].alarm
        self.assertEqual(alarm("TRIGGER:-P1DT2H"), 26 * 60)
        self.assertEqual(alarm("TRIGGER:PT15M"), 0)
        self.assertIsNone(alarm("TRIGGER;VALUE=DATE-TIME:20261001T090000Z"))
        self.assertIsNone(alarm("TRIGGER;RELATED=END:-PT5M"))

    def test_duration_format_round_trip(self):
        """format_duration/parse_duration agree for days, hours, minutes and negatives."""
        for td in (dt.timedelta(0), dt.timedelta(days=2), dt.timedelta(hours=-3), dt.timedelta(minutes=-90),
                   dt.timedelta(weeks=1)):
            self.assertEqual(ics.parse_duration(ics.format_duration(td)), td)
        self.assertIsNone(ics.parse_duration("bogus"))

    def test_fold_never_splits_multibyte(self):
        """Folding counts octets and never cuts a UTF-8 character in two."""
        line = "SUMMARY:" + "日本語😀" * 40
        folded = ics._fold(line)
        parts = folded.split("\r\n")
        self.assertTrue(all(len(p.encode("utf-8")) <= 75 for p in parts))
        self.assertEqual(ics._unfold(folded), [line])

    def test_all_day_exdate_round_trip(self):
        """EXDATEs of an all-day series are written as dates and still exclude the day after re-parse."""
        ev = ics.Event(uid="ad", summary="Gym", start=D(2026, 10, 1), end=D(2026, 10, 2), all_day=True,
                       freq="DAILY", count=5, exdates=[D(2026, 10, 3)])
        text = ics.serialize([ev])
        self.assertIn("EXDATE;VALUE=DATE:20261003", text)
        back = ics.parse(text)[1][0]
        self.assertEqual(starts(back, D(2026, 10, 1), D(2026, 11, 1)),
                         [D(2026, 10, d) for d in (1, 2, 4, 5)])

    @unittest.expectedFailure
    def test_all_day_until_is_written_as_date(self):
        """RFC 5545: UNTIL must be a DATE when DTSTART is a DATE (other apps reject the datetime form)."""
        # BUG: event_lines always writes UNTIL as YYYYMMDDTHHMMSS, even for all-day series
        ev = ics.Event(uid="u", start=D(2026, 10, 1), end=D(2026, 10, 2), all_day=True, freq="DAILY",
                       until=D(2026, 10, 5, 23, 59, 59, 999999))
        self.assertIn("UNTIL=20261005;", ics.serialize([ev]).replace("\r\n", ";"))

    @unittest.expectedFailure
    def test_unsupported_rrule_parts_survive_round_trip(self):
        """A foreign weekly BYDAY rule must not be silently reduced to 'weekly on DTSTART only' on save."""
        # BUG: _parse_rrule drops BYDAY/BYMONTHDAY/... and serialize() rewrites the rule without them
        text = ("BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\nDTEND:20261005T100000\n"
                "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\nEND:VEVENT\n")
        ev = ics.parse(text)[1][0]
        self.assertIn("BYDAY=MO,WE,FR", ics.serialize([ev]))

    @unittest.expectedFailure
    def test_recurring_utc_event_follows_local_dst(self):
        """A weekly 13:00Z meeting is 14:00 in Berlin in winter and 15:00 after the DST switch."""
        # BUG: UTC/TZID starts are converted to local wall time once, then recurrence runs on naive
        # local times, so occurrences after a DST change are an hour off
        with _TZ("Europe/Berlin"):
            text = "BEGIN:VEVENT\nUID:z\nDTSTART:20260305T130000Z\nDURATION:PT1H\nRRULE:FREQ=WEEKLY\nEND:VEVENT\n"
            ev = ics.parse(text)[1][0]
            self.assertEqual(ev.start, D(2026, 3, 5, 14))
            self.assertEqual(starts(ev, D(2026, 4, 2), D(2026, 4, 3)), [D(2026, 4, 2, 15)])

    def test_tzid_converted_to_local(self):
        """A TZID start is converted to the local zone; an unknown TZID keeps the wall time."""
        with _TZ("America/Sao_Paulo"):
            d, is_date = ics.parse_datetime("20260701T100000", {"TZID": "Europe/Berlin"})
            self.assertEqual((d, is_date), (D(2026, 7, 1, 5), False))
            self.assertEqual(ics.parse_datetime("20260701T100000", {"TZID": "Mars/Base"})[0], D(2026, 7, 1, 10))


class RecurrenceReviewTest(unittest.TestCase):
    def test_exdate_on_timed_series(self):
        """An EXDATE removes exactly that occurrence and nothing else."""
        ev = ics.Event(uid="d", start=D(2026, 10, 1, 9), end=D(2026, 10, 1, 10), freq="DAILY",
                       exdates=[D(2026, 10, 2, 9)])
        self.assertEqual(starts(ev, D(2026, 10, 1), D(2026, 10, 4)), [D(2026, 10, 1, 9), D(2026, 10, 3, 9)])

    def test_count_counts_skipped_dates(self):
        """COUNT counts real occurrences only; months without the 31st do not consume the count."""
        ev = ics.Event(uid="m", start=D(2026, 1, 31), end=D(2026, 1, 31, 1), freq="MONTHLY", count=3)
        self.assertEqual(starts(ev, D(2026, 1, 1), D(2030, 1, 1)),
                         [D(2026, 1, 31), D(2026, 3, 31), D(2026, 5, 31)])

    def test_far_monthly_and_yearly_jump_match_walk(self):
        """The jump-ahead for MONTHLY/YEARLY series finds the same occurrences as a full walk."""
        for freq, interval in (("MONTHLY", 1), ("MONTHLY", 5), ("YEARLY", 1), ("YEARLY", 3)):
            ev = ics.Event(uid="j", start=D(2001, 3, 31, 8), end=D(2001, 3, 31, 9), freq=freq, interval=interval)
            far = starts(ev, D(2040, 1, 1), D(2045, 1, 1))
            walked = [s for s in starts(ev, D(2001, 1, 1), D(2045, 1, 1)) if s >= D(2040, 1, 1)]
            self.assertEqual(far, walked, (freq, interval))

    def test_multiday_occurrence_overlapping_range_start(self):
        """A daily 3-day event is returned when it started before the range but still runs inside it."""
        ev = ics.Event(uid="x", start=D(2026, 10, 1), end=D(2026, 10, 4), all_day=True, freq="WEEKLY")
        self.assertEqual(starts(ev, D(2026, 10, 10), D(2026, 10, 11)), [D(2026, 10, 8)])

    def test_alert_not_repeated_or_missed(self):
        """due() reports an alert exactly once over consecutive (after, until] windows."""
        ev = ics.Event(uid="a", summary="A", start=D(2026, 10, 1, 10), end=D(2026, 10, 1, 11), alarm=15)
        w1 = alerts.due([ev], D(2026, 10, 1, 9), D(2026, 10, 1, 9, 45))
        w2 = alerts.due([ev], D(2026, 10, 1, 9, 45), D(2026, 10, 1, 10, 30))
        self.assertEqual([t for t, _o in w1], [D(2026, 10, 1, 9, 45)])
        self.assertEqual(w2, [])


class CalendarStoreReviewTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_move_event_between_calendars_rewrites_both(self):
        """Moving an event to another calendar removes it from the old file and adds it to the new one."""
        st = model.Store(self.dir)
        st.load()
        home, work = st.calendars[0].id, st.calendars[1].id
        ev = ics.Event(uid="mv", summary="Move me", start=D(2026, 10, 1, 9), end=D(2026, 10, 1, 10), calendar=home)
        st.put(ev)
        moved = ev.copy()
        moved.calendar = work
        st.put(moved)
        st2 = model.Store(self.dir)
        st2.load()
        self.assertEqual(st2.events["mv"].calendar, work)
        with open(os.path.join(self.dir, home + ".ics"), encoding="utf-8") as f:
            self.assertNotIn("Move me", f.read())

    def test_import_replaces_same_uid_and_unique_ids(self):
        """Importing an event with a known UID replaces it (also across calendars); calendar ids stay unique."""
        st = model.Store(self.dir)
        st.load()
        a = st.add_calendar("Home")
        self.assertNotEqual(a.id, st.calendars[0].id)
        st.put(ics.Event(uid="dup", summary="Old", start=D(2026, 1, 1, 9), end=D(2026, 1, 1, 10),
                         calendar=st.calendars[0].id))
        n = st.import_text("BEGIN:VEVENT\nUID:dup\nSUMMARY:New\nDTSTART:20260101T090000\nEND:VEVENT\n", a.id)
        self.assertEqual(n, 1)
        st2 = model.Store(self.dir)
        st2.load()
        self.assertEqual((st2.events["dup"].summary, st2.events["dup"].calendar), ("New", a.id))

    @unittest.expectedFailure
    def test_recurrence_id_override_does_not_replace_master(self):
        """A modified instance (RECURRENCE-ID, same UID) must not wipe out the recurring master."""
        # BUG: Store keys events by UID only and ics.parse ignores RECURRENCE-ID, so the override
        # replaces the master series in memory and the next write drops the series from disk
        st = model.Store(self.dir)
        st.load()
        cal = st.calendars[0].id
        st.import_text("BEGIN:VEVENT\nUID:s\nSUMMARY:Series\nDTSTART:20261001T090000\nDURATION:PT1H\n"
                       "RRULE:FREQ=DAILY\nEND:VEVENT\nBEGIN:VEVENT\nUID:s\nRECURRENCE-ID:20261003T090000\n"
                       "SUMMARY:Moved one\nDTSTART:20261003T120000\nDURATION:PT1H\nEND:VEVENT\n", cal)
        self.assertEqual(st.events["s"].freq, "DAILY")

    @unittest.expectedFailure
    def test_delete_calendar_wins_over_pending_async_write(self):
        """Deleting a calendar right after an edit must not have the queued write resurrect its file."""
        # BUG: delete_calendar removes the file without taking _lock; a write thread still waiting
        # for the lock recreates <id>.ics afterwards, so the deleted calendar comes back on next load
        st = model.Store(self.dir)
        st.load()
        st.async_writes = True
        cal = st.add_calendar("Temp")
        path = os.path.join(self.dir, cal.id + ".ics")
        before = set(threading.enumerate())
        with st._lock:
            st.put(ics.Event(uid="t", start=D(2026, 1, 1, 9), end=D(2026, 1, 1, 10), calendar=cal.id))
            st.delete_calendar(cal.id)
        for t in set(threading.enumerate()) - before:
            t.join(2)
        self.assertFalse(os.path.exists(path))


# -- notes: markup --------------------------------------------------------------------------------
class MarkupReviewTest(unittest.TestCase):
    def test_random_inline_runs_round_trip(self):
        """Any mix of inline styles and marker characters survives serialize -> parse."""
        rnd = random.Random(1234)
        alphabet = "ab *_~\\#-1.x"
        tagsets = [frozenset(s) for s in ((), ("bold",), ("italic",), ("underline",), ("strike",),
                                          ("bold", "italic"), ("italic", "strike"), ("bold", "underline", "strike"))]
        for _ in range(400):
            runs, last = [], None
            for _r in range(rnd.randint(1, 5)):
                tags = rnd.choice([t for t in tagsets if t != last])
                runs.append(("".join(rnd.choice(alphabet) for _c in range(rnd.randint(1, 6))), tags))
                last = tags
            line = markup.serialize_line("body", False, runs)
            kind, _c, back = markup.parse_line(line)
            self.assertEqual(kind, "body", line)
            self.assertEqual(back, runs, line)

    def test_body_lines_that_look_like_prefixes(self):
        """Body text starting like a heading, list or number stays body text after a round trip."""
        for text in ("# not a title", "## nope", "- not a bullet", "12. not numbered", "- [ ] not a check", "#tag"):
            line = markup.serialize_line("body", False, [(text, frozenset())])
            self.assertEqual(markup.parse_line(line), ("body", False, [(text, frozenset())]), line)

    def test_every_kind_round_trips(self):
        """Each paragraph kind (and checked state) comes back from its serialized form."""
        runs = [("text", frozenset())]
        for kind in markup.KINDS:
            for checked in ((False, True) if kind == "check" else (False,)):
                self.assertEqual(markup.parse_line(markup.serialize_line(kind, checked, runs)), (kind, checked, runs))

    @unittest.expectedFailure
    def test_bullet_text_starting_with_checkbox_stays_bullet(self):
        """A bullet whose text begins with '[ ] ' must not reload as a checklist item."""
        # BUG: serialize_line writes '- ' + '[ ] buy', which parse_line reads as an unchecked check item
        runs = [("[ ] buy", frozenset())]
        self.assertEqual(markup.parse_line(markup.serialize_line("bullet", False, runs))[0], "bullet")

    def test_trailing_backslash_and_unclosed_marker(self):
        """A lone trailing backslash is literal; an unclosed '**' styles the rest without losing text."""
        self.assertEqual(markup.parse_inline("a\\"), [("a\\", frozenset())])
        self.assertEqual(markup.parse_inline("x **y"), [("x ", frozenset()), ("y", frozenset({"bold"}))])

    def test_title_and_preview_skip_blank_lines(self):
        """The title is the first non-blank line without markers; the preview the next one."""
        self.assertEqual(markup.title_and_preview("\n\n# **Big** day\n\n- milk\n"), ("Big day", "milk"))


# -- notes: store ---------------------------------------------------------------------------------
class NotesStoreReviewTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_unicode_body_round_trip(self):
        """Non-ASCII note bodies are stored as UTF-8 and read back identically."""
        st = S.Store(self.dir)
        body = "# Café ☕\nNaïve résumé — 日本語 😀\n\\- literal"
        n = st.new_note(body=body)
        self.assertEqual(S.Store(self.dir).note(n["id"])["body"], body)
        with open(os.path.join(self.dir, "notes.json"), encoding="utf-8") as f:
            self.assertIn("日本語", f.read())                     # ensure_ascii=False

    def test_update_note_unchanged_does_not_write(self):
        """Saving identical text neither bumps 'modified' nor rewrites the file."""
        st = S.Store(self.dir)
        n = st.new_note(body="same", now=100)
        with mock.patch.object(S, "write_json") as w:
            st.update_note(n, "same", now=200)
        w.assert_not_called()
        self.assertEqual(n["modified"], 100)

    def test_new_note_in_virtual_or_missing_folder_goes_to_notes(self):
        """New notes in All / Recently Deleted / unknown folders land in the default folder."""
        st = S.Store(self.dir)
        for folder in (S.ALL, S.DELETED, "nope"):
            self.assertEqual(st.new_note(folder)["folder"], S.DEFAULT_FOLDER)

    def test_recover_note_of_deleted_folder(self):
        """A note whose folder disappeared is recovered into the default folder."""
        st = S.Store(self.dir)
        f = st.add_folder("Trip")
        n = st.new_note(f["id"], "x")
        st.delete_note(n)
        st.folders = [x for x in st.folders if x is not f]
        st.recover_note(n)
        self.assertEqual((n["folder"], n["deleted"]), (S.DEFAULT_FOLDER, None))

    @unittest.expectedFailure
    def test_corrupt_notes_file_is_not_overwritten(self):
        """A damaged notes.json must be kept aside, not silently replaced by an empty store on next save."""
        # BUG: _read() returns {} on ValueError and the next save_notes() overwrites the user's file
        path = os.path.join(self.dir, "notes.json")
        damaged = '{"folders": [], "notes": [{"id": "a", "body": "precious'
        with open(path, "w", encoding="utf-8") as f:
            f.write(damaged)
        S.Store(self.dir).new_note(body="new")
        kept = []
        for name in os.listdir(self.dir):
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                kept.append(f.read())
        self.assertIn(damaged, kept)

    @unittest.expectedFailure
    def test_completing_twice_keeps_completed_at(self):
        """Marking an already-completed reminder completed again keeps its original completion time."""
        # BUG: update_reminder always sets completed_at = now when 'completed' is passed, so the
        # equality short-circuit never triggers and the completion time is bumped (and the file rewritten)
        st = S.Store(self.dir)
        r = st.new_reminder(title="x")
        st.update_reminder(r, completed=True)
        first = r["completed_at"] = 1000.0
        st.update_reminder(r, completed=True)
        self.assertEqual(r["completed_at"], first)

    def test_due_datetime_formats(self):
        """All-day dues alert at 9:00, timed dues at their time, junk is ignored."""
        self.assertEqual(S.due_datetime({"due": "2026-10-03"}), D(2026, 10, 3, S.ALL_DAY_HOUR))
        self.assertEqual(S.due_datetime({"due": "2026-10-03T07:05"}), D(2026, 10, 3, 7, 5))
        self.assertIsNone(S.due_datetime({"due": "tomorrow"}))
        self.assertEqual(S.make_due(dt.date(2026, 1, 2), 7, 5), "2026-01-02T07:05")
        self.assertEqual(S.make_due(dt.date(2026, 1, 2)), "2026-01-02")

    def test_next_due_ignores_completed_and_past(self):
        """next_due_after returns the earliest open reminder strictly after the given time."""
        st = S.Store(self.dir)
        st.new_reminder(title="past", due="2026-10-01T08:00")
        done = st.new_reminder(title="done", due="2026-10-02T08:00")
        st.update_reminder(done, completed=True)
        st.new_reminder(title="next", due="2026-10-03T08:00")
        self.assertEqual(st.next_due_after(D(2026, 10, 1, 12)), D(2026, 10, 3, 8))


# -- textedit -------------------------------------------------------------------------------------
class TextEditFormatsReviewTest(unittest.TestCase):
    def test_round_trip_all_encodings_with_bom(self):
        """Every offered encoding (with BOM where it exists) round-trips bytes exactly."""
        for enc in document.ENCODINGS:
            text = "Olá\r\nmundo\r\n" if enc != "utf-16-be" else "Olá\nmundo\n"
            for bom in ((False, True) if enc.startswith("utf") else (False,)):
                data = document.encode(document.normalize(text), enc, bom, document.detect_newline(text))
                d = document.decode(data)
                self.assertEqual(document.encode(d.text, d.encoding, d.bom, d.newline), data, (enc, bom))

    def test_utf8_beats_latin1_guess(self):
        """Valid UTF-8 is never misdetected as a Latin encoding."""
        d = document.decode("ação çé".encode("utf-8"))
        self.assertEqual((d.text, d.encoding), ("ação çé", "utf-8"))

    @unittest.expectedFailure
    def test_nul_after_sniff_window_is_not_corrupted_on_save(self):
        """A file with NUL bytes beyond the 8 KB sniff window must not be silently altered on save."""
        # BUG: decode() only looks for NUL in the first 8 KB and then replaces every NUL with U+FFFD,
        # so open + save rewrites those bytes as EF BF BD (data loss) instead of refusing the file
        data = b"a" * 9000 + b"\0tail"
        try:
            d = document.decode(data)
        except document.BinaryFile:
            return
        self.assertEqual(document.encode(d.text, d.encoding, d.bom, d.newline), data)

    def test_mixed_newlines_use_majority(self):
        """Mixed line endings normalise to \\n and save with the majority style."""
        d = document.decode(b"a\r\nb\r\nc\nd")
        self.assertEqual((d.text, d.newline), ("a\nb\nc\nd", "CRLF"))

    def test_literal_replace_keeps_backslashes_and_groups(self):
        """A literal replacement containing '\\1' or '\\g<0>' is inserted verbatim."""
        self.assertEqual(search.replace_all("a.b", ".", r"\1\g<0>"), (r"a\1\g<0>b", 1))
        self.assertEqual(search.replace_all("A a", "a", "x", case=True), ("A x", 1))

    def test_whole_word_regex_alternation(self):
        """whole=True wraps a regex alternation so both alternatives need word boundaries."""
        self.assertEqual(search.find_all("cat catalog dog", "cat|dog", regex=True, whole=True), [(0, 3), (12, 15)])


class SessionReviewTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(session, "data_dir", lambda: self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_buffers_round_trip_and_orphans_removed(self):
        """Unsaved text survives; buffers no tab refers to are deleted, foreign files are left alone."""
        session.write_buffer("aaaaaaaa", "keep ✓")
        session.write_buffer("bbbbbbbb", "orphan")
        foreign = os.path.join(self.dir, "buffers", "README.txt")
        with open(foreign, "w") as f:
            f.write("x")
        session.save([{"tabs": [{"id": "aaaaaaaa", "backup": True}]}])
        self.assertEqual(session.read_buffer("aaaaaaaa"), "keep ✓")
        self.assertIsNone(session.read_buffer("bbbbbbbb"))
        self.assertTrue(os.path.exists(foreign))
        self.assertEqual(session.load(), [{"tabs": [{"id": "aaaaaaaa", "backup": True}]}])

    def test_ids_are_validated(self):
        """Buffer ids outside [0-9a-f]{8,64} (e.g. path traversal) are never read or written."""
        session.write_buffer("../../evil", "x")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "buffers")))
        self.assertIsNone(session.read_buffer("../etc/passwd"))
        self.assertIsNone(session.read_buffer(None))

    def test_corrupt_session_loads_empty(self):
        """A truncated or wrongly shaped session.json gives no windows instead of an exception."""
        for raw in ('{"windows": [', '[]', '{"windows": [1, {"tabs": "x"}, {"tabs": []}]}'):
            with open(os.path.join(self.dir, "session.json"), "w") as f:
                f.write(raw)
            self.assertIn(session.load(), ([], [{"tabs": []}]))


if __name__ == "__main__":
    unittest.main()
