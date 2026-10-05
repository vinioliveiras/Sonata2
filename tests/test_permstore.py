"""Vini (security review): no place to see and take back what sandboxed apps
were allowed (camera, location, screenshots, notifications, background).
Security & Privacy lists them from the portals' permission store."""
import unittest
from unittest import mock

from gi.repository import GLib

from sonata2.backend import permstore as P


class FakeBus:
    def __init__(self, tables):
        self.tables, self.calls = tables, []

    def call_sync(self, _bus, _path, _iface, method, params, _rt, _flags, _t, _c):
        self.calls.append((method, params.unpack() if params else None))
        if method == "Lookup":
            table, ident = params.unpack()
            if (table, ident) not in self.tables:
                raise GLib.Error("no such table")
            return GLib.Variant("(a{sas}v)", (self.tables[(table, ident)], GLib.Variant("s", "")))
        return None


class PermStoreTest(unittest.TestCase):
    def test_allowed(self):
        self.assertTrue(P.allowed(["yes"], "yes"))
        self.assertFalse(P.allowed(["no"], "yes"))
        self.assertFalse(P.allowed([], "yes"))
        self.assertTrue(P.allowed(["EXACT", "123"], None))
        self.assertFalse(P.allowed(["NONE", "123"], None))

    def test_all(self):
        bus = FakeBus({("devices", "camera"): {"com.discordapp.Discord": ["yes"], "org.zoom.Zoom": ["no"]}})
        with mock.patch.object(P, "_bus", return_value=bus):
            got = dict((k, apps) for k, _t, apps in P.all_permissions())
        self.assertEqual(got["camera"], [("com.discordapp.Discord", True), ("org.zoom.Zoom", False)])
        self.assertEqual(got["location"], [])                          # nobody asked: no table

    def test_set_and_reset(self):
        bus = FakeBus({("location", "location"): {"org.gnome.Maps": ["EXACT", "99"]}})
        with mock.patch.object(P, "_bus", return_value=bus):
            self.assertTrue(P.set_allowed("camera", "com.discordapp.Discord", False))
            self.assertTrue(P.set_allowed("location", "org.gnome.Maps", False))
            self.assertTrue(P.reset("camera", "com.discordapp.Discord"))
        sets = [c for c in bus.calls if c[0] == "SetPermission"]
        self.assertEqual(sets[0][1], ("devices", False, "camera", "com.discordapp.Discord", ["no"]))
        self.assertEqual(sets[1][1], ("location", False, "location", "org.gnome.Maps", ["NONE", "99"]))
        self.assertIn(("DeletePermission", ("devices", "camera", "com.discordapp.Discord")), bus.calls)

    def test_on_the_page(self):
        import inspect
        from sonata2.settings import app
        self.assertIn("self._permissions_group()", inspect.getsource(app.Settings._page_privacy))


if __name__ == "__main__":
    unittest.main()
