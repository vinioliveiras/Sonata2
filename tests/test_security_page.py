"""Vini (security review): Security & Privacy lacked the firewall, disk
encryption (macOS: Firewall, FileVault) and GNOME's USB protection."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2.backend import security as S
from sonata2.backend import usbprotect as U

LSBLK_LUKS = {"blockdevices": [{"name": "nvme0n1", "type": "disk", "fstype": None, "mountpoints": [None],
                                "children": [{"name": "p1", "type": "part", "fstype": "vfat", "mountpoints": ["/boot"]},
                                             {"name": "p2", "type": "part", "fstype": "crypto_LUKS", "mountpoints": [None],
                                              "children": [{"name": "root", "type": "crypt", "fstype": "btrfs",
                                                            "mountpoints": ["/", "/home"]}]}]}]}
LSBLK_PLAIN = {"blockdevices": [{"name": "sda", "type": "disk", "mountpoints": [None],
                                 "children": [{"name": "sda1", "type": "part", "fstype": "ext4", "mountpoints": ["/"]}]}]}


class FirewallTest(unittest.TestCase):
    def test_ufw_conf(self):
        f = tempfile.NamedTemporaryFile("w", delete=False)
        f.write("# comment\nENABLED=yes\nLOGLEVEL=low\n")
        f.close()
        self.assertTrue(S._ufw_enabled(f.name))
        with open(f.name, "w") as g:
            g.write("ENABLED=no\n")
        self.assertFalse(S._ufw_enabled(f.name))
        self.assertFalse(S._ufw_enabled("/nonexistent"))

    def test_kind(self):
        with mock.patch.object(S.shutil, "which", side_effect=lambda c: "/usr/bin/ufw" if c == "ufw" else None), \
                mock.patch.object(S, "_ufw_enabled", return_value=True), mock.patch.object(S, "_active", return_value=True):
            self.assertEqual(S.firewall(), {"kind": "ufw", "on": True})
        with mock.patch.object(S.shutil, "which", return_value=None):
            self.assertEqual(S.firewall(), {"kind": None, "on": False})

    def test_commands_go_through_pkexec(self):
        for kind in ("ufw", "firewalld"):
            for on in (True, False):
                self.assertEqual(S.firewall_command(kind, on)[0], "pkexec")
        self.assertEqual(S.firewall_command(None, True), [])


class EncryptionTest(unittest.TestCase):
    def test_luks(self):
        self.assertEqual(S.encryption(LSBLK_LUKS), {"/": True, "/home": True})
        self.assertEqual(S.encryption(LSBLK_PLAIN), {"/": False, "/home": None})
        self.assertEqual(S.encryption({}), {"/": None, "/home": None})


class UsbTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.d})
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_block_and_restore(self):
        calls = []
        with mock.patch.object(U, "available", return_value=True), \
                mock.patch.object(U, "_set", side_effect=lambda v: (calls.append(v), "allow")[1]):
            self.assertTrue(U.block())
            self.assertFalse(U.block())                         # already blocked: the first policy kept
            self.assertTrue(U.restore())
        self.assertEqual(calls, ["block", "allow"])
        self.assertFalse(os.path.exists(U.marker()))

    def test_never_left_blocked(self):
        with open(U.marker(), "w") as f:
            f.write("block")                                    # a broken previous value
        calls = []
        with mock.patch.object(U, "_set", side_effect=lambda v: (calls.append(v), "block")[1]):
            self.assertTrue(U.restore_if_stale(lambda: False))
        self.assertEqual(calls, ["apply-policy"])
        self.assertFalse(U.restore_if_stale(lambda: False))     # nothing to do

    def test_still_locked_left_alone(self):
        with open(U.marker(), "w") as f:
            f.write("allow")
        with mock.patch.object(U, "_set") as st:
            self.assertFalse(U.restore_if_stale(lambda: True))
        st.assert_not_called()

    def test_without_usbguard(self):
        with mock.patch.object(U, "available", return_value=False), mock.patch.object(U, "_set") as st:
            self.assertFalse(U.block())
        st.assert_not_called()

    def test_wired(self):
        import inspect
        from sonata2.shell import idlelock, lock, topbar
        self.assertTrue(idlelock.DEFAULTS["usb_protection"])
        src = inspect.getsource(lock.LockScreen)
        self.assertIn("usbprotect.restore()", src)
        self.assertIn("usbprotect.block", src)
        self.assertIn("restore_if_stale", inspect.getsource(topbar.TopBarWindow.__init__))


class PageTest(unittest.TestCase):
    def test_page_has_the_groups(self):
        import inspect
        from sonata2.settings import app
        src = inspect.getsource(app.SettingsWindow._page_privacy) if hasattr(app, "SettingsWindow") else \
            inspect.getsource(app)
        self.assertIn("_firewall_group()", src)
        self.assertIn("_encryption_group()", src)
        self.assertIn("_usb_group(sec)", src)


if __name__ == "__main__":
    unittest.main()
