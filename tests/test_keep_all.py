"""Memory review: each `sonata2 keep` imported GTK/Gio (shell.layer, apps,
webapps) and five of them sat in the session doing nothing but wait.
`keep` now imports no GTK, and `keep-all` supervises several components in
one process, each one restarted on its own with keep's rules."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import sonata2.__main__ as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a fake component: `ok` stops on purpose, `flaky` crashes on its first run
# (a marker file) and stops on the second, `args` records its arguments
CHILD = r'''
d="$SONATA_TEST_DIR"; echo "$@" >> "$d/$1.runs"
case "$1" in
  ok) exit 0 ;;
  flaky) if [ -e "$d/flaky.once" ]; then exit 0; fi; touch "$d/flaky.once"; exit 3 ;;
  crash) exit 4 ;;
esac
exit 0
'''


class KeepImportsTest(unittest.TestCase):
    def test_supervisor_imports_no_gtk(self):
        """The `keep` path must stay a small process: no gi / Gio."""
        code = ("import sys, sonata2.__main__ as M\n"
                "assert 'gi' not in sys.modules, sorted(m for m in sys.modules if m.startswith('gi'))\n"
                "assert 'sonata2.webapps' not in sys.modules\n"
                "print(M.component_limit_mb(M._mem_total_mb()))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertGreaterEqual(int(out.stdout), 1024)

    def test_session_starts_one_supervisor(self):
        with open(os.path.join(ROOT, "config", "wayfire.ini")) as f:
            ini = f.read()
        self.assertIn("sonata2 keep-all wallpaper dock topbar launchpad,--background spotlight,--background", ini)
        self.assertNotIn("sonata2 keep dock", ini)


class KeepAllTest(unittest.TestCase):
    def run_all(self, specs, fast=False):
        d = tempfile.mkdtemp()
        real_ended = M._Kept.ended

        def ended(k, code):
            verdict, n = real_ended(k, code)
            return (verdict, 0) if fast and verdict == "again" else (verdict, n)
        with mock.patch.dict(os.environ, {"SONATA_TEST_DIR": d}), \
                mock.patch.object(M, "self_argv", return_value=["sh", "-c", CHILD, "sh"]), \
                mock.patch("sonata2.logs.log_dir", return_value=d), \
                mock.patch.object(M, "share_session_env"), mock.patch.object(M, "_socket_id"), \
                mock.patch.object(M, "_same_session", return_value=True), \
                mock.patch.object(M._Kept, "ended", ended), mock.patch("sys.stderr"):
            code = M.keep_all(specs)
        return code, d

    def runs(self, d, name):
        try:
            with open(os.path.join(d, f"{name}.runs")) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def test_each_restarted_on_its_own(self):
        code, d = self.run_all(["ok", "flaky,--background"], fast=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.runs(d, "ok"), ["ok"])                       # stopped on purpose: once
        self.assertEqual(self.runs(d, "flaky"), ["flaky --background"] * 2)   # crashed: started again
        for name in ("ok", "flaky"):                                       # each its own log
            self.assertTrue(os.path.exists(os.path.join(d, f"{name}.log")))

    def test_a_crash_loop_gives_up_alone(self):
        code, d = self.run_all(["crash", "ok"], fast=True)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.runs(d, "crash")), 5)
        self.assertEqual(self.runs(d, "ok"), ["ok"])

    def test_restart_names(self):
        p = subprocess.Popen(["sh", "-c", "sleep 5", "sonata2", "keep-all", "dock", "launchpad,--background"])
        try:
            # (cmdline is sh's own: -c, the script, then $0 and the specs)
            self.assertEqual(M._keep_all_names(p.pid), ["dock", "launchpad"])
        finally:
            p.kill()
            p.wait()
        self.assertEqual(M._keep_all_names(999999999), [])


if __name__ == "__main__":
    unittest.main()
