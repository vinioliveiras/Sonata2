"""Screen Sharing (backend/screenshare.py, Settings > Sharing). Vini wanted
remote access; Chrome Remote Desktop can't show a Wayfire session.
Run: xvfb-run python3 -m unittest tests.test_screenshare"""
import os
import stat
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.makedirs(os.path.join(os.environ["XDG_CACHE_HOME"], "sonata2"), exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.backend import screenshare as S  # noqa: E402


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeProc:
    n = 0

    def __init__(self, cmd, **_k):
        self.cmd, self.alive = cmd, True
        FakeProc.n += 1
        self.pid = 999999

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class ConfigTest(unittest.TestCase):
    def test_always_a_password_and_private_files(self):
        path = S.write_config()
        text = open(path).read()
        user, pw = S.credentials()
        self.assertIn("enable_auth=true\n", text)
        self.assertIn(f"username={user}\n", text)
        self.assertIn(f"password={pw}\n", text)
        self.assertRegex(pw, r"^[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}$")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(S.credentials(), (user, pw))                       # kept
        self.assertNotEqual(S.new_password(), pw)

    def test_rsa_key_when_ssh_keygen_is_there(self):
        if not __import__("shutil").which("ssh-keygen"):
            self.skipTest("no ssh-keygen")
        text = open(S.write_config()).read()
        self.assertIn("rsa_private_key_file=", text)                       # encrypted (RSA-AES)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        config.save(S.NAME, {"screen": False})
        self.p = [mock.patch.object(S, "installed", return_value=True),
                  mock.patch("subprocess.Popen", side_effect=FakeProc),
                  mock.patch("gi.repository.GLib.child_watch_add")]
        for p in self.p:
            p.start()
        self.s = S.ScreenSharing()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_on_off_display_and_new_password(self):
        self.s.apply()
        self.assertIsNone(self.s.proc)                                       # off: nothing runs
        config.save(S.NAME, {"screen": True, "output": "HDMI-A-1"})
        self.s.apply()
        cmd = self.s.proc.cmd
        self.assertEqual(cmd[0], "wayvnc")
        self.assertEqual(cmd[-2:], ["-o", "HDMI-A-1"])
        first = self.s.proc
        self.s.apply()
        self.assertIs(self.s.proc, first)                                    # same settings: left alone
        config.save(S.NAME, {"screen": True, "output": "HDMI-A-1", "rev": 1})
        self.s.apply()
        self.assertIsNot(self.s.proc, first)                                 # a new password: started again
        self.assertFalse(first.alive)
        config.save(S.NAME, {"screen": False})
        last = self.s.proc
        self.s.apply()
        self.assertIsNone(self.s.proc)
        self.assertFalse(last.alive)

    def test_started_again_when_it_quits(self):
        config.save(S.NAME, {"screen": True})
        self.s.apply()
        proc = self.s.proc
        with mock.patch("gi.repository.GLib.timeout_add") as later:
            self.s._ended(proc.pid, 1, proc)
        self.assertEqual(later.call_args[0][0], S.RESTART_MS[0])
        later.call_args[0][1]()
        self.assertIsNotNone(self.s.proc)

    def test_never_without_wayvnc(self):
        config.save(S.NAME, {"screen": True})
        with mock.patch.object(S, "installed", return_value=False):
            self.s.apply()
        self.assertIsNone(self.s.proc)


class WiringTest(unittest.TestCase):
    def test_menu_bar_runs_it_and_settings_shows_it(self):
        root = os.path.dirname(os.path.dirname(S.__file__))
        top = open(os.path.join(root, "shell", "topbar.py")).read()
        self.assertIn("ScreenSharing()", top)
        st = open(os.path.join(root, "settings", "app.py")).read()
        page = st[st.index("def _page_sharing"):st.index("def _page_accessibility")]
        self.assertIn("_screen_sharing_group()", page)
        self.assertIn("Never open port 5900", page)
        inst = open(os.path.join(root, "..", "install.sh")).read()
        self.assertIn("wayvnc", inst)


if __name__ == "__main__":
    unittest.main()
