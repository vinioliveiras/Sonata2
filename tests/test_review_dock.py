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

    # BUG: a launchpad.json folder with "apps": null (hand-edited / older
    # writer) makes Model() raise TypeError -> Launchpad can't start.
    @unittest.expectedFailure
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

    # BUG: check_password's thread only schedules an idle callback; the idle
    # lambda itself calls pam.authenticate, so PAM (with its fail delay)
    # runs on the GTK main loop and freezes the Dock.
    @unittest.expectedFailure
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

    # BUG: _read catches ValueError/AttributeError but not TypeError:
    # {"apps": 5} raises and breaks Feedbacker's before_crash().
    @unittest.expectedFailure
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

    # BUG: under GLib 2.80 + GioUnix (apps.DesktopAppInfo), get_filename is
    # bound unbound ("takes exactly 1 argument"); app_file / set_open_at_login /
    # Launchpad's "Show in Files" raise TypeError (tests.test_dock fails too).
    @unittest.expectedFailure
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

    # BUG: _fade never cancels a running fade: scale turned off and on again
    # within FADE_MS -> the old fade-out finishes last-but-one and its done()
    # hides the window while scale is active (no backdrop behind the windows).
    @unittest.expectedFailure
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

    # BUG: StackRow._save writes self.dock.cfg directly (config.save), so
    # icon_size is the shrunk-to-fit size, not the size the user chose
    # (Dock.save_cfg writes user_size). Adding a stack on a full Dock
    # permanently shrinks it.
    @unittest.expectedFailure
    def test_stack_save_keeps_chosen_size(self):
        """Adding a stack saves the user's chosen icon size, not the fitted one."""
        d = self.dock
        d.user_size, d.cfg["icon_size"] = 48, 30            # the fit shrank the icons
        d.stacks.add(tempfile.mkdtemp())
        self.assertEqual(config.load("dock", D.DEFAULTS)["icon_size"], 48)

    # BUG: StackRow.remove leaves the folder's Gio.FileMonitor running (kept
    # in _monitors, never cancelled): a leak that keeps refreshing a gone tile.
    @unittest.expectedFailure
    def test_removed_stack_stops_watching(self):
        """Removing a stack cancels its folder monitor."""
        d = self.dock
        d.stacks.add(tempfile.mkdtemp())
        tile = d.stacks.tiles()[-1]
        mon = d.stacks._monitors[-1]
        d.stacks.remove(tile)
        self.assertTrue(mon.is_cancelled())

    # BUG: Dock.detach() (called on every rebuild: position / recents change)
    # doesn't undo ui.on_change(self._appearance_changed) -- ui.theme has no
    # way to remove a listener -- nor the LauncherEntry D-Bus subscription,
    # so every rebuilt Dock (its whole widget tree) stays alive.
    @unittest.expectedFailure
    def test_detach_drops_appearance_listener(self):
        """A detached Dock is no longer referenced by the theme's listeners."""
        from sonata2.ui import theme
        self.dock.detach()
        self.assertFalse(any(getattr(cb, "__self__", None) is self.dock for cb in theme._listeners))

    # BUG: _mag_animate pauses the running Adw animation, which never emits
    # "done": its FrameStats is never stopped, so its tick callback runs
    # every frame forever and its list of frame times grows without bound.
    # Same pattern in DockWindow._slide and Launchpad._animate.
    @unittest.expectedFailure
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


if __name__ == "__main__":
    unittest.main()
