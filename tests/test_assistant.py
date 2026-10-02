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
from sonata2.assistant import api, keystore, markdown, tools  # noqa: E402
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

    def run_job(self, model="m", msgs=None, tools_=None):
        got = {"text": "", "done": False, "error": None, "blocks": None, "stop": None}
        job = api.stream("k", model, msgs or [{"role": "user", "content": "hi"}],
                         lambda t: got.__setitem__("text", got["text"] + t),
                         lambda b, st: got.update(done=True, blocks=b, stop=st),
                         lambda e: got.__setitem__("error", e), tools=tools_)
        wait_for(lambda: got["done"] or got["error"])
        return job, got

    def test_streams_text(self):
        _job, got = self.run_job()
        self.assertEqual((got["text"], got["done"], got["error"]), ("Hello", True, None))
        headers, body = _Fake.seen[0]
        self.assertEqual(headers.get("x-api-key"), "k")
        self.assertEqual(headers.get("anthropic-version"), api.VERSION)
        self.assertEqual((body["model"], body["stream"]), ("m", True))

    def test_tool_use_streamed(self):
        _Fake.events = [delta("Let me look."),
                        {"type": "content_block_start", "index": 1,
                         "content_block": {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {}}},
                        {"type": "content_block_delta", "index": 1,
                         "delta": {"type": "input_json_delta", "partial_json": '{"path": "/tmp/'}},
                        {"type": "content_block_delta", "index": 1,
                         "delta": {"type": "input_json_delta", "partial_json": 'a.txt"}'}},
                        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"}]
        _job, got = self.run_job(tools_=tools.TOOLS)
        self.assertEqual(got["stop"], "tool_use")
        self.assertEqual(got["blocks"], [{"type": "text", "text": "Let me look."},
                                         {"type": "tool_use", "id": "tu1", "name": "read_file",
                                          "input": {"path": "/tmp/a.txt"}}])
        self.assertEqual([t["name"] for t in _Fake.seen[0][1]["tools"]], ["list_folder", "read_file", "create_file"])

    def test_merge_blocks_and_text(self):
        res = [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]
        merged = api.merge_turns([{"role": "user", "content": res}, {"role": "user", "content": "and now?"}])
        self.assertEqual(merged, [{"role": "user", "content": res + [{"type": "text", "text": "and now?"}]}])
        self.assertEqual(api.text_of([{"type": "text", "text": "a"}, {"type": "tool_use"}]), "a")

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


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.allowed = os.path.join(self.root, "allowed")
        os.makedirs(os.path.join(self.allowed, "sub"))
        with open(os.path.join(self.allowed, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.root, "secret.txt"), "w") as f:
            f.write("no")
        os.symlink(self.root, os.path.join(self.allowed, "escape"))
        self.folders = [self.allowed]

    def deny(self, name, **args):
        with self.assertRaises(tools.Denied):
            tools.check(name, args, self.folders)

    def test_only_inside_the_folders(self):
        self.deny("read_file", path=os.path.join(self.root, "secret.txt"))
        self.deny("read_file", path=os.path.join(self.allowed, "..", "secret.txt"))
        self.deny("read_file", path=os.path.join(self.allowed, "escape", "secret.txt"))     # symlink out
        self.deny("read_file", path="a.txt")                                              # relative
        self.deny("create_file", path=os.path.join(self.root, "x.txt"), content="x")
        self.deny("read_file", path=os.path.join(self.allowed, "a.txt") + "x")             # missing
        self.deny("list_folder", path=os.path.join(self.allowed, "a.txt"))                 # not a folder
        self.deny("delete_everything", path=self.allowed)
        self.assertFalse(tools.inside(self.allowed + "-other", self.folders))              # prefix, not inside
        with self.assertRaises(tools.Denied):
            tools.check("read_file", {"path": self.allowed}, [])                           # no folders: nothing

    def test_list_read_create(self):
        act = tools.check("list_folder", {"path": self.allowed}, self.folders)
        self.assertEqual(tools.run(act), ("a.txt\t5\nescape/\nsub/", False))
        act = tools.check("read_file", {"path": os.path.join(self.allowed, "a.txt")}, self.folders)
        self.assertEqual(tools.run(act), ("hello", False))
        new = os.path.join(self.allowed, "new", "b.md")
        act = tools.check("create_file", {"path": new, "content": "# B"}, self.folders)
        self.assertEqual((act.verb, act.summary()), ("create", "Created b.md"))
        self.assertFalse(tools.run(act)[1])
        self.assertEqual(open(new).read(), "# B")
        self.deny("create_file", path=new, content="again")                               # exists
        act = tools.check("create_file", {"path": new, "content": "v2", "overwrite": True}, self.folders)
        self.assertEqual(act.verb, "replace")
        tools.run(act)
        self.assertEqual(open(new).read(), "v2")

    def test_binary_refused(self):
        p = os.path.join(self.allowed, "bin")
        with open(p, "wb") as f:
            f.write(b"\x00\x01")
        self.assertTrue(tools.run(tools.check("read_file", {"path": p}, self.folders))[1])


class KeystoreTest(unittest.TestCase):
    def test_environment_fallback(self):
        keystore._cache["key"] = None
        os.environ["ANTHROPIC_API_KEY"] = " env-key "
        try:
            self.assertEqual(keystore.cached(), "env-key")
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        keystore._cache["key"] = "kept"
        got = []
        keystore.load(got.append)
        self.assertEqual(got, ["kept"])
        keystore._cache["key"] = None


class FakeJob:
    def __init__(self, key, model, messages, on_text, on_done, on_error, system=None, tools=None):
        self.args = (key, model, [dict(m) for m in messages])
        self.system, self.tools = system, tools
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
        self._stream, self._alert = api.stream, ui.dialog.alert
        api.stream = lambda *a, **k: self.jobs.append(FakeJob(*a, **k)) or self.jobs[-1]
        keystore._cache["key"] = "test-key"
        self.alerts = []                      # (heading, body, responses, answer): answered by the test
        ui.dialog.alert = lambda h, b, r, cb=None, parent=None, check=None: self.alerts.append((h, b, r, cb)) or None
        self.win = W.AssistantWindow(self.app, S.Store(self.dir))
        self.win.present()
        settle()

    def tearDown(self):
        api.stream, ui.dialog.alert = self._stream, self._alert
        keystore._cache["key"] = None
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
            out.append(w.text if isinstance(w, self.W.Answer) else w.label.get_text() if isinstance(w, self.W.ToolRow) else
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
        self.jobs[0].on_done(None, "end_turn")
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
        keystore._cache["key"] = ""
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
        self.jobs[0].on_done(None, "end_turn")
        self.assertEqual(S.Store(self.dir).load(first)["messages"][-1]["content"], "Answer")

    def test_reopen_restores_selected(self):
        self.type_send("Keep me")
        self.jobs[0].on_text("ok")
        self.jobs[0].on_done(None, "end_turn")
        cid = self.win.chat["id"]
        self.win.close()
        settle(50)
        win = self.W.AssistantWindow(self.app, S.Store(self.dir))
        self.assertEqual(win.chat["id"], cid)
        self.assertEqual([m["content"] for m in win.chat["messages"]], ["Keep me", "ok"])
        win.destroy()

    def test_rename_and_delete(self):
        self.type_send("Old title")
        self.jobs[0].on_done(None, "end_turn")
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

    def tool_turn(self, *uses):
        """Claude answers with file actions."""
        blocks = [{"type": "text", "text": "Checking."}] + [
            {"type": "tool_use", "id": f"t{i}", "name": n, "input": inp} for i, (n, inp) in enumerate(uses)]
        self.jobs[-1].on_text("Checking.")
        self.jobs[-1].on_done(blocks, "tool_use")
        settle(30)

    def answer(self, rid):
        _h, _b, _r, cb = self.alerts.pop(0)
        cb(rid)
        settle(30)

    def folder(self):
        d = os.path.realpath(tempfile.mkdtemp())
        with open(os.path.join(d, "notes.txt"), "w") as f:
            f.write("buy milk")
        self.win.cfg["folders"] = [d]
        return d

    def test_tools_only_with_folders(self):
        self.type_send("hi")
        self.assertIsNone(self.jobs[0].tools)
        self.jobs[0].on_done(None, "end_turn")
        d = self.folder()
        self.type_send("again")
        self.assertEqual(self.jobs[1].tools, tools.TOOLS)
        self.assertIn(d, self.jobs[1].system)

    def test_each_action_asks_and_runs(self):
        d = self.folder()
        self.type_send("What's in my notes?")
        self.tool_turn(("read_file", {"path": os.path.join(d, "notes.txt")}),
                       ("create_file", {"path": os.path.join(d, "todo.md"), "content": "- milk"}))
        self.assertEqual(len(self.alerts), 1)                         # one at a time
        self.assertIn("read “notes.txt”", self.alerts[0][0])
        self.assertEqual(len(self.jobs), 1)                           # nothing sent before the answers
        self.answer("allow")
        self.assertIn("create “todo.md”", self.alerts[0][0])
        self.assertIn("1 line", self.alerts[0][1])
        self.answer("deny")
        self.assertFalse(os.path.exists(os.path.join(d, "todo.md")))  # refused: not created
        self.assertEqual(len(self.jobs), 2)                           # then the conversation goes on
        sent = self.jobs[1].args[2]
        self.assertEqual(sent[-2]["content"][1]["type"], "tool_use")
        results = sent[-1]["content"]
        self.assertEqual(results[0], {"type": "tool_result", "tool_use_id": "t0", "content": "buy milk"})
        self.assertTrue(results[1]["is_error"])
        self.jobs[1].on_text("You need milk.")
        self.jobs[1].on_done(None, "end_turn")
        texts = self.texts()
        self.assertIn("Read notes.txt", texts)
        self.assertIn("Not allowed: Created todo.md", texts)
        # reopened from disk: same rows, results not shown as messages
        cid = self.win.chat["id"]
        win = self.W.AssistantWindow(self.app, S.Store(self.dir))
        self.win.destroy()
        self.win = win
        win.open_chat(cid)
        self.assertEqual(self.texts(), ["What's in my notes?", "Checking.", "Read notes.txt",
                                        "Not allowed: Created todo.md", "You need milk."])

    def test_action_outside_folders_never_asks(self):
        self.folder()
        self.type_send("read /etc/passwd")
        self.tool_turn(("read_file", {"path": "/etc/passwd"}))
        self.assertEqual(self.alerts, [])
        self.assertTrue(self.jobs[1].args[2][-1]["content"][0]["is_error"])

    def test_stop_while_asking(self):
        d = self.folder()
        self.type_send("Make files")
        self.tool_turn(("create_file", {"path": os.path.join(d, "a.txt"), "content": "a"}),
                       ("create_file", {"path": os.path.join(d, "b.txt"), "content": "b"}))
        self.assertEqual(self.win.send_btn.get_icon_name(), "media-playback-stop-symbolic")
        self.win.stop()
        settle(30)
        self.assertIsNone(self.win.pending)
        self.assertEqual(len(self.jobs), 1)                           # stopped: no new answer
        last = S.Store(self.dir).load(self.win.chat["id"])["messages"][-1]
        self.assertEqual([r["tool_use_id"] for r in last["content"]], ["t0", "t1"])   # every action answered
        self.assertTrue(all(r.get("is_error") for r in last["content"]))
        self.answer("allow")                                          # a late click does nothing
        self.assertFalse(os.path.exists(os.path.join(d, "a.txt")))
        self.type_send("ok")                                          # and the chat goes on
        self.assertEqual(len(self.jobs), 2)

    def test_settings_panel(self):
        found = []
        old = api.models_async
        api.models_async = lambda key, cb: found.append(cb)
        saved = []
        old_save = keystore.save
        keystore.save = lambda key, cb=None: (saved.append(key), keystore._cache.update(key=key), cb(True))
        try:
            self.win.show_settings()
            settle(50)
            found[0]([("model-new", "New"), ("model-old", "Old")])
            dd = self.win.model_row.get_first_child()
            self.assertEqual(dd.get_model().get_n_items(), 3)          # Newest Available + two
            dd.set_selected(2)
            self.assertEqual(self.win.cfg["model"], "model-old")
            from sonata2 import config
            self.assertEqual(config.load("assistant", self.W.DEFAULTS)["model"], "model-old")
            self.win.key_field.set_text("  new-key ")
            self.win._save_key()
            self.assertEqual(saved, ["new-key"])
            self.assertEqual(self.win.key_caption.get_label(), "Saved in your keyring.")
            self.win.cfg["folders"] = ["/tmp/x"]
            self.win._fill_folders()
            self.win.remove_folder("/tmp/x")
            self.assertEqual(config.load("assistant", self.W.DEFAULTS)["folders"], [])
            self.win.settings_panel.popdown()
        finally:
            api.models_async, keystore.save = old, old_save

    def test_settings_key_field_takes_focus(self):
        # regression (Vini): the API key field couldn't be typed in -- the gear
        # button (can_focus=False) kept the panel's fields from taking focus
        self.win.show_settings()
        settle(100)
        self.assertTrue(self.win.key_field.grab_focus())
        self.win.settings_panel.popdown()
        settle(100)
        self.assertFalse(self.win.settings_btn.get_can_focus())       # restored once the panel closes

    def test_ui_kit_only(self):
        # Vini: every Sonata UI comes from the UI kit (no libadwaita widgets, no system dialogs)
        import inspect
        src = inspect.getsource(self.W)
        for bad in ("Adw.", "Gtk.FileDialog", "Gtk.ColorDialog", "Gtk.AlertDialog", "Gtk.MessageDialog"):
            self.assertNotIn(bad, src)
        self.assertIsInstance(self.win.composer, ui.controls.TextArea)

    def test_desktop_file_and_icon(self):
        import sonata2
        self.assertIn("Icon=sonata-assistant\n", open(self.W.assistant_desktop_file("sonata2")).read())
        path = os.path.join(os.path.dirname(sonata2.__file__), "data", "icons", "Sonata", "apps", "scalable",
                            "sonata-assistant.svg")
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
