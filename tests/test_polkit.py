"""Polkit agent: a request shows the panel; Cancel answers "cancelled"
(xvfb-run python3 -m unittest tests.test_polkit; skipped without polkit's
introspection data)."""
import os
import unittest

os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402

try:
    from sonata2.shell import polkit as P
    from gi.repository import Polkit
except (ImportError, ValueError):
    P = None


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipIf(P is None, "no polkit introspection data")
class PolkitTest(unittest.TestCase):
    def test_cancel(self):
        Adw.init()
        ui.setup()
        P.layer.overlay_fullscreen = lambda w, n: (w.set_default_size(600, 400), True)[1]
        app = Adw.Application(application_id="io.test.polkit")
        app.register(None)
        agent = P.Agent(app)
        res = {}

        def done(src, r):
            try:
                res["v"] = src.initiate_authentication_finish(r)
            except GLib.Error as e:
                res["v"] = e.message
        agent.initiate_authentication("org.test.act", "Test", "", Polkit.Details.new(), "c1",
                                      [Polkit.UnixUser.new(os.getuid())], None, done)
        settle(300)
        self.assertIn("c1", agent.dialogs)
        agent.dialogs["c1"].finish(False)
        settle(200)
        self.assertEqual(res.get("v"), "Cancelled by the user")
        self.assertNotIn("c1", agent.dialogs)


if __name__ == "__main__":
    unittest.main()
