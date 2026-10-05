"""Vini (Files vs Finder): no Connect to Server. Ctrl+K: smb / sftp / WebDAV
/ FTP / NFS addresses mounted through GIO, the servers used last listed, and
Network in the sidebar when gvfs can browse it."""
import inspect
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.files import server  # noqa: E402


class AddressTest(unittest.TestCase):
    def test_normalize(self):
        N = server.normalize
        self.assertEqual(N("nas/fotos"), "smb://nas/fotos")              # Finder: smb by default
        self.assertEqual(N("\\\\nas\\fotos"), "smb://nas/fotos")         # a Windows path
        self.assertEqual(N("ssh://me@host"), "sftp://me@host")
        self.assertEqual(N(" davs://cloud.example.com/dav "), "davs://cloud.example.com/dav")
        for bad in ("", "   ", "smb://", "http://example.com", "file:///etc"):
            self.assertIsNone(N(bad), bad)

    def test_missing_backend_is_named(self):
        with mock.patch.object(server, "supported", return_value={"file", "sftp"}):
            self.assertEqual(server.why_not("sftp://h"), "")
            self.assertIn("gvfs-smb", server.why_not("smb://nas/x"))
            self.assertIn("gvfs", server.why_not("davs://h"))


class RecentTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)

    def test_recent(self):
        self.assertEqual(server.recent(), [])
        for u in ("smb://a/x", "sftp://b", "smb://a/x"):
            server.remember(u)
        self.assertEqual(server.recent(), ["smb://a/x", "sftp://b"])
        server.forget("smb://a/x")
        self.assertEqual(server.recent(), ["sftp://b"])
        for i in range(20):
            server.remember(f"smb://h{i}")
        self.assertEqual(len(server.recent()), server.RECENT_MAX)

    def test_connect_mounts_and_remembers(self):
        calls, opened, errors = [], [], []

        class FakeFile:
            def mount_enclosing_volume(self, flags, op, cancel, cb):
                calls.append(op)
                cb(self, "res")

            def mount_enclosing_volume_finish(self, res):
                return True
        with mock.patch.object(Gio.File, "new_for_uri", return_value=FakeFile()):
            server.connect("smb://nas/x", None, opened.append, errors.append)
        self.assertEqual(opened, ["smb://nas/x"])
        self.assertEqual(server.recent(), ["smb://nas/x"])
        self.assertIsInstance(calls[0], Gio.MountOperation)              # asks for a password

        class Mounted(FakeFile):
            def mount_enclosing_volume_finish(self, res):
                raise GLib.Error.new_literal(Gio.io_error_quark(), "already", Gio.IOErrorEnum.ALREADY_MOUNTED)
        with mock.patch.object(Gio.File, "new_for_uri", return_value=Mounted()):
            server.connect("sftp://h", None, opened.append, errors.append)
        self.assertEqual(opened[-1], "sftp://h")
        self.assertEqual(errors, [])

    def test_dialog(self):
        """(a fake alert: Adw.AlertDialog crashes in the headless test box)"""
        class FakeAlert:
            def set_extra_child(self, child):
                self.child = child

            def set_response_enabled(self, rid, on):
                self.enabled = on

            def close(self):
                pass
        server.remember("sftp://me@host")
        fake = FakeAlert()
        with mock.patch.object(ui.dialog, "alert", return_value=fake):
            dlg = server.dialog(None, lambda u: None)
        self.assertEqual(dlg.entry.get_text(), "sftp://me@host")       # the last one, ready
        self.assertTrue(fake.enabled)
        self.assertIsNotNone(dlg.servers)

    def test_wiring(self):
        from sonata2 import __main__ as M
        from sonata2.files import sidebar, window
        from sonata2.shell import topbar
        self.assertIn('"<Control>k", self.connect_to_server', inspect.getsource(window.FilesWindow))
        self.assertIn("server.NETWORK", inspect.getsource(sidebar.Sidebar.rebuild))
        self.assertIn("sonata:connect", inspect.getsource(M.run_files))
        self.assertIn("Connect to Server", inspect.getsource(topbar))


if __name__ == "__main__":
    unittest.main()
