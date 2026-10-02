"""Launchpad in a window (shell/launchpad_window.py; Settings > Launchpad >
Style). Run: xvfb-run python3 -m unittest tests.test_launchpad_window"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import launchpad_window as LW  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeInfo:
    def __init__(self, name, cats):
        self.name, self.cats = name, cats
        self.launched = 0

    def get_display_name(self):
        return self.name

    def get_categories(self):
        return self.cats

    def get_string(self, key):
        return self.cats if key == "Categories" else ""

    def get_generic_name(self):
        return ""

    def get_icon(self):
        from gi.repository import Gio
        return Gio.ThemedIcon.new("application-x-executable")

    def get_id(self):
        return self.name.lower() + ".desktop"

    def get_name(self):
        return self.name

    def get_executable(self):
        return self.name.lower()

    def get_commandline(self):
        return self.name.lower()

    def get_startup_wm_class(self):
        return None

    def get_filename(self):
        return None

    def launch(self, *_a):
        self.launched += 1
        return True


APPS = {"discord": FakeInfo("Discord", "Network;InstantMessaging;"),
        "gimp": FakeInfo("GIMP", "Graphics;2DGraphics;"),
        "spotify": FakeInfo("Spotify", "Audio;Music;Player;AudioVideo;"),
        "calc": FakeInfo("Calc", "Office;Spreadsheet;"),
        "files": FakeInfo("Files", "System;FileManager;"),
        "odd": FakeInfo("Odd", "")}


class ModelTest(unittest.TestCase):
    def setUp(self):
        config.save(LW.NAME, {})
        config.save("dock", {})

    def test_categories(self):
        got = {k: LW.category_of(v) for k, v in APPS.items()}
        self.assertEqual(got, {"discord": "social", "gimp": "creativity", "spotify": "entertainment",
                               "calc": "productivity", "files": "utilities", "odd": "other"})

    def test_style_default_and_bad_value(self):
        self.assertEqual(LW.style(), "fullscreen")              # the current Launchpad stays the default
        config.update(LW.NAME, style="window")
        self.assertEqual(LW.style(), "window")
        config.update(LW.NAME, style="???")
        self.assertEqual(LW.style(), "fullscreen")


class MenuModeTest(unittest.TestCase):
    """Vini: the Apps Menu is Launchpad itself in another layout -- nothing
    duplicated: its pages, tiles, search, keys, drags, menus and folders."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        config.save(LW.NAME, {"style": "window"})
        config.save("dock", {"pinned": ["files"]})
        config.save("launchpad", {"pages": [], "hidden": []})
        from sonata2.shell import launchpad as L
        self.L = L
        self.p = mock.patch.object(L, "installed_apps", return_value=dict(APPS))
        self.p.start()
        self.pad = L.Launchpad(None)
        self.pad._dock_above = lambda *_a: None
        self.pad.set_default_size(1400, 900)
        self.pad.open_launchpad()
        settle(300)
        self.menu = self.pad.menu

    def tearDown(self):
        self.pad.destroy()
        self.p.stop()
        config.save(LW.NAME, {})

    def items(self):
        return [t.item for t in self.menu.visible_items()]

    def tile(self, item):
        return next(t for t in self.menu.visible_items() if t.item == item)

    def test_same_component(self):
        self.assertEqual(self.pad.mode, "menu")
        self.assertIs(self.pad.get_child(), self.menu.root)
        self.assertTrue(all(isinstance(g, self.L.PageGrid) for g in self.menu.pages))   # the full screen's grids
        self.assertIs(self.pad.search.get_parent(), self.menu.head)                        # its search field
        self.assertIs(self.tile("gimp"), self.pad.widgets["gimp"])                         # its tiles
        self.assertEqual(list(self.menu.tab_buttons), ["social", "creativity", "entertainment", "productivity",
                                                       "utilities", "other"])

    def test_starts_in_launchpad_order(self):
        order = [it for p in self.pad.model.pages for it in p]
        self.assertEqual(self.items(), order)
        self.menu.set_tab("utilities")
        self.assertEqual(self.items(), ["files"])
        self.menu.set_tab(None)
        self.assertEqual(self.items(), order)

    def test_reorder_by_dragging_is_launchpads(self):
        """The full screen's drag code: beside an icon, after a pause, it moves."""
        order = [it for p in self.pad.model.pages for it in p]
        last = order[-1]
        self.pad._drag = {"item": last, "widget": self.tile(last), "folder": None, "target": None,
                          "pending": (0, 0)}
        self.pad._reorder_to(0, 0)
        self.pad._drag = None
        self.assertEqual(self.items()[0], last)

    def test_folder_takes_its_apps_off_the_grid(self):
        self.pad.model.make_folder("calc", "gimp", "Work")
        self.pad.save()
        self.pad.render()
        self.assertNotIn("gimp", self.items())
        self.menu.set_tab("creativity")
        self.assertNotIn("gimp", self.items())

    def test_item_menu_has_the_launchpad_actions(self):
        with mock.patch.object(ui.menu, "popup") as popup:
            self.pad.item_menu(self.tile("gimp"), 1, 1)
        labels = [i.label for sec in popup.call_args[0][1] for i in sec]
        for want in ("Open", "Keep in Dock", "Hide"):
            self.assertIn(want, labels)

    def test_hide_and_drag_out_of_hidden(self):
        self.pad.hide_app("gimp")
        self.assertNotIn("gimp", self.items())
        hidden = self.pad._hidden_item
        self.assertIn(hidden, self.items())                     # Hidden, last
        self.pad._drag = {"item": "gimp", "widget": self.L.LaunchItem(self.pad, "gimp", 64),
                          "folder": hidden, "target": None}
        self.pad.drag_over(self.menu.pages[0], 5, 5)            # Vini: dragging out of Hidden did nothing
        self.pad._drag = None
        self.assertNotIn("gimp", self.pad.model.hidden)

    def test_folders_open_in_the_menu_and_close_behind(self):
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        self.assertIs(self.pad.folder_view[0].get_parent(), self.menu.overlay)
        self.assertFalse(self.menu.content.get_can_target())
        ok, b = self.menu.panel.compute_bounds(self.menu.root)
        self.menu._outside(None, 1, b.get_x() + 4, b.get_y() + b.get_height() - 4)
        self.assertIsNone(self.pad.folder_view)
        self.assertTrue(self.pad.get_visible())

    def test_arrow_keys_move_the_selection(self):
        """Vini: in the Apps Menu the arrows did nothing (it has no carousel pages)."""
        items = self.items()
        self.pad._key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(self.pad.selected, 0)
        self.assertTrue(self.tile(items[0]).has_css_class("selected"))
        self.pad._key(None, Gdk.KEY_Right, 0, 0)
        self.assertEqual(self.pad.selected, 1)
        self.pad._key(None, Gdk.KEY_Left, 0, 0)
        self.assertEqual(self.pad.selected, 0)
        self.assertFalse(self.tile(items[1]).has_css_class("selected"))
        self.pad._activate_selected()
        settle(LW.CLOSE_MS + 200)
        self.assertFalse(self.pad.get_visible())                       # Enter opens it

    def test_escape_steps(self):
        self.menu.set_tab("social")
        self.pad._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertIsNone(self.menu.tab)
        self.pad._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertTrue(self.menu.panel.has_css_class("closing"))

    def test_search_enter_opens(self):
        self.pad.search.set_text("spo")
        settle(400)
        self.assertEqual(self.items(), ["spotify"])
        self.pad._activate_selected()
        settle(LW.CLOSE_MS + 200)
        self.assertEqual(APPS["spotify"].launched, 1)
        self.assertFalse(self.pad.get_visible())

    def test_jiggle_shows_badges(self):
        self.pad.set_jiggle(True)
        self.assertTrue(self.menu.root.has_css_class("jiggle"))
        badges = [t.badge for t in self.menu.visible_items() if hasattr(t, "badge")]
        self.assertTrue(badges and all(b.get_visible() for b in badges))

    def test_highlight_same_size(self):
        settle(100)
        widths = {t.get_width() for t in self.menu.visible_items() if isinstance(t.item, str)}
        self.assertLessEqual(max(widths) - min(widths), 1, widths)     # (a pixel of rounding)

    def test_settings_item_opens_appearance(self):
        with mock.patch("sonata2.shell.topbar.open_settings") as opened:
            LW.open_style_settings(self.pad)
            settle(LW.CLOSE_MS + 200)
        opened.assert_called_once_with("appearance")

    def test_back_to_full_screen(self):
        self.pad.close_launchpad()
        settle(LW.CLOSE_MS + 100)
        config.update(LW.NAME, style="fullscreen")
        self.pad.open_launchpad()
        settle(200)
        self.assertEqual(self.pad.mode, "fullscreen")
        self.assertIs(self.pad.get_child(), self.pad.bin)
        self.assertIs(self.pad.search.get_parent(), self.pad.col)          # the search field back home
        self.assertGreater(self.pad.carousel.get_n_pages(), 0)


class NamesTest(unittest.TestCase):
    def test_no_apple_names_on_screen(self):
        """Vini: no Apple names: the panel is the "Apps Menu"."""
        from sonata2 import names
        from sonata2.ui import glass as G
        self.assertEqual(dict(LW.STYLES)["window"], names.APPS_MENU)
        self.assertEqual(G.TITLES["launchpad"], names.APPS_MENU)
        shown = " ".join([G.TITLES["launchpad"], G.SUBTITLES["launchpad"]] + [t for _k, t in LW.STYLES])
        for word in ("Launchpad", "Applications", "macOS"):
            self.assertNotIn(word, shown)
        src = open(LW.__file__).read()
        self.assertNotIn('placeholder_text="Applications"', src)
        self.assertNotIn('"Use Full-Screen Launchpad"', src)


class PlaceAndSizeTest(unittest.TestCase):
    def test_option_in_appearance(self):
        """Vini: the style is chosen in Appearance (not under Apps)."""
        from sonata2.settings import app as st
        src = open(st.__file__).read()
        appearance = src[src.index("def _page_appearance"):src.index("def _glass_group")]
        launchpad = src[src.index("def _page_launchpad"):src.index("def _page_hidden")]
        self.assertIn("LW.STYLES", appearance)
        self.assertNotIn("LW.STYLES", launchpad)

    def test_smaller_panel(self):
        """Vini: it was too big on his 1920 x 1080 screen."""
        w, h = LW.panel_size(1920, 1080)
        self.assertLessEqual(w, 1920 * 0.5)
        self.assertLessEqual(h, 1080 * 0.6)
        self.assertEqual(LW.panel_size(4000, 3000), (940, 680))       # never huge
        self.assertEqual(LW.panel_size(800, 600), (560, 440))         # never cramped


class PrewarmTest(unittest.TestCase):
    """Vini: Esc stopped closing Launchpad -- the Apps Menu's warm-up called
    a method that no longer existed, and the surface kept no keyboard."""

    def test_warm_up_calls_only_what_exists(self):
        import re
        from sonata2 import __main__ as main
        src = open(main.__file__).read()
        line = next(ln for ln in src.splitlines() if "layer.prewarm(win, before=lambda: (win.menu." in ln)
        from sonata2.shell import launchpad as L
        for name in re.findall(r"win\.menu\.(\w+)\(", line):
            self.assertTrue(hasattr(LW.MenuView, name), name)
        for name in re.findall(r"win\.(\w+)\(\)", line):
            self.assertTrue(hasattr(L.Launchpad, name), name)

    def test_keyboard_back_even_when_warm_up_fails(self):
        from sonata2.shell import layer
        LS = mock.Mock()
        win = mock.Mock()
        win.get_visible.return_value = False
        win.keyboard_mode = "EXCLUSIVE"
        with mock.patch.object(layer, "layer_shell", return_value=LS), \
                mock.patch.object(layer, "set_input_region"), \
                mock.patch("gi.repository.GLib.timeout_add", side_effect=lambda _ms, fn: fn()):
            layer.prewarm(win, before=lambda: 1 / 0, frames=1)
        tick = win.add_tick_callback.call_args[0][0]
        tick(win, None)
        self.assertEqual(LS.set_keyboard_mode.call_args[0][1], "EXCLUSIVE")


class RoutingTest(unittest.TestCase):
    def test_one_launchpad(self):
        from sonata2 import __main__ as main
        src = open(main.__file__).read()
        self.assertNotIn("LaunchpadWindow", src)              # the same component, not a second window
        self.assertIn('win.mode == "menu"', src)              # an app opened elsewhere closes it too
        from sonata2.settings import app as st
        self.assertIn("LW.STYLES", open(st.__file__).read())


if __name__ == "__main__":
    unittest.main()
