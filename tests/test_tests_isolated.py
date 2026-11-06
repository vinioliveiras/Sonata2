"""The tests never reach Vini's session: a test that set a wallpaper ran
restart_sonata through the session's real launcher (SONATA2_LAUNCHER), which
in the Rust session (SONATA2_RS_SESSION=1) handed it to `sonata2-rs restart`;
that finds its processes in /proc, whoever's they are, and stopped his
wallpaper -- the desktop went grey."""
import os
import unittest
from unittest import mock

import tests


class SessionVarsTest(unittest.TestCase):
    def test_tests_restarted_the_real_wallpaper(self):
        for name in ("SONATA2_LAUNCHER", "SONATA2_RS_SESSION", "SONATA2_RS_SIDE_BY_SIDE"):
            self.assertIn(name, tests.SESSION_VARS)
            self.assertNotIn(name, os.environ)
        # and so a restart asked for in a test is this checkout's Python, never the launcher
        from sonata2.backend import system
        with mock.patch("subprocess.Popen") as popen:
            system.restart_sonata("wallpaper")
        cmd = popen.call_args[0][0]
        self.assertNotIn("/.local/bin/", cmd[0])
        self.assertEqual(cmd[1:4], ["-m", "sonata2", "restart"])


if __name__ == "__main__":
    unittest.main()
