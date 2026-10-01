"""Saved passwords: GNOME's login keyring unlocked by the login, the move
from KeePassXC (python3 -m unittest tests.test_keyring).
Regression: KeePassXC asked for its password at every login."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import config, keyring as K  # noqa: E402

GREETD = """#%PAM-1.0

auth       required     pam_securetty.so
auth       requisite    pam_nologin.so
auth       include      system-local-login
account    include      system-local-login
session    include      system-local-login
password   include      system-local-login
"""


class PamTest(unittest.TestCase):
    def test_lines_after_each_kind(self):
        new = K.pam_text(GREETD).splitlines()
        self.assertEqual(new[new.index("auth       include      system-local-login") + 1],
                         "auth       optional     pam_gnome_keyring.so")
        self.assertEqual(new[new.index("session    include      system-local-login") + 1],
                         "session    optional     pam_gnome_keyring.so auto_start")
        self.assertIn("password   optional     pam_gnome_keyring.so", new)
        self.assertEqual(K.pam_text("\n".join(new) + "\n"), "\n".join(new) + "\n")     # once only

    def test_write_keeps_a_copy(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "greetd")
        open(p, "w").write(GREETD)
        self.assertFalse(K.pam_ready(p))
        K.write_pam(p)
        self.assertTrue(K.pam_ready(p))
        self.assertEqual(open(p + ".sonata-bak").read(), GREETD)


class BackendTest(unittest.TestCase):
    def setUp(self):
        config.save("keyring", {"backend": ""})
        self.ini = os.path.join(tempfile.mkdtemp(), "keepassxc.ini")
        self.p = mock.patch.object(K, "CONFIG", self.ini)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def which(self, *have):
        return mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}" if n in have else None)

    def test_new_install_uses_the_login_keyring(self):
        with self.which("keepassxc", "gnome-keyring-daemon"):
            self.assertEqual(K.backend(), "gnome")
            self.assertFalse(K.start())                     # KeePassXC isn't started at login

    def test_keepassxc_in_use_stays_until_moved(self):
        open(self.ini, "w").write("[General]\nLastOpenedDatabases=/home/v/p.kdbx\n\n[FdoSecrets]\nEnabled=true\n")
        with self.which("keepassxc", "gnome-keyring-daemon"):
            self.assertEqual(K.backend(), "keepassxc")
            K.set_backend("gnome")
            self.assertEqual(K.backend(), "gnome")

    def test_secret_service_off_forced(self):
        open(self.ini, "w").write("[FdoSecrets]\nEnabled=true\n")
        self.assertFalse(K.enable_secret_service(self.ini, False))       # your choice stands
        self.assertTrue(K.enable_secret_service(self.ini, False, force=True))
        self.assertIn("Enabled=false", open(self.ini).read())


class SwitchTest(unittest.TestCase):
    def ops(self, **over):
        self.calls = []
        rec = lambda name, ret=True: (lambda *a: (self.calls.append((name, a)), ret)[1])     # noqa: E731
        o = dict(auth=rec("auth"), pam_ready=rec("pam_ready", False), write_pam=rec("write_pam"),
                 read=rec("read", [("Chrome Safe Storage", {"application": "chrome"}, b"k", "text/plain")]),
                 quit_kp=rec("quit_kp"), kp_service=rec("kp_service"), start_gnome=rec("start_gnome"),
                 write=lambda items: (self.calls.append(("write", items)), len(items))[1],
                 start_kp=rec("start_kp"), set_backend=rec("set_backend"))
        o.update(over)
        return o

    def names(self):
        return [c[0] for c in self.calls]

    def test_order_and_result(self):
        n = K.switch_to_gnome("pw", ops=self.ops())
        self.assertEqual(n, 1)
        self.assertEqual(self.names(), ["auth", "pam_ready", "write_pam", "read", "quit_kp", "kp_service",
                                        "start_gnome", "write", "set_backend"])
        self.assertEqual(self.calls[5], ("kp_service", (False,)))
        self.assertEqual(self.calls[-1], ("set_backend", ("gnome",)))

    def test_wrong_password_changes_nothing(self):
        with self.assertRaises(PermissionError):
            K.switch_to_gnome("bad", ops=self.ops(auth=lambda pw: False))
        self.assertEqual(self.names(), [])

    def test_failure_puts_keepassxc_back(self):
        def boom(_items):
            raise RuntimeError("no")
        with self.assertRaises(RuntimeError):
            K.switch_to_gnome("pw", ops=self.ops(write=boom))
        self.assertIn(("kp_service", (True,)), self.calls)
        self.assertIn("start_kp", self.names())
        self.assertNotIn("set_backend", self.names())


if __name__ == "__main__":
    unittest.main()
