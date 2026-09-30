"""Logs (sonata2/logs.py): small on normal installs, detailed on demand.
Run: python3 -m unittest tests.test_logs"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ.pop("SONATA_DEBUG", None)

from sonata2 import fullscreen, logs  # noqa: E402


class LogsTest(unittest.TestCase):
    def setUp(self):
        self._dev = logs.dev_install
        logs.dev_install = lambda: False            # the test checkout may be a git clone
        logs.set_verbose(False)

    def tearDown(self):
        logs.dev_install = self._dev
        logs.set_verbose(False)
        os.environ.pop("SONATA_DEBUG", None)

    def test_normal_install_is_quiet(self):
        self.assertFalse(logs.verbose())
        logs.set_verbose(True)                      # Settings > About
        self.assertTrue(logs.verbose())
        logs.set_verbose(False)
        os.environ["SONATA_DEBUG"] = "1"
        self.assertTrue(logs.verbose())

    def test_window_log_only_when_detailed(self):
        view = {"id": 1, "app-id": "x", "geometry": {}}
        fullscreen.log_view("view-mapped", view)
        self.assertFalse(os.path.exists(fullscreen.LOG))
        logs.set_verbose(True)
        os.makedirs(os.path.dirname(fullscreen.LOG), exist_ok=True)
        fullscreen.log_view("view-mapped", view)
        self.assertTrue(os.path.exists(fullscreen.LOG))

    def test_trim_keeps_the_newest_half(self):
        path = os.path.join(tempfile.mkdtemp(), "x.log")
        with open(path, "w") as f:
            f.writelines(f"line {i}\n" for i in range(20000))
        logs.trim(path, cap=10000)
        size = os.path.getsize(path)
        self.assertLess(size, 10000)
        with open(path) as f:
            text = f.read()
        self.assertIn("line 19999", text)
        self.assertNotIn("line 1\n", text)


if __name__ == "__main__":
    unittest.main()
