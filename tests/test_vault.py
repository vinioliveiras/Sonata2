"""Locked (encrypted) folders in Files (Vini: gocryptfs -- unreadable without
the password in any app). Run with the real gocryptfs and FUSE when they're
there: lock, open, close, a wrong password, unlock for good, and that no
file is lost when a step fails.
Run: python3 -m unittest tests.test_vault"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

HOME = tempfile.mkdtemp(prefix="vault-home-")
os.environ["HOME"] = HOME
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="vault-run-")

from sonata2 import vault as V  # noqa: E402


def _fuse() -> bool:
    return V.available() and os.path.exists("/dev/fuse") and os.access("/dev/fuse", os.R_OK | os.W_OK)


class Rules(unittest.TestCase):
    def test_what_can_be_locked(self):
        d = os.path.join(HOME, "Docs", "Mine")
        os.makedirs(d, exist_ok=True)
        self.assertTrue(V.can_lock(d))
        self.assertFalse(V.can_lock(HOME))                        # not the home itself
        self.assertFalse(V.can_lock("/usr"))                      # nothing outside it
        self.assertFalse(V.can_lock(os.path.join(HOME, "nope")))
        self.assertEqual(V.cipher_dir(d), os.path.join(HOME, "Docs", ".Mine" + V.SUFFIX))


@unittest.skipUnless(_fuse(), "needs gocryptfs and FUSE")
class RealVault(unittest.TestCase):
    PW = "s3cret-login"

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=HOME)
        self.path = os.path.join(self.dir, "Secret")
        os.makedirs(os.path.join(self.path, "sub"))
        with open(os.path.join(self.path, "note.txt"), "w") as f:
            f.write("TOP-SECRET-CONTENT")
        with open(os.path.join(self.path, "sub", "b.bin"), "wb") as f:
            f.write(os.urandom(70000))
        os.symlink("note.txt", os.path.join(self.path, "link"))

    def tearDown(self):
        V.close(self.path)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _plain_anywhere(self, root):
        for dp, _ds, fs in os.walk(root):
            for f in fs:
                p = os.path.join(dp, f)
                if os.path.isfile(p) and not os.path.islink(p) and b"TOP-SECRET" in open(p, "rb").read():
                    return True
        return False

    def test_lock_open_close(self):
        ok, err = V.lock(self.path, self.PW)
        self.assertTrue(ok, err)
        self.assertTrue(V.is_vault(self.path))
        self.assertEqual(os.listdir(self.path), [])                       # closed: nothing to read
        self.assertFalse(self._plain_anywhere(V.cipher_dir(self.path)))   # encrypted on disk
        self.assertEqual(V.open(self.path, "wrong-one"), (False, V.WRONG))
        self.assertFalse(V.is_open(self.path))
        self.assertEqual(V.open(self.path, self.PW), (True, None))
        self.assertTrue(V.is_open(self.path))
        with open(os.path.join(self.path, "note.txt")) as f:
            self.assertEqual(f.read(), "TOP-SECRET-CONTENT")
        self.assertEqual(os.path.getsize(os.path.join(self.path, "sub", "b.bin")), 70000)
        self.assertEqual(os.readlink(os.path.join(self.path, "link")), "note.txt")
        self.assertTrue(V.close(self.path))
        self.assertEqual(os.listdir(self.path), [])

    def test_unlock_for_good(self):
        V.lock(self.path, self.PW)
        self.assertEqual(V.unlock(self.path, "wrong-one"), (False, V.WRONG))
        self.assertTrue(V.is_vault(self.path))                           # nothing changed
        self.assertEqual(V.unlock(self.path, self.PW), (True, None))
        self.assertFalse(V.is_vault(self.path))
        self.assertFalse(os.path.exists(V.cipher_dir(self.path)))
        with open(os.path.join(self.path, "note.txt")) as f:
            self.assertEqual(f.read(), "TOP-SECRET-CONTENT")

    def test_a_failed_copy_loses_nothing(self):
        with mock.patch.object(V, "_copy_into", return_value=False):
            ok, _err = V.lock(self.path, self.PW)
        self.assertFalse(ok)
        self.assertFalse(V.is_vault(self.path))
        self.assertFalse(os.path.exists(V.cipher_dir(self.path)))
        with open(os.path.join(self.path, "note.txt")) as f:
            self.assertEqual(f.read(), "TOP-SECRET-CONTENT")              # the originals: where they were

    def test_open_vault_not_locked_twice(self):
        V.lock(self.path, self.PW)
        self.assertFalse(V.can_lock(self.path))



@unittest.skipUnless(_fuse(), "needs gocryptfs and FUSE")
class CloseAll(unittest.TestCase):
    def test_open_ones_closed_with_the_session_or_the_screen(self):
        d = tempfile.mkdtemp(dir=HOME)
        a, b = os.path.join(d, "A"), os.path.join(d, "B")
        for p in (a, b):
            os.makedirs(p)
            open(os.path.join(p, "f"), "w").write("x")
            self.assertTrue(V.lock(p, "pw")[0])
            self.assertTrue(V.open(p, "pw")[0])
        self.assertEqual(sorted(V.open_vaults()), sorted([a, b]))
        V.close_all()
        self.assertEqual(V.open_vaults(), [])
        shutil.rmtree(d, ignore_errors=True)

    def test_called_at_session_end_and_lock(self):
        import inspect
        from sonata2 import __main__ as M
        from sonata2.shell import quitapps
        self.assertIn("vault.close_all", inspect.getsource(quitapps.end_session))
        self.assertIn("vault.close_all", inspect.getsource(M.run_lock))


class FilesMenu(unittest.TestCase):
    def test_files_offers_lock_now_and_unlock(self):
        import inspect
        from sonata2.files import window as W
        src = inspect.getsource(W.FilesWindow._vault_items)
        for label in ("Lock Folder…", "Lock Now", "Unlock Folder…"):
            self.assertIn(label, src)
        self.assertIn("vault.is_vault(path) and not vault.is_open(path)", inspect.getsource(W.FilesWindow.go))

if __name__ == "__main__":
    unittest.main()
