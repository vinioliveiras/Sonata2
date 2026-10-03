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

    def test_all_day_until_is_written_as_date(self):
        """RFC 5545: UNTIL must be a DATE when DTSTART is a DATE (other apps reject the datetime form)."""
        ev = ics.Event(uid="u", start=D(2026, 10, 1), end=D(2026, 10, 2), all_day=True, freq="DAILY",
                       until=D(2026, 10, 5, 23, 59, 59, 999999))
        self.assertIn("UNTIL=20261005;", ics.serialize([ev]).replace("\r\n", ";"))

    def test_unsupported_rrule_parts_survive_round_trip(self):
        """A foreign weekly BYDAY rule must not be silently reduced to 'weekly on DTSTART only' on save."""
        text = ("BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\nDTEND:20261005T100000\n"
                "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\nEND:VEVENT\n")
        ev = ics.parse(text)[1][0]
        self.assertIn("BYDAY=MO,WE,FR", ics.serialize([ev]))

    def test_recurring_utc_event_follows_local_dst(self):
        """A weekly 13:00Z meeting is 14:00 in Berlin in winter and 15:00 after the DST switch."""
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

    def test_recurrence_id_override_does_not_replace_master(self):
        """A modified instance (RECURRENCE-ID, same UID) must not wipe out the recurring master."""
        st = model.Store(self.dir)
        st.load()
        cal = st.calendars[0].id
        st.import_text("BEGIN:VEVENT\nUID:s\nSUMMARY:Series\nDTSTART:20261001T090000\nDURATION:PT1H\n"
                       "RRULE:FREQ=DAILY\nEND:VEVENT\nBEGIN:VEVENT\nUID:s\nRECURRENCE-ID:20261003T090000\n"
                       "SUMMARY:Moved one\nDTSTART:20261003T120000\nDURATION:PT1H\nEND:VEVENT\n", cal)
        self.assertEqual(st.events["s"].freq, "DAILY")

    def test_delete_calendar_wins_over_pending_async_write(self):
        """Deleting a calendar right after an edit must not have the queued write resurrect its file."""
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

    def test_bullet_text_starting_with_checkbox_stays_bullet(self):
        """A bullet whose text begins with '[ ] ' must not reload as a checklist item."""
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

    def test_corrupt_notes_file_is_not_overwritten(self):
        """A damaged notes.json must be kept aside, not silently replaced by an empty store on next save."""
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

    def test_completing_twice_keeps_completed_at(self):
        """Marking an already-completed reminder completed again keeps its original completion time."""
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

    def test_nul_after_sniff_window_is_not_corrupted_on_save(self):
        """A file with NUL bytes beyond the 8 KB sniff window must not be silently altered on save."""
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

# -- regression tests for the fixes ----------------------------------------------------------------
class CalendarFixesTest(unittest.TestCase):
    SERIES = ("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VTIMEZONE\nTZID:Custom Zone\nBEGIN:STANDARD\n"
              "DTSTART:19700101T000000\nTZOFFSETFROM:+0100\nTZOFFSETTO:+0100\nEND:STANDARD\nEND:VTIMEZONE\n"
              "BEGIN:VEVENT\nUID:s\nSUMMARY:Series\nDTSTART:20261001T090000\nDURATION:PT1H\n"
              "RRULE:FREQ=DAILY\nATTENDEE;CN=Ana:mailto:ana@x.org\nX-FOO:bar\nEND:VEVENT\n"
              "BEGIN:VEVENT\nUID:s\nRECURRENCE-ID:20261003T090000\nSUMMARY:Moved one\n"
              "DTSTART:20261003T120000\nDURATION:PT1H\nEND:VEVENT\nEND:VCALENDAR\n")

    def test_override_replaces_its_slot(self):
        """A RECURRENCE-ID instance shows at its new time and hides the series' original slot."""
        ev = ics.parse(self.SERIES)[1][0]
        occ = ics.expand([ev], D(2026, 10, 2), D(2026, 10, 5))
        self.assertEqual([(o.start, o.event.summary) for o in occ],
                         [(D(2026, 10, 2, 9), "Series"), (D(2026, 10, 3, 12), "Moved one"),
                          (D(2026, 10, 4, 9), "Series")])

    def test_foreign_properties_and_overrides_survive_store_edit(self):
        """Editing an event keeps ATTENDEE/X- properties, its RECURRENCE-ID instances and the VTIMEZONE."""
        folder = tempfile.mkdtemp()
        st = model.Store(folder)
        st.load()
        cal = st.calendars[0].id
        st.import_text(self.SERIES, cal)
        ev = st.events["s"].copy()
        ev.summary = "Renamed"
        st.put(ev)
        with open(os.path.join(folder, cal + ".ics"), encoding="utf-8") as f:
            text = ics._unfold(f.read())
        for line in ("ATTENDEE;CN=Ana:mailto:ana@x.org", "X-FOO:bar", "TZID:Custom Zone",
                     "RECURRENCE-ID:20261003T090000", "SUMMARY:Moved one", "SUMMARY:Renamed"):
            self.assertIn(line, text)
        st2 = model.Store(folder)
        st2.load()
        self.assertEqual(len(st2.events["s"].overrides), 1)

    def test_unknown_valarm_kept(self):
        """A VALARM Calendar can't show (absolute trigger) is written back verbatim."""
        text = ("BEGIN:VEVENT\nUID:a\nDTSTART:20261001T100000\nBEGIN:VALARM\nACTION:AUDIO\n"
                "TRIGGER;VALUE=DATE-TIME:20261001T090000Z\nEND:VALARM\nEND:VEVENT\n")
        out = ics._unfold(ics.serialize(ics.parse(text)[1]))
        self.assertIn("TRIGGER;VALUE=DATE-TIME:20261001T090000Z", out)
        self.assertEqual(out.count("BEGIN:VALARM"), 1)

    def test_weekly_byday_expands(self):
        """FREQ=WEEKLY;BYDAY=MO,WE,FR repeats on those three days (COUNT counts each)."""
        text = ("BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\nDTEND:20261005T100000\n"
                "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=4\nEND:VEVENT\n")
        ev = ics.parse(text)[1][0]
        self.assertEqual(starts(ev, D(2026, 10, 1), D(2026, 11, 1)),
                         [D(2026, 10, 5, 9), D(2026, 10, 7, 9), D(2026, 10, 9, 9), D(2026, 10, 12, 9)])

    def test_changed_freq_drops_foreign_rule_parts(self):
        """BYDAY belongs to the rule it came with: switching to monthly writes a plain monthly rule."""
        ev = ics.parse("BEGIN:VEVENT\nUID:w\nDTSTART:20261005T090000\n"
                       "RRULE:FREQ=WEEKLY;BYDAY=MO,WE\nEND:VEVENT\n")[1][0]
        ev.freq = "MONTHLY"
        self.assertNotIn("BYDAY", ics.serialize([ev]))

    def test_tzid_written_back_in_its_zone(self):
        """A TZID / UTC event is written in its own zone, UNTIL in UTC, and re-reads the same."""
        with _TZ("America/Sao_Paulo"):
            text = ("BEGIN:VEVENT\nUID:z\nDTSTART;TZID=Europe/Berlin:20260301T100000\nDURATION:PT1H\n"
                    "RRULE:FREQ=WEEKLY;UNTIL=20260501T000000Z\nEXDATE;TZID=Europe/Berlin:20260308T100000\n"
                    "END:VEVENT\n")
            ev = ics.parse(text)[1][0]
            out = ics.serialize([ev])
            self.assertIn("DTSTART;TZID=Europe/Berlin:20260301T100000", out)
            self.assertIn("UNTIL=20260501T000000Z", out)
            self.assertIn("EXDATE;TZID=Europe/Berlin:20260308T100000", out)
            back = ics.parse(out)[1][0]
            a, b = D(2026, 3, 1), D(2026, 5, 2)
            self.assertEqual(starts(back, a, b), starts(ev, a, b))
            # Berlin 10:00 is 06:00 in São Paulo before 29 March and 05:00 after
            self.assertEqual(starts(ev, D(2026, 3, 15), D(2026, 3, 16)), [D(2026, 3, 15, 6)])
            self.assertEqual(starts(ev, D(2026, 4, 5), D(2026, 4, 6)), [D(2026, 4, 5, 5)])

    def test_async_writes_keep_order(self):
        """Queued writes run in order on one worker: the last edit is what ends up on disk."""
        st = model.Store(self.dir if hasattr(self, "dir") else tempfile.mkdtemp())
        st.load()
        st.async_writes = True
        cal = st.calendars[0].id
        before = set(threading.enumerate())
        with st._lock:                       # hold the worker: everything queues up
            for i in range(20):
                st.put(ics.Event(uid="o", summary=f"v{i}", start=D(2026, 1, 1, 9), end=D(2026, 1, 1, 10),
                                 calendar=cal))
            self.assertLessEqual(len(set(threading.enumerate()) - before), 1)   # one worker, not 20 threads
        for t in set(threading.enumerate()) - before:
            t.join(5)
        st2 = model.Store(st.folder)
        st2.load()
        self.assertEqual(st2.events["o"].summary, "v19")

    def test_store_uses_shared_atomic_write(self):
        """Calendar files go through config.atomic_write (unique temp + fsync + rename)."""
        st = model.Store(tempfile.mkdtemp())
        with mock.patch.object(model, "atomic_write") as w:
            st.add_calendar("X")
        w.assert_called_once()


class NotesFixesTest(unittest.TestCase):
    def test_wrong_shape_notes_file_kept_aside(self):
        """A notes.json that is valid JSON but not an object is moved aside too, never overwritten."""
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "notes.json"), "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        S.Store(d).new_note(body="new")
        aside = [n for n in os.listdir(d) if n.startswith("notes.json.corrupt-")]
        self.assertEqual(len(aside), 1)

    def test_missing_file_is_not_moved(self):
        """A first start (no notes.json yet) creates nothing aside."""
        d = tempfile.mkdtemp()
        S.Store(d).new_note(body="x")
        self.assertFalse([n for n in os.listdir(d) if ".corrupt-" in n])

    def test_uncompleting_clears_time(self):
        """Un-checking a reminder clears completed_at; checking again sets a new one."""
        st = S.Store(tempfile.mkdtemp())
        r = st.new_reminder(title="x")
        st.update_reminder(r, completed=True)
        st.update_reminder(r, completed=False)
        self.assertIsNone(r["completed_at"])
        st.update_reminder(r, completed=True)
        self.assertIsNotNone(r["completed_at"])

    def test_notes_use_shared_atomic_write(self):
        """notes.json goes through config.atomic_write."""
        with mock.patch.object(S, "atomic_write") as w:
            S.write_json(os.path.join(tempfile.mkdtemp(), "n.json"), {"a": 1})
        w.assert_called_once()


class TextEditFixesTest(unittest.TestCase):
    def test_nul_inside_sniff_window_still_binary(self):
        """NUL bytes anywhere make a non-UTF-16 file binary (never decoded with U+FFFD)."""
        for data in (b"x" * 20000 + b"\0", "utf8 é".encode() + b"\0" * 3):
            with self.assertRaises(document.BinaryFile):
                document.decode(data)

    def test_session_uses_shared_atomic_write(self):
        """Session buffers are written with config.atomic_write (unique temp file)."""
        d = tempfile.mkdtemp()
        with mock.patch.object(session, "data_dir", lambda: d), \
                mock.patch.object(session, "atomic_write") as w:
            session.write_buffer("abcdef12", "x")
        w.assert_called_once()


# -- GTK: on-disk change check, async import, animations ------------------------------------------
_GTK = {}


def _gtk():
    if not _GTK:
        os.environ.setdefault("GDK_BACKEND", "x11")
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio, GLib, Gtk
        from sonata2 import ui
        Adw.init()
        ui.setup()
        app = Adw.Application(application_id="io.test.reviewdocs")
        app.register(None)
        _GTK.update(Adw=Adw, Gio=Gio, GLib=GLib, Gtk=Gtk, ui=ui, app=app)
    return _GTK


def _settle(ms=150):
    GLib = _gtk()["GLib"]
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class GtkFixesTest(unittest.TestCase):
    def test_textedit_asks_before_overwriting_outside_change(self):
        """Saving over a file another program changed asks first; Cancel keeps their text, Save Anyway writes."""
        g = _gtk()
        from sonata2.textedit import window as TW
        path = os.path.join(tempfile.mkdtemp(), "e.txt")
        with open(path, "w") as f:
            f.write("mine\n")
        w = TW.TextEditWindow(g["app"], path)
        _settle()
        w.buffer.insert(w.buffer.get_end_iter(), "edit\n")
        with open(path, "w") as f:
            f.write("theirs\n")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        asked = []
        with mock.patch.object(TW.ui.dialog, "alert", lambda h, b, r, cb=None, **k: asked.append((h, cb))):
            w.save()
        self.assertEqual(len(asked), 1)
        self.assertIn("changed by another application", asked[0][0])
        with open(path) as f:
            self.assertEqual(f.read(), "theirs\n")
        asked[0][1]("cancel")
        with open(path) as f:
            self.assertEqual(f.read(), "theirs\n")
        asked[0][1]("overwrite")
        with open(path) as f:
            self.assertEqual(f.read(), "mine\nedit\n")
        w.buffer.insert(w.buffer.get_end_iter(), "more\n")
        with mock.patch.object(TW.ui.dialog, "alert") as a:
            w.save()                          # our own last save: no question
        a.assert_not_called()
        w.buffer.set_modified(False)
        w.destroy()

    def test_calendar_import_reads_off_main_loop(self):
        """Importing an .ics file reads it asynchronously (no load_contents on the main loop)."""
        g = _gtk()
        from sonata2.calendar.window import CalendarWindow
        path = os.path.join(tempfile.mkdtemp(), "x.ics")
        with open(path, "w") as f:
            f.write("BEGIN:VEVENT\nUID:i\nDTSTART:20261001T090000\nEND:VEVENT\n")
        win = CalendarWindow(g["app"], folder=tempfile.mkdtemp())
        win.store.async_writes = False
        got = []
        with mock.patch.object(win, "_ask_import", lambda f, text: got.append(text)):
            win.import_files([g["Gio"].File.new_for_path(path)])
            self.assertEqual(got, [])
            _settle(300)
        self.assertEqual(len(got), 1)
        self.assertIn("UID:i", got[0])
        win.destroy()

    def test_calendar_delete_only_changed_instance(self):
        """Deleting a changed instance removes it and its slot, the series stays."""
        g = _gtk()
        from sonata2.calendar.window import CalendarWindow
        win = CalendarWindow(g["app"], folder=tempfile.mkdtemp())
        win.store.async_writes = False
        cal = win.store.calendars[0].id
        win.store.import_text(CalendarFixesTest.SERIES, cal)
        win.data_changed()
        occ = next(o for o in ics.expand(win.store.visible_events(), D(2026, 10, 3), D(2026, 10, 4))
                   if o.event.recurrence_id)
        win.select(occ)
        with mock.patch("sonata2.ui.dialog.alert", lambda h, b, r, cb=None, **k: cb("this")):
            win.delete_selected()
        ev = win.store.events["s"]
        self.assertEqual((ev.freq, ev.overrides, ev.exdates), ("DAILY", [], [D(2026, 10, 3, 9)]))
        self.assertEqual(starts(ev, D(2026, 10, 3), D(2026, 10, 4)), [])
        win.destroy()


class AnimationTests(unittest.TestCase):
    """Every main user action of Calendar, Notes and TextEdit runs an animation."""

    def _revealer_ok(self, rev):
        Gtk = _gtk()["Gtk"]
        self.assertIsInstance(rev, Gtk.Revealer)
        self.assertNotEqual(rev.get_transition_type(), Gtk.RevealerTransitionType.NONE)
        self.assertGreater(rev.get_transition_duration(), 0)

    def test_popovers_open_animated(self):
        """Popovers (the event editor, menus) fade+grow in through the shared CSS keyframes."""
        g = _gtk()
        css = g["ui"].theme._templates["motion-open"][0]
        self.assertIn("popover > contents { animation: sonata-open", css)

    def test_calendar_view_switch_sidebar_search(self):
        """Day/Week/Month/Year cross-fade; the sidebar and search results slide."""
        g = _gtk()
        Gtk = g["Gtk"]
        from sonata2.calendar.window import CalendarWindow
        win = CalendarWindow(g["app"], folder=tempfile.mkdtemp())
        win.store.async_writes = False
        win.present()
        _settle()
        self.assertEqual(win.stack.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        self.assertGreater(win.stack.get_transition_duration(), 0)
        win.set_view("month")
        win.set_view("day")
        self.assertTrue(win.stack.get_transition_running() or win.stack.get_visible_child_name() == "day")
        self._revealer_ok(win.sidebar_rev)
        self._revealer_ok(win.results_rev)
        win.destroy()

    def test_calendar_new_event_opens_popover(self):
        """A new event opens its editor in an (animated) popover."""
        g = _gtk()
        Gtk = g["Gtk"]
        from sonata2.calendar.window import CalendarWindow
        win = CalendarWindow(g["app"], folder=tempfile.mkdtemp())
        win.store.async_writes = False
        win.present()
        _settle()
        win.new_event()
        _settle()
        self.assertIsNotNone(win.editor)
        self.assertIsInstance(win.editor.pop, Gtk.Popover)
        win.editor.pop.popdown()
        win.destroy()

    def test_notes_notes_reminders_switch(self):
        """Switching between notes and reminders cross-fades."""
        g = _gtk()
        Gtk = g["Gtk"]
        from sonata2.notes.window import NotesWindow
        w = NotesWindow(g["app"], store=S.Store(tempfile.mkdtemp()))
        self.assertEqual(w.stack.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        self.assertGreater(w.stack.get_transition_duration(), 0)
        w.destroy()

    def test_textedit_tabs_find_bar_and_save_sheet(self):
        """The tab strip and find bar slide in; reordering tabs glides; the save question is a kit (Adw) sheet."""
        g = _gtk()
        from sonata2.textedit import window as TW
        w = TW.TextEditWindow(g["app"])
        self._revealer_ok(w.tabs_rev)
        self._revealer_ok(w.find_bar)
        w._show_find()
        self.assertTrue(w.find_bar.get_reveal_child())
        w._hide_find()
        a = w.doc
        b = w.new_tab()
        self.assertTrue(w.tabs_rev.get_reveal_child())
        with mock.patch.object(TW.ui.transition, "glide_play") as glide:
            w.tabs.move(b, 0)
        glide.assert_called_once()
        # the sheet itself is an Adw.AlertDialog (libadwaita animates it in); building
        # one segfaults under this xvfb, so only the kit call is checked here
        got = []
        a.buffer.set_text("unsaved")
        with mock.patch.object(TW.ui.dialog, "alert", lambda *args, **kw: got.append(args)):
            w.close_tab(a)
        self.assertEqual(len(got), 1)
        self.assertTrue(TW.ui.dialog._MODERN or hasattr(g["Adw"], "MessageDialog"))
        for d in w.docs:
            d.buffer.set_modified(False)
        w.destroy()


if __name__ == "__main__":
    unittest.main()
