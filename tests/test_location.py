"""Vini: a Location switch for every app -- Location Services (all apps,
Flatpak or not) and a switch per packaged app. Sonata writes GeoClue's own
settings (/etc/geoclue/conf.d/90-sonata.conf) through pkexec."""
import configparser
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import config  # noqa: E402
from sonata2.backend import appmanage, location as L  # noqa: E402


def parse(text):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(text)
    return cp


class LocationTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        self.p.start()
        self.writes = []

    def tearDown(self):
        self.p.stop()

    def run_ok(self, cmd, data):
        self.writes.append((cmd, data))
        return True

    def test_off_turns_every_source_off(self):
        cp = parse(L.render(False, []))
        for src in ("wifi", "3g", "cdma", "modem-gps", "network-nmea", "static-source"):
            self.assertEqual(cp[src]["enable"], "false")
        self.assertEqual(parse(L.render(True, [])).sections(), [])

    def test_an_app_turned_off(self):
        cp = parse(L.render(True, ["org.gnome.Maps"]))
        self.assertEqual(dict(cp["org.gnome.Maps"]), {"allowed": "false", "system": "false", "users": ""})
        self.assertEqual(parse(L.render(True, ["bad]name", "x" + chr(10) + "y"])).sections(), [])   # never a broken file

    def test_set_app_and_enabled_through_pkexec(self):
        self.assertTrue(L.set_app("org.gnome.Maps.desktop", False, run=self.run_ok))
        self.assertFalse(L.allowed("org.gnome.Maps.desktop"))
        cmd, data = self.writes[-1]
        self.assertEqual(cmd[:3], ["pkexec", "sh", "-c"])
        self.assertIn(L.CONF, cmd[3])
        self.assertIn("[org.gnome.Maps]", data)
        self.assertTrue(L.set_enabled(False, run=self.run_ok))
        self.assertFalse(L.enabled())
        self.assertIn("[org.gnome.Maps]", self.writes[-1][1])           # both kept in one file
        self.assertTrue(L.set_app("org.gnome.Maps", True, run=self.run_ok))
        self.assertTrue(L.allowed("org.gnome.Maps"))

    def test_cancelled_password_changes_nothing(self):
        self.assertFalse(L.set_enabled(False, run=lambda *a: False))
        self.assertTrue(L.enabled())
        self.assertFalse(L.set_app("x", False, run=lambda *a: False))
        self.assertTrue(L.allowed("x"))

    def test_packaged_apps_get_the_switch(self):
        info = mock.Mock()
        info.get_id.return_value = "org.gnome.Maps.desktop"
        owner = mock.Mock(kind="package", manager="pacman")
        owner.name = "gnome-maps"
        with mock.patch.object(L, "available", return_value=True):
            keys = [k for k, _t, _on in appmanage.permissions(owner, info)]
        self.assertIn("location", keys)
        with mock.patch.object(L, "set_app", return_value=True) as s:
            self.assertTrue(appmanage.set_permission(owner, "location", False, info))
        s.assert_called_once_with("org.gnome.Maps.desktop", False)
        with mock.patch.object(L, "available", return_value=False):
            keys = [k for k, _t, _on in appmanage.permissions(owner, info)]
        self.assertNotIn("location", keys)

    def test_settings_has_location_services(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "sonata2", "settings", "app.py")).read()
        self.assertIn('switch_row("Location Services", location.enabled()', src)
        self.assertIn("location.set_enabled, done, on", src)


if __name__ == "__main__":
    unittest.main()
