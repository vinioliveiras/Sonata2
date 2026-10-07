"""Logs (sonata2/logs.py): off by default (Vini: Settings > About > Logs,
off unless turned on) -- errors only, in memory; on: detailed, kept in
~/.cache/sonata2. Run: python3 -m unittest tests.test_logs"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
os.environ.pop("SONATA_DEBUG", None)

from sonata2 import fullscreen, logs  # noqa: E402


class LogsTest(unittest.TestCase):
    def setUp(self):
        logs.set_verbose(False)

    def tearDown(self):
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
        if os.path.exists(fullscreen.LOG):
            os.remove(fullscreen.LOG)
        fullscreen.log_view("view-mapped", view)
        self.assertFalse(os.path.exists(fullscreen.LOG))
        logs.set_verbose(True)
        os.makedirs(os.path.dirname(fullscreen.LOG), exist_ok=True)
        fullscreen.log_view("view-mapped", view)
        self.assertTrue(os.path.exists(fullscreen.LOG))

    def test_off_by_default_even_in_a_git_clone(self):
        self.assertFalse(logs.verbose())

    def test_off_logs_in_memory_on_logs_to_cache(self):
        run = os.environ["XDG_RUNTIME_DIR"]
        self.assertEqual(logs.log_dir(), os.path.join(run, "sonata2-logs"))
        p = logs.path("x.log")
        self.assertTrue(os.path.isdir(os.path.dirname(p)))
        logs.set_verbose(True)
        self.assertEqual(logs.log_dir(), os.path.join(os.environ["XDG_CACHE_HOME"], "sonata2"))

    def test_every_writer_uses_the_log_folder(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("sonata2/stallwatch.py", "sonata2/vram.py", "sonata2/shell/quitonclose.py",
                    "sonata2/shell/loginui.py", "sonata2/backend/screenshare.py", "sonata2/shell/capture.py"):
            with open(os.path.join(root, rel)) as f:
                self.assertIn("logs.path(", f.read(), rel)
        for rel in ("sonata2/fullscreen.py", "sonata2/backend/equalizer.py", "sonata2/feedback/report.py"):
            with open(os.path.join(root, rel)) as f:
                self.assertIn("log_dir()", f.read(), rel)
        with open(os.path.join(root, "tools", "sonata-session")) as f:
            sess = f.read()
        self.assertIn('logs="${XDG_RUNTIME_DIR:-/tmp}/sonata2-logs"', sess)
        self.assertIn('sonata2/debug-logging" ]; then', sess)
        self.assertIn('touch "$state/display-gpu-crashed"', sess)       # markers stay on disk
        with open(os.path.join(root, "install.sh")) as f:
            self.assertIn('logdir="\\${XDG_RUNTIME_DIR:-/tmp}/sonata2-logs"', f.read())

    def test_about_switch_is_visible(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "sonata2", "settings", "app.py")) as f:
            src = f.read()
        self.assertIn('switch_row("Logs", logs.verbose()', src)
        self.assertNotIn("logs_row.set_visible(", src)              # no more 7 clicks to find it

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
