"""Vini: the grey menu bars of GitHub Desktop (and every Electron app: VS
Code, Discord...) under Sonata's dark title bar -- much darker. Electron
reads GTK 3's GtkMenuBar#menubar; Sonata-Dark sets it.

    python3 -m unittest tests.test_theme_menubar
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = """
import gi, sys
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
Gtk.Settings.get_default().set_property("gtk-theme-name", "Sonata-Dark")
bar = Gtk.MenuBar(); win = Gtk.Window(); win.add(bar); win.show_all()
c = bar.get_style_context().get_property("background-color", Gtk.StateFlags.NORMAL)
print(*(round(v * 255) for v in (c.red, c.green, c.blue)))
"""


class MenuBarTest(unittest.TestCase):
    def test_rule(self):
        css = open(os.path.join(ROOT, "sonata2", "data", "themes", "Sonata-Dark", "gtk-3.0", "gtk.css")).read()
        self.assertIn("menubar { background-color: #141416;", css)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
    def test_gtk3_draws_it_dark(self):
        data = tempfile.mkdtemp()
        shutil.copytree(os.path.join(ROOT, "sonata2", "data", "themes", "Sonata-Dark"),
                        os.path.join(data, "themes", "Sonata-Dark"))
        r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, XDG_DATA_HOME=data))
        if r.returncode != 0:
            self.skipTest("no GTK 3 here")
        rgb = [int(v) for v in r.stdout.split()[-3:]]
        self.assertTrue(all(v <= 24 for v in rgb), rgb)                 # was (55, 55, 55)


if __name__ == "__main__":
    unittest.main()
