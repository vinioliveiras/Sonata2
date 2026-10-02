"""Every component's CSS parses (a bad rule is skipped silently: the
shake animations never ran because GTK's CSS has no "0%, 100%" keyframe
lists). Run: xvfb-run python3 -m unittest tests.test_css"""
import importlib
import os
import pkgutil
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class CssTest(unittest.TestCase):
    def test_all_css_parses(self):
        Adw.init()
        import sonata2
        for m in pkgutil.walk_packages(sonata2.__path__, "sonata2."):
            if m.name.endswith("__main__"):
                continue
            try:
                importlib.import_module(m.name)
            except Exception:                      # (an optional dependency missing here)
                continue
        from sonata2 import ui
        from sonata2.ui import theme as T
        ui.setup()
        vals = T.values()
        old_gtk = (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 16)    # no :root before 4.16
        errors = []
        for key, (template, local) in T._templates.items():
            css = T._MS.sub(lambda mm: f"{T.tokens.ms(float(mm.group(1)))}ms", template % {**vals, **local})
            p = Gtk.CssProvider()
            found = []
            p.connect("parsing-error", lambda _p, sec, err, f=found: f.append(
                (sec.get_start_location().lines + 1, err.message)))
            p.load_from_string(css)
            lines = css.split("\n")
            for ln, msg in found:
                text = lines[ln - 1].strip() if ln - 1 < len(lines) else ""
                if old_gtk and text.startswith(":root"):
                    continue
                errors.append(f"{str(key)[:40]}:{ln}: {msg}: {text[:80]}")
        self.assertEqual(errors, [])

    def test_no_keyframe_lists(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent / "sonata2"
        bad = [str(p) for p in root.rglob("*.py")
               if re.search(r"@keyframes[^{]*\{[^}]*?\d+%%?\s*,\s*\d+%%?\s*\{", p.read_text())]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
