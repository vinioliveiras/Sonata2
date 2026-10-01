"""Sonata's own updates from GitHub releases. Run:
python3 -m unittest tests.test_selfupdate"""
import io
import os
import tarfile
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

from sonata2 import config  # noqa: E402
from sonata2.backend import selfupdate as S  # noqa: E402


class VersionTest(unittest.TestCase):
    def test_order(self):
        order = ["v0.1.0-alpha", "0.1.0-alpha.2", "v0.1.0-beta", "v0.1.0-rc.1", "v0.1.0", "v0.1.1", "v0.2.0-alpha",
                 "1.0.0"]
        self.assertEqual(sorted(order, key=S.parse_version), order)

    def test_newer(self):
        self.assertTrue(S.newer("v0.2.0", "0.1.0-alpha"))
        self.assertFalse(S.newer("v0.1.0-alpha", "0.1.0-alpha"))
        self.assertFalse(S.newer("v0.0.9", "0.1.0-alpha"))

    def test_pick(self):
        rels = [{"tag_name": "v0.1.0-alpha", "html_url": "u1"}, {"tag_name": "v0.3.0", "draft": True},
                {"tag_name": "v0.2.0-beta", "name": "Beta", "body": "notes", "html_url": "u2"}, "junk", {}]
        r = S.pick(rels)
        self.assertEqual((r["tag"], r["name"], r["body"], r["url"]), ("v0.2.0-beta", "Beta", "notes", "u2"))
        self.assertIn("tarball/v0.2.0-beta", r["tarball"])
        self.assertIsNone(S.pick([]))
        self.assertIsNone(S.pick({"message": "rate limited"}))

    def test_offline_is_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(S.latest_release())
            self.assertIsNone(S.available())


class InstallTest(unittest.TestCase):
    def test_install_args_keep_the_users_choices(self):
        with mock.patch("os.path.exists", return_value=False):
            self.assertEqual(S.install_args("/home/v/.local/share/sonata2"), ["--yes", "--no-greeter"])
        with mock.patch("os.path.exists", return_value=True):            # Sonata's login screen: kept updated
            self.assertEqual(S.install_args("/usr/local/share/sonata2"), ["--yes", "--system"])

    def test_clone_updates_with_git(self):
        with mock.patch.object(S, "install_root", return_value="/home/v/GitHub/sonata2"), \
                mock.patch.object(S, "is_clone", return_value=True):
            cmd = S.terminal_command("v0.2.0")
        self.assertIn("git -C '/home/v/GitHub/sonata2' pull --ff-only", cmd)
        self.assertIn("restart", cmd)
        with mock.patch.object(S, "is_clone", return_value=False):
            self.assertTrue(S.terminal_command("v0.2.0").endswith("self-update v0.2.0"))

    def _tarball(self, members) -> str:
        path = os.path.join(tempfile.mkdtemp(), "rel.tar.gz")
        with tarfile.open(path, "w:gz") as t:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return path

    def test_download_extracts_the_release(self):
        tb = self._tarball([("sonata2-abc/install.sh", b"#!/bin/sh\n"), ("sonata2-abc/sonata2/__init__.py", b"")])
        folder = S.download({"tag": "v9.9.9", "tarball": "file://" + tb}, say=lambda _t: None)
        self.assertTrue(os.path.exists(os.path.join(folder, "install.sh")))

    def test_download_never_writes_outside(self):
        tb = self._tarball([("sonata2-abc/install.sh", b"x"), ("../../evil.sh", b"x")])
        try:
            S.download({"tag": "v9.9.8", "tarball": "file://" + tb}, say=lambda _t: None)
        except (OSError, tarfile.TarError):
            pass
        cache = S._cache()
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(os.path.dirname(cache)), "evil.sh")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(cache), "evil.sh")))

    def test_check_line_for_software_update(self):
        from sonata2.backend import updates
        rel = {"tag": "v0.2.0", "name": "", "body": "", "url": "", "tarball": ""}
        with mock.patch.object(S, "available", return_value=rel), \
                mock.patch("builtins.print") as p:
            S.main(["--check"])
        line = p.call_args[0][0]
        self.assertEqual([(u.name, u.old, u.new) for u in updates.parse_arrow(line)],
                         [("Sonata", S.__version__, "0.2.0")])
        with mock.patch.object(S, "available", return_value=None), mock.patch("builtins.print") as p:
            S.main(["--check"])
        p.assert_not_called()                                  # up to date: nothing listed

    def test_source_is_last(self):
        from sonata2.backend import updates
        srcs = updates.sources()
        self.assertEqual(srcs[-1].id, "sonata")                # its update restarts Sonata: after the rest
        self.assertIsNone(srcs[-1].install)                    # in a terminal (sudo may be asked)


class NotifierTest(unittest.TestCase):
    def setUp(self):
        config.save(S.NAME, {})
        from sonata2.shell import updatenotify
        self.U = updatenotify

    def notifier(self):
        n = self.U.UpdateNotifier.__new__(self.U.UpdateNotifier)
        n._now, n.sent, n._id, n._sub, n.timer = (lambda: 1000), [], 0, 0, 0
        n.notify = lambda rel: n.sent.append(rel["tag"])
        return n

    def test_once_per_release(self):
        n = self.notifier()
        rel = {"tag": "v0.2.0"}
        n.found(rel)
        n.found(rel)                                           # the next check: already told
        n.found(None)
        n.found({"tag": "v0.3.0"})
        self.assertEqual(n.sent, ["v0.2.0", "v0.3.0"])
        self.assertEqual(config.load(S.NAME, S.DEFAULTS)["last_check"], 1000)

    def test_click_opens_software_update(self):
        n = self.notifier()
        n._id = 7
        from gi.repository import GLib
        with mock.patch.object(self.U, "open_updates") as op:
            n._invoked(None, None, None, None, None, GLib.Variant("(us)", (8, "default")))
            op.assert_not_called()                             # another app's notification
            n._invoked(None, None, None, None, None, GLib.Variant("(us)", (7, "default")))
            op.assert_called_once()


if __name__ == "__main__":
    unittest.main()
