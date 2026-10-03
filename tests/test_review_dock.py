"""Review tests for the Dock / Launchpad area: pure logic (geometry, the
Launchpad model, Dock<->Launchpad folder transfer, open-apps notes, the
puff, stacks, desktop grid math) plus a few headless widget checks.
Tests marked expectedFailure document real bugs found in the review.
Run: xvfb-run -a python3.12 -m unittest tests.test_review_dock -v"""
import json
import os
import tempfile
import threading
import types
import unittest
from unittest import mock

for _var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_var] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2 import config, launchpad_model as M, open_apps as O  # noqa: E402
from sonata2.shell import dock as D, dock_drop, dock_folder as F, dock_menu, dock_stack, poof  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


def bare_cfg(**over):
    """A Dock config that needs no installed apps."""
    return dict(D.DEFAULTS, pinned=[], stacks=[], recent=[], folders={}, **over)


# -- Dock geometry (pure) ----------------------------------------------------------------------
class GeometryTest(unittest.TestCase):
    def test_dot_gaps_shrink_with_icon_art_inset(self):
        """The icon->dot gap loses the icon's transparent margin, never below 0."""
        self.assertEqual(D.dot_gaps(48), (0, D.DOT_GAP))          # 4 - round(48/12) = 0
        self.assertEqual(D.dot_gaps(16), (D.DOT_GAP - 1, D.DOT_GAP))
        self.assertEqual(D.dot_gaps(128)[0], 0)                   # never negative

    def test_plate_height_and_reserved(self):
        """Plate thickness = pad + icon + dot row; an auto-hiding Dock reserves nothing."""
        cfg = dict(D.DEFAULTS, icon_size=48)
        self.assertEqual(D.plate_height(cfg), D.PAD_TOP + 48 + D.dot_row(cfg))
        self.assertEqual(D.reserved(cfg), cfg["edge_gap"] + 2 * D.PAD_TOP + 48 + D.dot_row(cfg))
        self.assertEqual(D.reserved(dict(cfg, autohide=True)), 0)

    def test_max_icon_follows_magnification(self):
        """The surface is sized for the magnified icon only when magnification is on."""
        cfg = dict(D.DEFAULTS, icon_size=48, magnified_size=80)
        self.assertEqual(D.max_icon(cfg), 48)
        self.assertEqual(D.max_icon(dict(cfg, magnification=True)), 80)
        self.assertEqual(D.max_icon(dict(cfg, magnification=True, magnified_size=30)), 48)

    def test_merge_order_appends_new_tiles_and_drops_duplicates(self):
        """Pinned order follows tiles; untiled keys keep their slot; dups collapse."""
        self.assertEqual(D.merge_order(["a", "x", "b"], ["b", "a"]), ["b", "x", "a"])
        self.assertEqual(D.merge_order(["a", "b", "a"], ["a", "b"]), ["a", "b"])
        self.assertEqual(D.merge_order([], ["a"]), [])            # tiles never add pins by themselves


# -- Launchpad model (pure) --------------------------------------------------------------------
def installed(n):
    return {f"app{i:03d}": f"App {i:03d}" for i in range(n)}


class LaunchpadModelTest(unittest.TestCase):
    def setUp(self):
        self._grid = (M.COLS, M.ROWS, M.PER_PAGE)

    def tearDown(self):
        M.COLS, M.ROWS, M.PER_PAGE = self._grid

    def test_set_grid_clamps_and_reports_change(self):
        """Responsive grid: 3..8 each way; True only when the size changes."""
        self.assertTrue(M.set_grid(20, 1))
        self.assertEqual((M.COLS, M.ROWS, M.PER_PAGE), (8, 3, 24))
        self.assertFalse(M.set_grid(8, 3))
        self.assertTrue(M.set_grid(5.9, 4.2))                   # floats from the window math
        self.assertEqual((M.COLS, M.ROWS), (5, 4))

    def test_repack_fills_pages_to_new_size(self):
        """After the grid grows, pages are refilled in order and the grid noted."""
        M.set_grid(3, 3)
        m = M.Model({}, installed(20))
        self.assertEqual([len(p) for p in m.pages], [9, 9, 2])
        M.set_grid(4, 5)
        m.repack()
        self.assertEqual([len(p) for p in m.pages], [20])
        self.assertEqual(m.grid, (4, 5))
        self.assertEqual(m.all_apps(), sorted(installed(20)))

    def test_reconcile_keeps_link_and_skips_hidden(self):
        """A folder keeps its Dock link; hidden apps are not re-added as new."""
        data = {"pages": [[{"folder": "F", "apps": ["app000", "app001"], "link": "L1", "junk": 1}]],
                "hidden": ["app002"]}
        m = M.Model(data, installed(4))
        self.assertEqual(m.pages[0][0], {"folder": "F", "apps": ["app000", "app001"], "link": "L1"})
        self.assertNotIn("app002", m.all_apps())
        self.assertIn("app003", m.all_apps())

    def test_duplicate_app_shown_once(self):
        """An app listed twice (top level and in a folder) is shown only once."""
        data = {"pages": [["app000", {"folder": "F", "apps": ["app000", "app001", "app002"]}]]}
        m = M.Model(data, installed(3))
        self.assertEqual(m.all_apps().count("app000"), 1)
        self.assertEqual(m.pages[0][1]["apps"], ["app001", "app002"])

    def test_hide_dissolves_two_app_folder(self):
        """Hiding one of a 2-app folder's apps turns the folder back into the other app."""
        m = M.Model({"pages": [[{"folder": "F", "apps": ["app000", "app001"]}]]}, installed(2))
        m.hide("app000")
        self.assertEqual(m.pages, [["app001"]])
        self.assertEqual(m.hidden, ["app000"])

    def test_add_folder_dragged_back_replaces_linked_copy(self):
        """A linked folder dropped back: the old copy leaves its other apps in place."""
        data = {"pages": [["app000", {"folder": "F", "apps": ["app001", "app002", "app003"], "link": "L"},
                           "app004"]]}
        m = M.Model(data, installed(5))
        item = m.add_folder("F", ["app001", "app002"], 0, 3, link="L")
        self.assertEqual(m.pages[0], ["app000", "app003", "app004", item])
        self.assertEqual(item, {"folder": "F", "apps": ["app001", "app002"], "link": "L"})

    def test_add_folder_ignores_hidden_and_unknown(self):
        """Dock folder dropped in: hidden/uninstalled apps are left out; one left = plain app."""
        m = M.Model({"hidden": ["app001"]}, installed(3))
        self.assertEqual(m.add_folder("F", ["app001", "nope", "app002"], 0, 0), "app002")
        self.assertIsNone(m.add_folder("F", ["nope"], 0, 0))

    def test_search_ranks_prefix_word_substring_extra(self):
        """Search ranks: name prefix, word prefix, substring, then extra text; empty = nothing."""
        meta = {"a": ("Text Editor", ""), "b": ("Editor", ""), "c": ("Reditor", ""), "d": ("Notes", "editor tool")}
        self.assertEqual(M.search(meta, " edi "), ["b", "a", "c", "d"])
        self.assertEqual(M.search(meta, "   "), [])
        self.assertEqual(M.search(meta, "edi", limit=2), ["b", "a"])

    def test_decode_folder_rejects_bad_payloads(self):
        """Only well-formed folder payloads are accepted from a drag."""
        good = M.encode_folder("Work", ["a", "b"], "L1")
        self.assertEqual(M.decode_folder(good), {"folder": "Work", "apps": ["a", "b"], "link": "L1"})
        self.assertIsNone(M.decode_folder(M.FOLDER_SCHEME + "%7Bbroken"))
        self.assertIsNone(M.decode_folder(M.FOLDER_SCHEME + "%5B%5D"))          # a list, not a dict
        bad_apps = M.FOLDER_SCHEME + json.dumps({"folder": "x", "apps": [1]})
        self.assertIsNone(M.decode_folder(bad_apps))
        self.assertIsNone(M.decode_folder(b"bytes"))

    def test_corrupt_folder_entry_does_not_crash(self):
        """A malformed folder entry is dropped instead of crashing the Launchpad."""
        m = M.Model({"pages": [[{"folder": "F", "apps": None}, "app000"]]}, installed(1))
        self.assertEqual(m.all_apps(), ["app000"])


# -- Dock folders (pure parts) -----------------------------------------------------------------
class DockFolderModelTest(unittest.TestCase):
    def test_new_id_fills_first_gap(self):
        """New folder ids reuse the first free number."""
        self.assertEqual(F.new_id({}), "1")
        self.assertEqual(F.new_id({"1": {}, "3": {}}), "2")

    def test_is_folder_only_strings(self):
        """Only "folder:<id>" strings are folder keys."""
        self.assertTrue(F.is_folder("folder:2"))
        self.assertFalse(F.is_folder(None))
        self.assertFalse(F.is_folder("firefox"))
        self.assertEqual(F.folder_id("folder:12"), "12")

    def test_as_launchpad_copies_apps(self):
        """The Launchpad shape is a copy: editing it doesn't touch the Dock's folder."""
        f = {"name": "W", "apps": ["a"], "locked": True}
        lp = F.as_launchpad(f)
        lp["apps"].append("b")
        self.assertEqual(f["apps"], ["a"])
        self.assertEqual(lp, {"folder": "W", "apps": ["a", "b"]})

    def test_password_checked_off_the_main_thread(self):
        """PAM runs in the worker thread; only the result comes back on the main loop."""
        seen, result = [], []
        with mock.patch("sonata2.pam.authenticate",
                        side_effect=lambda u, p: (seen.append(threading.current_thread()), True)[1]):
            F.check_password("pw", result.append)
            for _ in range(200):
                if result:
                    break
                settle(10)
        self.assertEqual(result, [True])
        self.assertIsNot(seen[0], threading.main_thread())


# -- open apps (Reopen Apps after a crash) -------------------------------------------------------
class OpenAppsTest(unittest.TestCase):
    def setUp(self):
        for n in (O.FILE, O.BEFORE):
            try:
                os.remove(os.path.join(O._dir(), n))
            except OSError:
                pass

    def test_names_fall_back_to_id(self):
        """Unknown ids are shown by id, known ones by display name."""
        info = mock.Mock()
        info.get_display_name.return_value = "Firefox"
        with mock.patch("sonata2.apps.lookup", side_effect=lambda i: info if i == "firefox" else None):
            self.assertEqual(O.names(["firefox", "ghost"]), ["Firefox", "ghost"])

    def test_note_coalesces_a_burst_into_one_write(self):
        """Several notes within the delay give one save with the latest list."""
        with mock.patch.object(O, "DELAY_MS", 30), mock.patch.object(O, "save_now") as save:
            O.note(["a"])
            O.note(["a", "b"])
            settle(150)
        save.assert_called_once_with(["a", "b"])

    def test_read_ignores_garbage_file(self):
        """A broken cache file reads as an empty list."""
        os.makedirs(O._dir(), exist_ok=True)
        with open(os.path.join(O._dir(), O.FILE), "w") as f:
            f.write("not json")
        self.assertEqual(O._read(O.FILE), [])
        with open(os.path.join(O._dir(), O.FILE), "w") as f:
            json.dump({"apps": ["a", 3, None, "b"]}, f)
        self.assertEqual(O._read(O.FILE), ["a", "b"])

    def test_read_non_list_apps(self):
        """{"apps": <not a list>} reads as empty instead of raising."""
        os.makedirs(O._dir(), exist_ok=True)
        with open(os.path.join(O._dir(), O.FILE), "w") as f:
            json.dump({"apps": 5}, f)
        self.assertEqual(O._read(O.FILE), [])


# -- the puff of smoke -----------------------------------------------------------------------------
class PoofFrameTest(unittest.TestCase):
    def test_swells_then_fades_inside_its_box(self):
        """Puffs grow from t=0 to 1, alpha ends at 0, all stay inside the SIZE box."""
        start, end = poof.frame(0.0), poof.frame(1.0)
        self.assertEqual(len(start), len(poof.PUFFS))
        self.assertTrue(all(a == 1.0 for *_x, a in start))
        self.assertTrue(all(a == 0.0 for *_x, a in end))
        self.assertTrue(all(e[2] > s[2] for s, e in zip(start, end)))
        for t in (0.0, 0.5, 1.0):
            for cx, cy, r, _a in poof.frame(t):
                self.assertGreaterEqual(cx - r, -0.01)
                self.assertLessEqual(cx + r, 1.01)
                self.assertGreaterEqual(cy - r, -0.01)
                self.assertLessEqual(cy + r, 1.01)


# -- drops / menu helpers -------------------------------------------------------------------------
class DropHelpersTest(unittest.TestCase):
    def test_folder_uri_kept_raw(self):
        """A Launchpad folder URI is kept as-is (GVfs would rewrite it) and decoded."""
        uri = M.encode_folder("Games", ["a", "b"])
        item = dock_drop._item(uri)
        self.assertIsInstance(item, dock_drop.RawUri)
        self.assertIsNone(item.get_path())
        self.assertEqual(dock_drop._folders([item, Gio.File.new_for_path("/tmp/x")]),
                         [{"folder": "Games", "apps": ["a", "b"]}])
        self.assertIsInstance(dock_drop._item("file:///tmp/x"), Gio.File)

    def test_can_open_needs_files_and_info(self):
        """Nothing to open, or no app: the icon doesn't light up."""
        self.assertFalse(dock_drop.can_open(None, [Gio.File.new_for_path("/tmp")]))
        self.assertFalse(dock_drop.can_open(mock.Mock(), []))

    def test_app_file_survives_unbound_get_filename(self):
        """Open File Location works whatever way PyGObject binds get_filename."""
        class Info:
            def get_executable(self):
                return "flatpak"
            get_filename = staticmethod(lambda self: "/usr/share/applications/x.desktop")
        self.assertEqual(dock_menu.app_file(Info()), "/usr/share/applications/x.desktop")


# -- stacks (folder contents) --------------------------------------------------------------------
class StackItemsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i, name in enumerate(("b.txt", "A.txt", ".hidden", "c.png")):
            p = os.path.join(self.dir, name)
            with open(p, "w") as f:
                f.write("x")
            os.utime(p, (1000 + i, 1000 + i))

    def test_sorted_by_name_hidden_skipped(self):
        """Name sort is case-insensitive; dot files never show."""
        names = [i.get_name() for i, _f in dock_stack._items(self.dir, "name")]
        self.assertEqual(names, ["A.txt", "b.txt", "c.png"])

    def test_sorted_by_modified_newest_first(self):
        """Date Modified: newest first."""
        names = [i.get_name() for i, _f in dock_stack._items(self.dir, "modified")]
        self.assertEqual(names, ["c.png", "A.txt", "b.txt"])

    def test_capped_and_missing_folder(self):
        """At most MAX_ITEMS items; a missing folder gives nothing (no crash)."""
        with mock.patch.object(dock_stack, "MAX_ITEMS", 2):
            self.assertEqual(len(dock_stack._items(self.dir, "name")), 2)
        self.assertEqual(dock_stack._items(os.path.join(self.dir, "nope"), "name"), [])


# -- desktop grid math -----------------------------------------------------------------------------
class DesktopGridTest(unittest.TestCase):
    def grid(self, w=1920, h=1080, margins=(14, 14, 80)):
        from sonata2.shell import desktop as DK
        ns = types.SimpleNamespace(_size=(w, h), _margins=margins, cfg={"sort": "none", "positions": {}},
                                   screen="DP-1", main=True)
        for name in ("rows", "cols", "cell_xy", "cell_at", "_display_of", "mine"):
            setattr(ns, name, getattr(DK.Desktop, name).__get__(ns))
        return DK, ns

    def test_cell_round_trip(self):
        """Every cell's top-left maps back to the same cell (columns from the right)."""
        _DK, g = self.grid()
        self.assertEqual(g.cols(), (1920 - 28) // 96)
        self.assertEqual(g.rows(), (1080 - 34 - 80) // 104)
        for c in (0, 3, g.cols() - 1):
            for r in (0, g.rows() - 1):
                x, y = g.cell_xy(c, r)
                self.assertEqual(g.cell_at(x + 1, y + 1), (c, r))
        self.assertEqual(g.cell_at(-500, 99999), (g.cols() - 1, g.rows() - 1))     # clamped

    def test_icons_follow_their_display(self):
        """A spot naming an unplugged display shows on the main desktop only."""
        DK, g = self.grid()
        g.cfg["positions"] = {"a": [0, 0, "HDMI-A-1"], "b": [1, 1], "c": [0, 1, "DP-1"]}
        with mock.patch.object(DK, "_connected", return_value={"DP-1", "HDMI-A-1"}):
            self.assertEqual([n for n in "abc" if g.mine(n)], ["b", "c"])
        with mock.patch.object(DK, "_connected", return_value={"DP-1"}):
            self.assertEqual([n for n in "abc" if g.mine(n)], ["a", "b", "c"])
        g.main, g.screen = False, "HDMI-A-1"
        with mock.patch.object(DK, "_connected", return_value={"DP-1", "HDMI-A-1"}):
            self.assertEqual([n for n in "abc" if g.mine(n)], ["a"])


# -- Mission Control backdrop ----------------------------------------------------------------------
class FakeWin:
    def __init__(self):
        self.opacity, self.visible = 1.0, True

    def get_opacity(self):
        return self.opacity

    def set_opacity(self, v):
        self.opacity = v

    def get_visible(self):
        return self.visible

    def set_visible(self, v):
        self.visible = v

    def present(self):
        self.visible = True


class MissionTest(unittest.TestCase):
    def make(self):
        from sonata2.shell import mission
        with mock.patch.object(mission.layer, "layer_shell", return_value=None):
            mb = mission.MissionBackdrop(None)
        win = FakeWin()
        win.backdrop = mock.Mock()
        mb._window = lambda name: win
        return mission, mb, win

    def test_hides_after_scale_ends(self):
        """Scale off on that display: the backdrop fades out and hides."""
        mission, mb, win = self.make()
        with mock.patch.object(mission, "wallpaper_texture", return_value=None), \
                mock.patch.object(mission.layer, "set_input_region"):
            mb._event({"plugin": "scale", "state": True, "output-data": {"name": "DP-1"}})
            settle(mission.FADE_MS + 120)
            mb.windows["DP-1"] = win
            mb._event({"plugin": "scale", "state": False, "output-data": {"name": "DP-1"}})
            settle(mission.FADE_MS + 120)
        self.assertFalse(win.visible)

    def test_quick_off_on_keeps_backdrop(self):
        """Scale toggled off then on quickly: the backdrop stays shown."""
        mission, mb, win = self.make()
        mb.windows["DP-1"] = win
        mb.active.add(("scale", "DP-1"))
        with mock.patch.object(mission, "wallpaper_texture", return_value=None), \
                mock.patch.object(mission.layer, "set_input_region"):
            mb._event({"plugin": "scale", "state": False, "output-data": {"name": "DP-1"}})
            settle(80)
            mb._event({"plugin": "scale", "state": True, "output-data": {"name": "DP-1"}})
            settle(mission.FADE_MS + 200)
        self.assertTrue(win.visible)
        self.assertAlmostEqual(win.opacity, 1.0, places=2)


# -- fixes without a widget ------------------------------------------------------------------------------
class ReviewFixTest(unittest.TestCase):
    def test_corrupt_layout_shapes(self):
        """pages/hidden/apps of the wrong type (hand-edited launchpad.json) are dropped."""
        m = M.Model({"pages": None, "hidden": 5}, installed(2))
        self.assertEqual(m.all_apps(), ["app000", "app001"])
        m = M.Model({"pages": [[["app000"], {"folder": 3, "apps": ["app000", "app001"]}, 7], "x"]},
                    installed(2))
        self.assertEqual(m.pages, [[{"folder": "Untitled Folder", "apps": ["app000", "app001"]}]])
        self.assertEqual(M.Model("junk", installed(1)).all_apps(), ["app000"])

    def test_open_apps_written_atomically(self):
        """open-apps.json goes through config.atomic_write (unique temp, no shared .tmp)."""
        with mock.patch.object(config, "atomic_write") as aw:
            O._write(O.FILE, ["a"])
        path, data = aw.call_args[0][:2]
        self.assertEqual(path, os.path.join(O._dir(), O.FILE))
        self.assertEqual(json.loads(data), {"apps": ["a"]})

    def test_open_at_login_survives_unbound_get_filename(self):
        """Open at Login copies the .desktop file whatever way get_filename is bound."""
        src = tempfile.NamedTemporaryFile(suffix=".desktop", delete=False)
        src.write(b"[Desktop Entry]\n")
        src.close()

        class Info:
            def get_id(self):
                return "zz-review.desktop"
            get_filename = staticmethod(lambda self: src.name)
        with mock.patch.object(dock_menu, "AUTOSTART_DIR", tempfile.mkdtemp()):
            dock_menu.set_open_at_login(Info(), True)
            self.assertTrue(dock_menu.opens_at_login("zz-review"))

    def test_launchpad_never_calls_get_filename(self):
        """Launchpad's Show in Files / drag to Dock use apps.app_filename (GioUnix-safe)."""
        import inspect
        from sonata2.shell import launchpad as L
        self.assertNotIn(".get_filename(", inspect.getsource(L))
        self.assertNotIn(".get_filename(", inspect.getsource(dock_menu))

    def test_launchpad_search_meta_built_once(self):
        """Search text for the apps is built once, again only when the apps change."""
        from sonata2.shell import launchpad as L
        info = mock.Mock()
        info.get_display_name.return_value = "X"
        info.get_executable.return_value = "x"
        ns = types.SimpleNamespace(installed={"app000": info, "app001": info},
                                   model=M.Model({}, installed(2)))
        with mock.patch.object(L.apps, "_entry_field", return_value="") as field, \
                mock.patch("sonata2.shell.spotlight._keywords", return_value=""):
            first = L.Launchpad._search_meta(ns)
            self.assertIs(L.Launchpad._search_meta(ns), first)
            self.assertEqual(field.call_count, 2)
            ns.model.hide("app001")
            self.assertEqual(list(L.Launchpad._search_meta(ns)), ["app000"])

    def test_launchpad_dock_config_cached(self):
        """dock.json is read once for the grid math, not on every allocation."""
        from sonata2.shell import launchpad as L
        ns = types.SimpleNamespace(_dock_cfg=None)
        with mock.patch.object(L.config, "load", return_value={"position": "bottom"}) as load:
            for _ in range(3):
                L.Launchpad._dock_config(ns)
        self.assertEqual(load.call_count, 1)

    def test_apps_menu_sized_for_main_display(self):
        """Not shown yet: the Apps Menu is sized for the main display, not monitor 0."""
        from sonata2.shell import launchpad_window as LW, monitors
        mon = mock.Mock()
        mon.get_geometry.return_value = types.SimpleNamespace(width=3000, height=2000)
        ns = types.SimpleNamespace(pad=mock.Mock(), panel=mock.Mock())
        ns.pad.get_surface.return_value = None
        with mock.patch.object(monitors, "main", return_value=mon):
            LW.MenuView._size(ns)
        ns.panel.set_size_request.assert_called_once_with(*LW.panel_size(3000, 2000))

    def test_mission_wallpaper_decoded_once(self):
        """Mission Control reuses the decoded wallpaper; a settings change drops it."""
        from sonata2.shell import mission
        with mock.patch.object(mission.layer, "layer_shell", return_value=None):
            mb = mission.MissionBackdrop(None)
        with mock.patch.object(mission, "wallpaper_texture", return_value=object()) as wt:
            first = mb._wallpaper()
            self.assertIs(mb._wallpaper(), first)
            mb._textures.clear()                          # what the prefs watch does
            mb._wallpaper()
        self.assertEqual(wt.call_count, 2)


# -- headless widget checks (Dock with no apps) ------------------------------------------------------
class DockWidgetTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        self.cfg = bare_cfg(magnification=True)
        D.load_css(self.cfg)
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle(150)

    def tearDown(self):
        self.dock.detach()
        self.win.destroy()

    def test_layer_shell_absent_on_x11(self):
        """Without Wayland layer-shell the Dock falls back to a plain window."""
        from sonata2.shell import layer
        self.assertIsNone(layer.layer_shell())
        self.assertFalse(layer.anchor_edge(Gtk.Window(), "x", "bottom", 0))
        self.assertFalse(layer.take_keyboard(self.win, True))

    def test_trash_and_divider_present(self):
        """An empty Dock still has the divider and the Trash; no app tiles."""
        self.assertEqual(self.dock.app_tiles(), [])
        self.assertIs(self.dock.all_tiles()[-1], self.dock.trash)
        self.assertFalse(self.dock.recent_sep.get_visible())

    def test_badge_text(self):
        """LauncherEntry counts become badges: 0/invisible -> none, >99 -> "99+"."""
        d = self.dock
        tile = d._add_tile("chat", "Chat", Gio.ThemedIcon.new("x"))
        V = GLib.Variant
        d._launcher_update(None, None, None, None, None,
                           V("(sa{sv})", ("application://chat.desktop", {"count": V("x", 120),
                                                                          "count-visible": V("b", True)})))
        self.assertEqual(tile.icon.badge, "99+")
        d._launcher_update(None, None, None, None, None,
                           V("(sa{sv})", ("application://chat.desktop", {"count-visible": V("b", False)})))
        self.assertEqual(tile.icon.badge, "")

    def test_folder_lifecycle_without_installed_apps(self):
        """Folder add/remove/ungroup keeps pins consistent (apps resolved via a fake lookup)."""
        d = self.dock
        info = mock.Mock()
        info.get_display_name.return_value = "X"
        with mock.patch("sonata2.apps.lookup", return_value=info), \
                mock.patch("sonata2.icons.app_icon", return_value=Gio.ThemedIcon.new("x")), \
                mock.patch("sonata2.shell.dock_preview.attach"):
            d.cfg["pinned"] = ["a", "b", "c"]
            fkey = d.make_folder(["a", "b"], name="W")
            self.assertEqual(d.cfg["pinned"], [fkey, "c"])
            d.remove_from_folder(fkey, "a")                 # one left: ungrouped
            self.assertNotIn(fkey, d.cfg["pinned"])
            self.assertEqual(sorted(d.cfg["pinned"]), ["a", "b", "c"])
            self.assertEqual(d.cfg["folders"], {})

    def test_stack_save_keeps_chosen_size(self):
        """Adding a stack saves the user's chosen icon size, not the fitted one."""
        d = self.dock
        d.user_size, d.cfg["icon_size"] = 48, 30            # the fit shrank the icons
        d.stacks.add(tempfile.mkdtemp())
        self.assertEqual(config.load("dock", D.DEFAULTS)["icon_size"], 48)

    def test_removed_stack_stops_watching(self):
        """Removing a stack cancels its folder monitor."""
        d = self.dock
        d.stacks.add(tempfile.mkdtemp())
        tile = d.stacks.tiles()[-1]
        mon = d.stacks._monitors[-1]
        d.stacks.remove(tile)
        self.assertTrue(mon.is_cancelled())

    def test_detach_drops_appearance_listener(self):
        """A detached Dock is no longer referenced by the theme's listeners."""
        from sonata2.ui import theme
        self.dock.detach()
        self.assertFalse(any(getattr(h(), "__self__", None) is self.dock for h in theme._listeners))

    def test_detach_releases_bus_and_monitors(self):
        """detach() unsubscribes LauncherEntry and cancels the Trash and stack monitors."""
        d = self.dock
        bus = mock.Mock()
        d._launcher_sub = (bus, 42)
        d.stacks.add(tempfile.mkdtemp())
        stack_mon, trash_mon = d.stacks._monitors[-1], d._trash_mon
        d.detach()
        bus.signal_unsubscribe.assert_called_once_with(42)
        self.assertTrue(stack_mon.is_cancelled())
        self.assertTrue(trash_mon.is_cancelled())
        self.assertEqual(d._apps_mon, (None, 0))

    def test_stack_refresh_debounced(self):
        """A burst of folder events re-lists the stack's folder once."""
        d = self.dock
        d.stacks.add(tempfile.mkdtemp())
        tile = d.stacks.tiles()[-1]
        with mock.patch.object(d.stacks, "refresh_icon") as refresh:
            for _ in range(5):
                d.stacks._refresh_later(tile)
            settle(dock_stack.REFRESH_MS + 150)
        refresh.assert_called_once_with(tile)

    def test_rectangles_fetched_off_the_main_thread(self):
        """Allocation-driven minimize targets ask Wayfire in a worker thread."""
        seen, got = [], []
        from sonata2.wl import wfipc
        with mock.patch.object(wfipc.WayfireIPC, "call",
                               lambda _s, m, *_a: (seen.append(threading.current_thread()), ["v"])[1]), \
                mock.patch.object(self.dock, "_update_rectangles", side_effect=got.append):
            self.dock._update_rectangles_bg()
            for _ in range(100):
                if got:
                    break
                settle(10)
        self.assertEqual(got, [["v"]])
        self.assertIsNot(seen[0], threading.main_thread())

    def test_poof_spot_in_display_coordinates(self):
        """The fallback puff is placed in display coordinates, just off the plate."""
        d = self.dock
        native = d.get_native()
        ok, p = d.compute_point(native, D.Graphene.Point().init(100, 0))
        x, y = d._poof_spot({"x": 100}, 1920, 1080, 96)
        self.assertAlmostEqual(x, p.x, places=3)
        x0, y0, _w, _h = d.plate_rect()
        self.assertAlmostEqual(y, 1080 - native.get_height() + p.y + y0 - 48, places=3)

    def test_poof_spot_side_dock(self):
        """A left Dock: the puff goes right of the plate, at the icon's height."""
        win = Gtk.Window()
        side = D.Dock(bare_cfg(position="left"))
        win.set_child(side)
        win.present()
        settle(150)
        try:
            ok, p = side.compute_point(win, D.Graphene.Point().init(0, 30))
            x, y = side._poof_spot({"y": 30}, 1920, 1080, 96)
            x0, _y0, pw, _h = side.plate_rect()
            self.assertAlmostEqual(y, p.y, places=3)
            self.assertAlmostEqual(x, p.x + x0 + pw + 48, places=3)
        finally:
            side.detach()
            win.destroy()

    def test_interrupted_slide_stops_frame_stats(self):
        """Auto-hide reversed mid-slide: the first slide's FrameStats is stopped."""
        made = []
        real = D.ui.transition.FrameStats

        class Rec(real):
            def __init__(s, *a):
                super().__init__(*a)
                made.append(s)
        host = Gtk.Window()
        host.set_child(Gtk.Box(width_request=50, height_request=50))
        host.present()
        host._hidden, host._hide_anim, host.dock = False, None, self.dock
        host._update_input = lambda: None
        settle(50)
        try:
            with mock.patch.object(D.ui.transition, "FrameStats", Rec):
                D.DockWindow._slide(host, True)
                settle(30)
                D.DockWindow._slide(host, False)
                settle(D.HIDE_MS + 300)
            self.assertEqual(len(made), 2)
            self.assertTrue(all(s.tick is None for s in made))
        finally:
            host.destroy()

    def test_interrupted_magnify_stops_frame_stats(self):
        """Every FrameStats started by the magnification wave is stopped."""
        made = []
        real = D.ui.transition.FrameStats

        class Rec(real):
            def __init__(s, *a):
                super().__init__(*a)
                made.append(s)
        with mock.patch.object(D.ui.transition, "FrameStats", Rec):
            self.dock._mag_animate(1.0, D.MAG_IN_MS)
            settle(30)
            self.dock._mag_animate(0.0, D.MAG_OUT_MS)
            settle(D.MAG_OUT_MS + 300)
        self.assertEqual(len(made), 2)
        self.assertTrue(all(s.tick is None for s in made))


# -- every user-facing action animates (the real code paths) ----------------------------------------
def source(module) -> str:
    import inspect
    return inspect.getsource(module)


class AnimationTests(unittest.TestCase):
    """Each main Dock / Launchpad action starts an animation: an Adw
    animation playing, icons gliding (ui.transition.glide_play), a CSS
    animation class, or a frame-clock tick."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Adw
        Adw.init()
        D.ui.setup()

    def setUp(self):
        Gtk.init()
        self.cfg = bare_cfg(magnification=True)
        D.load_css(self.cfg)
        info = mock.Mock()
        info.get_display_name.return_value = "X"
        self.patches = [mock.patch("sonata2.apps.lookup", return_value=info),
                        mock.patch("sonata2.icons.app_icon", return_value=Gio.ThemedIcon.new("x")),
                        mock.patch("sonata2.shell.dock_preview.attach")]
        for p in self.patches:
            p.start()
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg)
        self.win.set_child(self.dock)
        self.win.present()
        for k in ("a", "b", "c"):
            self.dock.pin_at(k)
        settle(200)
        self.glides = []
        real = D.ui.transition.glide_play
        self.glide = mock.patch.object(D.ui.transition, "glide_play",
                                       side_effect=lambda before, *a, **k: (self.glides.append(before),
                                                                            real(before, *a, **k)))
        self.glide.start()

    def tearDown(self):
        self.glide.stop()
        for p in self.patches:
            p.stop()
        self.dock.detach()
        self.win.destroy()

    def playing(self, anim):
        from gi.repository import Adw
        return anim is not None and anim.get_state() == Adw.AnimationState.PLAYING

    def test_dock_add_icon_others_slide_aside(self):
        """Keep in Dock at a place: the icons after it glide aside."""
        first = self.dock.app_tiles()[0]
        self.dock.pin_at("d", before=first)
        self.assertTrue(self.glides and self.glides[-1])

    def test_dock_reorder_glides(self):
        """Dragging an icon to another slot: the others glide."""
        tile = self.dock.tiles["a"]
        self.dock._move_to_slot(tile, 2)
        self.assertTrue(self.glides and self.glides[-1])

    def test_dock_drop_settles(self):
        """A dropped icon glides from the pointer into its slot."""
        self.dock._settle(self.dock.tiles["b"], 3.0, 3.0)
        self.assertTrue(self.glides)

    def test_dock_remove_closes_up(self):
        """Remove from Dock: the empty slot shrinks with an Adw animation."""
        self.dock.set_pinned("a", False)
        slot = next(w for w in self._children() if w.has_css_class("dock-closing-slot"))
        self.assertTrue(self.playing(slot._anim))

    def test_launchpad_drag_opens_a_gap(self):
        """An app dragged in from Launchpad: the icons make room (glide)."""
        ok, b = self.dock.tiles["b"].compute_bounds(self.dock)
        self.dock.show_drop_gap(b.get_x() + 2, b.get_y() + 2)
        self.assertTrue(self.glides and self.glides[-1])
        self.dock.hide_drop_gap()

    def test_magnification_animates(self):
        """Pointer enters: the magnification wave eases in (Adw.TimedAnimation)."""
        self.dock._mag_animate(1.0, D.MAG_IN_MS)
        self.assertTrue(self.playing(self.dock._mag_anim))

    def test_autohide_slides(self):
        """Auto-hide: the Dock slides out with an Adw animation."""
        host = Gtk.Window()
        host.set_child(Gtk.Box(width_request=40, height_request=40))
        host.present()
        host._hidden, host._hide_anim, host.dock = False, None, self.dock
        host._update_input = lambda: None
        settle(50)
        try:
            D.DockWindow._slide(host, True)
            self.assertTrue(self.playing(host._hide_anim))
        finally:
            host.destroy()

    def test_launch_bounces(self):
        """Launching an app: its icon gets the CSS bounce animation."""
        tile = self.dock.tiles["c"]
        tile.bounce(D.BOUNCE_MS)
        self.assertTrue(tile.has_css_class("launching"))
        self.assertIn("dock-tile.launching .dock-icon { animation:", D.CSS)
        tile._stop_bounce()

    def test_folder_panel_zooms_in(self):
        """Opening a Dock folder: its panel zooms in (CSS animation class)."""
        key = self.dock.make_folder(["a", "b"], name="W")
        settle(50)
        pop = F.open_panel(self.dock, self.dock.tiles[key])
        try:
            self.assertTrue(pop.view.has_css_class("dock-folder-view"))
            self.assertIn("dock-folder-view { animation:", source(F))
        finally:
            pop.popdown()
            settle(50)

    def test_minimize_genie_aimed_at_icon(self):
        """Minimize (genie): the window's target is its Dock icon's rectangle."""
        mgr = mock.Mock()
        self.dock.manager = mgr
        try:
            self.dock._aim_at(self.dock.tiles["a"], ["w"])
        finally:
            self.dock.manager = None
        args = mgr.set_rectangle.call_args[0]
        self.assertEqual(args[0], "w")
        self.assertGreater(args[4], 0)
        self.assertGreater(args[5], 0)

    def test_poof_plays(self):
        """Dragged off the Dock: the puff of smoke runs on the frame clock."""
        w = poof.Poof(None, None, 0, 0)
        w.present()
        settle(120)
        t = w.cloud.t
        w.destroy()
        self.assertGreater(t, 0.0)

    def test_mission_backdrop_fades(self):
        """Mission Control: the backdrop fades in over several frames."""
        from sonata2.shell import mission
        with mock.patch.object(mission.layer, "layer_shell", return_value=None):
            mb = mission.MissionBackdrop(None)
        win = FakeWin()
        win.opacity = 0.0
        mb._fade(win, 1.0)
        settle(mission.FADE_MS // 3)
        self.assertTrue(0.0 < win.opacity < 1.0)
        settle(mission.FADE_MS + 100)
        self.assertAlmostEqual(win.opacity, 1.0, places=2)

    def _children(self):
        out, w = [], self.dock.get_first_child()
        while w is not None:
            out.append(w)
            w = w.get_next_sibling()
        return out


class LaunchpadAnimationTests(unittest.TestCase):
    """Launchpad open / close / page flip / folders, and the Apps Menu."""

    @classmethod
    def setUpClass(cls):
        from gi.repository import Adw
        Adw.init()
        D.ui.setup()

    def setUp(self):
        from tests.test_launchpad_window import APPS
        from sonata2.shell import launchpad as L, launchpad_window as LW
        self.L, self.LW = L, LW
        config.save("launchpad", {"pages": [], "hidden": []})
        config.save(LW.NAME, {})
        self.p = mock.patch.object(L, "installed_apps", return_value=dict(APPS))
        self.p.start()
        self.pad = L.Launchpad(None)
        self.pad._dock_above = lambda *_a: None
        self.pad.set_default_size(1400, 900)

    def tearDown(self):
        self.pad.destroy()
        self.p.stop()
        config.save(self.LW.NAME, {})

    def playing(self, anim):
        from gi.repository import Adw
        return anim is not None and anim.get_state() == Adw.AnimationState.PLAYING

    def test_open_and_close_animate(self):
        """Launchpad opens and closes with an Adw animation of its zoom/fade."""
        self.pad.open_launchpad()
        for _ in range(100):
            if self.pad._anim is not None:
                break
            settle(10)
        self.assertTrue(self.playing(self.pad._anim))
        settle(self.L.OPEN_MS + 200)
        self.pad.close_launchpad()
        self.assertTrue(self.playing(self.pad._anim))
        settle(self.L.CLOSE_MS + 200)
        self.assertFalse(self.pad.get_visible())

    def test_page_flip_animates(self):
        """Turning a page scrolls the carousel with its animation."""
        self.pad.open_launchpad()
        settle(300)
        with mock.patch.object(self.pad.carousel, "scroll_to") as scroll:
            self.pad._flip(0)
        self.assertTrue(scroll.call_args[0][1])          # animate=True

    def test_folder_opens_with_zoom(self):
        """A Launchpad folder opens with the folder-in CSS animation."""
        self.pad.open_launchpad()
        settle(300)
        folder = {"folder": "F", "apps": ["gimp", "calc"]}
        self.pad._open_folder(folder)
        self.assertTrue(self.pad.folder_view[0].has_css_class("lp-folder-view"))
        self.assertIn("lp-folder-view { animation:", source(self.L))

    def test_apps_menu_opens_animated(self):
        """The Apps Menu (Launchpad as a window) opens with its CSS animation."""
        config.save(self.LW.NAME, {"style": "window"})
        self.pad.open_launchpad()
        settle(200)
        self.assertTrue(self.pad.menu.panel.has_css_class("opening"))
        self.assertIn("lpw-panel.opening { animation:", source(self.LW))


if __name__ == "__main__":
    unittest.main()
