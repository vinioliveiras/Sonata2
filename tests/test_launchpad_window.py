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

    def test_suggestions_recent_then_dock(self):
        config.save("dock", {"pinned": ["files.desktop", "gimp", "nope"]})
        LW.note_opened("calc")
        LW.note_opened("spotify")
        LW.note_opened("calc")
        self.assertEqual(LW.suggestions(APPS), ["calc", "spotify", "files", "gimp"])


class MenuModeTest(unittest.TestCase):
    """Vini: the Apps Menu is Launchpad itself in another layout -- every
    action of the full screen (and with the Dock) works there too."""

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

    def test_same_component(self):
        self.assertEqual(self.pad.mode, "menu")
        self.assertIs(self.pad.get_child(), self.menu.root)
        self.assertTrue(all(isinstance(t, self.L.LaunchItem) for t in self.menu.tiles))
        self.assertEqual(list(self.menu.tab_buttons), ["social", "creativity", "entertainment", "productivity",
                                                       "utilities", "other"])

    def test_item_menu_has_the_launchpad_actions(self):
        tile = next(t for t in self.menu.tiles if t.item == "gimp")
        with mock.patch.object(ui.menu, "popup") as popup:
            self.pad.item_menu(tile, 1, 1)
        labels = [i.label for sec in popup.call_args[0][1] for i in sec]
        for want in ("Open", "Keep in Dock", "Hide"):
            self.assertIn(want, labels)

    def test_hide_moves_it_into_hidden(self):
        self.pad.hide_app("gimp")
        settle(50)
        keys = [t.item for t in self.menu.tiles]
        self.assertNotIn("gimp", keys)
        hidden = [t for t in self.menu.tiles if isinstance(t.item, dict) and t.item.get("locked")]
        self.assertEqual(len(hidden), 1)                       # the Hidden folder, under Folders

    def test_folders_open_in_the_menu(self):
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.tiles if isinstance(t.item, dict) and t.item.get("folder") == "Work")
        self.pad.activate_item(folder)
        settle(50)
        self.assertIsNotNone(self.pad.folder_view)
        self.assertIs(self.pad.folder_view[0].get_parent(), self.menu.overlay)
        self.assertTrue(self.menu.content.has_css_class("dimmed"))
        self.menu.key(Gdk.KEY_Escape)                          # Esc: the folder first
        self.assertIsNone(self.pad.folder_view)
        self.assertTrue(self.pad.get_visible())

    def test_hold_app_on_app_makes_a_folder(self):
        a = next(t for t in self.menu.tiles if t.item == "gimp")
        b = next(t for t in self.menu.tiles if t.item == "calc")
        self.pad._drag = {"item": "gimp", "widget": a, "folder": None, "target": b}
        b.add_css_class("folder-target")
        self.assertTrue(self.menu.drag_drop("sonata2-launchpad-item"))
        self.pad._drag = None
        folders = [it for p in self.pad.model.pages for it in p if isinstance(it, dict)]
        self.assertEqual(len(folders), 1)
        self.assertEqual(set(folders[0]["apps"]), {"gimp", "calc"})

    def test_dock_app_dropped_here_is_accepted(self):
        self.assertTrue(self.menu.drag_drop("spotify"))        # the Dock then lets it go (unpins)

    def test_jiggle_shows_badges(self):
        self.pad.set_jiggle(True)
        self.assertTrue(self.menu.root.has_css_class("jiggle"))
        badges = [t.badge for t in self.menu.tiles if hasattr(t, "badge")]
        self.assertTrue(badges and all(b.get_visible() for b in badges))
        self.menu.key(Gdk.KEY_Escape)
        self.assertFalse(self.pad.jiggling)

    def test_search_enter_opens_and_suggests(self):
        self.menu.search.set_text("spo")
        settle(400)                                            # (the entry's search delay)
        self.assertEqual([t.item for t in self.menu.tiles], ["spotify"])
        self.menu.open_selected()
        settle(LW.CLOSE_MS + 200)
        self.assertEqual(APPS["spotify"].launched, 1)
        self.assertEqual(config.load(LW.NAME, LW.DEFAULTS)["recent"][0], "spotify")
        self.assertFalse(self.pad.get_visible())

    def test_tiles_built_once(self):
        first = {t.item: t for t in self.menu.tiles if isinstance(t.item, str)}
        self.menu.search.set_text("g")
        self.menu.search.set_text("")
        settle(100)
        self.assertIs({t.item: t for t in self.menu.tiles if t.item == "gimp"}["gimp"], first["gimp"])

    def test_back_to_full_screen(self):
        self.pad.close_launchpad()
        settle(LW.CLOSE_MS + 100)
        config.update(LW.NAME, style="fullscreen")
        self.pad.open_launchpad()
        settle(200)
        self.assertEqual(self.pad.mode, "fullscreen")
        self.assertIs(self.pad.get_child(), self.pad.bin)


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
