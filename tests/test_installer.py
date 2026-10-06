"""Vini: a friend's install failed. Installing from scratch in a clean Arch
(tools/test-install.sh) stopped without a word: localectl had nothing to
answer and, under pipefail, the login-screen step ended the whole install.
Every installer script now says where it stopped, and the fresh install's
doctor no longer warns about what install.sh itself built."""
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ScriptsTest(unittest.TestCase):
    def test_a_failing_step_says_where(self):
        for f in ("install.sh", "tools/greeter-setup.sh"):
            src = (ROOT / f).read_text()
            self.assertIn("set -euo pipefail", src, f)
            self.assertRegex(src, r"trap '.*stopped at line \$LINENO.*' ERR", f)

    def test_no_localed_is_not_an_error(self):
        """greeter-setup's keyboard layout with a localectl that fails."""
        src = (ROOT / "tools/greeter-setup.sh").read_text()
        self.assertIn('loc="$(localectl status 2>/dev/null || true)"', src)
        self.assertNotIn('layout="$(localectl status', src)
        # the same lines, run under the same shell options with a failing localectl
        start = src.index('loc="$(localectl')
        block = src[start:src.index("\n", src.index('variant="$(printf', start))]
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "localectl")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho 'System has not been booted with systemd' >&2\nexit 1\n")
        os.chmod(fake, 0o755)
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block + '\necho "layout=${layout:-us}"'],
                           capture_output=True, text=True, env={**os.environ, "PATH": d + ":" + os.environ["PATH"]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("layout=us", r.stdout)

    def test_install_scripts_parse(self):
        for f in ("install.sh", "tools/greeter-setup.sh", "tools/test-install.sh", "tools/build-wayfire.sh"):
            if (ROOT / f).exists():
                r = subprocess.run(["bash", "-n", str(ROOT / f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f + ": " + r.stderr)


class DoctorTest(unittest.TestCase):
    def test_finds_the_plugins_install_built(self):
        from sonata2 import doctor
        home = tempfile.mkdtemp()
        user = os.path.join(home, ".local/share/wayfire/plugin-manager/install/lib/wayfire")
        own = os.path.join(home, ".local/opt/sonata-wayfire/lib/wayfire")
        for d in (user, own):
            os.makedirs(d)
        with mock.patch.dict(os.environ, {"HOME": home}):
            dirs = doctor._plugin_dirs()
        self.assertIn(user, dirs)                             # pixdecor, sonata-corners: built there
        self.assertIn(own, dirs)

    def test_a_normal_install_is_not_a_warning(self):
        import inspect
        from sonata2 import doctor
        src = inspect.getsource(doctor)
        self.assertNotIn('r.add(WARN, "installed copy', src)


if __name__ == "__main__":
    unittest.main()
