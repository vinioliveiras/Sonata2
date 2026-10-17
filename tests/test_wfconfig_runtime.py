"""wfconfig.runtime_set: the session-only writes behind lighter effects while
gaming. Wayfire reloads the whole file on every change, so a wrong line here
(a key glued to the one before, a comment lost, a section written twice)
breaks the running session's config, not only one option. Headless, temp
dirs only.
Run: python3 -m unittest tests.test_wfconfig_runtime"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()

from sonata2 import wfconfig  # noqa: E402


class RuntimeSetTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.run = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cfg, "XDG_RUNTIME_DIR": self.run})
        self.env.start()
        self.live = os.path.join(self.run, "sonata2-wayfire.ini")
        self.over = os.path.join(self.cfg, "sonata2", "wayfire-overrides.ini")

    def tearDown(self):
        self.env.stop()

    def write(self, text):
        with open(self.live, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self):
        with open(self.live, encoding="utf-8") as f:
            return f.read()

    BASE = ("# made at login by tools/wayfire-config.sh\n"
            "[animate]\n"
            "# open_animation = fade\n"
            "open_animation = zoom\n"
            "duration = 300\n"
            "\n"
            "[blur]\n"
            "mode = normal\n")

    def test_replace_keeps_comments_and_other_lines(self):
        """Only the live key's line changes: the commented-out copy of it and
        the file's own comments stay as they are."""
        self.write(self.BASE)
        self.assertTrue(wfconfig.runtime_set("animate", "open_animation", "none"))
        self.assertEqual(self.read(), self.BASE.replace("open_animation = zoom", "open_animation = none"))

    def test_new_key_in_an_existing_section(self):
        """A new key goes at the end of its own section, before the blank line
        (not under the next header, where it would belong to [blur])."""
        self.write(self.BASE)
        self.assertTrue(wfconfig.runtime_set("animate", "close_animation", "none"))
        self.assertEqual(self.read(), self.BASE.replace("duration = 300\n", "duration = 300\nclose_animation = none\n"))
        self.assertEqual(wfconfig.wayfire_get("animate", "close_animation"), "none")
        self.assertEqual(wfconfig.wayfire_get("blur", "close_animation"), "")

    def test_new_key_in_a_missing_section(self):
        """No [decoration] yet: the section is added once, at the end; a second
        key joins it instead of a second header."""
        self.write(self.BASE)
        self.assertTrue(wfconfig.runtime_set("decoration", "font", "Inter 10"))
        self.assertTrue(wfconfig.runtime_set("decoration", "title_height", 28))
        self.assertEqual(self.read(), self.BASE + "\n[decoration]\nfont = Inter 10\ntitle_height = 28\n")
        self.assertEqual(self.read().count("[decoration]"), 1)

    def test_remove_a_key(self):
        """None takes the line out (Wayfire's default is back); a commented
        copy of the key and the neighbours stay."""
        self.write(self.BASE)
        self.assertTrue(wfconfig.runtime_set("animate", "open_animation", None))
        self.assertEqual(self.read(), self.BASE.replace("open_animation = zoom\n", ""))
        self.assertIn("# open_animation = fade", self.read())

    def test_removing_what_isnt_there_writes_nothing(self):
        """A missing key or section with None: nothing to do, no write (every
        write makes Wayfire reload: a display modeset) and no empty section."""
        self.write(self.BASE)
        before = os.stat(self.live).st_ino
        self.assertTrue(wfconfig.runtime_set("animate", "minimize_animation", None))
        self.assertTrue(wfconfig.runtime_set("nosuch", "key", None))
        self.assertEqual(self.read(), self.BASE)
        self.assertEqual(os.stat(self.live).st_ino, before)

    def test_same_value_writes_nothing(self):
        self.write(self.BASE)
        before = os.stat(self.live).st_ino
        self.assertTrue(wfconfig.runtime_set("blur", "mode", "normal"))
        self.assertEqual(os.stat(self.live).st_ino, before)

    def test_key_names_match_whole(self):
        """`duration` doesn't match `duration_ms` (or the other way round), and
        a key written without spaces round `=` is still found."""
        self.write("[animate]\nduration_ms = 1\nduration=300\n")
        self.assertTrue(wfconfig.runtime_set("animate", "duration", 0))
        self.assertEqual(self.read(), "[animate]\nduration_ms = 1\nduration = 0\n")

    def test_bools_as_wayfire_writes_them(self):
        self.write("[blur]\n")
        self.assertTrue(wfconfig.runtime_set("blur", "enabled", False))
        self.assertEqual(wfconfig.wayfire_get("blur", "enabled"), "false")

    def test_last_line_without_a_newline(self):
        """A file ending without a newline (edited by hand, or by another
        tool): a key added to its last section was glued onto that line
        ("mode = normalradius = 4"), and Wayfire read one broken option."""
        self.write("[blur]\nmode = normal")
        self.assertTrue(wfconfig.runtime_set("blur", "radius", 4))
        self.assertEqual(self.read(), "[blur]\nmode = normal\nradius = 4\n")
        self.write("[blur]\nmode = normal")
        self.assertTrue(wfconfig.runtime_set("animate", "open_animation", "none"))
        self.assertEqual(self.read(), "[blur]\nmode = normal\n\n[animate]\nopen_animation = none\n")

    def test_session_only(self):
        """Never Sonata's overrides (they outlive the session), and nothing at
        all without the session copy."""
        self.assertFalse(wfconfig.runtime_set("animate", "open_animation", "none"))
        self.assertFalse(os.path.exists(self.live))
        self.write(self.BASE)
        self.assertTrue(wfconfig.runtime_set("animate", "open_animation", "none"))
        self.assertFalse(os.path.exists(self.over))


if __name__ == "__main__":
    unittest.main()
