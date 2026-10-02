"""Setup Assistant: pages navigate, choices save, Get Started marks it done
(xvfb-run python3 -m unittest tests.test_setup)."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import config, ui  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class SetupTest(unittest.TestCase):
    def test_flow(self):
        Adw.init()
        ui.setup()
        from sonata2.backend import system
        from sonata2.shell import setup as S
        system.set_dark_mode = lambda d: None
        S.layer.overlay_fullscreen = lambda w, n: (w.set_default_size(900, 600), True)[1]
        app = Adw.Application(application_id="io.test.setup")
        app.register(None)
        self.assertFalse(S.done())
        a = S.SetupAssistant(app)
        settle()
        for name in S.SetupAssistant.PAGES[1:]:
            a._go(1)
            settle(500)
            self.assertEqual(a.stack.get_visible_child_name(), name)
        a._go(-1)
        self.assertEqual(a.stack.get_visible_child_name(), "sonata")
        # Vini: the first setup asks how to see the apps
        self.assertIn("apps", S.SetupAssistant.PAGES)
        from sonata2.shell import launchpad_window as LW
        self.assertEqual(set(a.app_cards), {"fullscreen", "window"})
        a._set_apps_style("window")
        self.assertEqual(LW.style(), "window")
        self.assertTrue(a.app_cards["window"].has_css_class("selected"))
        self.assertFalse(a.app_cards["fullscreen"].has_css_class("selected"))
        a._set_apps_style("fullscreen")
        self.assertEqual(LW.style(), "fullscreen")
        a._set_accent("green")
        self.assertEqual(config.load("appearance", {"accent": ""})["accent"], "green")
        a.finish()
        settle(600)
        self.assertTrue(S.done())


if __name__ == "__main__":
    unittest.main()
