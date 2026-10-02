"""Assistant: chat storage, Markdown blocks, the streamed API (against a
local fake server) and the window (dbus-run-session -- xvfb-run python3 -m
unittest tests.test_assistant)."""
import http.server
import json
import os
import tempfile
import threading
import time
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"
os.environ.pop("ANTHROPIC_API_KEY", None)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.assistant import api, markdown  # noqa: E402
from sonata2.assistant import store as S  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def wait_for(cond, ms=3000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.002)
    return cond()


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_round_trip_and_order(self):
        st = S.Store(self.dir)
        a = st.new_chat()
        self.assertIsNone(st.get(a["id"]))            # not saved before its first message
        a["messages"].append({"role": "user", "content": "How do I bake bread?\nDetails..."})
        st.save(a)
        b = st.new_chat()
        b["messages"].append({"role": "user", "content": "Second"})
        st.save(b)
        st2 = S.Store(self.dir)
        self.assertEqual([c["id"] for c in st2.chats], [b["id"], a["id"]])     # newest first
        self.assertEqual(st2.get(a["id"])["title"], "How do I bake bread? Details...")
        self.assertEqual(st2.load(a["id"])["messages"], a["messages"])
        a["messages"].append({"role": "assistant", "content": "Flour."})
        st2.save(st2.load(a["id"]) | {"messages": a["messages"]})
        self.assertEqual(S.Store(self.dir).chats[0]["id"], a["id"])          # answered: back on top

    def test_rename_delete(self):
        st = S.Store(self.dir)
        c = st.new_chat()
        c["messages"].append({"role": "user", "content": "hi"})
        st.save(c)
        st.rename(c["id"], "  Greetings  ")
        self.assertEqual(S.Store(self.dir).load(c["id"])["title"], "Greetings")
        st.rename(c["id"], "   ")                                             # blank: ignored
        self.assertEqual(st.get(c["id"])["title"], "Greetings")
        st.delete(c["id"])
        self.assertEqual(S.Store(self.dir).chats, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "chats", c["id"] + ".json")))

    def test_title_and_bad_files(self):
        self.assertEqual(S.title_from(""), "New Chat")
        t = S.title_from("word " * 40)
        self.assertTrue(t.endswith("…") and len(t) <= S.TITLE_LEN + 1)
        with open(os.path.join(self.dir, "index.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(S.Store(self.dir).chats, [])


class MarkdownTest(unittest.TestCase):
    def test_blocks(self):
        text = ("# Title\nSome *text*\nwrapped.\n\n- one\n  - two\n1. first\n> quote\n> more\n---\n"
                "```python\nx = 1 < 2\n```\nafter")
        kinds = [b[0] for b in markdown.blocks(text)]
        self.assertEqual(kinds, ["h1", "p", "li", "li", "ol", "quote", "hr", "code", "p"])
        b = markdown.blocks(text)
        self.assertEqual(b[1][1], "Some *text* wrapped.")
        self.assertEqual(b[5][1], "quote\nmore")
        self.assertEqual(b[7], ("code", "x = 1 < 2", "python"))

    def test_unclosed_fence_while_streaming(self):
        self.assertEqual(markdown.blocks("Look:\n```\ncode so far"), [("p", "Look:"), ("code", "code so far")])

    def test_inline(self):
        s = markdown.inline("**bold** *it* `a<b & **c**` [x](https://e.com/?a=1&b=2) 3 * 4 a_b_c")
        self.assertIn("<b>bold</b>", s)
        self.assertIn("<i>it</i>", s)
        self.assertIn("<tt>a&lt;b &amp; **c**</tt>", s)          # code spans never formatted
        self.assertIn('<a href="https://e.com/?a=1&amp;b=2">x</a>', s)
        self.assertIn("3 * 4 a_b_c", s)
        from gi.repository import Pango
        import re
        plain = re.sub(r"</?a[^>]*>", "", s)                      # <a> is GtkLabel's own tag
        self.assertTrue(Pango.parse_markup(plain, -1, "\0")[0])    # valid markup
        self.assertTrue(Pango.parse_markup(markdown.inline("<script> & **unclosed"), -1, "\0")[0])


class _Fake(http.server.BaseHTTPRequestHandler):
    status = 200
    events = []
    seen = []

    def log_message(self, *_a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "model-new", "display_name": "New"},
                                    {"id": "model-old", "display_name": "Old"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        _Fake.seen.append(({k.lower(): v for k, v in self.headers.items()}, json.loads(self.rfile.read(n))))
        if _Fake.status != 200:
            body = json.dumps({"type": "error", "error": {"message": "Bad thing"}}).encode()
            self.send_response(_Fake.status)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for ev in _Fake.events:
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()


def delta(t):
    return {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.old = api.API
        api.API = f"http://127.0.0.1:{cls.srv.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        api.API = cls.old
        cls.srv.shutdown()

    def setUp(self):
        api._models = None
        _Fake.status, _Fake.seen = 200, []
        _Fake.events = [{"type": "message_start"}, {"type": "ping"}, delta("Hel"), delta("lo"),
                        {"type": "message_stop"}]

    def run_job(self, model="m", msgs=None):
        got = {"text": "", "done": False, "error": None}
        job = api.stream("k", model, msgs or [{"role": "user", "content": "hi"}],
                         lambda t: got.__setitem__("text", got["text"] + t),
                         lambda: got.__setitem__("done", True),
                         lambda e: got.__setitem__("error", e))
        wait_for(lambda: got["done"] or got["error"])
        return job, got

    def test_streams_text(self):
        _job, got = self.run_job()
        self.assertEqual((got["text"], got["done"], got["error"]), ("Hello", True, None))
        headers, body = _Fake.seen[0]
        self.assertEqual(headers.get("x-api-key"), "k")
        self.assertEqual(headers.get("anthropic-version"), api.VERSION)
        self.assertEqual((body["model"], body["stream"]), ("m", True))

    def test_newest_model_when_none_chosen(self):
        self.run_job(model=None)
        self.assertEqual(_Fake.seen[0][1]["model"], "model-new")

    def test_http_error_is_readable(self):
        _Fake.status = 400
        _job, got = self.run_job()
        self.assertEqual(got["error"], "Bad thing")
        _Fake.status = 401
        self.assertEqual(self.run_job()[1]["error"], "The API key was not accepted.")

    def test_stream_error_event(self):
        _Fake.events = [delta("Par"), {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]
        _job, got = self.run_job()
        self.assertEqual(got["error"], "Overloaded")

    def test_consecutive_same_role_merged(self):
        # a message sent after a failed answer: two user turns in a row
        self.run_job(msgs=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
        self.assertEqual(_Fake.seen[0][1]["messages"], [{"role": "user", "content": "a\n\nb"}])

    def test_sse_parser(self):
        lines = [b"event: x\n", b'data: {"a":\n', b"data: 1}\n", b"\n", b": comment\n", b"data: nope\n", b"\n"]
        self.assertEqual(list(api.parse_sse(lines)), [("x", {"a": 1})])


class FakeJob:
    def __init__(self, key, model, messages, on_text, on_done, on_error, system=None):
        self.args = (key, model, [dict(m) for m in messages])
        self.on_text, self.on_done, self.on_error = on_text, on_done, on_error
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.assistanttest")
        cls.app.register(None)

    def setUp(self):
        from sonata2.assistant import window as W
        self.W = W
        self.dir = tempfile.mkdtemp()
        self.jobs = []
        self._stream, self._key = api.stream, api.api_key
        api.stream = lambda *a, **k: self.jobs.append(FakeJob(*a, **k)) or self.jobs[-1]
        api.api_key = lambda: "test-key"
        self.win = W.AssistantWindow(self.app, S.Store(self.dir))
        self.win.present()
        settle()

    def tearDown(self):
        api.stream, api.api_key = self._stream, self._key
        self.win.destroy()
        settle(50)

    def type_send(self, text):
        self.win.input.get_buffer().set_text(text)
        self.win.send()
        settle(50)

    def texts(self):
        out = []
        child = self.win.messages.get_first_child()
        while child is not None:
            w = child.get_child() if isinstance(child, Gtk.Revealer) else child
            out.append(w.text if isinstance(w, self.W.Answer) else
                       w.get_text() if isinstance(w, Gtk.Label) else type(w).__name__)
            child = child.get_next_sibling()
        return out

    def test_send_stream_and_save(self):
        self.assertTrue(self.win.empty.get_visible())
        self.assertFalse(self.win.send_btn.get_sensitive())
        self.type_send("Hello there")
        self.assertEqual(self.win.input.get_buffer().get_char_count(), 0)
        self.assertEqual(self.jobs[0].args[2], [{"role": "user", "content": "Hello there"}])
        self.assertEqual(self.win.send_btn.get_icon_name(), "media-playback-stop-symbolic")
        self.jobs[0].on_text("Hi **you**")
        self.jobs[0].on_text("!")
        self.assertEqual(self.texts(), ["Hello there", "Hi **you**!"])
        self.jobs[0].on_done()
        self.assertIsNone(self.win.job)
        st = S.Store(self.dir)
        self.assertEqual(st.chats[0]["title"], "Hello there")
        self.assertEqual(st.load(st.chats[0]["id"])["messages"][-1], {"role": "assistant", "content": "Hi **you**!"})
        self.assertIsNotNone(self.win._row(st.chats[0]["id"]))

    def test_answer_updates_only_changed_blocks(self):
        ans = self.W.Answer("para one\n\npara")
        first = ans.get_first_child()
        ans.set_text("para one\n\npara two")
        self.assertIs(ans.get_first_child(), first)            # untouched block kept
        self.assertEqual(ans.get_last_child().get_text(), "para two")
        ans.set_text("para one\n\n```\ncode")
        self.assertIn("as-code", ans.get_last_child().get_css_classes())

    def test_stop_keeps_partial(self):
        self.type_send("Write a poem")
        self.jobs[0].on_text("Roses")
        self.win.stop()
        self.assertTrue(self.jobs[0].cancelled)
        st = S.Store(self.dir)
        self.assertEqual(st.load(st.chats[0]["id"])["messages"][-1]["content"], "Roses")
        self.assertTrue(self.win.send_btn.get_icon_name() == "go-up-symbolic")

    def test_error_then_retry(self):
        self.type_send("Question")
        self.jobs[0].on_error("Could not reach the service.")
        settle(50)
        self.assertIn("Box", self.texts())
        err = self.win.messages.get_last_child().get_child()
        btn = err.get_last_child()
        btn.emit("clicked")
        settle(50)
        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(self.jobs[1].args[2], [{"role": "user", "content": "Question"}])   # not sent twice
        self.assertNotIn("Box", self.texts())

    def test_no_key_message(self):
        api.api_key = lambda: ""
        self.type_send("Hi")
        self.assertEqual(self.jobs, [])
        self.assertIn("Box", self.texts())

    def test_switch_chats_while_answering(self):
        self.type_send("First")
        first = self.win.chat["id"]
        self.win.new_chat()
        settle(50)
        self.assertEqual(self.texts(), [])
        self.jobs[0].on_text("Answer")                           # arrives while another chat is shown
        self.assertEqual(self.texts(), [])
        self.win.open_chat(first)
        self.assertEqual(self.texts(), ["First", "Answer"])
        self.jobs[0].on_done()
        self.assertEqual(S.Store(self.dir).load(first)["messages"][-1]["content"], "Answer")

    def test_reopen_restores_selected(self):
        self.type_send("Keep me")
        self.jobs[0].on_text("ok")
        self.jobs[0].on_done()
        cid = self.win.chat["id"]
        self.win.close()
        settle(50)
        win = self.W.AssistantWindow(self.app, S.Store(self.dir))
        self.assertEqual(win.chat["id"], cid)
        self.assertEqual([m["content"] for m in win.chat["messages"]], ["Keep me", "ok"])
        win.destroy()

    def test_rename_and_delete(self):
        self.type_send("Old title")
        self.jobs[0].on_done()
        cid = self.win.chat["id"]
        row = self.win._row(cid)
        self.win.rename(cid)
        row.name.set_text("New title")
        row.name.stop_editing(True)
        settle(50)
        self.assertEqual(S.Store(self.dir).get(cid)["title"], "New title")
        self.assertFalse(row.name.get_editable())
        self.win.store.delete(cid)                               # what the alert's Delete does
        self.win.new_chat()
        self.win.rebuild_sidebar()
        self.assertIsNone(self.win._row(cid))

    def test_new_chat_is_not_duplicated(self):
        before = self.win.chat
        self.win.new_chat()
        self.assertIs(self.win.chat, before)                     # a fresh empty chat stays

    def test_desktop_file_and_icon(self):
        import sonata2
        self.assertIn("Icon=sonata-assistant\n", open(self.W.assistant_desktop_file("sonata2")).read())
        path = os.path.join(os.path.dirname(sonata2.__file__), "data", "icons", "Sonata", "apps", "scalable",
                            "sonata-assistant.svg")
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
