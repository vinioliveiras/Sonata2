"""Show Desktop, Sonata's own (shell/dock.py toggle_show_desktop). Vini:
after Super+D, a click on one app brought every app back.
Run: python3 -m unittest tests.test_show_desktop"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from sonata2.shell import dock as D  # noqa: E402


class T:
    def __init__(self, name):
        self.name, self.minimized = name, False


class Manager:
    def __init__(self, *names):
        self.toplevels = [T(n) for n in names]
        self.order = []

    def minimize(self, t):
        t.minimized = True

    def activate(self, t):
        t.minimized = False
        self.order.append(t.name)


class ShowDesktopTest(unittest.TestCase):
    def setUp(self):
        D._DESK["hidden"] = []

    def test_click_brings_back_only_that_app(self):
        m = Manager("files", "chrome", "spotify")
        self.assertEqual(D.toggle_show_desktop(m), "hid")
        self.assertTrue(all(t.minimized for t in m.toplevels))
        m.activate(m.toplevels[1])                                   # a click on Chrome's icon
        self.assertEqual([t.minimized for t in m.toplevels], [True, False, True])   # only Chrome

    def test_again_brings_them_back(self):
        m = Manager("files", "chrome")
        D.toggle_show_desktop(m)
        self.assertEqual(D.toggle_show_desktop(m), "restored")
        self.assertFalse(any(t.minimized for t in m.toplevels))
        self.assertEqual(m.order, ["chrome", "files"])               # the front one last: in front again

    def test_with_one_back_again_hides_it_too(self):
        m = Manager("files", "chrome")
        D.toggle_show_desktop(m)
        m.activate(m.toplevels[0])
        self.assertEqual(D.toggle_show_desktop(m), "hid")            # something showing: everything goes
        self.assertTrue(all(t.minimized for t in m.toplevels))
        self.assertEqual(D.toggle_show_desktop(m), "restored")       # and both come back
        self.assertFalse(any(t.minimized for t in m.toplevels))

    def test_minimized_before_stay_minimized(self):
        m = Manager("files", "chrome")
        m.toplevels[0].minimized = True                              # minimized by you, before
        D.toggle_show_desktop(m)
        D.toggle_show_desktop(m)
        self.assertEqual([t.minimized for t in m.toplevels], [True, False])

    def test_wired_to_super_d(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(D.__file__)))
        ini = open(os.path.join(root, "config", "wayfire.ini")).read()
        self.assertIn("binding_showdesktop = <super> KEY_D", ini)
        self.assertIn("command_showdesktop = sonata2 show-desktop", ini)
        self.assertIn("toggle_showdesktop = none", ini)              # Wayfire's own off
        from sonata2 import shortcuts
        src = open(shortcuts.__file__).read()
        self.assertIn('Shortcut("desktop", "Show Desktop", "Windows", "command", "binding_showdesktop")', src)
        main = open(os.path.join(root, "sonata2", "__main__.py")).read()
        self.assertIn('Gio.SimpleAction.new("show-desktop", None)', main)


if __name__ == "__main__":
    unittest.main()
