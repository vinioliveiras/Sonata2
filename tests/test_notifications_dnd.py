"""Do Not Disturb (xvfb-run python3 -m unittest tests.test_notifications_dnd).
Regression: notifications still showed banners with it on."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import notifications as N  # noqa: E402


class DndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.dnd")
        cls.app.register(None)

    def setUp(self):
        config.save("notifications", {"dnd": False, "apps": {}})
        self.nc = N.Notifications.__new__(N.Notifications)    # no D-Bus name, no windows
        self.nc.notes, self.nc._next, self.nc.nc, self.nc.listeners = [], 1, None, []
        self.nc.cfg = config.load("notifications", N.DEFAULTS)
        self.shown, self.hidden = [], []
        self.nc._banner = lambda n: (self.shown.append(n.id), self.nc._banners.__setitem__(n.id, None))
        self.nc._banners = {}
        self.nc._hide_banner = lambda nid, animate=True: (self.hidden.append(nid), self.nc._banners.pop(nid, None))

    def send(self, app="Discord", urgency=1):
        return self.nc.notify(app, 0, "", "Hi", "msg", [], {"urgency": GLib.Variant("y", urgency).unpack(),
                                                              "desktop-entry": app.lower()}, -1)

    def test_no_banner_even_critical(self):
        self.nc.set_dnd(True)
        self.send(urgency=1)
        self.send(urgency=2)                       # Electron marks messages critical
        self.assertEqual(self.shown, [])
        self.assertEqual(len(self.nc.notes), 2)    # still in the Notification Center

    def test_on_hides_banners_on_screen(self):
        nid = self.send()
        self.assertEqual(self.shown, [nid])
        self.nc.set_dnd(True)
        self.assertIn(nid, self.hidden)

    def test_new_app_keeps_dnd_from_settings(self):
        """Settings turns DND on; a new app's first notification saved an
        older copy of the settings over it."""
        config.update("notifications", dnd=True)    # Settings, before the file watch reloads
        self.send(app="NewApp")
        self.assertTrue(config.load("notifications", N.DEFAULTS)["dnd"])
        self.assertEqual(self.shown, [])
        self.assertIn("newapp", config.load("notifications", N.DEFAULTS)["apps"])


if __name__ == "__main__":
    unittest.main()
