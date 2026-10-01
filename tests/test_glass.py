"""Glass per part (ui/glass.py) and Settings > Appearance. Run:
xvfb-run python3 -m unittest tests.test_glass"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.ui import glass as G  # noqa: E402

TOK = {"glass_tint": "rgba(228, 228, 234, 0.60)", "solid_tint": "rgba(236, 236, 240, 0.78)",
       "bar_bg": "rgba(228, 228, 234, 0.60)", "window_bg": "#ececec", "menu_bg": "rgba(236, 236, 236, 0.97)",
       "window_glass": "rgba(210, 210, 218, 0.74)", "sidebar_bg": "#ebebed"}


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class GlassModelTest(unittest.TestCase):
    def setUp(self):
        config.save("appearance", {})
        config.save("dock", {})

    def test_defaults(self):
        s = G.settings()
        for item in G.ITEMS:
            self.assertEqual(s[item], {"on": True, "alpha": None})
        self.assertEqual(s["blur"], G.BLUR_DEFAULT)
        self.assertEqual(G.blur_offset(G.BLUR_DEFAULT), 4.5)          # wayfire.ini's own value

    def test_old_translucent_switch_off_means_all_solid(self):
        config.save("dock", {"glass": False})
        s = G.settings()
        self.assertFalse(any(s[i]["on"] for i in G.ITEMS))
        config.save("appearance", {"glass": {"dock": {"on": True}}})  # a part set since: its choice
        s = G.settings()
        self.assertTrue(s["dock"]["on"])
        self.assertFalse(s["menus"]["on"])

    def test_bad_values_cleaned(self):
        s = G.settings({"glass": {"dock": {"on": True, "alpha": 0.1}, "menus": "x", "blur": 500}})
        self.assertEqual(s["dock"]["alpha"], G.ALPHA_RANGE[0])         # never below the blur's threshold
        self.assertEqual(s["menus"], {"on": True, "alpha": None})
        self.assertEqual(s["blur"], 100)
        self.assertEqual(G.settings({"glass": "nope"})["blur"], G.BLUR_DEFAULT)

    def test_material(self):
        cfg = G.settings({"glass": {"dock": {"on": True, "alpha": 0.8}, "menus": {"on": False}}})
        self.assertEqual(G.material("dock", TOK, True, cfg), "rgba(228, 228, 234, 0.800)")
        self.assertEqual(G.material("menubar", TOK, True, cfg), TOK["bar_bg"])          # the theme's own
        self.assertEqual(G.material("menus", TOK, True, cfg), TOK["menu_bg"])           # off: solid
        self.assertEqual(G.material("dock", TOK, False, cfg), TOK["solid_tint"])        # no blur: solid
        self.assertEqual(G.material("windows", TOK, False, cfg), TOK["sidebar_bg"])
        self.assertEqual(G.alpha_of("menubar", TOK, cfg), 0.60)
        self.assertEqual(G.alpha_of("dock", TOK, cfg), 0.8)

    def test_with_alpha(self):
        self.assertEqual(G.with_alpha("#102030", 0.5), "rgba(16, 32, 48, 0.500)")
        self.assertEqual(G.with_alpha("rgb(1, 2, 3)", 0.7), "rgba(1, 2, 3, 0.700)")

    def test_glass_is_a_known_appearance_key(self):
        # config.load keeps only known keys: "glass" must be a default
        from sonata2.icons import APPEARANCE_DEFAULTS
        self.assertIn("glass", APPEARANCE_DEFAULTS)
        config.update("appearance", glass={"blur": 70})
        self.assertEqual(G.settings()["blur"], 70)


class BlurRuleTest(unittest.TestCase):
    """Performance: a solid part gets no blur pass behind it."""

    def rule(self, **off):
        return G.blur_rule(G.settings({"glass": {k: {"on": False} for k in off}}))

    def test_all_on_is_wayfire_ini(self):
        from sonata2 import titlebars
        config.save("dock", {})
        self.assertEqual(G.BLUR_ALL, titlebars.BLUR)
        self.assertEqual(self.rule(), G.BLUR_ALL)

    def test_parts_left_out(self):
        config.save("dock", {})
        r = self.rule(dock=True, menus=True)
        self.assertNotIn("sonata2-dock", r)
        self.assertNotIn("unmanaged", r)
        self.assertIn('app_id is "sonata2-topbar"', r)
        self.assertIn('app_id contains "sonata2."', r)
        self.assertEqual(self.rule(dock=True, menubar=True, menus=True, windows=True), G.BLUR_NONE)
        self.assertNotIn("!", self.rule(windows=True))           # positive rules only

    def test_apply_colors_uses_it(self):
        from unittest import mock
        from sonata2 import titlebars
        config.save("dock", {})
        config.save("appearance", {"glass": {"dock": {"on": False}, "menus": {"on": False}}})
        calls = {}
        with mock.patch("sonata2.backend.system.wayfire_set",
                        side_effect=lambda sec, key, val: calls.__setitem__((sec, key), val)), \
                mock.patch.object(titlebars, "glass_bars", return_value=False):
            titlebars.apply_colors(False)
        self.assertNotIn("sonata2-dock", calls[("blur", "blur_by_default")])
        self.assertEqual(calls[("blur", "kawase_offset")], "4.5")
        config.save("appearance", {})


class ThemeMaterialsTest(unittest.TestCase):
    def test_tokens_follow_the_settings(self):
        from sonata2.ui import theme
        os.environ["SONATA_GLASS"] = "1"
        try:
            config.save("dock", {})
            config.save("appearance", {"glass": {"dock": {"on": True, "alpha": 0.9}, "menubar": {"on": False}}})
            theme._glass_seen = None
            theme._reduce = None
            v = theme.values()
            self.assertTrue(v["dock_material"].endswith("0.900)"))
            self.assertEqual(v["bar_material"], v["window_bg"])
            self.assertEqual(v["panel_material"], v["glass_tint"])
            config.update("appearance", reduce_transparency=True)          # Accessibility wins
            theme._glass_seen = None
            theme._reduce = None
            v = theme.values()
            self.assertEqual(v["dock_material"], v["solid_tint"])
        finally:
            os.environ.pop("SONATA_GLASS", None)
            config.save("appearance", {})
            theme._glass_seen = None
            theme._reduce = None


class ThemeReloadTest(unittest.TestCase):
    """Performance: a glass slider re-styles without the cross-fade (which
    pictures every window); a switch keeps it; nothing new: no re-parse."""

    @classmethod
    def setUpClass(cls):
        Gtk.init()
        from sonata2 import ui
        ui.setup()

    def test_fade_only_for_switches(self):
        from unittest import mock
        from sonata2.ui import theme
        config.save("dock", {})
        config.save("appearance", {})
        theme._appearance_changed()
        loads = []
        with mock.patch.object(theme, "_load", side_effect=lambda *a, fade=False: loads.append(fade)):
            config.save("appearance", {"glass": {"dock": {"alpha": 0.8}}})
            theme._appearance_changed()
            config.save("appearance", {"glass": {"dock": {"alpha": 0.8, "on": False}}})
            theme._appearance_changed()
            theme._appearance_changed()                       # nothing new
        self.assertEqual(loads, [False, True])
        config.save("appearance", {})
        theme._appearance_changed()

    def test_same_css_not_parsed_again(self):
        from unittest import mock
        from sonata2.ui import theme
        theme._load()
        with mock.patch.object(theme._provider, "load_from_string") as parse:
            theme._load()
        parse.assert_not_called()


class SettingsAppearanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()

    def setUp(self):
        config.save("appearance", {})
        config.save("dock", {})
        from sonata2.settings import app as st
        self.st = st
        self.w = st.Settings(None, "appearance")
        self.w.present()
        self.w.select("appearance", from_sidebar=True)
        settle(200)

    def tearDown(self):
        self.w.destroy()

    def groups(self, sid):
        from tests.test_regressions import rows_of
        return [g.get_title() for g in rows_of(self.w.pages[sid], Adw.PreferencesGroup)]

    def test_section_is_appearance(self):
        title = next(s[1] for s in self.st.SECTIONS if s[0] == "appearance")
        self.assertEqual(title, "Appearance")
        titles = [t.replace("&amp;", "&") for t in self.groups("appearance")]
        for t in ("Appearance", "Style", "Title Bars", "Glass & Transparency", "Corners"):
            self.assertIn(t, titles)

    def test_moved_rows(self):
        from tests.test_regressions import rows_of
        page = self.w.pages["appearance"]
        names = [r.get_title() for r in rows_of(page, Adw.PreferencesRow)]
        self.assertNotIn("Menu bar logo", names)                       # -> Menu Bar
        self.assertNotIn("Default web browser", names)                 # -> Desktop & Dock
        self.w.select("dock", from_sidebar=True)
        settle(200)
        names = [r.get_title() for r in rows_of(self.w.pages["dock"], Adw.PreferencesRow)]
        self.assertIn("Menu bar logo", names)
        self.assertNotIn("Translucent glass", names)                   # -> Appearance, per part

    def test_switch_saves_and_greys_the_slider(self):
        sw, sl = self.w.glass_rows["menus"]
        self.assertTrue(sl.get_sensitive())
        sw.set_active(False)
        settle(50)
        self.assertFalse(G.settings()["menus"]["on"])
        self.assertFalse(sl.get_sensitive())
        self.assertTrue(G.settings()["dock"]["on"])                    # only that part

    def test_slider_saves_after_it_stops(self):
        _sw, sl = self.w.glass_rows["dock"]
        sl.slider.set_value(100)                                       # most see-through
        settle(50)
        self.assertIsNone(G.settings()["dock"]["alpha"])               # not every step
        settle(400)
        self.assertEqual(G.settings()["dock"]["alpha"], G.ALPHA_RANGE[0])
        self.w.glass_rows["blur"].slider.set_value(80)
        settle(400)
        self.assertEqual(G.settings()["blur"], 80)

    def test_slider_mapping_round_trips(self):
        for a in (0.5, 0.6, 0.74, 0.95):
            self.assertAlmostEqual(self.st.Settings._slider_to_alpha(self.st.Settings._alpha_to_slider(a)), a, 2)


if __name__ == "__main__":
    unittest.main()
