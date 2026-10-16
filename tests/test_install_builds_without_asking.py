"""install.sh builds Wayfire without asking (Vini: "build Wayfire still asks
yes or no -- I want it to just go ahead"). Both builds: Wayfire 0.12 for an
older system Wayfire, and Wayfire with Sonata's patches.

    python3 -m unittest tests.test_install_builds_without_asking
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallBuildsWithoutAskingTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            self.src = f.read()

    def test_no_question_before_a_wayfire_build(self):
        for script in ("build-wayfire-stack.sh", "build-wayfire.sh"):
            i = self.src.index(f'bash "$SRC/tools/{script}" ||')
            block = self.src[self.src.rindex("\nif ", 0, i):i]                 # the condition that leads to it
            self.assertNotRegex(block, r"\bask(_no)?\b", script)
            self.assertIn("say ", block, script)                              # it still says what it does

    def test_other_questions_stay(self):
        self.assertTrue(re.search(r'ask "Use Sonata\'s own login screen', self.src))

    def test_syntax(self):
        subprocess.run(["bash", "-n", os.path.join(ROOT, "install.sh")], check=True)


if __name__ == "__main__":
    unittest.main()
