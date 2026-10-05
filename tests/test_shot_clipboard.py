"""Vini: a screenshot saved to the folder AND on the clipboard at once
(Options > "Also Copy Screenshots to Clipboard"). Also: the recording /
sharing pills are bare, the red stop button their only colour."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import capture as C  # noqa: E402


class ShotTest(unittest.TestCase):
    def shoot(self, cfg):
        d = tempfile.mkdtemp()
        cap = C.Capture.__new__(C.Capture)
        cap.shot_taken = mock.Mock()
        runs = []

        def run(cmd, **k):
            runs.append(cmd)
            if cmd[0] == "grim":
                open(cmd[-1], "wb").close()
            return mock.Mock(returncode=0)
        with mock.patch.object(C.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(C.subprocess, "run", side_effect=run), \
                mock.patch.object(C, "shots_dir", return_value=d), mock.patch("sonata2.sounds.play"):
            cap._shoot(None, cfg, output="eDP-1")
        path = cap.shot_taken.call_args.args[0]
        return path, d, [r for r in runs if r[0] == "wl-copy"]

    def test_folder_and_clipboard(self):
        path, d, copies = self.shoot({"shots_to": "pictures", "shots_copy": True})
        self.assertEqual(os.path.dirname(path), d)                   # kept in the folder
        self.assertEqual(len(copies), 1)                             # and copied

    def test_folder_only(self):
        path, d, copies = self.shoot({"shots_to": "pictures"})
        self.assertEqual(os.path.dirname(path), d)
        self.assertEqual(copies, [])

    def test_clipboard_only(self):
        path, d, copies = self.shoot({"shots_to": "clipboard"})
        self.assertNotEqual(os.path.dirname(path), d)
        self.assertEqual(len(copies), 1)

    def test_default_and_menu(self):
        import inspect
        self.assertFalse(C.DEFAULTS["shots_copy"])
        self.assertIn("Also Copy Screenshots to Clipboard", inspect.getsource(C))


class PillStyleTest(unittest.TestCase):
    def test_stop_is_the_only_colour(self):
        import re
        css = inspect_css()
        pill = re.search(r"\.rec-pill \{([^}]*)\}", css).group(1)
        self.assertIn("background: none", pill)
        self.assertIn("sys_red", re.search(r"\.rec-pill button \{([^}]*)\}", css).group(1))
        dot = re.search(r"\.rec-dot \{([^}]*)\}", css).group(1)
        self.assertNotIn("sys_red", dot)
        self.assertNotIn("sys_purple", css)

    def test_stop_button(self):
        b = C.stop_button("Stop")
        self.assertIsInstance(b, Gtk.Button)
        self.assertIn("stop-glyph", b.get_child().get_css_classes())


def inspect_css():
    import inspect
    return inspect.getsource(C)


if __name__ == "__main__":
    unittest.main()
