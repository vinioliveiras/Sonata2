"""Settings > Default Apps (Vini: one place for every default app)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from sonata2 import defaultapps as DA  # noqa: E402

Adw.init()


class FakeApp:
    def __init__(self, did, name, types, show=True):
        self.did, self.name, self.types, self.show = did, name, types, show
        self.set = []

    def get_id(self):
        return self.did

    def get_display_name(self):
        return self.name

    def should_show(self):
        return self.show

    def get_supported_types(self):
        return self.types

    def set_as_default_for_type(self, t):
        self.set.append(t)


class DefaultAppsTest(unittest.TestCase):
    def test_kinds(self):
        ids = [k.id for k in DA.KINDS]
        for k in ("web", "mail", "calendar", "music", "video", "photos", "pdf", "text", "files"):
            self.assertIn(k, ids)

    def test_candidates_once_and_shown_only(self):
        apps = [FakeApp("a.desktop", "A", []), FakeApp("a.desktop", "A", []), FakeApp("h.desktop", "H", [], False)]
        with mock.patch.object(DA.Gio.AppInfo, "get_all_for_type", return_value=apps):
            self.assertEqual(DA.candidates("music"), [("a.desktop", "A")])

    def test_default_for_the_types_the_app_opens(self):
        app = FakeApp("loupe.desktop", "Image Viewer", ["image/png", "image/jpeg", "text/plain"])
        with mock.patch("sonata2.apps.DesktopAppInfo.new", return_value=app):
            self.assertTrue(DA.set_default("photos", "loupe.desktop"))
        self.assertEqual(app.set, ["image/jpeg", "image/png"])           # never text/plain

    def test_browser_through_xdg_settings(self):
        with mock.patch("sonata2.backend.system.set_default_browser", return_value=True) as sb:
            DA.set_default("web", "google-chrome.desktop")
        sb.assert_called_once_with("google-chrome.desktop")

    def test_settings_page(self):
        from sonata2.settings import app as S
        self.assertIn("defaults", [s[0] for s in S.SECTIONS])
        apps = [FakeApp("x.desktop", "X", []), FakeApp("y.desktop", "Y", [])]
        cur = FakeApp("y.desktop", "Y", [])
        win = S.Settings.__new__(S.Settings)
        with mock.patch.object(DA.Gio.AppInfo, "get_all_for_type", return_value=apps), \
                mock.patch.object(DA.Gio.AppInfo, "get_default_for_type", return_value=cur), \
                mock.patch.object(S.system, "run_async") as ra:
            groups = S.Settings._page_defaults(win)
            self._check(groups, ra)

    def _check(self, groups, ra):
        rows = []
        child = groups[0]

        def walk(w):
            if isinstance(w, Adw.ComboRow):
                rows.append(w)
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        walk(child)
        self.assertEqual(len(rows), len(DA.KINDS))
        mail = next(r for r in rows if r.get_title() == "Mail")
        self.assertEqual(mail.values[mail.get_selected()], "y.desktop")   # the current default shown
        ra.reset_mock()
        mail.set_selected(0)
        self.assertEqual(ra.call_args[0][2:], ("mail", "x.desktop"))     # saved off the main loop


if __name__ == "__main__":
    unittest.main()
