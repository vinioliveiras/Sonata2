"""Vini (memory review): a shell component that leaks over a long day was
never noticed. `sonata2 keep` starts the Dock / menu bar again when it holds
more than component_limit_mb -- not counted as a crash, not a stop."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import sonata2.__main__ as M


class FakeChild:
    def __init__(self, script):
        self.script, self.pid, self.terminated = list(script), 4242, False

    def wait(self, timeout=None):
        step = self.script.pop(0)
        if step == "timeout":
            raise subprocess.TimeoutExpired("x", timeout)
        return step

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass


class KeepMemoryTest(unittest.TestCase):
    def test_limit(self):
        self.assertEqual(M.component_limit_mb(8 * 1024), 1024)
        self.assertEqual(M.component_limit_mb(30 * 1024), 1536)
        self.assertEqual(M.component_limit_mb(128 * 1024), 2048)

    def test_rss(self):
        self.assertGreater(M.rss_mb(os.getpid()), 0)
        self.assertEqual(M.rss_mb(999999999), 0)

    def test_restarted_when_too_big(self):
        first = FakeChild(["timeout", -15])           # grows past the limit, then stops on SIGTERM
        second = FakeChild([0])                       # the new one: stopped on purpose (logout)
        children = [first, second]
        d = tempfile.mkdtemp()
        with mock.patch("sonata2.logs.log_dir", return_value=d), \
                mock.patch.object(M, "share_session_env"), \
                mock.patch.object(M, "_socket_id", return_value=(1, 2, 3)), \
                mock.patch.object(M, "_same_session", return_value=True), \
                mock.patch.object(subprocess, "Popen",
                                  side_effect=lambda *a, **k: children.pop(0)), \
                mock.patch.object(M, "rss_mb", return_value=99999), \
                mock.patch("sonata2.logs.trim"), mock.patch("time.sleep"):
            self.assertEqual(M.keep(["dock"]), 0)
        self.assertTrue(first.terminated)
        self.assertEqual(children, [])                # started again
        with open(os.path.join(d, "dock.log")) as f:
            self.assertIn("restarting", f.read())

    def test_healthy_left_alone(self):
        child = FakeChild(["timeout", 0])
        with mock.patch("sonata2.logs.log_dir", return_value=tempfile.mkdtemp()), \
                mock.patch.object(M, "share_session_env"), mock.patch.object(M, "_socket_id"), \
                mock.patch.object(subprocess, "Popen", return_value=child), \
                mock.patch.object(M, "rss_mb", return_value=200), mock.patch("sonata2.logs.trim"):
            self.assertEqual(M.keep(["dock"]), 0)
        self.assertFalse(child.terminated)


if __name__ == "__main__":
    unittest.main()
