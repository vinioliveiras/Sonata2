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
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

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
        # The hidden .x.lock (cross-process flock) is expected; no temp file is.
        self.assertEqual(sorted(n for n in os.listdir(self.d) if not n.endswith(".lock")), ["x.json"])

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

    def test_missing_socket_variable_is_an_error_the_greeter_handles(self):
        """No $GREETD_SOCK: a GreetdError the login screen shows, never a KeyError."""
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(greetd.GreetdError):
                greetd.Client()


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

    def test_lock_service_in_usr_lib_is_not_a_failure(self):
        """A PAM service in /usr/lib/pam.d (pam._service() accepts it) is OK for the doctor too."""
        r = doctor.Report()
        with mock.patch("os.path.exists", side_effect=lambda p: p == "/usr/lib/pam.d/system-local-login"):
            doctor.check_lock(r)
        self.assertEqual(r.rows[0][0], doctor.OK)

    def test_errors_read_from_where_keep_writes_logs(self):
        """The doctor reads the logs in $XDG_CACHE_HOME/sonata2, where `sonata2 keep` writes them."""
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

    def test_other_users_clicked_while_logging_in(self):
        """A login that succeeds finishes (saves the user) even if "Other Users"
        was clicked while the spinner turned."""
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


# -- fixes from the review (regression tests) ----------------------------------------------------
class Inline:
    """threading.Thread stand-in: runs the target at once."""
    def __init__(self, target, daemon=None):
        self.target = target

    def start(self):
        self.target()


def drain():
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)


class GreeterErrorsTest(GreeterLoginTest):
    def test_unexpected_error_in_the_login_worker_ends_the_spinner(self):
        """Any exception in the login thread (not only GreetdError/OSError) brings the field back."""
        self.g._pick(self.g.users[0])
        self.g.entry.set_text("pw")
        with mock.patch.object(G.threading, "Thread", Inline), \
                mock.patch.object(G.greetd, "login", side_effect=ValueError("bad reply")):
            self.g._login()
            drain()
        self.assertEqual(self.g.slot.get_visible_child_name(), "field")
        self.assertTrue(self.g.entry.get_sensitive())
        self.assertIn("bad reply", self.g.hint.get_label())
        self.assertTrue(self.g.links.get_sensitive())

    def test_other_users_is_disabled_while_logging_in(self):
        """The links row (Other Users, session) can't be used mid-login."""
        self.g._pick(self.g.users[0])
        self.g.entry.set_text("pw")
        with mock.patch.object(G.threading, "Thread", lambda target, daemon=None: mock.Mock()):
            self.g._login()
        self.assertFalse(self.g.links.get_sensitive())
        page = self.g.center.get_visible_child()
        self.g._pick(None)
        self.assertIs(self.g.center.get_visible_child(), page)


class LockErrorsTest(unittest.TestCase):
    def test_pam_crash_ends_the_spinner_and_keeps_the_lock(self):
        """An exception from PAM is a failed attempt: the field comes back, nothing unlocks."""
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        ls.entry = mock.Mock(get_text=lambda: "pw")
        ls.guard = mock.Mock(blocked=lambda _u: False)
        ls.spinner, ls.slot = mock.Mock(), mock.Mock()
        done = []
        ls._done = done.append
        with mock.patch.object(L.threading, "Thread", Inline), \
                mock.patch.object(L.GLib, "idle_add", lambda fn, *a: fn(*a)), \
                mock.patch.object(L.pam, "authenticate", side_effect=RuntimeError("libpam")):
            ls._check()
        self.assertEqual(done, [False])


class FakePam:
    """libpam stand-in: pam_authenticate / pam_acct_mgmt answer `auth` / `acct`."""
    def __init__(self, auth=0, acct=0):
        self.auth, self.acct, self.calls = auth, acct, []
        self._calloc = self._strdup = lambda *a: 0

    def pam_start(self, *a):
        return 0

    def pam_authenticate(self, *a):
        self.calls.append("authenticate")
        return self.auth

    def pam_acct_mgmt(self, *a):
        self.calls.append("acct_mgmt")
        return self.acct

    def pam_setcred(self, *a):
        self.calls.append("setcred")
        return 0

    def pam_end(self, *a):
        return 0


class PamAccountTest(unittest.TestCase):
    def _auth(self, **kw):
        lib = FakePam(**kw)
        with mock.patch.object(pam, "_pam", return_value=lib):
            return pam.authenticate("vini", "pw"), lib.calls

    def test_expired_or_locked_account_does_not_unlock(self):
        """The right password of an expired/locked account (pam_acct_mgmt fails) is refused."""
        self.assertEqual(self._auth(acct=13), (False, ["authenticate", "acct_mgmt"]))      # PAM_ACCT_EXPIRED
        self.assertEqual(self._auth(acct=9)[0], False)                                    # PAM_PERM_DENIED

    def test_valid_account_unlocks(self):
        """A valid account (or only the password expired) authenticates and refreshes credentials."""
        self.assertEqual(self._auth(), (True, ["authenticate", "acct_mgmt", "setcred"]))
        self.assertTrue(self._auth(acct=pam.PAM_NEW_AUTHTOK_REQD)[0])

    def test_wrong_password_skips_the_account_check(self):
        """A wrong password ends there."""
        self.assertEqual(self._auth(auth=7), (False, ["authenticate"]))


class KeyringRollbackTest(unittest.TestCase):
    def test_rollback_frees_the_secrets_name_before_keepassxc(self):
        """A failed switch stops gnome-keyring before KeePassXC is started again."""
        calls = []
        rec = lambda name, ret=True: (lambda *a: (calls.append(name), ret)[1])     # noqa: E731

        def boom(_items):
            raise RuntimeError("no")
        ops = {k: rec(k) for k in ("auth", "write_pam", "quit_kp", "kp_service", "start_gnome", "stop_gnome",
                                   "start_kp", "set_backend")}
        ops.update(pam_ready=rec("pam_ready", True), read=rec("read", []), write=boom)
        with self.assertRaises(RuntimeError):
            keyring.switch_to_gnome("pw", ops=ops)
        self.assertLess(calls.index("stop_gnome"), calls.index("start_kp"))

    def test_unpam_removes_only_sonatas_lines(self):
        """greeter-setup.sh revert: Sonata's keyring lines go, the rest (and the backup) too."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "greetd")
        orig = "#%PAM-1.0\nauth include system-login\nsession include system-login\n"
        write(path, orig)
        write(path + ".sonata-bak", orig)
        write(path, keyring.pam_text(orig))
        self.assertEqual(keyring.unwrite_pam(path), 0)
        with open(path) as f:
            self.assertEqual(f.read(), orig)
        self.assertFalse(os.path.exists(path + ".sonata-bak"))

    def test_unpam_leaves_files_sonata_never_changed(self):
        """No .sonata-bak: the user's own pam_gnome_keyring lines stay."""
        d = tempfile.mkdtemp()
        path = os.path.join(d, "greetd")
        text = keyring.pam_text("auth include system-login\n")
        write(path, text)
        keyring.unwrite_pam(path)
        with open(path) as f:
            self.assertEqual(f.read(), text)


class SelfCommandTest(unittest.TestCase):
    def test_desktop_exec_keeps_a_path_with_spaces_whole(self):
        """self_command() quotes for Exec=: a clone path with spaces/$ parses back to the same argv."""
        from gi.repository import GLib as GL
        with mock.patch.object(main, "REPO", "/home/a b/$x%"), mock.patch.dict(os.environ, {"SONATA2_LAUNCHER": ""}):
            d = tempfile.mkdtemp()
            path = os.path.join(d, "x.desktop")
            write(path, f"[Desktop Entry]\nType=Application\nName=x\nExec={main.self_command()} settings\n")
            kf = GL.KeyFile()
            kf.load_from_file(path, GL.KeyFileFlags.NONE)
            exec_line = kf.get_string("Desktop Entry", "Exec").replace("%%", "%")
            argv = GL.shell_parse_argv(exec_line)[1]
            self.assertEqual(argv, main.self_argv() + ["settings"])

    def test_autostart_spawns_argv_lists(self):
        """Autostart starts Setup / polkit / Files as argv lists (a path with spaces stays one argument)."""
        from sonata2 import autostart
        spawned = []
        argv = ["/opt/my apps/sonata2"]
        with mock.patch("sonata2.__main__.self_argv", return_value=argv), \
                mock.patch("sonata2.titlebars.apply"), mock.patch("sonata2.gtkstyle.reset_env"), \
                mock.patch("sonata2.flatpak_theme.apply"), mock.patch("sonata2.keyring.start"), \
                mock.patch("sonata2.feedback.report.crash", return_value=False), \
                mock.patch("sonata2.config.load", return_value={"done": False}), \
                mock.patch("subprocess.run", return_value=types.SimpleNamespace(returncode=1)), \
                mock.patch.object(autostart, "entries", return_value=[]), \
                mock.patch.object(autostart.GLib, "spawn_async", lambda a, **_k: spawned.append(a)):
            autostart.run()
        self.assertEqual(spawned, [argv + ["setup"], argv + ["keep", "polkit"], argv + ["files", "--background"]])

    def test_main_display_change_moves_brightness_target(self):
        """When the main display changes, the main bar's monitor (Control Center brightness) follows."""
        from sonata2.shell import layer, monitors, topbar
        win = mock.MagicMock()
        cbs = []
        with mock.patch.object(topbar, "TopBarWindow", return_value=win), \
                mock.patch.object(layer, "layer_shell", return_value=mock.MagicMock()), \
                mock.patch.object(monitors, "main", return_value="old"), \
                mock.patch.object(monitors, "ensure_refresh"), mock.patch.object(monitors, "each"), \
                mock.patch.object(monitors, "on_main_changed", cbs.append), \
                mock.patch("sonata2.shell.capture.Capture"), mock.patch.object(GLib, "timeout_add"), \
                mock.patch.object(GLib, "timeout_add_seconds"):
            main.run_topbar(mock.MagicMock(), types.SimpleNamespace(preview=False, menu=-1), None)
            cbs[0]("new")
        self.assertEqual(win.bar.monitor, "new")


class IntroRaceTest(unittest.TestCase):
    def test_marker_removed_while_the_monitor_starts_does_not_wait_the_timeout(self):
        """The marker gone between the check and the monitor: the callback still comes at once."""
        from sonata2.shell import intro
        d = tempfile.mkdtemp()
        mark = os.path.join(d, "intro")
        write(mark)
        hits = []
        real = intro.Gio.File.new_for_path

        def racing(path):
            os.unlink(mark)                   # deleted just before the monitor exists
            return real(path)
        with mock.patch.object(intro, "MARK", mark), mock.patch.object(intro.Gio.File, "new_for_path", racing), \
                mock.patch.object(intro, "TIMEOUT_S", 60):
            intro.wait(lambda: hits.append(1))
            end = GLib.get_monotonic_time() + 300_000
            while not hits and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
        self.assertEqual(hits, [1])


class PortalCacheTest(unittest.TestCase):
    def test_read_serves_the_watched_values(self):
        """Settings Read/ReadAll answer from the values the watchers keep, without re-reading files."""
        p = portal.Portal.__new__(portal.Portal)
        p._last = {"org.gnome.desktop.interface": {"color-scheme": GLib.Variant("s", "prefer-dark")}}
        inv = mock.Mock()
        with mock.patch.object(portal, "settings_values", side_effect=AssertionError("rebuilt")):
            p._settings_call(None, None, None, None, "Read",
                             GLib.Variant("(ss)", ("org.gnome.desktop.interface", "color-scheme")), inv)
        self.assertEqual(inv.return_value.call_args[0][0].unpack(), ("prefer-dark",))

    def test_settings_values_reads_system_json_once(self):
        """One build of the values loads system.json once, not once per key."""
        from sonata2 import prefs
        with mock.patch.object(prefs, "_load", wraps=prefs._load) as load:
            portal.settings_values()
        self.assertEqual(load.call_count, 1)

    def test_prefs_defaults_computed_once(self):
        """prefs computes the default wallpaper once at import (it was twice)."""
        import importlib
        from sonata2 import prefs, wallpapers
        with mock.patch.object(wallpapers, "default_uris", wraps=wallpapers.default_uris) as uris:
            importlib.reload(prefs)
        importlib.reload(prefs)
        self.assertEqual(uris.call_count, 1)


class DoctorPamTest(unittest.TestCase):
    def test_greeter_revert_undoes_keyring_and_quiet_console(self):
        """tools/greeter-setup.sh revert also takes out the keyring PAM lines and the quiet console."""
        with open(os.path.join(main.REPO, "tools", "greeter-setup.sh")) as f:
            text = f.read()
        revert = text[text.index('if [ "$ACTION" = revert ]'):text.index("exit 0")]
        self.assertIn("keyring unpam", revert)
        self.assertIn("quiet-console.sh\" revert", revert)

    def test_installer_never_adds_the_mount_rule_unasked(self):
        """install.sh: the passwordless-mount polkit rule is opt-in (default No; --yes doesn't add it)."""
        with open(os.path.join(main.REPO, "install.sh")) as f:
            text = f.read()
        self.assertIn('ask_no() { [ "$YES" = 1 ] && return 1;', text)
        block = text[text.index("MOUNT_RULES="):text.index("# -- Sonata's login screen")]
        self.assertIn('[ "$MOUNTRULE" = 1 ] || ask_no ', block)
        self.assertIn("--mount-without-password) MOUNTRULE=1", text)


try:
    gi.require_version("Polkit", "1.0")
    from gi.repository import Polkit
    from sonata2.shell import polkit as PK
except (ImportError, ValueError):
    PK = None


@unittest.skipIf(PK is None, "no polkit introspection data")
class PolkitAgentTest(unittest.TestCase):
    def _agent(self):
        a = PK.Agent.__new__(PK.Agent)
        a.app, a.dialogs, a.polkitd = None, {}, ":1.5"
        return a

    def test_only_polkitd_may_begin_authentication(self):
        """Another bus client calling BeginAuthentication gets an error and no dialog."""
        a, inv = self._agent(), mock.Mock()
        params = GLib.Variant("(sssa{ss}sa(sa{sv}))", ("x", "m", "", {}, "c1",
                                                      [("unix-user", {"uid": GLib.Variant("u", 1000)})]))
        with mock.patch.object(PK, "AuthDialog") as dlg:
            a._call(None, ":1.99", None, None, "BeginAuthentication", params, inv)
        inv.return_dbus_error.assert_called_once()
        self.assertIn("NotAuthorized", inv.return_dbus_error.call_args[0][0])
        dlg.assert_not_called()
        with mock.patch.object(PK, "AuthDialog") as dlg:
            a._call(None, ":1.5", None, None, "BeginAuthentication", params, mock.Mock())
        dlg.assert_called_once()

    def test_identity_without_uid_is_never_root(self):
        """A unix-user without a uid is skipped, not taken as uid 0."""
        self.assertIsNone(PK._identity("unix-user", {}))
        self.assertIsNone(PK._identity("unix-group", {}))
        self.assertEqual(PK._identity("unix-user", {"uid": 1000}).get_uid(), 1000)


def _app(name):
    app = Adw.Application(application_id=f"io.github.vinioliveiras.sonata2.test.{name}")
    app.register(None)
    return app


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipIf(PK is None, "no polkit introspection data")
class PolkitDialogTest(unittest.TestCase):
    def test_avatar_follows_the_chosen_account(self):
        """Picking another account in the pop-up changes the picture next to it."""
        from sonata2 import ui
        Adw.init()
        ui.setup()
        with mock.patch.object(PK.layer, "overlay_fullscreen", return_value=True):
            d = PK.AuthDialog(_app("pkav"), "m", [Polkit.UnixUser.new(os.getuid()), Polkit.UnixUser.new(0)],
                              "c", lambda ok: None)
        first = d.face
        other = 1 - d.index
        d.account.set_selected(other)
        self.assertIsNot(d.face, first)
        self.assertIs(d.face.get_parent(), d.who)
        self.assertIsNone(first.get_parent())
        d.finish(False)


class AnimationTests(unittest.TestCase):
    """Every main action of the session area animates."""

    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def _greeter(self):
        d = tempfile.mkdtemp()
        for name, value in (("STATE", os.path.join(d, "state.json")), ("WAITS", os.path.join(d, "waits.json")),
                            ("users", lambda: [G.User("vini", "Vini"), G.User("ana", "Ana")]),
                            ("sessions", lambda: [G.Session("sonata", "Sonata", ["sonata"], "Sonata")])):
            p = mock.patch.object(G, name, value)
            p.start()
            self.addCleanup(p.stop)
        g = G.Greeter(_app("anigreeter"))
        self.addCleanup(lambda: [w.destroy() for w in g.windows])
        return g

    def test_login_screen_fades_in_and_user_switch_crossfades(self):
        """Greeter: the screen fades in; picking a user cross-fades to a rising password page."""
        g = self._greeter()
        over = g.windows[0].get_child()
        self.assertTrue(over.has_css_class("gr-fade-in"))
        self.assertEqual(g.center.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        self.assertGreater(g.center.get_transition_duration(), 0)
        g._pick(g.users[0])
        self.assertTrue(g.center.get_visible_child().has_css_class("gr-rise"))
        settle(30)
        self.assertTrue(g.center.get_transition_running())

    def test_login_spinner_crossfade_and_leave(self):
        """Greeter login: the field cross-fades to the spinner; success fades the column and bar away."""
        g = self._greeter()
        g._pick(g.users[0])
        g.entry.set_text("sonata")
        self.assertEqual(g.slot.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        with mock.patch.object(G.threading, "Thread", Inline), mock.patch.object(G.GLib, "timeout_add"):
            g._login()
            self.assertEqual(g.slot.get_visible_child_name(), "progress")
            drain()
        self.assertTrue(g.column.has_css_class("gr-leave"))
        self.assertTrue(g.power.has_css_class("gr-leave"))

    def test_wrong_password_shakes(self):
        """A failed login shakes the field (CSS animation class)."""
        g = self._greeter()
        g._pick(g.users[0])
        with mock.patch.object(g.guard, "failed"):
            g._failed(greetd.GreetdError("auth_error", ""), "vini")
        drain()                                   # (added on the next idle: restarts the animation)
        self.assertTrue(g.entry.has_css_class("shake"))

    def test_lock_screen_in_and_out(self):
        """Lock screen: fades in, the column rises; unlocking fades it away before unlocking."""
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        ls.app, ls.lock, ls.texture, ls.windows = _app("anilock"), mock.Mock(), None, []
        mon = Gdk.Display.get_default().get_monitors().get_item(0)
        ls._window(mon, primary=True)
        self.addCleanup(lambda: [w.destroy() for w in ls.windows])
        self.assertTrue(ls.windows[0].get_child().has_css_class("gr-fade-in"))
        self.assertTrue(ls.column.has_css_class("gr-rise"))
        self.assertEqual(ls.slot.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        ls.guard = mock.Mock()
        timers = []
        with mock.patch.object(L.GLib, "timeout_add", lambda ms, fn: timers.append(ms)):
            ls._done(True)
        self.assertTrue(ls.column.has_css_class("gr-leave"))
        self.assertTrue(ls.power.has_css_class("gr-leave"))
        self.assertTrue(timers and timers[0] > 0)          # unlock only after the fade
        ls.lock.unlock.assert_not_called()

    def test_polkit_dialog_animates_in(self):
        """The password sheet scales in (auth-in keyframes on .auth-panel)."""
        if PK is None:
            self.skipTest("no polkit introspection data")
        with mock.patch.object(PK.layer, "overlay_fullscreen", return_value=True):
            d = PK.AuthDialog(_app("anipk"), "m", [Polkit.UnixUser.new(os.getuid())], "c", lambda ok: None)
        panel = d.win.get_child()
        self.assertTrue(panel.has_css_class("auth-panel"))
        from sonata2.ui import theme
        self.assertIn("animation: auth-in", theme._templates["polkit-agent"][0])
        d.finish(False)

    def test_setup_assistant_pages_slide_and_finish_fades(self):
        """Setup Assistant: pages slide (Stack transition), Get Started fades the window (tick callback)."""
        from sonata2.shell import setup as S
        with mock.patch.object(S.layer, "overlay_fullscreen", return_value=True):
            a = S.SetupAssistant(_app("anisetup"))
        self.addCleanup(a.win.destroy)
        self.assertGreater(a.stack.get_transition_duration(), 0)
        a._go(1)
        self.assertEqual(a.stack.get_transition_type(), Gtk.StackTransitionType.SLIDE_LEFT)
        settle(30)
        self.assertTrue(a.stack.get_transition_running())
        with mock.patch.object(S.config, "update"), mock.patch.object(a.win, "add_tick_callback") as tick:
            a.finish()
        tick.assert_called_once()
        self.assertTrue(a.stack.has_css_class("gr-leave"))


if __name__ == "__main__":
    unittest.main()
