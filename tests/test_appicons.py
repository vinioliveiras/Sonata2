"""Settings > App Icons: per-app icon source and shape (icons.py). Run:
xvfb-run python3 -m unittest tests.test_appicons"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from sonata2 import config, icons  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeInfo:
    def __init__(self, did="org.test.App", icon="text-editor"):
        self.did, self.icon = did, icon

    def get_id(self):
        return self.did + ".desktop"

    def get_icon(self):
        return Gio.ThemedIcon.new(self.icon)

    def get_name(self):
        return "Test"

    def get_executable(self):
        return "testapp"

    def get_commandline(self):
        return "testapp"

    def get_display_name(self):
        return "Test App"

    def get_startup_wm_class(self):
        return None


class PrefsTest(unittest.TestCase):
    def setUp(self):
        config.save("icons", {})
        icons.forget_prefs()

    def test_defaults(self):
        """Vini: the default is macOS' squircle, as Sonata has always drawn."""
        self.assertEqual(icons.ICON_DEFAULTS["shape"], "squircle")
        self.assertEqual(icons.prefs()["shape"], "squircle")
        info = FakeInfo()
        self.assertEqual(icons.app_pref(info), {"source": "auto", "path": "", "name": "", "scale": None,
                                                 "shape": "squircle"})
        self.assertIn("apps", icons.ICON_DEFAULTS)

    def test_set_and_reset(self):
        info = FakeInfo()
        icons.set_app_pref(info.did, source="theme", name="firefox", shape="circle")
        p = icons.app_pref(info)
        self.assertEqual((p["source"], p["name"], p["shape"]), ("theme", "firefox", "circle"))
        config.update("icons", shape="rounded")
        icons.forget_prefs()
        self.assertEqual(icons.app_pref(info)["shape"], "circle")                # its own shape wins
        self.assertEqual(icons.app_pref(FakeInfo("other"))["shape"], "rounded")  # others: the default
        icons.set_app_pref(info.did, source=None, name=None, shape=None)
        self.assertNotIn(info.did, icons.prefs()["apps"])                      # nothing left: back to auto
        self.assertEqual(icons.ICON_DEFAULTS["apps"], {})                      # defaults never filled in

    def test_all_from_packages(self):
        """Vini: one button for every app's own (package) icon."""
        a, b = FakeInfo("org.a"), FakeInfo("org.b")
        icons.set_app_pref("org.b", source="file", path="/x.png")
        config.update("icons", source="package")
        icons.forget_prefs()
        icons.set_app_pref("org.a", source="auto", shape="circle")       # this one: Sonata's anyway
        self.assertEqual(icons.app_pref(FakeInfo("org.c"))["source"], "package")
        self.assertEqual(icons.app_pref(b)["source"], "file")                 # a picture stays
        self.assertEqual(icons.app_pref(a)["source"], "auto")
        icons.set_app_pref("org.d", source="package")                        # = everyone's: nothing kept
        self.assertNotIn("org.d", icons.prefs()["apps"])

    def test_scale(self):
        """Vini: how big the picture sits in its frame, up to all of it."""
        info = FakeInfo()
        icons.set_app_pref(info.did, scale=5)
        self.assertEqual(icons.app_pref(info)["scale"], 1.0)
        icons.set_app_pref(info.did, scale=0.01)
        self.assertEqual(icons.app_pref(info)["scale"], icons.SCALE_RANGE[0])
        self.assertTrue(icons.SCALE_RANGE[0] < icons.default_scale() < 1.0)

    def test_no_macos_in_labels(self):
        """Vini: no macOS mentions on screen."""
        self.assertNotIn("macOS", " ".join(icons.SHAPE_TITLES.values()))

    def test_bad_values_ignored(self):
        config.save("icons", {"shape": "star", "apps": {"org.test.App": {"source": "?", "shape": "hex"}}})
        icons.forget_prefs()
        p = icons.app_pref(FakeInfo())
        self.assertEqual((p["source"], p["shape"]), ("auto", "squircle"))


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def setUp(self):
        config.save("icons", {})
        icons.forget_prefs()

    def test_shape_paths(self):
        for s in icons.SHAPES:
            self.assertIsNotNone(icons.shape_path(s, 0, 0, 100, 100))

    def test_each_shape_its_own_file(self):
        info = FakeInfo()
        seen, files = set(), 0
        for s in icons.SHAPES:
            config.update("icons", shape=s)
            icons.forget_prefs()
            g = icons.app_icon(info)
            if isinstance(g, Gio.FileIcon):             # (Sonata's own squircle artwork stays a theme icon)
                files += 1
                seen.add(g.get_file().get_path())
        self.assertEqual(len(seen), files)              # one picture per shape, never a stale one

    def test_custom_picture(self):
        path = os.path.join(tempfile.mkdtemp(), "pic.png")
        from gi.repository import GdkPixbuf
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 64)
        pb.fill(0xff8800ff)
        pb.savev(path, "png", [], [])
        info = FakeInfo()
        icons.set_app_pref(info.did, source="file", path=path)
        g = icons.app_icon(info)
        self.assertIsInstance(g, Gio.FileIcon)
        self.assertTrue(g.get_file().get_path().startswith(icons.GENERATED))
        icons.set_app_pref(info.did, path="/nonexistent.png")
        self.assertIsNotNone(icons.app_icon(info))                     # a missing picture: the usual icon

    def test_theme_and_package_sources(self):
        info = FakeInfo(icon="text-editor")
        icons.set_app_pref(info.did, source="theme", name="folder")
        g = icons.app_icon(info)
        self.assertIn("folder", g.to_string() if not isinstance(g, Gio.FileIcon) else "folder")
        icons.set_app_pref(info.did, source="package", name=None)
        self.assertIsNotNone(icons.app_icon(info))


class SettingsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()

    def setUp(self):
        config.save("icons", {})
        icons.forget_prefs()
        from unittest import mock
        from sonata2.settings import app as st, appicons_page as P
        self.P = P
        self.patch = mock.patch.object(P, "app_list", return_value=[("org.test.App", FakeInfo()),
                                                                    ("org.test.Two", FakeInfo("org.test.Two"))])
        self.patch.start()
        self.w = st.Settings(None, "appicons")
        self.w.present()
        self.w.select("appicons", from_sidebar=True)
        settle(300)
        self.page = self.w.appicons_page

    def tearDown(self):
        self.w.destroy()
        self.patch.stop()

    def test_section_listed(self):
        from sonata2.settings import app as st
        self.assertIn("appicons", [s[0] for s in st.SECTIONS])

    def test_rows_and_search(self):
        self.assertEqual(set(self.page.rows), {"org.test.App", "org.test.Two"})
        self.page.search.set_text("two")
        self.assertFalse(self.page.rows["org.test.App"].get_visible())
        self.assertTrue(self.page.rows["org.test.Two"].get_visible())

    def test_shape_for_all(self):
        self.page.set_shape("circle")
        self.assertEqual(icons.prefs()["shape"], "circle")

    def test_buttons_for_all(self):
        icons.set_app_pref("org.test.App", source="auto", shape="circle")
        icons.set_app_pref("org.test.Two", source="file", path="/x.png")
        self.page.set_source_all("package")
        p = icons.prefs()
        self.assertEqual(p["source"], "package")
        self.assertEqual(p["apps"]["org.test.App"], {"shape": "circle"})   # its source follows all; shape kept
        self.assertEqual(p["apps"]["org.test.Two"]["source"], "file")
        self.assertIn("own", self.page.rows["org.test.App"].get_subtitle())
        self.page.set_source_all("auto")
        self.assertEqual(icons.prefs()["source"], "auto")

    def test_reset_all(self):
        """Vini: one button resets everything -- every app its package's icon."""
        icons.set_app_pref("org.test.App", source="file", path="/x.png", shape="circle")
        self.page.set_shape("rounded")
        from unittest import mock
        with mock.patch.object(self.P.ui.dialog, "alert") as alert:     # asked first (Sonata's alert)
            self.page.ask_reset_all()
        self.assertEqual(icons.prefs()["apps"]["org.test.App"]["source"], "file")   # not before the answer
        answer = alert.call_args[0][3]
        answer("cancel")
        self.assertEqual(icons.prefs()["apps"]["org.test.App"]["source"], "file")
        answer("reset")
        p = icons.prefs()
        self.assertEqual((p["shape"], p["source"], p["apps"]), ("squircle", "package", {}))
        self.assertEqual(self.page.shape_row.get_selected(), list(icons.SHAPES).index("squircle"))
        self.assertIn("own", self.page.rows["org.test.App"].get_subtitle())

    def test_app_panel_uses_the_kit(self):
        row = self.page.rows["org.test.App"]
        pop = self.page.edit(row)
        settle(50)
        self.assertTrue(pop.has_css_class("sonata-panel"))
        self.assertTrue(pop.source.has_css_class("sonata-popup"))
        self.assertFalse(pop.file_rv.get_reveal_child())               # only for a custom picture
        self.assertFalse(pop.name_rv.get_reveal_child())
        pop.source.set_selected(list(icons.SOURCES).index("file"))
        self.assertTrue(pop.file_rv.get_reveal_child())                # slides in
        pop.source.set_selected(list(icons.SOURCES).index("theme"))
        self.assertTrue(pop.name_rv.get_reveal_child())
        self.assertFalse(pop.file_rv.get_reveal_child())
        self.assertTrue(pop.size_rv.get_reveal_child())                # a theme icon sits on the plate
        pop.size.set_value(100)                                        # fills the frame, cut to its shape
        settle(300)
        self.assertEqual(icons.app_pref(row.info)["scale"], 1.0)
        pop.shape.set_selected(1 + list(icons.SHAPES).index("rounded"))
        self.assertEqual(icons.app_pref(row.info)["shape"], "rounded")
        self.assertIn("Rounded", row.get_subtitle())
        pop.popdown()
        settle(50)


if __name__ == "__main__":
    unittest.main()
