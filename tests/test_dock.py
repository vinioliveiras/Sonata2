"""Dock logic tests on a real (virtual) display: drag-reorder, drag-out,
Keep in Dock. Run: xvfb-run python3 -m unittest tests.test_dock
Uses a temporary XDG_CONFIG_HOME and the apps installed on the machine."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.shell import dock as D, dock_menu  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class OrderTest(unittest.TestCase):
    """Regression: the Dock's order changed after a log-in -- a pinned app
    without an icon at that moment (Flatpak not seen yet, a Steam game) was
    dropped on the next save, then came back at the end."""

    def test_merge_keeps_untiled_in_place(self):
        self.assertEqual(D.merge_order(["a", "b", "x", "c"], ["a", "b", "c"]), ["a", "b", "x", "c"])
        self.assertEqual(D.merge_order(["a", "b", "x", "c"], ["c", "a", "b"]), ["c", "a", "x", "b"])
        self.assertEqual(D.merge_order(["a", "b", "new"], ["a", "new", "b", "run"]), ["a", "new", "b"])
        self.assertEqual(D.merge_order(["a", "a"], ["a"]), ["a"])

    def test_pinned_kept_through_a_save(self):
        from unittest import mock
        Gtk.init()
        cfg = D.load_config()
        D.load_css(cfg)
        real = [k for k in cfg["pinned"] if k not in D.PERMANENT]
        if len(real) < 2:
            self.skipTest("needs 2 installed default apps")
        cfg = dict(cfg, pinned=cfg["pinned"][:2] + ["not.installed.yet", "steam_app_570"] + cfg["pinned"][2:])
        with mock.patch("sonata2.steamgames.name", side_effect=lambda aid: "Dota 2" if aid == "570" else None), \
                mock.patch("sonata2.steamgames.icon_path", return_value=None):
            win = Gtk.Window()
            d = D.Dock(cfg)
            win.set_child(d)
            win.present()
            settle()
            self.assertIn("steam_app_570", d.tiles)               # a pinned Steam game has its icon
            self.assertNotIn("not.installed.yet", d.tiles)
            d._save_order()
            self.assertEqual(d.cfg["pinned"][2:4], ["not.installed.yet", "steam_app_570"])
            d.forget_missing()                                    # Steam games aren't "uninstalled"
            self.assertIn("steam_app_570", d.cfg["pinned"])
            win.destroy()


class DropAnimationTest(unittest.TestCase):
    def test_poof_swells_then_fades(self):
        """Dragged out of the Dock: a puff of smoke at the pointer (macOS)."""
        from sonata2.shell import poof
        start, mid, end = poof.frame(0.0), poof.frame(0.5), poof.frame(1.0)
        self.assertGreater(mid[0][2], start[0][2])              # swells
        self.assertEqual(start[0][3], 1.0)
        self.assertEqual(end[0][3], 0.0)                        # gone
        self.assertNotIn("sonata2", "sonata-poof")              # (not blurred)

    def test_drop_settles_without_a_blank_frame(self):
        """Regression: on drop the icon blinked in its slot (it stayed hidden until
        the drag ended): it glides from the pointer into place, shown at once."""
        import inspect
        src = inspect.getsource(D.Dock._drag_drop)
        self.assertIn("_settle", src)
        settle = inspect.getsource(D.Dock._settle)
        self.assertIn('remove_css_class("dragging")', settle)
        self.assertIn("glide_play", settle)
        self.assertIn("_poof", inspect.getsource(D.Dock._drag_cancel))


class DockTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        self.cfg = D.load_config()
        D.load_css(self.cfg)
        if len(self.cfg["pinned"]) < 3:
            self.skipTest("needs at least 3 installed default apps")
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle()

    def tearDown(self):
        self.win.destroy()

    def keys(self):
        return [t.key for t in self.dock.app_tiles()]

    def removable(self):
        return [k for k in self.keys() if k not in D.PERMANENT]

    def center_x(self, key):
        ok, b = self.dock.tiles[key].compute_bounds(self.dock)
        return b.get_x() + b.get_width() / 2

    def start_drag(self, key):
        self.dock._drag = {"key": key, "index": self.keys().index(key), "left": False, "dropped": False}

    def test_settle_glides_the_dropped_icon(self):
        keys = self.removable()
        tile = self.dock.tiles[keys[0]]
        tile.add_css_class("dragging")
        self.dock._settle(tile, 5.0, 5.0)
        self.assertFalse(tile.has_css_class("dragging"))
        settle(80)
        dx, dy = getattr(tile, "_glide", (0, 0))
        self.assertTrue(abs(dx) > 0.5 or abs(dy) > 0.5)        # still on its way into the slot

    def test_drag_to_trash_uninstalls(self):
        """Vini: a Dock icon dragged onto the Trash is uninstalled (asked
        first); it stays in the Dock until that's done."""
        key = self.removable()[0]
        self.start_drag(key)
        ok, b = self.dock.trash.compute_bounds(self.dock)
        x, y = b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2
        asked = []
        self.dock._uninstall_dropped = lambda t: asked.append(t.key)
        self.assertEqual(self.dock._drag_motion(None, x, y), Gdk.DragAction.MOVE)
        self.assertTrue(self.dock.trash.has_css_class("drop-hover"))
        self.assertEqual(self.keys().index(key), self.dock._drag["index"])   # kept its place
        self.assertTrue(self.dock._drag_drop(None, None, x, y))
        self.assertEqual(asked, [key])
        self.assertIn(key, self.dock.cfg["pinned"])                         # until uninstalled
        self.assertFalse(self.dock.trash.has_css_class("drop-hover"))
        files = self.dock.tiles[D.PERMANENT[0]] if D.PERMANENT[0] in self.dock.tiles else None
        if files is not None:
            self.assertFalse(self.dock.can_uninstall(files))                 # Files never

    def test_drag_out_closes_up(self):
        key = self.removable()[0]
        self.start_drag(key)
        self.dock._drag_leave(None)
        self.assertFalse(self.dock.tiles[key].get_visible())      # the others close up
        self.dock._drag_motion(None, self.center_x(self.keys()[0]), 10)
        self.assertTrue(self.dock.tiles[key].get_visible())       # back over the Dock

    def test_drop_gap(self):
        keys = self.keys()
        x = (self.center_x(keys[0]) + self.center_x(keys[1])) / 2      # between the first two
        self.dock.show_drop_gap(x, 10)
        settle(100)
        self.assertEqual(self.dock._gap.slot, 1)
        self.assertIs(self.dock._gap.get_prev_sibling(), self.dock.tiles[keys[0]])
        self.dock._relayout()                                   # a window opening meanwhile
        self.assertIs(self.dock._gap.get_prev_sibling(), self.dock.tiles[keys[0]])
        self.assertEqual(self.dock.hide_drop_gap(), 1)
        self.assertIsNone(self.dock._gap.get_parent())
        self.assertEqual(self.keys(), keys)

    def test_reorder_first_to_last(self):
        first, last = self.keys()[0], self.keys()[-1]
        self.start_drag(first)
        self.dock._drag_motion(None, self.center_x(last) + 5, 10)
        self.assertEqual(self.keys()[-1], first)
        self.dock._drag_drop(None, first, 0, 0)
        self.assertEqual(config.load("dock", D.DEFAULTS)["pinned"][-1], first)

    def test_esc_restores_order(self):
        before = self.keys()
        self.start_drag(before[0])
        self.dock._drag_motion(None, self.center_x(before[2]) + 5, 10)
        self.dock._drag_cancel(None, None, Gdk.DragCancelReason.USER_CANCELLED,
                               self.dock.tiles[before[0]])
        self.assertEqual(self.keys(), before)

    def test_drag_out_removes(self):
        key = self.removable()[1]
        self.start_drag(key)
        self.dock._drag_leave(None)
        self.dock._drag_cancel(None, None, Gdk.DragCancelReason.NO_TARGET, self.dock.tiles[key])
        self.assertNotIn(key, self.keys())
        self.assertNotIn(key, config.load("dock", D.DEFAULTS)["pinned"])

    def test_menus_build(self):
        key = self.keys()[0]
        pop = dock_menu.app_menu(self.dock, key, self.dock.tiles[key])
        self.assertIsNotNone(pop.get_menu_model())
        pop.popdown()
        dock_menu.trash_menu(self.dock.trash).popdown()

    def test_app_file_is_real_path(self):
        path = dock_menu.app_file(self.dock.tiles[self.keys()[0]].info)
        self.assertTrue(os.path.isabs(path) and os.path.exists(path), path)

    def test_pin_at_position(self):
        key = self.removable()[0]
        self.dock.set_pinned(key, False)
        self.dock.pin_at(key, before=self.dock.tiles[self.keys()[1]])
        self.assertEqual(self.keys().index(key), 1)
        self.assertIn(key, config.load("dock", D.DEFAULTS)["pinned"])

    def test_pin_at_keeps_one_already_kept(self):
        """Vini: an app dragged in from Launchpad that's already in the Dock
        moved there; it stays where it is (and bounces)."""
        keys = self.keys()
        key = keys[-1]
        order = list(self.dock.cfg["pinned"])
        self.dock.pin_at(key, before=self.dock.tiles[keys[0]])
        self.assertEqual(self.keys(), keys)
        self.assertEqual(self.dock.cfg["pinned"], order)
        self.assertTrue(self.dock.tiles[key].has_css_class("launching"))

    def test_can_open_by_mime(self):
        from gi.repository import Gio
        from sonata2.shell import dock_drop
        path = os.path.join(os.environ["XDG_CONFIG_HOME"], "a.txt")
        with open(path, "w") as fh:
            fh.write("hi")
        f = Gio.File.new_for_path(path)
        info = self.dock.tiles[self.keys()[0]].info
        expect = any(Gio.content_type_is_a("text/plain", t) for t in (info.get_supported_types() or []))
        self.assertEqual(dock_drop.can_open(info, [f]), expect)

    def test_resize_saves(self):
        self.dock.set_icon_size(64)
        self.assertEqual(config.load("dock", D.DEFAULTS)["icon_size"], 64)
        self.assertEqual(self.dock.tiles[self.keys()[0]].icon._size, 64)
        self.dock.set_icon_size(500)
        self.assertEqual(self.cfg["icon_size"], D.MAX_SIZE)

    def test_magnification_wave(self):
        self.dock.set_magnification(True, 80)
        tiles = self.dock.all_tiles()
        ok, b = tiles[1].compute_bounds(self.win)
        self.dock._mag_pos, self.dock._mag_strength = b.get_x() + b.get_width() / 2, 1.0
        self.dock._apply_magnification()
        sizes = [t.icon._size for t in tiles]
        self.assertEqual(max(sizes), sizes[1])          # biggest under the pointer
        self.assertGreater(sizes[1], sizes[0])
        self.assertGreater(sizes[0], self.cfg["icon_size"] - 1)
        self.assertEqual(sizes[-1], self.cfg["icon_size"])   # far away: unchanged
        self.dock._mag_strength = 0.0
        self.dock._apply_magnification()
        self.assertTrue(all(t.icon._size == self.cfg["icon_size"] for t in tiles))

    def test_side_positions(self):
        for edge in ("left", "right"):
            cfg = dict(self.cfg, position=edge)
            d = D.Dock(cfg)
            w = Gtk.Window()
            w.set_child(d)
            w.present()
            settle()
            x, y, pw, ph = d.plate_rect()
            self.assertEqual(pw, D.plate_height(cfg))
            self.assertEqual(x, 0 if edge == "left" else d.get_width() - pw)
            self.assertTrue(d.vertical)
            w.destroy()

    def test_recents(self):
        from sonata2 import apps
        key = self.keys()[-1]
        self.dock.set_pinned(key, False)          # not running, not recent -> gone
        self.assertNotIn(key, self.dock.tiles)
        self.dock._note_recent(key)
        self.assertEqual(self.cfg["recent"][0], key)
        info = apps.lookup(key)
        self.dock._add_tile(key, info.get_display_name(), info.get_icon(), info)
        self.dock._relayout()
        self.assertTrue(self.dock.recent_sep.get_visible())
        self.assertIs(self.dock._first_extra(), self.dock.tiles[key])

    def test_stack_panel(self):
        from sonata2.shell import dock_stack
        folder = os.path.join(os.environ["XDG_CONFIG_HOME"], "stackdir")
        os.makedirs(folder, exist_ok=True)
        for n in ("a.txt", "b.txt"):
            with open(os.path.join(folder, n), "w") as fh:
                fh.write(n)
        self.dock.stacks.add(folder)
        tile = self.dock.stacks.tiles()[-1]
        self.assertEqual(len(dock_stack._items(folder, "name")), 2)
        pop = self.dock.stacks.open_panel(tile)
        settle()
        pop.popdown()
        dock_stack.stack_menu(self.dock.stacks, tile).popdown()

    def test_dropped_folder_opens_its_place_and_leaves_when_deleted(self):
        """Vini: a folder dragged onto the Dock (from the desktop) just popped
        in, and once deleted it stayed in the Dock."""
        import shutil
        from sonata2.shell import dock_stack
        folder = os.path.join(os.environ["XDG_CONFIG_HOME"], "fromdesktop")
        os.makedirs(folder, exist_ok=True)
        if self.dock.get_root() is None:
            win = Gtk.Window(child=self.dock)
            win.present()
            self.addCleanup(win.destroy)
        settle()
        self.assertTrue(self.dock.get_mapped())
        self.dock.stacks.add(folder)
        tile = self.dock.stacks.tiles()[-1]
        self.assertFalse(tile.get_visible())                         # its place opens first
        end = GLib.get_monotonic_time() + (D.OPEN_UP_MS * 2 + 400) * 1000
        while GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertTrue(tile.get_visible())
        self.assertAlmostEqual(tile.get_opacity(), 1.0, places=2)    # faded in
        shutil.rmtree(folder)                                        # deleted
        end = GLib.get_monotonic_time() + (dock_stack.REFRESH_MS + 1500) * 1000
        while tile in self.dock.stacks.tiles() and GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertNotIn(tile, self.dock.stacks.tiles())
        self.assertNotIn(folder, [s["path"] for s in self.cfg["stacks"]])

    def test_folder_deleted_while_the_dock_was_off(self):
        from sonata2.shell import dock_stack
        missing = os.path.join(os.environ["XDG_CONFIG_HOME"], "deleted-meanwhile")
        self.assertTrue(dock_stack.gone(missing))
        self.assertTrue(dock_stack.gone("/media/unplugged-drive/Photos"))    # its drive unplugged: gone too
        self.assertFalse(dock_stack.gone(os.environ["XDG_CONFIG_HOME"]))

    def test_unplugged_drive_folder_leaves(self):
        """Vini: a pen drive's / external disk's folder leaves the Dock when
        the drive is unplugged."""
        import shutil
        folder = os.path.join(os.environ["XDG_CONFIG_HOME"], "pendrive")
        os.makedirs(folder, exist_ok=True)
        self.dock.stacks.add(folder)
        tile = self.dock.stacks.tiles()[-1]
        shutil.rmtree(folder)                                        # (the mount gone)
        self.dock.stacks.drop_gone()                                 # what mount-removed runs
        self.assertNotIn(tile, self.dock.stacks.tiles())
        import inspect
        from sonata2.shell import dock_stack
        self.assertIn('"mount-removed"', inspect.getsource(dock_stack.StackRow._drives))

    def test_keep_in_dock_toggle(self):
        key = self.removable()[0]
        self.dock.set_pinned(key, False)
        self.assertNotIn(key, self.keys())

    def test_files_and_launchpad_stay(self):
        for key in [k for k in D.PERMANENT if k in self.keys()]:
            self.dock.set_pinned(key, False)
            self.start_drag(key)
            self.dock._drag_leave(None)
            self.dock._drag_cancel(None, None, Gdk.DragCancelReason.NO_TARGET, self.dock.tiles[key])
            self.dock._drag = None
            self.assertIn(key, self.keys())
            self.assertIn(key, config.load("dock", D.DEFAULTS)["pinned"])


    def test_removed_icon_closes_up_smoothly(self):
        """Removing an icon: its place shrinks over a few frames (the Dock
        narrows smoothly), then nothing is left of it."""
        key = self.removable()[1]
        full = self.dock.get_width()
        cell = self.dock.tiles[key].get_width()
        self.dock.set_pinned(key, False)
        self.assertNotIn(key, self.keys())
        slots = [w for w in self._children() if w.has_css_class("dock-closing-slot")]
        self.assertEqual(len(slots), 1)
        settle(40)
        mid = self.dock.get_width()
        settle(D.CLOSE_UP_MS + 200)
        final = self.dock.get_width()
        self.assertFalse([w for w in self._children() if w.has_css_class("dock-closing-slot")])
        self.assertLessEqual(final, full - cell)
        self.assertTrue(final < mid < full, (full, mid, final))      # part of the way, not at once

    def _children(self):
        out, w = [], self.dock.get_first_child()
        while w is not None:
            out.append(w)
            w = w.get_next_sibling()
        return out

    def test_uninstalled_app_leaves_no_slot(self):
        key = self.removable()[0]
        from unittest import mock
        real = D.apps.lookup
        with mock.patch.object(D.apps, "lookup", lambda k: None if k == key else real(k)):
            self.dock.forget_missing()
        settle()
        self.assertNotIn(key, self.keys())
        self.assertNotIn(key, self.cfg["pinned"])


if __name__ == "__main__":
    unittest.main()


class GenieTargetTest(unittest.TestCase):
    """Regression (Vini): on the laptop's screen the genie flew to the wrong
    icon -- with a Dock on each display, a window got its minimize target from
    the other display's Dock (Wayfire can't translate it: "Minimize hint set
    to surface on a different output")."""

    class Win:
        def __init__(self, app_id, title):
            self.app_id, self.title, self.minimized, self.activated = app_id, title, False, True

    class Manager:
        def __init__(self):
            self.listeners, self.rects, self.available, self.toplevels = [], [], True, []

        def set_rectangle(self, t, _surface, x, y, w, h):
            self.rects.append((t.title, w > 0))

    def setUp(self):
        Gtk.init()
        self.cfg = dict(D.load_config(), all_displays=True)
        D.load_css(self.cfg)
        self.mgr = self.Manager()
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg, self.mgr)
        self.dock._schedule_sync = lambda *a: None
        self.win.set_child(self.dock)
        self.win.present()
        settle()
        from gi.repository import Gio
        self.key = "genie-test-app"
        self.dock._add_tile(self.key, "Test", Gio.ThemedIcon.new("application-x-executable"))
        settle(100)
        self.mgr.rects.clear()

    def tearDown(self):
        self.dock.detach()
        self.win.destroy()

    def aim(self, mine, placed, *wins):
        self.dock.windows = {self.key: list(wins)}
        self.dock._windows_here = lambda _s: (mine, placed)
        self.mgr.rects.clear()
        self.dock._update_rectangles()
        return self.mgr.rects

    def test_ipc_silent_aims_nothing(self):
        a = self.Win(self.key, "Doc")
        self.assertEqual(self.aim(set(), None, a), [])                 # tried again later instead

    def test_only_windows_on_this_display(self):
        here, there = self.Win(self.key, "Here"), self.Win(self.key, "There")
        rects = self.aim({(self.key, "Here")}, {(self.key, "Here"), (self.key, "There")}, here, there)
        self.assertEqual(rects, [("Here", True)])

    def test_unknown_title_of_an_app_on_both_displays(self):
        new = self.Win(self.key, "Renamed")                            # Wayfire hadn't seen this title
        rects = self.aim({(self.key, "Here")}, {(self.key, "Here"), (self.key, "There")}, new)
        self.assertEqual(rects, [])
        rects = self.aim({(self.key, "Here")}, {(self.key, "Here")}, new)   # the app only here: aimed
        self.assertEqual(rects, [("Renamed", True)])

    def test_docks_registered_for_display_changes(self):
        self.assertIn(self.dock, D._DOCKS)
        self.dock.detach()
        self.assertNotIn(self.dock, D._DOCKS)
