"""Session, login and system plumbing (review): config files, the greetd
client, PAM service choice, doctor, the file chooser portal's option
mapping, the login screen's helpers, `sonata2 keep` and friends.
Run: xvfb-run -a python3 -m unittest tests.test_review_session"""
import io
import json
import os
import socket
import struct
import tempfile
import threading
import types
import unittest
from unittest import mock

os.environ.setdefault("SONATA_GREETER_FAKE", "1")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import config, doctor, greetd, keyring, pam, portal, userdata  # noqa: E402
from sonata2 import __main__ as main  # noqa: E402
from sonata2.shell import greeter as G  # noqa: E402


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# -- config.py -------------------------------------------------------------------------------
class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        p = mock.patch.object(config, "CONFIG_DIR", self.d)
        p.start()
        self.addCleanup(p.stop)

    def test_load_keeps_only_known_keys_and_survives_bad_files(self):
        """Unknown keys never leak into a component's settings; a corrupt or
        non-object file falls back to the defaults instead of crashing."""
        write(os.path.join(self.d, "x.json"), json.dumps({"a": 5, "junk": 1}))
        self.assertEqual(config.load("x", {"a": 1, "b": 2}), {"a": 5, "b": 2})
        write(os.path.join(self.d, "x.json"), "{not json")
        self.assertEqual(config.load("x", {"a": 1}), {"a": 1})
        write(os.path.join(self.d, "x.json"), "[1, 2]")
        self.assertEqual(config.load("x", {"a": 1}), {"a": 1})

    def test_update_keeps_other_keys_and_leaves_no_temp_file(self):
        """update() changes only the given keys (also ones other versions
        wrote) and replaces the file atomically."""
        config.save("x", {"keep": "me", "n": 1})
        config.update("x", n=2)
        with open(os.path.join(self.d, "x.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"keep": "me", "n": 2})
        self.assertEqual(sorted(os.listdir(self.d)), ["x.json"])

    def test_update_over_a_corrupt_file_starts_fresh(self):
        """A broken file is replaced by the new values, never kept half-read."""
        write(os.path.join(self.d, "x.json"), "garbage")
        config.update("x", a=1)
        self.assertEqual(config.load("x", {"a": 0}), {"a": 1})


# -- greetd.py -------------------------------------------------------------------------------
class FakeGreetd:
    """A unix-socket server speaking greetd's framing, one scripted reply per request."""

    def __init__(self, replies, close_after=None):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "greetd.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.replies, self.close_after, self.got = list(replies), close_after, []
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _recv(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _serve(self):
        conn, _ = self.srv.accept()
        with conn:
            while True:
                head = self._recv(conn, 4)
                if head is None:
                    return
                body = self._recv(conn, struct.unpack("=I", head)[0])
                self.got.append(json.loads(body))
                if self.close_after is not None and len(self.got) >= self.close_after:
                    return
                data = json.dumps(self.replies.pop(0)).encode()
                conn.sendall(struct.pack("=I", len(data)) + data)


class ScriptedClient:
    """greetd client stand-in recording calls (no socket)."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def _next(self):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def create_session(self, username):
        self.calls.append(("create", username))
        return self._next()

    def answer(self, response):
        self.calls.append(("answer", response))
        return self._next()

    def cancel(self):
        self.calls.append(("cancel",))


class GreetdTest(unittest.TestCase):
    def test_client_frames_json_with_native_length(self):
        """Each request is a native-endian u32 length + JSON, and the reply is
        decoded the same way (greetd-ipc(7))."""
        srv = FakeGreetd([{"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"}])
        c = greetd.Client(srv.path)
        try:
            reply = c.create_session("vini")
        finally:
            c.close()
        self.assertEqual(reply["auth_message_type"], "secret")
        self.assertEqual(srv.got, [{"type": "create_session", "username": "vini"}])

    def test_error_reply_raises_with_its_type(self):
        """An error reply becomes GreetdError carrying greetd's error_type, so
        the screen can tell a wrong password from a broken login."""
        srv = FakeGreetd([{"type": "error", "error_type": "auth_error", "description": "nope"}])
        c = greetd.Client(srv.path)
        try:
            with self.assertRaises(greetd.GreetdError) as cm:
                c.answer("bad")
        finally:
            c.close()
        self.assertEqual((cm.exception.error_type, str(cm.exception)), ("auth_error", "nope"))
        self.assertEqual(srv.got, [{"type": "post_auth_message_response", "response": "bad"}])

    def test_closed_connection_is_a_greetd_error(self):
        """greetd going away mid-call is reported as GreetdError (the screen
        shows it), never an endless read."""
        srv = FakeGreetd([], close_after=1)
        c = greetd.Client(srv.path)
        try:
            with self.assertRaises(greetd.GreetdError):
                c.start_session(["sonata"], [])
        finally:
            c.close()

    def test_login_acknowledges_info_then_answers_the_password(self):
        """Info prompts are acknowledged with no response; the secret prompt
        gets the password; a stale session is cancelled first."""
        c = ScriptedClient([{"type": "auth_message", "auth_message_type": "info", "auth_message": "Hi"},
                            {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"},
                            {"type": "success"}])
        greetd.login(c, "vini", "pw")
        self.assertEqual(c.calls, [("cancel",), ("create", "vini"), ("answer", None), ("answer", "pw")])

    def test_wrong_password_cancels_the_half_made_session(self):
        """After a wrong password the session is cancelled, so the next try
        doesn't answer the old one."""
        c = ScriptedClient([{"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"},
                            greetd.GreetdError("auth_error", "Authentication failed")])
        with self.assertRaises(greetd.GreetdError) as cm:
            greetd.login(c, "vini", "bad")
        self.assertEqual(cm.exception.error_type, "auth_error")
        self.assertEqual(c.calls[-1], ("cancel",))

    def test_second_secret_prompt_is_a_wrong_password(self):
        """Asked for the secret again (2FA or a PAM retry): auth_error, not a loop."""
        secret = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"}
        c = ScriptedClient([secret, secret])
        with self.assertRaises(greetd.GreetdError) as cm:
            greetd.login(c, "vini", "pw")
        self.assertEqual(cm.exception.error_type, "auth_error")

    def test_unsupported_prompt_fails_cleanly(self):
        """A prompt the screen can't answer (visible input) ends in an error
        with greetd's own message, and the session is cancelled."""
        c = ScriptedClient([{"type": "auth_message", "auth_message_type": "visible", "auth_message": "OTP:"}])
        with self.assertRaises(greetd.GreetdError) as cm:
            greetd.login(c, "vini", "pw")
        self.assertEqual((cm.exception.error_type, str(cm.exception)), ("error", "OTP:"))
        self.assertIn(("cancel",), c.calls[1:])

    def test_fake_accepts_only_sonata(self):
        """The preview stand-in behaves like greetd: "sonata" logs in, anything else is auth_error."""
        f = greetd.Fake()
        greetd.login(f, "x", "sonata")
        with mock.patch("time.sleep"), self.assertRaises(greetd.GreetdError):
            greetd.login(f, "x", "wrong")

    @unittest.expectedFailure
    def test_missing_socket_variable_is_an_error_the_greeter_handles(self):
        """The greeter's worker only catches GreetdError/OSError."""
        # BUG: without $GREETD_SOCK, Client() raises KeyError, which escapes
        # greeter.Greeter._login's worker thread: the spinner turns forever.
        with mock.patch.dict(os.environ, {}, clear=True):
            try:
                greetd.Client()
            except (greetd.GreetdError, OSError):
                pass


# -- pam.py ----------------------------------------------------------------------------------
class PamTest(unittest.TestCase):
    def test_service_prefers_sonata_lock_then_distro_files(self):
        """The first PAM service present (in /etc/pam.d or /usr/lib/pam.d) is used."""
        with mock.patch.object(pam.os.path, "exists", side_effect=lambda p: p == "/usr/lib/pam.d/system-auth"):
            self.assertEqual(pam._service(), "system-auth")
        with mock.patch.object(pam.os.path, "exists",
                               side_effect=lambda p: p in ("/etc/pam.d/login", "/etc/pam.d/sonata-lock")):
            self.assertEqual(pam._service(), "sonata-lock")
        with mock.patch.object(pam.os.path, "exists", return_value=False):
            self.assertEqual(pam._service(), "login")

    def test_no_libpam_never_authenticates(self):
        """Without libpam every password is refused (the lock never opens by accident)."""
        with mock.patch.object(pam, "_pam", return_value=None):
            self.assertFalse(pam.available())
            self.assertFalse(pam.authenticate("vini", "anything"))


# -- doctor.py -------------------------------------------------------------------------------
class DoctorTest(unittest.TestCase):
    def test_report_counts_problems_and_shows_fixes(self):
        """The summary says how many problems and optional gaps there are,
        with each fix under its line."""
        r = doctor.Report()
        r.add(doctor.OK, "Python")
        r.add(doctor.FAIL, "Wayfire missing", fix="install wayfire")
        r.add(doctor.WARN, "grim missing", "screenshots")
        t = r.text()
        self.assertIn("[FAIL] Wayfire missing", t)
        self.assertIn("       fix: install wayfire", t)
        self.assertIn("[warn] grim missing -- screenshots", t)
        self.assertIn("1 problem(s) to fix before logging in.", t)
        self.assertIn("1 optional thing(s) missing", t)
        ok = doctor.Report()
        ok.add(doctor.OK, "all")
        self.assertIn("Ready to log in to Sonata.", ok.text())

    def test_last_session_errors_keeps_only_error_lines(self):
        """Bug reports get the error lines of each log, newest last, capped."""
        home = tempfile.mkdtemp()
        lines = [f"Error {i}" for i in range(30)] + ["all good"]
        write(os.path.join(home, ".cache", "sonata2", "dock.log"), "\n".join(lines))
        with mock.patch.dict(os.environ, {"HOME": home}):
            out = doctor.last_session_errors(n=5).splitlines()
        self.assertEqual(out, ["--- dock.log ---"] + [f"Error {i}" for i in range(25, 30)])

    @unittest.expectedFailure
    def test_lock_service_in_usr_lib_is_not_a_failure(self):
        """pam._service() accepts /usr/lib/pam.d; the doctor must agree."""
        # BUG: check_lock only looks in /etc/pam.d, so a distro shipping PAM
        # files in /usr/lib/pam.d gets a false FAIL ("problem to fix").
        r = doctor.Report()
        with mock.patch("os.path.exists", side_effect=lambda p: p == "/usr/lib/pam.d/system-local-login"):
            doctor.check_lock(r)
        self.assertEqual(r.rows[0][0], doctor.OK)

    @unittest.expectedFailure
    def test_errors_read_from_where_keep_writes_logs(self):
        """`sonata2 keep` logs to $XDG_CACHE_HOME/sonata2; the doctor should read there."""
        # BUG: last_session_errors() hard-codes ~/.cache, so with XDG_CACHE_HOME
        # set the report and bug reports miss every component's errors.
        home, cache = tempfile.mkdtemp(), tempfile.mkdtemp()
        write(os.path.join(cache, "sonata2", "dock.log"), "Traceback (most recent call last):")
        with mock.patch.dict(os.environ, {"HOME": home, "XDG_CACHE_HOME": cache}):
            self.assertIn("Traceback", doctor.last_session_errors())


# -- portal.py (file chooser option mapping) ---------------------------------------------------
class PortalParseTest(unittest.TestCase):
    FILTERS = [("Images", [(0, "*.png"), (1, "image/jpeg")]), ("All", [(0, "*")])]

    def test_open_file_options(self):
        """OpenFile: filters kept, the current one selected, multiple honoured,
        directory=True asks for a folder."""
        a = portal.parse("OpenFile", "Open", {"filters": self.FILTERS, "current_filter": ("All", [(0, "*")]),
                                              "multiple": True, "accept_label": "Pick"})
        self.assertEqual((a["mode"], a["multiple"], a["current_filter"], a["accept_label"]), ("open", True, 1, "Pick"))
        self.assertEqual(a["filters"][0], ("Images", [(0, "*.png"), (1, "image/jpeg")]))
        self.assertEqual(portal.parse("OpenFile", "t", {"directory": True})["mode"], "folder")

    def test_unknown_current_filter_is_added_first(self):
        """A current filter the app didn't list is shown (first) rather than dropped."""
        a = portal.parse("OpenFile", "t", {"filters": self.FILTERS, "current_filter": ("Text", [(0, "*.txt")])})
        self.assertEqual(a["filters"][0], ("Text", [(0, "*.txt")]))
        self.assertEqual((len(a["filters"]), a["current_filter"]), (3, 0))

    def test_save_file_takes_folder_and_name_from_current_file(self):
        """SaveFile: NUL-terminated byte paths decoded; current_file gives the
        folder and the name; multiple is never allowed."""
        a = portal.parse("SaveFile", "Save", {"current_file": b"/home/v/Docs/report.pdf\0", "multiple": True})
        self.assertEqual(a["mode"], "save")
        self.assertFalse(a["multiple"])
        self.assertEqual(a["name"], "report.pdf")
        self.assertEqual(a["folder"], "file:///home/v/Docs")
        b = portal.parse("SaveFile", "Save", {"current_folder": b"/tmp\0", "current_name": "a.txt",
                                              "current_file": b"/other/b.txt\0"})
        self.assertEqual((b["folder"], b["name"]), ("file:///tmp", "a.txt"))     # explicit values win

    def test_save_files_puts_each_file_in_the_chosen_folder(self):
        """SaveFiles: a folder is chosen and the answer lists one uri per file in it."""
        a = portal.parse("SaveFiles", "Save", {"files": [b"/x/one.txt\0", b"two.png\0"]})
        self.assertEqual(a["mode"], "folder")
        res = portal.results(a, ["file:///tmp/dest"], 0)
        self.assertEqual(res["uris"].unpack(), ["file:///tmp/dest/one.txt", "file:///tmp/dest/two.png"])
        self.assertNotIn("current_filter", res)
        self.assertTrue(res["writable"].unpack())

    def test_results_report_the_chosen_filter(self):
        """The answer names the filter the user picked, typed (sa(us))."""
        a = portal.parse("OpenFile", "t", {"filters": self.FILTERS})
        res = portal.results(a, ["file:///a.png"], 0)
        self.assertEqual(res["current_filter"].get_type_string(), "(sa(us))")
        self.assertEqual(res["current_filter"].unpack(), ("Images", [(0, "*.png"), (1, "image/jpeg")]))


# -- greeter.py (data helpers) ------------------------------------------------------------------
SESSION = """[Desktop Entry]
Name={name}
Exec={exec}
{extra}
"""


class GreeterDataTest(unittest.TestCase):
    def test_sessions_list(self):
        """Wayland sessions: hidden ones skipped, field codes stripped,
        DesktopNames as XDG_CURRENT_DESKTOP, the first folder wins, Sonata first."""
        local, usr = tempfile.mkdtemp(), tempfile.mkdtemp()
        write(os.path.join(usr, "aaa.desktop"), SESSION.format(name="Alpha", exec="alpha %U --x", extra=""))
        write(os.path.join(usr, "sonata.desktop"), SESSION.format(name="Sonata", exec="/usr/local/bin/sonata-login",
                                                                  extra="DesktopNames=Sonata;wlroots;"))
        write(os.path.join(usr, "hidden.desktop"), SESSION.format(name="H", exec="h", extra="Hidden=true"))
        write(os.path.join(usr, "nodisp.desktop"), SESSION.format(name="N", exec="n", extra="NoDisplay=true"))
        write(os.path.join(usr, "dup.desktop"), SESSION.format(name="System copy", exec="sys", extra=""))
        write(os.path.join(local, "dup.desktop"), SESSION.format(name="Local copy", exec="loc", extra=""))
        with mock.patch.object(G, "SESSION_DIRS", (local, usr)):
            out = G.sessions()
        self.assertEqual([s.key for s in out], ["sonata", "aaa", "dup"])
        self.assertEqual(out[0].desktops, "Sonata:wlroots")
        self.assertEqual(out[1].cmd, ["alpha", "--x"])
        self.assertEqual(out[2].name, "Local copy")

    def test_best_modes(self):
        """One entry per resolution at its highest refresh rate, biggest first."""
        modes = [(1920, 1080, 60.0), (1920, 1080, 144.0), (2560, 1440, 59.95), (1280, 720, 60.0)]
        self.assertEqual(G.best_modes(modes), [(2560, 1440, 59.95), (1920, 1080, 144.0), (1280, 720, 60.0)])

    def test_state_round_trip_and_corrupt_file(self):
        """Last user / sessions survive a restart; a corrupt state file is ignored."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "sub", "state.json")
        with mock.patch.object(G, "STATE", path):
            G.save_state({"user": "vini", "sessions": {"vini": "sonata"}})
            self.assertEqual(G.load_state(), {"user": "vini", "sessions": {"vini": "sonata"}})
            self.assertFalse(os.path.exists(path + ".new"))
            write(path, "{broken")
            self.assertEqual(G.load_state(), {})

    def test_users_from_passwd_without_accountsservice(self):
        """No AccountsService: real people from /etc/passwd (uid 1000-59999,
        a login shell), sorted by their real name."""
        pw = lambda n, uid, gecos, shell: types.SimpleNamespace(pw_name=n, pw_uid=uid, pw_gecos=gecos,
                                                                pw_shell=shell)
        entries = [pw("root", 0, "root", "/bin/bash"), pw("zed", 1001, "Ana Z,,,", "/bin/zsh"),
                   pw("vini", 1000, "Vini", "/bin/bash"), pw("svc", 1002, "", "/usr/sbin/nologin"),
                   pw("nobody", 65534, "", "/bin/false"), pw("bob", 1003, "", "/bin/bash")]
        with mock.patch.object(G.Gio, "bus_get_sync", side_effect=GLib.Error("no bus")), \
                mock.patch.object(G.pwd, "getpwall", return_value=entries):
            us = G.users()
        self.assertEqual([(u.name, u.real) for u in us], [("zed", "Ana Z"), ("bob", "bob"), ("vini", "Vini")])

    def test_outputs_and_set_mode_command(self):
        """wlr-randr's JSON: disabled outputs skipped, modes and the current
        one read; a mode is set as WxH@Hz with 3 decimals."""
        data = [{"name": "eDP-1", "description": "Panel", "enabled": True,
                 "modes": [{"width": 1920, "height": 1080, "refresh": 60.0, "current": True},
                           {"width": 1920, "height": 1080, "refresh": 165.003}]},
                {"name": "HDMI-A-1", "enabled": False, "modes": []}]
        run = mock.Mock(return_value=types.SimpleNamespace(stdout=json.dumps(data), returncode=0))
        with mock.patch.object(G.subprocess, "run", run):
            outs = G.outputs()
            self.assertTrue(G.set_mode("eDP-1", (1920, 1080, 165.003)))
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["current"], (1920, 1080, 60.0))
        self.assertEqual(run.call_args[0][0], ["wlr-randr", "--output", "eDP-1", "--mode", "1920x1080@165.003Hz"])


class GreeterLoginTest(unittest.TestCase):
    """The login screen itself (fake greetd, plain windows under xvfb)."""

    def setUp(self):
        d = tempfile.mkdtemp()
        for name, value in (("STATE", os.path.join(d, "state.json")), ("WAITS", os.path.join(d, "waits.json")),
                            ("users", lambda: [G.User("vini", "Vini"), G.User("ana", "Ana")]),
                            ("sessions", lambda: [G.Session("sonata", "Sonata", ["sonata"], "Sonata")])):
            p = mock.patch.object(G, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.test.greeter")
        self.g = G.Greeter(self.app)
        self.addCleanup(lambda: [w.destroy() for w in self.g.windows])

    def _drain(self):
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)

    def test_several_users_start_on_the_picker(self):
        """Two accounts and no remembered one: the users row first, then the
        password page for the one picked."""
        self.assertIsNone(self.g.user)
        self.g._pick(self.g.users[1])
        self.assertEqual(self.g.user.name, "ana")
        self.assertTrue(hasattr(self.g, "entry"))

    @unittest.expectedFailure
    def test_other_users_clicked_while_logging_in(self):
        """A login that succeeds must finish (save the user, quit) even if
        "Other Users" was clicked while the spinner turned."""
        # BUG: _started/_failed read self.user, which "Other Users" sets to None
        # during the login thread: AttributeError, the greeter never quits and
        # greetd never starts the session.
        self.g._pick(self.g.users[1])
        self.g.entry.set_text("sonata")

        class Inline:
            def __init__(self, target, daemon=None):
                self.target = target

            def start(self):
                self.target()
        with mock.patch.object(G.threading, "Thread", Inline), mock.patch.object(G.GLib, "timeout_add"):
            self.g._login()                      # success queued for the main loop
            self.g._pick(None)                   # "Other Users" before it runs
            self._drain()
        self.assertEqual(G.load_state().get("user"), "ana")


# -- __main__.py: launcher, keep, session env ------------------------------------------------------
class MainTest(unittest.TestCase):
    def test_self_command(self):
        """The installed launcher when it exists; else this interpreter on this clone."""
        d = tempfile.mkdtemp()
        launcher = os.path.join(d, "sonata2")
        write(launcher)
        with mock.patch.dict(os.environ, {"SONATA2_LAUNCHER": launcher}):
            self.assertEqual(main.self_command(), launcher)
        with mock.patch.dict(os.environ, {"SONATA2_LAUNCHER": os.path.join(d, "gone")}):
            cmd = main.self_command()
        self.assertTrue(cmd.startswith(f"env PYTHONPATH={main.REPO} "))
        self.assertTrue(cmd.endswith(" -m sonata2"))

    def _keep(self, child_cls, argv=("dock",)):
        import subprocess
        d = tempfile.mkdtemp()
        write(os.path.join(d, "wayland-1"))
        env = {"XDG_RUNTIME_DIR": d, "XDG_CACHE_HOME": d, "WAYLAND_DISPLAY": "wayland-1"}
        with mock.patch.dict(os.environ, env), mock.patch.object(subprocess, "Popen", child_cls), \
                mock.patch.object(main, "share_session_env"), \
                mock.patch.object(main, "_compositor_alive", return_value=True), \
                mock.patch("time.sleep"), mock.patch("sys.stderr", io.StringIO()):
            return main.keep(list(argv)), d

    def test_keep_rotates_the_log_and_stops_on_a_clean_exit(self):
        """The previous run's log becomes <name>.old.log; exit 0 is not restarted."""
        state = {"n": 0}

        class Child:
            def __init__(self, cmd, **_k):
                state["n"] += 1
                state["cmd"] = cmd

            def wait(self, timeout=None):
                return 0
        import subprocess
        d = tempfile.mkdtemp()
        write(os.path.join(d, "sonata2", "dock.log"), "previous run\n")
        write(os.path.join(d, "wayland-1"))
        env = {"XDG_RUNTIME_DIR": d, "XDG_CACHE_HOME": d, "WAYLAND_DISPLAY": "wayland-1"}
        with mock.patch.dict(os.environ, env), mock.patch.object(subprocess, "Popen", Child), \
                mock.patch.object(main, "share_session_env"):
            self.assertEqual(main.keep(["dock", "--background"]), 0)
        self.assertEqual(state["n"], 1)
        self.assertEqual(state["cmd"][-2:], ["dock", "--background"])
        with open(os.path.join(d, "sonata2", "dock.old.log")) as f:
            self.assertEqual(f.read(), "previous run\n")
        with open(os.path.join(d, "sonata2", "dock.log")) as f:
            self.assertIn("dock --background", f.readline())

    def test_keep_gives_up_on_a_crash_loop(self):
        """Five crashes within a minute: no more restarts, exit 1."""
        state = {"n": 0}

        class Child:
            def __init__(self, *_a, **_k):
                state["n"] += 1

            def wait(self, timeout=None):
                return -11
        code, _d = self._keep(Child)
        self.assertEqual((code, state["n"]), (1, 5))

    def test_keep_respects_sigterm(self):
        """Stopped on purpose (SIGTERM from `sonata2 restart`, logout): not restarted."""
        state = {"n": 0}

        class Child:
            def __init__(self, *_a, **_k):
                state["n"] += 1

            def wait(self, timeout=None):
                return -15
        code, _d = self._keep(Child)
        self.assertEqual((code, state["n"]), (0, 1))

    def test_share_session_env_once_per_compositor(self):
        """The display variables that exist go to D-Bus/systemd activation, once
        per Wayland socket (a marker newer than the socket)."""
        d = tempfile.mkdtemp()
        write(os.path.join(d, "wayland-9"))
        os.utime(os.path.join(d, "wayland-9"), (1000, 1000))
        env = {"WAYLAND_DISPLAY": "wayland-9", "XDG_RUNTIME_DIR": d, "XDG_CURRENT_DESKTOP": "Sonata"}
        run = mock.Mock()
        with mock.patch.dict(os.environ, env), mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("shutil.which", return_value="/usr/bin/dbus-update-activation-environment"), \
                mock.patch("subprocess.run", run):
            for k in ("DISPLAY", "XDG_DATA_DIRS", "DCONF_PROFILE", "XCURSOR_THEME", "XCURSOR_SIZE",
                      "XCURSOR_PATH", "QT_QPA_PLATFORMTHEME", "SONATA_GLASS", "XDG_SESSION_DESKTOP"):
                os.environ.pop(k, None)
            main.share_session_env()
            main.share_session_env()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ["/usr/bin/dbus-update-activation-environment", "--systemd",
                                               "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP"])
        self.assertTrue(os.path.exists(os.path.join(d, "sonata2-env-wayland-9")))

    def test_wayfire_reload_keeps_the_running_plugin_list(self):
        """`sonata2 restart` rewrites the live Wayfire config in place but keeps
        the plugins line the session started with (a new plugin can abort Wayfire)."""
        run_dir, cfg_home = tempfile.mkdtemp(), tempfile.mkdtemp()
        run_cfg = os.path.join(run_dir, "sonata2-wayfire.ini")
        write(run_cfg, "[core]\nplugins = a b\nvalue = 1\n")

        def fake_run(cmd, check=False):
            write(cmd[3], "[core]\nplugins = a b newplugin\nvalue = 2\n")
            return types.SimpleNamespace(returncode=0)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": run_dir, "XDG_CONFIG_HOME": cfg_home}), \
                mock.patch("subprocess.run", side_effect=fake_run):
            main._reload_wayfire_config()
        with open(run_cfg) as f:
            self.assertEqual(f.read(), "[core]\nplugins = a b\nvalue = 2\n")
        self.assertFalse(os.path.exists(run_cfg + ".new"))


# -- keyring.py ------------------------------------------------------------------------------
class KeyringTest(unittest.TestCase):
    def test_include_only_pam_file_gets_lines_once(self):
        """A Debian-style greetd PAM file (@include lines only) gets the three
        keyring lines appended, and a second pass changes nothing."""
        text = "#%PAM-1.0\n@include common-auth\n@include common-account\n@include common-session\n"
        new = keyring.pam_text(text)
        for _kind, line in keyring.PAM_LINES:
            self.assertEqual(new.count(line), 1)
        self.assertEqual(keyring.pam_text(new), new)

    def test_pam_ready_on_missing_file(self):
        """No greetd PAM file: not ready (doctor suggests the fix), no crash."""
        self.assertFalse(keyring.pam_ready(os.path.join(tempfile.mkdtemp(), "nope")))

    def test_cli(self):
        """`sonata2 keyring pam` refuses to run as a normal user; bad usage is exit 2."""
        with mock.patch.object(keyring.os, "geteuid", return_value=1000), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(keyring.main(["pam"]), 1)
            self.assertEqual(keyring.main([]), 2)
        out = io.StringIO()
        with mock.patch.object(keyring, "backend", return_value="gnome"), mock.patch("sys.stdout", out):
            self.assertEqual(keyring.main(["backend"]), 0)
        self.assertEqual(out.getvalue(), "gnome\n")


# -- userdata.py -----------------------------------------------------------------------------
class UserdataTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        p = mock.patch.object(userdata, "GLib", types.SimpleNamespace(get_user_data_dir=lambda: self.d))
        p.start()
        self.addCleanup(p.stop)

    def test_old_data_moves_once(self):
        """Notes in the old install folder move to sonata2-data the first time."""
        write(os.path.join(self.d, "sonata2", "notes", "n.json"), "{}")
        path = userdata.folder("notes")
        self.assertEqual(path, os.path.join(self.d, "sonata2-data", "notes"))
        self.assertTrue(os.path.exists(os.path.join(path, "n.json")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "sonata2", "notes")))

    def test_existing_data_is_never_overwritten(self):
        """When the new folder exists, the old one is left alone."""
        write(os.path.join(self.d, "sonata2", "notes", "old.json"), "{}")
        write(os.path.join(self.d, "sonata2-data", "notes", "new.json"), "{}")
        self.assertFalse(userdata.migrate("notes"))
        self.assertTrue(os.path.exists(os.path.join(self.d, "sonata2", "notes", "old.json")))
        self.assertEqual(os.listdir(userdata.folder("notes")), ["new.json"])


if __name__ == "__main__":
    unittest.main()
