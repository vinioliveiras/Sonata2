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

    def test_same_version_is_not_an_update(self):
        """The release flow tags vX.Y.Z-alpha for __version__ X.Y.Z-alpha: the
        same version written with v/V, spaces or an explicit .0 must never be
        offered as an update (Sonata would ask to update to itself forever)."""
        for tag in ("v0.19.2-alpha", "V0.19.2-alpha", " v0.19.2-alpha ", "0.19.2-alpha.0", "v0.19.2-Alpha",
                    "0.19.2-a"):
            self.assertFalse(S.newer(tag, "0.19.2-alpha"), tag)
        self.assertFalse(S.newer("v0.19.2", "0.19.2"))
        self.assertFalse(S.newer("v0.19", "0.19.0"))

    def test_pre_releases_climb_to_the_release(self):
        """alpha < alpha.2 < beta < rc < the release itself, and a numbered
        pre-release without a dot (alpha2) counts the same."""
        self.assertTrue(S.newer("v0.19.2-alpha.2", "0.19.2-alpha"))
        self.assertTrue(S.newer("v0.19.2-alpha2", "0.19.2-alpha.1"))
        self.assertTrue(S.newer("v0.19.2-beta", "0.19.2-alpha.9"))
        self.assertTrue(S.newer("v0.19.2-rc.1", "0.19.2-beta.3"))
        self.assertTrue(S.newer("v0.19.2", "0.19.2-rc.4"))
        self.assertFalse(S.newer("v0.19.2-beta", "0.19.2"))           # the release never goes back to a beta
        self.assertFalse(S.newer("v0.19.2-alpha", "0.19.2-beta"))

    def test_numbers_compare_as_numbers(self):
        """0.19.10 is after 0.19.9 (as text it sorts before), and the next
        version's alpha is after this version's release."""
        self.assertTrue(S.newer("v0.19.10-alpha", "0.19.9-alpha"))
        self.assertTrue(S.newer("v0.20.0-alpha", "0.19.2"))
        self.assertTrue(S.newer("v1.0.0-alpha", "0.99.99"))
        self.assertFalse(S.newer("v0.9.0", "0.19.2-alpha"))

    def test_junk_tags_are_never_newer(self):
        """A tag without a version (or none at all) is never an update."""
        for tag in ("", None, "latest", "nightly"):
            self.assertFalse(S.newer(tag, "0.19.2-alpha"), tag)

    def test_running_version_parses(self):
        """__version__ itself (what newer() compares against by default) is a
        version the comparison understands."""
        from sonata2 import __version__
        self.assertGreater(S.parse_version(__version__)[:3], (0, 0, 0))
        self.assertFalse(S.newer(__version__))
        self.assertFalse(S.newer("v" + __version__))

    def test_code_version_is_the_published_tags(self):
        """The self-updater compares the release's tag with __version__: a
        release published as v0.19.1-alpha while the code said 0.20.0-alpha
        (and CHANGELOG.md with it) left the two apart. Every release tag has
        its CHANGELOG entry, the newest entry is __version__, and no tag is
        newer than the code."""
        import re
        import shutil
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not shutil.which("git") or not os.path.exists(os.path.join(root, ".git")):
            self.skipTest("not a clone")
        r = subprocess.run(["git", "-C", root, "tag", "--list", "v*"], capture_output=True, text=True)
        tags = r.stdout.split()
        if r.returncode != 0 or not tags:
            self.skipTest("no release tags here")
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
            released = re.findall(r"(?m)^## (\S+) -- ", f.read())
        self.assertEqual(released[0], S.__version__)
        for tag in tags:
            # (v0.16.0 was 0.16.0-alpha's tag, published without its suffix)
            self.assertTrue(tag[1:] in released or tag[1:] + "-alpha" in released, tag)
        self.assertFalse(S.newer(max(tags, key=S.parse_version)))

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
        self.assertIn("git -C /home/v/GitHub/sonata2 pull --ff-only", cmd)
        self.assertIn("restart", cmd)
        with mock.patch.object(S, "is_clone", return_value=False):
            self.assertTrue(S.terminal_command("v0.2.0").endswith("self-update v0.2.0"))

    def test_tag_and_path_never_become_shell_code(self):
        """Review: the GitHub tag went unquoted into a shell line (git refs may hold ; $ `)."""
        import shlex
        with mock.patch.object(S, "is_clone", return_value=False):
            cmd = S.terminal_command("v1;touch /tmp/x")
        self.assertEqual(shlex.split(cmd)[-2:], ["self-update", "v1;touch /tmp/x"])
        with mock.patch.object(S, "install_root", return_value="/home/v/my clone'x"), \
                mock.patch.object(S, "is_clone", return_value=True):
            cmd = S.terminal_command()
        self.assertEqual(shlex.split(cmd)[2], "/home/v/my clone'x")

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

    def test_sonata_apart_from_the_system_update(self):
        """Vini: Sonata's update is its own (About > Sonata Update), not one of
        the system's sources; its notification opens it."""
        from sonata2.backend import updates
        self.assertNotIn("sonata", [s.id for s in updates.sources()])
        from sonata2.settings import app as st
        self.assertEqual(st.parts_of("about"), ("about", "sonataupdate", "updates"))
        self.assertEqual(st.section_of("sonataupdate"), "about")
        src = open(updates.__file__.replace("backend/updates.py", "shell/updatenotify.py")).read()
        self.assertIn('"--page", "sonataupdate"', src)

    def test_sonata_update_group_states(self):
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from unittest import mock
        from sonata2.settings import app as st
        win = st.Settings.__new__(st.Settings)
        with mock.patch.object(st.system, "run_async"):
            g = st.Settings._page_sonataupdate(win)[0]
        g.found({"tag": "v99.0.0", "url": "https://example.invalid", "tarball": ""})
        self.assertEqual(g.status_row.get_title(), "Sonata 99.0.0 is available")
        g.found({"tag": "v0.0.1", "url": "", "tarball": ""})
        self.assertEqual(g.status_row.get_title(), "Sonata is up to date")
        g.found(None)
        self.assertEqual(g.status_row.get_title(), "Couldn't check for a new Sonata")


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
