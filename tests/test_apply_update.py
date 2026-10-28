"""tools/apply-update.sh (Vini: updates kept failing -- an earlier `git am`
left half-done, commits already applied, "file (1).mbox"). Against a local
"GitHub" (a bare repo): it undoes a stuck attempt, skips what's already
there, applies the rest, pushes; it stops on edits not committed.
Run: python3 -m unittest tests.test_apply_update"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "tools", "apply-update.sh")


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class ApplyUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, "Downloads"))
        src = os.path.join(self.tmp, "src")
        os.makedirs(src)
        git(src, "init", "-q", "-b", "main")
        git(src, "config", "user.name", "t")
        git(src, "config", "user.email", "t@t")
        os.makedirs(os.path.join(src, "tools"))
        shutil.copy(SCRIPT, os.path.join(src, "tools"))
        for i in range(4):                                       # c0 .. c3
            with open(os.path.join(src, f"f{i}.txt"), "w") as f:
                f.write(f"{i}\n")
            git(src, "add", "-A")
            git(src, "commit", "-q", "-m", f"commit {i}")
        mbox = git(src, "format-patch", "HEAD~3..HEAD", "--stdout")      # c1, c2, c3
        with open(os.path.join(self.home, "Downloads", "sonata2-faltando (1).mbox"), "w") as f:
            f.write(mbox)
        git(src, "reset", "-q", "--hard", "HEAD~2")               # GitHub has c0, c1
        self.remote = os.path.join(self.tmp, "remote.git")
        git(self.tmp, "clone", "-q", "--bare", src, self.remote)
        self.work = os.path.join(self.tmp, "work")
        git(self.tmp, "clone", "-q", self.remote, self.work)
        git(self.work, "config", "user.name", "t")
        git(self.work, "config", "user.email", "t@t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run(["bash", "tools/apply-update.sh", "--no-install"], cwd=self.work, env=env,
                              capture_output=True, text=True)

    def test_skips_what_is_there_applies_the_rest_and_pushes(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 applied, 1 already here", r.stdout)
        self.assertEqual(git(self.remote, "log", "--format=%s", "-1").strip(), "commit 3")
        self.assertEqual(os.listdir(os.path.join(self.home, "Downloads")), [])     # used: put away

    def test_undoes_a_stuck_attempt_first(self):
        os.makedirs(os.path.join(self.work, ".git", "rebase-apply"))       # a `git am` left half-done
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("half-applied", r.stdout)

    def test_stops_on_edits_not_committed(self):
        with open(os.path.join(self.work, "f0.txt"), "a") as f:
            f.write("mine\n")
        r = self.run_it()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not committed", r.stderr)
        self.assertEqual(git(self.remote, "log", "--format=%s", "-1").strip(), "commit 1")   # nothing pushed


if __name__ == "__main__":
    unittest.main()
