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


def walk(w):
    """w and every widget under it."""
    yield w
    c = w.get_first_child()
    while c is not None:
        yield from walk(c)
        c = c.get_next_sibling()


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

    def test_back_to_full_screen_without_a_new_grid(self):
        """Vini: the first full-screen open after the Apps Menu glitched --
        the menu's window size (inside the menu bar and the Dock) re-made the
        full screen's grid, and the display's size made it again on opening.
        The Apps Menu leaves the grid alone; the full screen uses its display's size."""
        from sonata2 import launchpad_model as M
        grid = (M.COLS, M.ROWS)
        with mock.patch.object(M, "set_grid", wraps=M.set_grid) as set_grid:
            self.pad.set_default_size(900, 500)
            settle(200)
            set_grid.assert_not_called()                       # menu mode: nothing re-made
        self.assertEqual((M.COLS, M.ROWS), grid)
        self.pad.layer = True                                  # a layer surface: its display's size
        mon = mock.Mock()
        mon.get_geometry.return_value = mock.Mock(width=1920, height=1080)
        disp = mock.Mock(get_monitor_at_surface=mock.Mock(return_value=mon))
        with mock.patch.object(Gdk.Display, "get_default", return_value=disp):
            self.assertEqual(self.pad.screen_size(900, 500), (1920, 1080))
        self.pad.layer = False
        self.assertEqual(self.pad.screen_size(900, 500), (900, 500))     # a plain window: its own

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
        self.assertIn("gimp", self.pad.model.hidden)             # held: not yet (only on letting go)
        self.assertTrue(self.pad.drag_drop(self.menu.pages[0], 5, 5, "gimp"))
        self.pad._drag = None
        self.assertNotIn("gimp", self.pad.model.hidden)
        self.assertEqual(self.pad.model.pages[0][0], "gimp")     # where it was let go

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

    def test_app_dragged_out_of_a_folder(self):
        """Vini: in the Apps Menu an app couldn't be dragged out of its open
        folder -- the dimmed grid let nothing through (so a click behind the
        folder closes it) and its drop target never heard the drag."""
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        panel_grid = self.pad.folder_view[2].get_first_child()
        tile = next(c for c in (panel_grid.get_child_at(i, 0) for i in range(panel_grid.cols))
                    if getattr(c, "item", None) == "calc")
        with mock.patch.object(self.L.ui.drag, "hang", return_value=None):
            self.pad._drag_begun(tile, None)
        self.assertTrue(self.menu.content.get_can_target())          # the grid hears the drag again
        ok, b = self.pad.folder_view[2].compute_bounds(self.menu.root)
        x, y = b.get_x() + b.get_width() / 2, b.get_y() - 60                     # the grid above the folder
        hit = self.menu.root.pick(x, y, Gtk.PickFlags.DEFAULT)
        grid = hit if isinstance(hit, self.L.PageGrid) else hit.get_ancestor(self.L.PageGrid)
        self.assertIsNotNone(grid)                                    # a page's grid: its drop target hears it
        self.pad.drag_over(grid, 4, 4)                                # out of the folder, onto the grid
        self.assertIsNone(self.pad.folder_view)
        self.assertNotIn("calc", self.pad.model.pages[0])             # held: still in its folder
        self.assertTrue(self.pad.drag_drop(grid, 4, 4, "calc"))       # let go
        self.pad._drag_ended(tile)
        settle(200)
        flat = [i for page in self.pad.model.pages for i in page]
        self.assertIn("calc", flat)                                   # its own tile again
        self.assertTrue(self.menu.content.get_can_target())

    def test_let_go_in_the_empty_room_below(self):
        """Under the last apps there's no grid: it takes the drag too (the
        end of the last page)."""
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        tile = self.pad.folder_view[2].get_first_child().get_child_at(0, 0)
        item = tile.item
        with mock.patch.object(self.L.ui.drag, "hang", return_value=None):
            self.pad._drag_begun(tile, None)
        self.assertTrue(any(isinstance(c, Gtk.DropTarget) for c in list(self.menu.scroll.observe_controllers())))
        self.menu._at_end(lambda g, x, y: self.pad.drag_over(g, x, y))
        self.assertIsNone(self.pad.folder_view)
        self.menu._at_end(lambda g, x, y: self.pad.drag_drop(g, x, y, item))
        self.pad._drag_ended(tile)
        settle(200)
        self.assertIn(item, [i for page in self.pad.model.pages for i in page])

    def test_held_out_of_a_folder_moves_only_when_let_go(self):
        """Vini: held out of its folder, the app moved at once (the grid
        jumping under it); now only a mark shows where it will go -- let go
        on the grid it goes there, let go elsewhere it stays in its folder."""
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        before = [list(p) for p in self.pad.model.pages]
        tile = self.pad.folder_view[2].get_first_child().get_child_at(0, 0)
        with mock.patch.object(self.L.ui.drag, "hang", return_value=None):
            self.pad._drag_begun(tile, None)
        grid = self.menu.pages[0]
        self.pad.drag_over(grid, 4, 4)
        self.assertEqual([list(p) for p in self.pad.model.pages], before)      # nothing moved
        mark = self.pad._drag.get("out_mark")
        self.assertIsNotNone(mark)
        self.assertTrue(mark.has_css_class("drop-before"))
        self.pad._drag_ended(tile)                                             # let go elsewhere
        settle(100)
        self.assertFalse(mark.has_css_class("drop-before"))
        f = next(it for page in self.pad.model.pages for it in page if isinstance(it, dict))
        self.assertIn(tile.item, f["apps"])                                    # still in its folder

    def test_dropped_back_in_the_folder_clicks_behind_close_it_again(self):
        self.pad.model.make_folder("calc", "files", "Work")
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        panel_grid = self.pad.folder_view[2].get_first_child()
        tile = panel_grid.get_child_at(0, 0)
        with mock.patch.object(self.L.ui.drag, "hang", return_value=None):
            self.pad._drag_begun(tile, None)
        self.pad._drag_ended(tile)                                    # never left it
        self.assertIsNotNone(self.pad.folder_view)
        self.assertFalse(self.menu.content.get_can_target())

    def _open_work(self, apps=("calc", "files")):
        self.pad.model.make_folder(apps[0], apps[1], "Work")
        f = next(it for page in self.pad.model.pages for it in page if isinstance(it, dict))
        for a in apps[2:]:
            self.pad.model.add_to_folder(f, a) if hasattr(self.pad.model, "add_to_folder") else f["apps"].append(a)
        self.pad.save()
        self.pad.render()
        folder = next(t for t in self.menu.visible_items() if isinstance(t.item, dict))
        self.pad.activate_item(folder)
        settle(100)
        return f

    def _folder_items(self):
        grid = self.pad.folder_view[2].get_first_child()
        return [c for c in (grid.get_child_at(i % grid.cols, i // grid.cols) for i in range(grid.cols * grid.rows))
                if getattr(c, "item", None)]

    def test_hide_inside_an_open_folder_updates_it(self):
        """Vini: Hide on an app in an open folder left the folder as it was."""
        apps = [a for a in APPS][:3]
        self._open_work(tuple(apps))
        self.assertEqual(len(self._folder_items()), 3)
        self.pad.hide_app(apps[0])
        settle(100)
        self.assertIsNotNone(self.pad.folder_view)
        self.assertNotIn(apps[0], [t.item for t in self._folder_items()])

    def test_folder_gone_closes_its_panel(self):
        f = self._open_work()
        for a in list(f["apps"]):
            self.pad.hide_app(a)
        settle(100)
        self.assertIsNone(self.pad.folder_view)

    def test_lock_inside_an_open_folder_shows_its_padlock(self):
        """Vini: Lock App on an app in an open folder showed no padlock there."""
        self._open_work()
        tile = self._folder_items()[0]
        self.assertIsNone(getattr(tile, "lock", None))
        from sonata2 import applock
        with mock.patch.object(applock, "locked", side_effect=lambda did: did.startswith(tile.item)):
            self.pad.widgets.clear()
            self.pad.render()                                      # (what the applock watch does)
            settle(100)
            again = next(t for t in self._folder_items() if t.item == tile.item)
            self.assertIsNotNone(getattr(again, "lock", None))

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

    def test_style_value_not_cut_by_its_subtitle(self):
        """Vini: Settings > Appearance > Apps style showed its value cut to
        "Ful…" -- the long subtitle took the row's width. Every language."""
        from sonata2 import i18n, names
        from sonata2.settings import app as st
        Adw.init()
        ui.setup()
        self.addCleanup(i18n.use, None)
        for lang in (None, "pt_BR", "pt_PT"):
            i18n.use(lang)
            win = st.Settings(None)
            win.present()
            settle(300)
            win.select("appearance")
            settle(400)
            title = i18n._("{apps} style").format(apps=i18n._(names.APPS))
            rows = [w for w in walk(win) if isinstance(w, Adw.ComboRow) and w.get_title() == title]
            self.assertEqual(len(rows), 1, lang)
            cut = [w.get_label() for w in walk(rows[0])
                   if isinstance(w, Gtk.Label) and w.get_mapped() and w.get_layout().is_ellipsized()]
            self.assertEqual(cut, [], lang)
            win.destroy()

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
