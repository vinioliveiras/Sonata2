"""Review tests for the small apps: Calculator, Clock alarms, Assistant
(tools, api, markdown, store, usage), Web Apps, Feedbacker and Terminal.
Headless and offline: temp folders, mocked network.
Run: xvfb-run -a python3 -m unittest tests.test_review_apps"""
import datetime as dt
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import zipfile
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from sonata2 import webapps as W  # noqa: E402
from sonata2.assistant import api, markdown, tools  # noqa: E402
from sonata2.assistant import store as S  # noqa: E402
from sonata2.assistant.usage import Usage  # noqa: E402
from sonata2.calculator.engine import Engine, fmt  # noqa: E402
from sonata2.clock import alarms as A  # noqa: E402
from sonata2.feedback import report  # noqa: E402


def calc(keys):
    e = Engine()
    for k in keys:
        e.press(k)
    return e.display()


def png(side):
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
    pix.fill(0x336699ff)
    return pix.save_to_bufferv("png", [], [])[1]


# -- Calculator ---------------------------------------------------------------------------------
class CalculatorEngineTest(unittest.TestCase):
    def test_precedence_over_a_long_chain(self):
        """× and ÷ bind tighter than + and − across a whole chain."""
        self.assertEqual(calc("2+3×4−6÷2="), "11")
        self.assertEqual(calc("10−2×3−1="), "3")

    def test_intermediate_display_folds_only_the_product_chain(self):
        """After × the display shows the trailing product, not the whole sum."""
        self.assertEqual(calc("2+3×4×"), "12")
        self.assertEqual(calc("2+3×4+"), "14")

    def test_division_by_zero_mid_chain_then_recovers(self):
        """÷0 shows Error even before =, and the next digit starts afresh."""
        self.assertEqual(calc("5÷0+"), "Error")
        self.assertEqual(calc("5÷0+7"), "7")
        self.assertEqual(calc("5÷0=C"), "0")

    def test_repeated_equals_with_multiplication(self):
        """= again repeats the last operation with the same operand."""
        self.assertEqual(calc("3×4=="), "48")
        self.assertEqual(calc("10−1==="), "7")

    def test_clear_entry_keeps_the_pending_operation(self):
        """C clears only the number being typed, not 5 + ..."""
        self.assertEqual(calc("5+3C2="), "7")

    def test_typing_is_limited_and_backspace_ignores_results(self):
        """At most 15 digits can be typed; ⌫ doesn't touch a shown result."""
        self.assertEqual(calc("1" * 20), "111,111,111,111,111")
        self.assertEqual(calc("2+3=⌫"), "5")

    def test_tiny_and_negative_results_format(self):
        """Very small results switch to scientific notation; signs survive grouping."""
        self.assertEqual(calc("1÷100000000000="), "1e-11")
        self.assertEqual(calc("1000±−1="), "-1,001")
        self.assertEqual(fmt(__import__("decimal").Decimal("-0.5")), "-0.5")

    def test_overflow_shows_error_instead_of_raising(self):
        """A result beyond Decimal's exponent range must show Error."""
        from decimal import Decimal
        e = Engine()
        e.value = Decimal("9e999990")
        e.press("×")
        e.entry = "1000000000000000"
        try:
            e.press("=")
        except ArithmeticError:
            self.fail("press() raised instead of showing Error")
        self.assertEqual(e.display(), "Error")

    def _paste(self, text):
        from sonata2.calculator import window as CW
        e = Engine()
        clip = mock.Mock()
        clip.read_text_finish.return_value = text
        CW.CalculatorWindow._pasted(SimpleNamespace(engine=e, _refresh=lambda: None), clip, None)
        return e.display()

    def test_paste_grouped_number(self):
        """Ctrl+V of a grouped number enters it digit by digit."""
        self.assertEqual(self._paste("1,234.5"), "1,234.5")
        self.assertEqual(self._paste("not a number"), "0")

    def test_paste_negative_and_exponent(self):
        """Pasting "-5" gives -5 and "1e5" gives 100,000 (or is refused)."""
        self.assertEqual(self._paste("-5"), "-5")
        self.assertIn(self._paste("1e5"), ("100,000", "0"))


# -- Clock alarms ---------------------------------------------------------------------------------
def alarm(h, m, repeat=(), enabled=True):
    return dict(A.new(h, m), repeat=list(repeat), enabled=enabled)


class AlarmMathTest(unittest.TestCase):
    MON = dt.datetime(2026, 10, 5, 8, 30, 15)          # a Monday

    def test_one_time_today_or_tomorrow(self):
        """A one-time alarm rings later today, else tomorrow; exactly now is not 'after'."""
        self.assertEqual(A.next_time(alarm(9, 0), self.MON), dt.datetime(2026, 10, 5, 9, 0))
        self.assertEqual(A.next_time(alarm(8, 0), self.MON), dt.datetime(2026, 10, 6, 8, 0))
        at = dt.datetime(2026, 10, 5, 9, 0)
        self.assertEqual(A.next_time(alarm(9, 0), at), dt.datetime(2026, 10, 6, 9, 0))

    def test_repeat_days_and_week_wrap(self):
        """Repeating alarms skip to their weekday, wrapping past Sunday to the same day next week."""
        self.assertEqual(A.next_time(alarm(7, 0, [0]), self.MON), dt.datetime(2026, 10, 12, 7, 0))
        self.assertEqual(A.next_time(alarm(7, 0, [5, 6]), self.MON), dt.datetime(2026, 10, 10, 7, 0))
        self.assertIsNone(A.next_time(alarm(7, 0, [0], enabled=False), self.MON))

    def test_due_over_a_suspend_and_next_any(self):
        """Alarms in (since, now] are due once; next_any picks the soonest enabled one."""
        a, b, c = alarm(9, 0), alarm(23, 0), alarm(9, 30, enabled=False)
        due = A.due([a, b, c], self.MON, dt.datetime(2026, 10, 5, 12, 0))
        self.assertEqual(due, [a])
        self.assertEqual(A.next_any([b, c], self.MON), dt.datetime(2026, 10, 5, 23, 0))
        self.assertIsNone(A.next_any([c], self.MON))

    def test_clean_normalizes_bad_entries(self):
        """Out-of-range hours wrap, bad weekdays drop, unknown sounds fall back, junk is refused."""
        a = A._clean({"hour": 25, "minute": 61, "repeat": [6, 9, 1, 1], "sound": "nope", "enabled": 1})
        self.assertEqual((a["hour"], a["minute"], a["repeat"], a["sound"], a["enabled"]),
                         (1, 1, [1, 6], A.SOUND, True))
        self.assertIsNone(A._clean("x"))
        self.assertIsNone(A._clean({"hour": "seven"}))

    def test_twelve_hour_text(self):
        """Midnight and noon read 12 AM / 12 PM in 12-hour format."""
        self.assertEqual(A.time_text(alarm(0, 5), h24=False), "12:05 AM")
        self.assertEqual(A.time_text(alarm(12, 0), h24=False), "12:00 PM")
        self.assertEqual(A.time_text(alarm(7, 3)), "07:03")
        self.assertEqual(A.repeat_text([4, 3, 2, 1, 0]), "Weekdays")


class AlarmStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "alarms.json")
        self.p = mock.patch.object(A, "path", return_value=self.file)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_save_load_round_trip_sorted(self):
        """Saved alarms come back sorted by time and without the temp file."""
        A.save([alarm(9, 0), alarm(6, 30)])
        self.assertEqual([(a["hour"], a["minute"]) for a in A.load()], [(6, 30), (9, 0)])
        self.assertEqual(os.listdir(self.tmp.name), ["alarms.json"])

    def test_corrupt_file_is_empty_list(self):
        """Invalid JSON or a list at the top reads as no alarms."""
        for text in ("{oops", "[1, 2]"):
            with open(self.file, "w") as f:
                f.write(text)
            self.assertEqual(A.load(), [])

    def test_object_without_alarms_key(self):
        """A file holding {} (or "alarms": null) reads as no alarms."""
        with open(self.file, "w") as f:
            f.write("{}")
        self.assertEqual(A.load(), [])


# -- Assistant -------------------------------------------------------------------------------------
class AssistantToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "allowed")
        self.out = os.path.join(self.tmp.name, "secret")
        os.makedirs(self.root)
        os.makedirs(self.out)
        with open(os.path.join(self.out, "key.txt"), "w") as f:
            f.write("s3cret")

    def tearDown(self):
        self.tmp.cleanup()

    def test_escapes_are_denied(self):
        """'..', symlinks out, relative paths and sibling-prefix folders never pass the check."""
        os.symlink(self.out, os.path.join(self.root, "link"))
        for p in (self.root + "/../secret/key.txt", self.root + "/link/key.txt", "key.txt",
                  self.root + "x/key.txt"):
            with self.assertRaises(tools.Denied, msg=p):
                tools.check("read_file", {"path": p}, [self.root])
        with self.assertRaises(tools.Denied):
            tools.check("delete_file", {"path": self.root}, [self.root])

    def test_create_refuses_overwrite_unless_asked(self):
        """create_file won't replace an existing file without overwrite=true."""
        p = os.path.join(self.root, "a.txt")
        self.assertEqual(tools.run(tools.check("create_file", {"path": p, "content": "1"}, [self.root])),
                         (f"Created {p}", False))
        with self.assertRaises(tools.Denied):
            tools.check("create_file", {"path": p, "content": "2"}, [self.root])
        act = tools.check("create_file", {"path": p, "content": "2", "overwrite": True}, [self.root])
        self.assertTrue(act.exists)
        tools.run(act)
        with open(p) as f:
            self.assertEqual(f.read(), "2")

    def test_read_is_capped_and_list_is_capped(self):
        """Reads stop at MAX_READ and listings at MAX_LIST entries."""
        big = os.path.join(self.root, "big.txt")
        with open(big, "w") as f:
            f.write("a" * (tools.MAX_READ + 10))
        text, err = tools.run(tools.check("read_file", {"path": big}, [self.root]))
        self.assertFalse(err)
        self.assertTrue(text.startswith("a" * tools.MAX_READ) and "cut at" in text)
        with mock.patch.object(tools, "MAX_LIST", 3):
            for i in range(5):
                open(os.path.join(self.root, f"f{i}"), "w").close()
            text, _ = tools.run(tools.check("list_folder", {"path": self.root}, [self.root]))
        self.assertEqual(len(text.splitlines()), 4)

    def test_lone_surrogate_content_is_an_error_not_a_crash(self):
        """JSON from the API may hold a lone surrogate: run() must return an error and leave no temp file."""
        act = tools.check("create_file", {"path": self.root + "/x.txt", "content": "a\ud83d"}, [self.root])
        try:
            _text, err = tools.run(act)
        except UnicodeError:
            self.fail("run() raised UnicodeEncodeError")
        self.assertTrue(err)
        self.assertEqual(os.listdir(self.root), [])


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


def sse(*events):
    out = []
    for kind, data in events:
        out += [f"event: {kind}\n".encode(), f"data: {json.dumps(data)}\n".encode(), b"\n"]
    return out


class AssistantApiTest(unittest.TestCase):
    def run_job(self, lines, messages, **kw):
        sent, got = {}, {}

        def urlopen(req, timeout=None):
            sent["url"], sent["headers"] = req.full_url, dict(req.header_items())
            sent["body"] = json.loads(req.data.decode())
            sent["timeout"] = timeout
            return FakeResponse(lines)
        with mock.patch.object(api.urllib.request, "urlopen", urlopen), \
                mock.patch.object(api.Job, "_post", lambda self, fn, *a: fn(*a)):
            job = api.stream("sk-test", "claude-x", messages, lambda t: got.setdefault("text", []).append(t),
                             lambda *a: got.__setitem__("done", a), lambda m: got.__setitem__("error", m), **kw)
            job.thread.join(5)
        return sent, got

    def test_request_is_built_with_tools_system_and_merged_turns(self):
        """The POST carries model, merged alternating turns, system and tools; the key only in a header."""
        lines = sse(("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 3}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "tool_use", "id": "t1", "name": "read_file"}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "input_json_delta", "partial_json": '{"path": "/a"}'}}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                                       "usage": {"output_tokens": 4}}),
                    ("message_stop", {"type": "message_stop"}))
        msgs = [{"role": "user", "content": "hi"}, {"role": "user", "content": "again"}]
        sent, got = self.run_job(lines, msgs, system="SYS", tools=tools.TOOLS)
        body = sent["body"]
        self.assertTrue(sent["url"].endswith("/v1/messages"))
        self.assertEqual(body["model"], "claude-x")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi\n\nagain"}])
        self.assertEqual(body["system"], "SYS")
        self.assertEqual([t["name"] for t in body["tools"]], ["list_folder", "read_file", "create_file"])
        self.assertTrue(body["stream"])
        self.assertNotIn("sk-test", json.dumps(body))
        self.assertEqual({k.lower(): v for k, v in sent["headers"].items()}["x-api-key"], "sk-test")
        self.assertIsNotNone(sent["timeout"])
        blocks, stop, usage = got["done"]
        self.assertEqual(blocks, [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "/a"}}])
        self.assertEqual((stop, api.total_tokens(usage)), ("tool_use", 7))
        self.assertEqual(msgs[1]["content"], "again")              # the caller's list is not modified

    def test_no_system_or_tools_when_not_given(self):
        """Without folders the request has no tools/system keys at all."""
        sent, got = self.run_job(sse(("message_stop", {"type": "message_stop"})),
                                 [{"role": "user", "content": "x"}])
        self.assertNotIn("tools", sent["body"])
        self.assertNotIn("system", sent["body"])
        self.assertEqual(got["done"][0], [])

    def test_error_texts_never_echo_the_key(self):
        """401 gets a fixed message; network errors a readable one."""
        e = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error": {"message": "bad key sk-ant-123"}}'))
        self.assertEqual(api._error_text(e), "The API key was not accepted.")
        self.assertIn("connection", api._error_text(urllib.error.URLError("down")))
        e = urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"<html>"))
        self.assertEqual(api._error_text(e), "The request failed (HTTP 503).")


class AssistantMarkdownTest(unittest.TestCase):
    def label_text(self, md):
        lab = Gtk.Label()
        lab.set_markup(markdown.inline(md))
        return lab.get_text()

    def test_markup_is_escaped(self):
        """Angle brackets and quotes from the model never become markup."""
        s = markdown.inline('<b onclick="x">hi</b> [l](https://a.org/?q="x")')
        self.assertNotIn("<b onclick", s)
        self.assertIn("&lt;b", s)

    def test_underscores_in_a_link_url_stay_literal(self):
        """A link to .../__init__.py keeps its URL intact."""
        s = markdown.inline("[f](https://github.com/a/b/__init__.py)")
        self.assertIn('href="https://github.com/a/b/__init__.py"', s)

    def test_overlapping_emphasis_keeps_the_text(self):
        """Overlapping ** and ~~ never make the whole paragraph vanish."""
        self.assertTrue(self.label_text("a ~~**b~~ c**"))


class AssistantStoreUsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_title_from_long_and_blank(self):
        """Titles are one line, cut at a word with an ellipsis; blank is New Chat."""
        t = S.title_from("word " * 30)
        self.assertTrue(t.endswith("…") and len(t) <= S.TITLE_LEN + 1)
        self.assertEqual(S.title_from("x" * 100), "x" * 60 + "…")
        self.assertEqual(S.title_from("  \n "), "New Chat")

    def test_load_drops_malformed_messages_and_rename_ignores_blank(self):
        """Bad messages in a chat file are skipped; a blank rename changes nothing."""
        st = S.Store(self.tmp.name)
        chat = st.new_chat()
        chat["messages"] = [{"role": "user", "content": "hello there"}]
        st.save(chat)
        path = os.path.join(self.tmp.name, "chats", chat["id"] + ".json")
        with open(path) as f:
            data = json.load(f)
        data["messages"] += [{"role": "system", "content": "x"}, "junk", {"role": "user", "content": 5}]
        with open(path, "w") as f:
            json.dump(data, f)
        st2 = S.Store(self.tmp.name)
        self.assertEqual(st2.load(chat["id"])["messages"], [{"role": "user", "content": "hello there"}])
        st2.rename(chat["id"], "   ")
        self.assertEqual(S.Store(self.tmp.name).get(chat["id"])["title"], "hello there")

    def test_usage_prunes_old_calls_and_ignores_junk(self):
        """Calls older than a week are dropped on add; malformed rows are ignored when read."""
        with open(os.path.join(self.tmp.name, "usage.json"), "w") as f:
            json.dump({"calls": [[0, 10], "x", [1, "y"], [100, 5]]}, f)
        u = Usage(self.tmp.name)
        self.assertEqual(u.calls, [(0.0, 10), (100.0, 5)])
        now = 100 + 8 * 86400
        u.add(7, now=now)
        self.assertEqual(Usage(self.tmp.name).calls, [(now, 7)])
        self.assertEqual((u.last_hour(now + 3599), u.last_hour(now + 3601)), (7, 0))


# -- Web apps -----------------------------------------------------------------------------------------
class WebAppsLogicTest(unittest.TestCase):
    def test_normalize_url_refuses_non_web_and_header_injection(self):
        """Only http(s) with a dotted host passes; CR/LF can't reach the .desktop file."""
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "a b.com", "intranet", "ftp://x.org", ""):
            self.assertIsNone(W.normalize_url(bad), bad)
        u = W.normalize_url("example.com/x\nExec=evil")
        self.assertTrue(u is None or ("\n" not in u and "\r" not in u))
        self.assertEqual(W.normalize_url("http://localhost:8080"), "http://localhost:8080/")

    def test_desktop_text_is_one_entry(self):
        """Newlines in a name can't add keys; Exec and StartupWMClass carry the app id."""
        with mock.patch.object(W, "data_dir", return_value="/nonexistent"):
            text = W.desktop_text("wabc", {"name": "Evil\nExec=rm -rf ~", "url": "https://a.org/"}, "sonata2")
        lines = text.splitlines()
        self.assertEqual([ln for ln in lines if ln.startswith("Exec=")], ["Exec=sonata2 webapp wabc"])
        self.assertIn("Icon=" + W.FALLBACK_ICON, lines)
        self.assertIn("StartupWMClass=" + W.APP_ID_PREFIX + "wabc", lines)

    def test_id_helpers(self):
        """Desktop ids map back to web app ids and are recognized."""
        self.assertTrue(W.is_webapp("sonata2-webapp-w123.desktop"))
        self.assertFalse(W.is_webapp("org.gnome.Nautilus.desktop"))
        self.assertEqual(W.id_of("sonata2-webapp-w123.desktop"), "w123")
        self.assertEqual(W.default_name("https://www.app.notion.so/"), "Notion")

    def test_smaller_icon_never_replaces_a_bigger_one(self):
        """A 16 px favicon must not replace a 32 px icon already saved."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(W, "data_dir", return_value=d):
            self.assertTrue(W.save_icon("w1", png(32)))
            W.save_icon("w1", png(16))
            self.assertEqual(GdkPixbuf.Pixbuf.get_file_info(W.icon_path("w1"))[1], 32)

    def test_icon_is_downscaled_and_garbage_refused(self):
        """Huge icons are stored at 256 px; non-images are refused."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(W, "data_dir", return_value=d):
            self.assertFalse(W.save_icon("w1", b"<html>not an image"))
            self.assertTrue(W.save_icon("w1", png(512)))
            self.assertEqual(GdkPixbuf.Pixbuf.get_file_info(W.icon_path("w1"))[1], 256)

    def test_other_schemes_leave_the_window(self):
        """mailto:/file:/custom schemes never load inside the web app."""
        from sonata2.webapps import window as WW
        for t in ("mailto:a@b.org", "file:///etc/passwd", "steam://run/1"):
            self.assertFalse(WW.stays_inside("https://web.whatsapp.com/", t), t)

    def test_ip_hosts_are_not_the_same_site(self):
        """Two different IP addresses are different sites."""
        from sonata2.webapps import window as WW
        self.assertFalse(WW.stays_inside("http://192.168.1.10/", "http://10.168.1.10/"))


# -- Feedbacker -----------------------------------------------------------------------------------------
class FeedbackReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        self.env = mock.patch.dict(os.environ, {"HOME": home, "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                                                "SONATA_DEBUG": "1"})     # logs on: kept in ~/.cache/sonata2
        self.env.start()
        self.logs = os.path.join(home, ".cache", "sonata2")
        os.makedirs(self.logs)
        self.info = mock.patch.object(report, "system_info", return_value={"Sonata": "1", "GPU": "X"})
        self.info.start()

    def tearDown(self):
        self.info.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_issue_url_stays_short_with_a_long_description(self):
        """A huge description is shortened so the GitHub link fits URL_MAX."""
        url = report.issue_url("T", "word " * 5000, "r.zip")
        self.assertLessEqual(len(url), report.URL_MAX)
        self.assertIn("full+text+in+the+report", url)

    def test_crash_file_parsing_and_text(self):
        """A malformed last-crash file reads as no crash; signals are named."""
        with open(os.path.join(self.logs, report.CRASH), "w") as f:
            f.write("garbage")
        self.assertEqual(report.crash(), {})
        with open(os.path.join(self.logs, report.CRASH), "w") as f:
            f.write("1700000000 139\n")
        c = report.crash()
        self.assertEqual(c, {"time": 1700000000, "code": 139})
        self.assertIn("SIGSEGV", report.crash_text(c))

    def test_history_skips_bad_lines_and_counts(self):
        """Bad history lines are skipped; the summary counts kinds."""
        with open(os.path.join(self.logs, report.HISTORY), "w") as f:
            f.write("100 amd-reset 134\nnonsense\n200 amd-reset x\n300 unknown 1\n")
        self.assertEqual(report.history(), [(100, "amd-reset", 134), (200, "amd-reset", 0), (300, "unknown", 1)])
        self.assertEqual(report.history_text(days=1, now=400),
                         "3 crashes in the last 1 days: 2 AMD reset, 1 unknown")

    def test_classify_needs_amd_context(self):
        """A bare 'timeout' in a log is not an AMD reset."""
        self.assertEqual(report.classify("usb timeout", ""), "unknown")
        self.assertEqual(report.classify("amdgpu: ring gfx timeout", ""), "amd-reset")

    def test_old_log_timestamp_does_not_break_the_report(self):
        """A log file dated before 1980 (clock reset) still goes in the report."""
        p = os.path.join(self.logs, "session.log")
        with open(p, "w") as f:
            f.write("x")
        os.utime(p, (0, 0))
        path = report.create("t", "d", doctor_text="ok")
        with zipfile.ZipFile(path) as z:
            self.assertIn("logs/session.log", z.namelist())


# -- Terminal ---------------------------------------------------------------------------------------------
class TerminalFolderTest(unittest.TestCase):
    def test_folder_of_a_file_is_its_parent(self):
        """Opening a file in Terminal starts in its folder; a folder starts in itself."""
        from sonata2.terminal import window as TW
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.txt")
            open(f, "w").close()
            self.assertEqual(TW._folder(f), d)
            self.assertEqual(TW._folder(d), d)
            self.assertEqual(TW._folder("file://" + f), d)

    def test_user_shell_falls_back_to_env(self):
        """Without a passwd entry the shell comes from $SHELL."""
        from sonata2.terminal import window as TW
        with mock.patch.object(TW.pwd, "getpwuid", side_effect=KeyError), \
                mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            self.assertEqual(TW.user_shell(), "/bin/zsh")

# -- Fixes without an earlier test ------------------------------------------------------------------
class ReviewFixesTest(unittest.TestCase):
    def test_percent_overflow_shows_error(self):
        """% on a value at the edge of Decimal's range shows Error, never raises."""
        from decimal import Decimal
        e = Engine()
        e.tokens = [Decimal("9e999999"), "+"]
        e.entry = "9e999999"
        e.press("%")
        self.assertEqual(e.display(), "Error")

    def test_alarm_save_uses_a_unique_temp(self):
        """Saving alarms never goes through a fixed alarms.json.tmp (two processes write it)."""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(A, "path", return_value=d + "/alarms.json"):
            with open(d + "/alarms.json.tmp", "w") as f:
                f.write("someone else's")
            A.save([alarm(7, 0)])
            with open(d + "/alarms.json.tmp") as f:
                self.assertEqual(f.read(), "someone else's")
            self.assertEqual(len(A.load()), 1)

    def test_lone_surrogate_path_is_denied(self):
        """A path holding a lone surrogate is refused by check(), not a crash in os calls."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(tools.Denied):
                tools.check("read_file", {"path": d + "/a\ud83d.txt"}, [d])

    def test_links_keep_bold_and_bold_keeps_links(self):
        """Emphasis still works around and inside links once hrefs are protected."""
        self.assertEqual(markdown.inline("**[x](https://a.org/__a__)**"), '<b><a href="https://a.org/__a__">x</a></b>')
        self.assertIn("<b>b</b>", markdown.inline("[a **b** c](https://x.org)"))

    def test_shared_suffixes_are_different_sites(self):
        """a.github.io and b.github.io are two sites; www and web of one domain are one."""
        from sonata2.webapps import window as WW
        self.assertFalse(WW.stays_inside("https://a.github.io/", "https://b.github.io/"))
        self.assertTrue(WW.stays_inside("https://web.whatsapp.com/", "https://www.whatsapp.com/x"))
        self.assertTrue(WW.stays_inside("http://192.168.1.10/", "http://192.168.1.10:8080/"))

    def test_desktop_name_escapes_cr_and_backslash(self):
        """\\r and backslashes in a name can't break or escape the desktop entry."""
        with mock.patch.object(W, "data_dir", return_value="/nonexistent"):
            text = W.desktop_text("wabc", {"name": "A\rB\\sC", "url": "https://a.org/"}, "sonata2")
        self.assertIn("Name=A B\\\\sC\n", text)
        self.assertNotIn("\r", text)

    def test_non_web_links_ask_first(self):
        """file:// and custom schemes open only after the user agrees; web, mail and tel go at once."""
        from sonata2.webapps import window as WW
        win = SimpleNamespace(entry={"name": "Chat"})
        with mock.patch.object(WW.Gtk, "UriLauncher") as launcher, mock.patch.object(WW.ui.dialog, "alert") as alert:
            for uri in ("https://x.org/", "mailto:a@b.org", "tel:+5511"):
                WW.open_outside(win, uri)
            self.assertEqual(launcher.call_count, 3)
            alert.assert_not_called()
            for uri in ("file:///etc/passwd", "steam://run/1"):
                WW.open_outside(win, uri)
            self.assertEqual((launcher.call_count, alert.call_count), (3, 2))
            responses, answer = alert.call_args[0][2], alert.call_args[0][3]
            self.assertEqual(responses[0][0], "cancel")             # Escape / first: never opens
            answer("cancel")
            self.assertEqual(launcher.call_count, 3)
            answer("open")
            self.assertEqual(launcher.call_args.kwargs["uri"], "steam://run/1")

    def test_long_title_link_fits(self):
        """A huge title (a pasted log) still gives a link under URL_MAX."""
        with mock.patch.object(report, "system_info", return_value={"Sonata": "1"}):
            url = report.issue_url("x" * 20000, "d")
        self.assertLessEqual(len(url), report.URL_MAX)

    def test_report_failure_leaves_no_part_file(self):
        """A report that fails midway leaves no .part file behind."""
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(report, "system_info", return_value={}), \
                mock.patch.object(report, "crash", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                report.create("t", "d", doctor_text="")
            self.assertEqual(os.listdir(report.folder()), [])

    def test_feedback_worker_gathers_info_once_and_never_stays_busy(self):
        """system_info runs once per report, and any error ends the busy state."""
        from sonata2.feedback import window as FW
        got = {}
        fake = SimpleNamespace(description=lambda: ("t", "d"), _set_busy=lambda b: None,
                               _created=lambda *a: got.setdefault("created", a))
        with mock.patch.object(report, "system_info", return_value={"A": "1"}) as info, \
                mock.patch.object(report, "create", return_value="/x/r.zip"), \
                mock.patch.object(report, "issue_url", return_value="u"), \
                mock.patch.object(FW.GLib, "idle_add", lambda fn, *a: fn(*a)), \
                mock.patch.object(FW.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target)):
            FW.FeedbackWindow._create(fake, True)
            self.assertEqual(info.call_count, 1)
            self.assertEqual(got["created"], ("/x/r.zip", None, "u"))
            got.clear()
            with mock.patch.object(report, "create", side_effect=ValueError("bad")):
                FW.FeedbackWindow._create(fake, False)
            self.assertIsNone(got["created"][0])
            self.assertIsInstance(got["created"][1], ValueError)


class AssistantToolThreadTest(unittest.TestCase):
    def test_allowed_action_runs_off_the_main_loop(self):
        """tools.run() runs in a worker thread; its result comes back on the main loop."""
        import threading
        from sonata2.assistant import window as AW
        ran, answers = {}, []

        def fake_run(act):
            ran["main"] = threading.current_thread() is threading.main_thread()
            return "ok", False
        with tempfile.TemporaryDirectory() as d:
            p = {"chat": {}, "queue": [{"id": "t1", "name": "read_file", "input": {"path": d}}],
                 "results": [], "rows": {}}
            fake = SimpleNamespace(pending=p, cfg={"folders": [d]}, _alert=None)
            fake._tool_result = lambda blk, text, err, row, line: answers.append((text, err))
            fake._next_tool = lambda: None
            os.makedirs(d + "/f")
            p["queue"][0]["input"]["path"] = d + "/f"
            p["queue"][0]["name"] = "list_folder"
            with mock.patch.object(AW.ui.dialog, "alert", lambda h, b, r, cb, parent=None: cb("allow")), \
                    mock.patch.object(AW.tools, "run", fake_run):
                AW.AssistantWindow._next_tool(fake)
                end = time.monotonic() + 3
                while not answers and time.monotonic() < end:
                    GLib.MainContext.default().iteration(False)
        self.assertFalse(ran["main"])
        self.assertEqual(answers, [("ok", False)])
        self.assertFalse(p["running"])


# -- Animations ------------------------------------------------------------------------------------
class AnimationTests(unittest.TestCase):
    def test_assistant_messages_slide_in(self):
        """Every message / tool row / error added to the chat goes in a SLIDE_DOWN revealer that opens."""
        from sonata2.assistant import window as AW
        box = Gtk.Box()
        fake = SimpleNamespace(messages=box, empty=Gtk.Label())
        lab = Gtk.Label(label="hi")
        AW.AssistantWindow._add(fake, lab)
        rev = box.get_first_child()
        self.assertIsInstance(rev, Gtk.Revealer)
        self.assertEqual(rev.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.assertGreater(rev.get_transition_duration(), 0)
        self.assertFalse(rev.get_reveal_child())
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        self.assertTrue(rev.get_reveal_child())

    def test_calculator_key_press_lights_up(self):
        """A typed key lights its button (.pressed) and the keys' colours change with a CSS transition."""
        from gi.repository import Gdk
        from sonata2 import ui
        from sonata2.calculator import window as CW
        ui.setup()
        win = CW.CalculatorWindow(None)
        try:
            with mock.patch.object(CW.GLib, "timeout_add") as later:
                self.assertTrue(win._key(None, Gdk.KEY_7, 0, 0))
            self.assertTrue(win.buttons["7"].has_css_class("pressed"))
            self.assertEqual(later.call_args[0][0], 110)
            later.call_args[0][1]()                              # the light goes off again
            self.assertFalse(win.buttons["7"].has_css_class("pressed"))
        finally:
            win.destroy()
        import inspect
        self.assertRegex(inspect.getsource(CW), r"button\.calc-key \{[^}]*transition: background-color")

    def test_feedback_busy_spins(self):
        """Saving a report shows a spinning spinner until it's done."""
        from sonata2.feedback import window as FW
        sp = Gtk.Spinner()
        fake = SimpleNamespace(spinner=sp, save_btn=Gtk.Button(), github_btn=Gtk.Button())
        FW.FeedbackWindow._set_busy(fake, True)
        self.assertTrue(sp.get_spinning() and sp.get_visible())
        FW.FeedbackWindow._set_busy(fake, False)
        self.assertFalse(sp.get_spinning())


if __name__ == "__main__":
    unittest.main()
