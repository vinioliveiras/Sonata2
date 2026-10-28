"""Files with locked (encrypted) folders: Lock Folder… in a folder's menu
encrypts it; opening it asks for the password and goes in; Lock Now closes
it (stepping out first); Unlock Folder… decrypts it for good. Real gocryptfs.
Run: dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_files_vault"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.test_vault import _fuse  # noqa: E402  (first: it sets HOME too)

HOME = tempfile.mkdtemp(prefix="fv-home-")
os.environ["HOME"] = HOME
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="fv-run-")
for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[v] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from sonata2 import ui, vault as V  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipUnless(_fuse(), "needs gocryptfs and FUSE")
class FilesVaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.filesvault")
        cls.app.register(None)

    def setUp(self):
        from sonata2.files.window import FilesWindow
        self.dir = tempfile.mkdtemp(dir=HOME)
        self.path = os.path.join(self.dir, "Private")
        os.makedirs(self.path)
        with open(os.path.join(self.path, "a.txt"), "w") as f:
            f.write("hello")
        self.p = [mock.patch.object(ui.dialog, "ask_password",
                                    side_effect=lambda h, b, on_ok, **kw: on_ok("pw")),
                  mock.patch("sonata2.backend.system.run_async",
                             side_effect=lambda fn, done=None, *a: done(fn(*a)) if done else fn(*a))]
        for p in self.p:
            p.start()
        self.win = FilesWindow(self.app, GLib.filename_to_uri(self.dir))
        self.win.present()
        settle(500)

    def tearDown(self):
        V.close(self.path)
        self.win.destroy()
        for p in self.p:
            p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def here(self):
        return Gio.File.new_for_uri(self.win.history[self.win.pos]).get_path()

    def test_lock_open_lock_now_unlock(self):
        self.win.lock_vault(self.path)
        settle(300)
        self.assertTrue(V.is_vault(self.path))
        self.assertEqual(os.listdir(self.path), [])
        self.win.go(GLib.filename_to_uri(self.path))                 # opening: the password, then in
        settle(500)
        self.assertTrue(V.is_open(self.path))
        self.assertEqual(self.here(), self.path)
        self.win.close_vault(self.path)                             # Lock Now: out first, then closed
        settle(600)
        self.assertEqual(self.here(), self.dir)
        self.assertFalse(V.is_open(self.path))
        self.win.unlock_vault(self.path)
        settle(800)
        self.assertFalse(V.is_vault(self.path))
        with open(os.path.join(self.path, "a.txt")) as f:
            self.assertEqual(f.read(), "hello")

    def test_closed_one_drawn_with_a_padlock(self):
        from sonata2.files import views
        info = Gio.File.new_for_path(self.path).query_info("standard::*", Gio.FileQueryInfoFlags.NONE, None)
        with mock.patch("sonata2.files.folder.file_of", return_value=Gio.File.new_for_path(self.path)):
            self.assertFalse(views._vault_closed(info))
            V.lock(self.path, "pw")
            self.assertTrue(views._vault_closed(info))
            V.open(self.path, "pw")
            self.assertFalse(views._vault_closed(info))             # open: a plain folder

    def test_menu_items(self):
        info = Gio.File.new_for_path(self.path).query_info("standard::*", Gio.FileQueryInfoFlags.NONE, None)
        with mock.patch("sonata2.files.window.file_of", return_value=Gio.File.new_for_path(self.path)):
            labels = [i.label for i in self.win._vault_items(info)]
            self.assertEqual(labels, ["Lock Folder…"])
            V.lock(self.path, "pw")
            self.assertEqual([i.label for i in self.win._vault_items(info)], ["Unlock Folder…"])
            V.open(self.path, "pw")
            self.assertEqual([i.label for i in self.win._vault_items(info)], ["Lock Now", "Unlock Folder…"])


if __name__ == "__main__":
    unittest.main()
