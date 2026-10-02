"""Dock folders: the model (keys, icon grid, names), making a folder,
moving apps in and out, ungrouping, removing, the open panel, and that a
folder survives a save + reload. Run:
xvfb-run python3 -m unittest tests.test_dock_folder"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.shell import dock as D, dock_folder as F  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class ModelTest(unittest.TestCase):
    def test_keys(self):
        self.assertTrue(F.is_folder("folder:3"))
        self.assertFalse(F.is_folder("org.gnome.Calculator.desktop"))
        self.assertFalse(F.is_folder({"folder": "x"}))
        self.assertEqual(F.folder_id("folder:12"), "12")
        self.assertEqual(F.new_id({}), "1")
        self.assertEqual(F.new_id({"1": {}, "2": {}}), "3")
        self.assertEqual(F.new_id({"2": {}}), "1")

    def test_mini_grid_fits_inside(self):
        rects = F.mini_rects(48, 12)
        self.assertEqual(len(rects), 9)                       # at most 3 x 3
        for x, y, side in rects:
            self.assertGreater(x, 0)
            self.assertGreater(y, 0)
            self.assertLessEqual(x + side, 48)
            self.assertLessEqual(y + side, 48)
        self.assertEqual(rects[1][1], rects[0][1])            # row by row
        self.assertGreater(rects[3][1], rects[0][1])

    def test_launchpad_shape(self):
        self.assertEqual(F.as_launchpad({"name": "Games", "apps": ["a"]}), {"folder": "Games", "apps": ["a"]})

    def test_folders_is_a_known_key(self):
        # config.load keeps only known keys: "folders" must be a default
        self.assertIn("folders", D.DEFAULTS)


class DockFolderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        cfg = D.load_config()
        D.load_css(cfg)
        cls.apps = [k for k in cfg["pinned"] if k not in D.PERMANENT and D.apps.lookup(k)]
        if len(cls.apps) < 3:
            raise unittest.SkipTest("needs 3 installed default apps")

    def setUp(self):
        cfg = D.load_config()
        cfg["folders"] = {}
        self.win = Gtk.Window()
        self.dock = D.Dock(cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle()

    def tearDown(self):
        self.win.destroy()
        config.save("dock", {})

    def test_make_folder_takes_first_place(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        at = d.cfg["pinned"].index(a)
        fkey = d.make_folder([a, b], name="Work")
        self.assertEqual(d.cfg["pinned"][at], fkey)
        self.assertNotIn(a, d.cfg["pinned"])
        self.assertNotIn(b, d.cfg["pinned"])
        self.assertEqual(d.folder(fkey), {"name": "Work", "apps": [a, b]})
        self.assertIn(fkey, d.tiles)
        self.assertIsInstance(d.tiles[fkey].icon, F.FolderIcon)
        self.assertEqual(d.tiles[fkey].icon.keys, [a, b])
        settle()
        self.assertNotIn(a, d.tiles)                          # not running: its own icon left
        self.assertTrue(d.in_folder(a))

    def test_default_name(self):
        # regression: DesktopAppInfo.get_categories() needs an argument on
        # newer GI -- the name came from a crash instead of the category
        name = F.default_name(self.apps[:2])
        self.assertIsInstance(name, str)
        self.assertTrue(name)

    def test_saved_and_reloaded(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b], name="Work")
        cfg = D.load_config()                                 # what the next login reads
        self.assertIn(fkey, cfg["pinned"])
        self.assertEqual(cfg["folders"][F.folder_id(fkey)]["apps"], [a, b])
        d2 = D.Dock(cfg)
        self.assertIn(fkey, d2.tiles)
        d2._save_order()                                      # a reorder keeps the folder
        self.assertIn(fkey, d2.cfg["pinned"])
        d2.forget_missing()                                   # a folder isn't "uninstalled"
        self.assertIn(fkey, d2.cfg["pinned"])

    def test_add_and_remove(self):
        d, a, b, c = self.dock, *self.apps[:3]
        fkey = d.make_folder([a, b])
        d.add_to_folder(fkey, c)
        self.assertEqual(d.folder(fkey)["apps"], [a, b, c])
        self.assertNotIn(c, d.cfg["pinned"])
        self.assertEqual(d.tiles[fkey].icon.keys, [a, b, c])
        d.remove_from_folder(fkey, c)                         # back right after the folder
        i = d.cfg["pinned"].index(fkey)
        self.assertEqual(d.cfg["pinned"][i + 1], c)
        self.assertIn(c, d.tiles)

    def test_one_app_left_ungroups(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        at = d.cfg["pinned"].index(fkey)
        d.remove_from_folder(fkey, b)
        self.assertNotIn(fkey, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})
        self.assertEqual(d.cfg["pinned"][at:at + 2], [a, b])
        settle()
        self.assertNotIn(fkey, d.tiles)

    def test_ungroup_and_remove(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        d.ungroup(fkey)
        self.assertIn(a, d.cfg["pinned"])
        self.assertIn(b, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})
        fkey = d.make_folder([a, b])
        d.set_pinned(fkey, False)                             # Remove from Dock
        self.assertNotIn(fkey, d.cfg["pinned"])
        self.assertEqual(d.cfg["folders"], {})

    def test_permanent_apps_stay_out(self):
        d = self.dock
        self.assertIsNone(d.make_folder(list(D.PERMANENT)))
        fkey = d.make_folder(self.apps[:2])
        d.add_to_folder(fkey, D.PERMANENT[0])
        self.assertNotIn(D.PERMANENT[0], d.folder(fkey)["apps"])
        self.assertEqual(F.app_items(d, D.PERMANENT[0]), [])
        self.assertEqual(F.app_items(d, fkey), [])

    def test_menu_items(self):
        d, a, b, c = self.dock, *self.apps[:3]
        self.assertEqual([i.label for i in F.app_items(d, c)], ["Add to New Folder"])
        d.make_folder([a, b], name="Work")
        self.assertEqual([i.label for i in F.app_items(d, c)], ["Add to New Folder", "Move to “Work”"])

    def test_click_opens_panel_with_apps(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b], name="Work")
        settle()
        pop = F.open_panel(d, d.tiles[fkey])
        settle()
        self.assertTrue(pop.view.has_css_class("dock-folder-view"))     # the zoom-in animation
        keys = []
        child = pop.flow.get_first_child()
        while child is not None:
            keys.append(child.get_child().key)
            child = child.get_next_sibling()
        self.assertEqual(keys, [a, b])
        F.close_panel(pop)
        self.assertTrue(pop.view.has_css_class("closing"))
        settle(F.CLOSE_MS + 400)
        self.assertFalse(pop.get_visible())

    # -- part 2: drop an app on another --------------------------------------------
    def _drag(self, key):
        d = self.dock
        d._drag = {"key": key, "index": d.app_tiles().index(d.tiles[key]), "left": False, "dropped": False}

    def _centre(self, tile):
        ok, b = tile.compute_bounds(self.dock)
        self.assertTrue(ok)
        return b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2

    def test_hold_over_app_then_drop_makes_folder(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        at = d.cfg["pinned"].index(b)
        order = [t.key for t in d.app_tiles()]
        self._drag(a)
        x, y = self._centre(d.tiles[b])
        d._drag_motion(None, x, y)
        self.assertEqual([t.key for t in d.app_tiles()], order)        # no reordering over the middle
        self.assertFalse(d.tiles[b].has_css_class("folder-target"))    # not before the hold
        settle(D.FOLDER_HOLD_MS + 100)
        self.assertTrue(d.tiles[b].has_css_class("folder-target"))
        target = d.tiles[b]
        self.assertTrue(d._drag_drop(None, a, x, y))
        self.assertFalse(target.has_css_class("folder-target"))
        fkey = next(k for k in d.cfg["pinned"] if F.is_folder(k))
        self.assertEqual(d.folder(fkey)["apps"], [b, a])               # the one under it first
        self.assertEqual(d.cfg["pinned"].index(fkey), at - 1)          # in its place (a left before it)
        self.assertNotIn(a, d.cfg["pinned"])
        self.assertNotIn(b, d.cfg["pinned"])

    def test_drop_on_folder_adds(self):
        d, a, b, c = self.dock, *self.apps[:3]
        fkey = d.make_folder([a, b])
        settle()
        self._drag(c)
        x, y = self._centre(d.tiles[fkey])
        d._drag_motion(None, x, y)
        settle(D.FOLDER_HOLD_MS + 100)
        d._drag_drop(None, c, x, y)
        self.assertEqual(d.folder(fkey)["apps"], [a, b, c])

    def test_quick_pass_only_reorders(self):
        # moving across an icon without stopping must not make a folder
        d, a, b = self.dock, self.apps[0], self.apps[1]
        self._drag(a)
        x, y = self._centre(d.tiles[b])
        d._drag_motion(None, x, y)
        settle(D.FOLDER_HOLD_MS // 3)
        d._drag_motion(None, x + d.tiles[b].get_width() / 2, y)       # moved on before the hold (between icons)
        settle(D.FOLDER_HOLD_MS + 100)
        self.assertFalse(d.tiles[b].has_css_class("folder-target"))
        d._drag_drop(None, a, x, y)
        self.assertFalse(any(F.is_folder(k) for k in d.cfg["pinned"]))
        self.assertIn(a, d.cfg["pinned"])

    def test_folder_or_permanent_cant_be_dropped_in(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        settle()
        perm = next(k for k in D.PERMANENT if k in d.tiles)
        x, y = self._centre(d.tiles[self.apps[2]])
        self.assertIsNone(d._folder_candidate(d.tiles[fkey], x, y))    # a folder is only moved
        self.assertIsNone(d._folder_candidate(d.tiles[perm], x, y))    # Files / Launchpad too
        x, y = self._centre(d.tiles[perm])
        self.assertIsNone(d._folder_candidate(d.tiles[self.apps[2]], x, y))   # nor onto them

    # -- part 3: locked folders ----------------------------------------------------
    def _locked(self):
        d = self.dock
        fkey = d.make_folder(self.apps[:2], name="Private")
        d.set_folder_locked(fkey, True)
        settle()
        return fkey

    def test_lock_saved_and_icon_hides_apps(self):
        d = self.dock
        fkey = self._locked()
        self.assertTrue(d.folder(fkey)["locked"])
        self.assertTrue(d.tiles[fkey].icon.locked)
        self.assertTrue(D.load_config()["folders"][F.folder_id(fkey)]["locked"])
        d2 = D.Dock(D.load_config())                          # after a login: still locked
        self.assertTrue(d2.tiles[fkey].icon.locked)
        d.set_folder_locked(fkey, False)
        self.assertNotIn("locked", d.folder(fkey))
        self.assertFalse(d.tiles[fkey].icon.locked)

    def test_locked_icon_draws(self):
        icon = F.FolderIcon(self.apps[:3], 48, locked=True)
        snap = Gtk.Snapshot()
        icon.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())
        self.assertEqual(len(F.lock_paths(48)), 2)

    def _open_with(self, fkey, ok, then=None):
        from unittest import mock
        calls = []
        with mock.patch.object(F, "check_password", side_effect=lambda pw, done: (calls.append(pw), done(ok))):
            pop = F.open_panel(self.dock, self.dock.tiles[fkey], then=then)
            settle()
            self.assertIsNotNone(pop.lock)                    # the password first
            self.assertIsNone(pop.flow)                       # nothing of the apps yet
            pop.entry.set_text("secret")
            pop.entry.emit("activate")
            settle()
        self.assertEqual(calls, ["secret"])
        return pop

    def test_locked_opens_only_with_password(self):
        fkey = self._locked()
        pop = self._open_with(fkey, False)
        self.assertIsNotNone(pop.lock)                        # wrong: still asking
        self.assertIsNone(pop.flow)
        self.assertEqual(pop.hint.get_label(), "Wrong password")
        self.assertEqual(pop.entry.get_text(), "")
        pop.popdown()
        settle()
        pop = self._open_with(fkey, True)
        self.assertIsNone(pop.lock)
        self.assertIsNotNone(pop.flow)                        # right: the apps
        self.assertTrue(self.dock.folder(fkey)["locked"])     # opening doesn't unlock it
        pop.popdown()

    def test_unlock_and_ungroup_ask_first(self):
        d = self.dock
        fkey = self._locked()
        self._open_with(fkey, False, then=lambda: d.set_folder_locked(fkey, False))
        self.assertTrue(d.folder(fkey)["locked"])
        self._open_with(fkey, True, then=lambda: d.set_folder_locked(fkey, False))
        self.assertNotIn("locked", d.folder(fkey))
        d.set_folder_locked(fkey, True)
        self._open_with(fkey, True, then=lambda: d.ungroup(fkey))
        self.assertNotIn(fkey, d.cfg["pinned"])

    def test_locked_folder_menu(self):
        from unittest import mock
        d = self.dock
        fkey = self._locked()
        with mock.patch.object(F.ui.menu, "popup", side_effect=lambda w, sections, **k: sections):
            labels = [i.label for sec in F.folder_menu(d, d.tiles[fkey]) for i in sec]
        self.assertIn("Unlock Folder\u2026", labels)
        self.assertIn("Ungroup\u2026", labels)                 # asks: it shows the apps
        d.set_folder_locked(fkey, False)
        with mock.patch.object(F.ui.menu, "popup", side_effect=lambda w, sections, **k: sections):
            labels = [i.label for sec in F.folder_menu(d, d.tiles[fkey]) for i in sec]
        self.assertIn("Lock Folder", labels)

    def test_drop_into_locked_folder(self):
        # adding needs no password (nothing inside is shown)
        d, c = self.dock, self.apps[2]
        fkey = self._locked()
        d.add_to_folder(fkey, c)
        self.assertIn(c, d.folder(fkey)["apps"])
        self.assertTrue(d.folder(fkey)["locked"])

    # -- part 4: Launchpad <-> Dock ------------------------------------------------
    def test_folder_drag_carries_its_apps(self):
        from sonata2.launchpad_model import decode_folder
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b], name="Work")
        self.assertEqual(decode_folder(d._drag_text(fkey)), {"folder": "Work", "apps": [a, b]})
        self.assertEqual(d._drag_text(self.apps[2]), self.apps[2])            # an app: its id
        d.set_folder_locked(fkey, True)
        self.assertIsNone(decode_folder(d._drag_text(fkey)))                  # locked: never shown elsewhere

    def test_folder_uri_read_from_the_drop_as_it_came(self):
        """Regression (Vini: Launchpad folders couldn't be dragged to the Dock):
        the uri went through Gio.File, which GVfs may rewrite for an unknown
        scheme; it's read from the drop's text/uri-list and kept as is."""
        from gi.repository import Gio
        from sonata2.launchpad_model import decode_folder, encode_folder
        from sonata2.shell import dock_drop
        uri = encode_folder("Games & Fun", ["a.desktop", "b"])
        data = (uri + "\r\n" + "file:///tmp/x.desktop\r\n").encode()

        class Drop:
            def read_async(self, mimes, _prio, _cancel, cb):
                self.mimes = mimes
                cb(self, None)

            def read_finish(self, _res):
                return Gio.MemoryInputStream.new_from_data(data), dock_drop.URI_LIST
        got = []
        dock_drop._read_uris(Drop(), got.append)
        settle(100)
        files = got[0]
        self.assertIsInstance(files[0], dock_drop.RawUri)
        self.assertEqual(decode_folder(files[0].get_uri()), {"folder": "Games & Fun", "apps": ["a.desktop", "b"]})
        self.assertIsNone(files[0].get_path())
        self.assertEqual(files[1].get_path(), "/tmp/x.desktop")            # ordinary files: Gio.File
        self.assertFalse(dock_drop._is_app(files[0]))
        self.assertFalse(dock_drop._is_dir(files[0]))

    def test_launchpad_folder_dropped_on_dock(self):
        from gi.repository import Gio
        from sonata2.launchpad_model import encode_folder
        from sonata2.shell import dock_drop
        d, a, b, c = self.dock, *self.apps[:3]
        files = [dock_drop._item(encode_folder("Games", [a, b, "not.installed"]))]
        self.assertEqual(dock_drop._folders(files), [{"folder": "Games", "apps": [a, b, "not.installed"]}])
        self.assertEqual(dock_drop._folders([Gio.File.new_for_path("/tmp")]), [])
        before = d.tiles[c]
        self.assertTrue(dock_drop.add_folders(d, dock_drop._folders(files), before=before))
        fkey = next(k for k in d.cfg["pinned"] if F.is_folder(k))
        self.assertEqual(d.folder(fkey), {"name": "Games", "apps": [a, b]})
        pins = d.cfg["pinned"]
        self.assertEqual(pins.index(fkey) + 1, pins.index(c))                 # right before where dropped
        self.assertNotIn(a, pins)
        self.assertFalse(dock_drop.add_folders(d, [{"folder": "X", "apps": ["nope"]}]))

    def test_magnifying_reuses_mini_icons(self):
        # performance: drawn once per 8 px step and kept; a sweep only scales
        icon = F.FolderIcon(self.apps[:3], 48)
        for size in range(48, 81):                       # a magnification sweep
            icon.set_size(size)
            icon.do_snapshot(Gtk.Snapshot())
        self.assertLessEqual(len(icon._nodes), 5)
        self.assertLessEqual(len({k[1] for k in icon._paint}), 5)
        built = dict(icon._nodes)
        for size in range(80, 47, -1):                   # and back: nothing drawn again
            icon.set_size(size)
            icon.do_snapshot(Gtk.Snapshot())
        self.assertEqual(icon._nodes, built)
        icon.set_locked(True)                            # a new look: drawn again
        self.assertEqual(icon._nodes, {})

    def test_panel_kept_and_reopened(self):
        d, a, b, c = self.dock, *self.apps[:3]
        fkey = d.make_folder([a, b])
        settle()
        tile = d.tiles[fkey]
        pop = F.open_panel(d, tile)
        settle()
        pop.popdown()
        settle()
        again = F.open_panel(d, tile)
        self.assertIs(again, pop)                       # not built again
        settle()
        self.assertTrue(pop.view.has_css_class("dock-folder-view"))     # its zoom-in replays
        self.assertEqual(pop.view.get_opacity(), 1)
        pop.popdown()
        settle()
        d.add_to_folder(fkey, c)                        # its apps changed: a new panel
        new = F.open_panel(d, tile)
        self.assertIsNot(new, pop)
        self.assertIsNone(pop.get_parent())
        new.popdown()
        settle()
        d.ungroup(fkey)                                 # the tile goes: its panel too
        settle(400)
        self.assertIsNone(new.get_parent())

    def test_locked_panel_never_kept(self):
        fkey = self._locked()
        pop = F.open_panel(self.dock, self.dock.tiles[fkey])
        self.assertIsNone(getattr(self.dock.tiles[fkey], "folder_pop", None))
        pop.popdown()

    def test_drop_targets_measured_once_per_layout(self):
        d, a, b = self.dock, self.apps[0], self.apps[1]
        self._drag(a)
        x, y = self._centre(d.tiles[b])
        d._folder_candidate(d.tiles[a], x, y)
        first = d._drag["bounds"]
        for _ in range(5):                              # pointer motions: the same measures
            self.assertIs(d._folder_candidate(d.tiles[a], x, y), d.tiles[b])
        self.assertIs(d._drag["bounds"], first)
        d._move_to_slot(d.tiles[a], 0)                  # a reorder: measured again
        self.assertNotIn("bounds", d._drag)
        d._drag = None

    def test_folder_drag_has_its_icon(self):
        """Vini: dragging a Dock folder showed big text by the pointer -- a
        folder has no gicon, the drag icon failed and GTK drew the drag's
        text. It hangs the folder's own picture now."""
        from unittest import mock
        from gi.repository import Gdk
        d, a, b = self.dock, self.apps[0], self.apps[1]
        fkey = d.make_folder([a, b])
        settle()
        tex = d.tiles[fkey].icon.texture(48)
        self.assertIsInstance(tex, Gdk.Texture)
        self.assertEqual((tex.get_width(), tex.get_height()), (48, 48))
        hung = []
        with mock.patch.object(D.ui.drag, "hang", side_effect=lambda drag, pic, size: hung.append(pic)):
            d._drag_begin(None, object(), d.tiles[fkey])
        self.assertIsInstance(hung[0], Gdk.Texture)
        d.tiles[fkey].remove_css_class("dragging")
        d._drag = None

    def test_panel_has_the_docks_glass(self):
        import inspect
        src = inspect.getsource(F)
        self.assertIn("background-color: %(dock_material)s", src)

    def test_launchpad_folders_use_the_same_icon(self):
        """Vini: Launchpad's folders as the Dock's -- exactly an app's frame."""
        from sonata2.shell import launchpad
        icon = launchpad.LaunchItem._folder_icon(None, {"folder": "W", "apps": self.apps[:3]}, 64)
        self.assertIsInstance(icon, F.FolderIcon)
        self.assertTrue(icon.on_scrim)
        self.assertTrue(icon.has_css_class("lp-folder"))             # jiggle / folder-target still apply
        self.assertEqual(icon.do_measure(Gtk.Orientation.HORIZONTAL, -1)[0], 64)
        hidden = launchpad.LaunchItem._folder_icon(None, {"folder": "Hidden", "apps": [], "locked": True}, 64)
        self.assertTrue(hidden.locked)
        snap = Gtk.Snapshot()
        hidden.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())

    def test_folder_reaches_the_other_displays_dock(self):
        """Vini (Dock on every display): making a folder made the apps vanish
        and no folder appeared -- the other display's Dock read the new pins
        without the new folder, dropped "folder:N", took the apps out and
        saved, and the two Docks undid it."""
        import copy
        import json
        from types import SimpleNamespace
        d, a, b = self.dock, self.apps[0], self.apps[1]
        other_cfg = copy.deepcopy(d.cfg)
        other = D.Dock(other_cfg)
        w2 = Gtk.Window(child=other)
        w2.present()
        settle()
        fkey = d.make_folder([a, b], name="Work")                     # on this display
        path = os.path.join(config.CONFIG_DIR, "dock.json")
        saved = open(path).read()
        win = SimpleNamespace(cfg=other_cfg, dock=other, LIVE_KEYS=D.DockWindow.LIVE_KEYS,
                              REBUILD_KEYS=D.DockWindow.REBUILD_KEYS, rebuild=lambda: None)
        D.DockWindow._config_changed(win)                            # the other display hears of it
        settle(400)
        self.assertIn(fkey, other.tiles)
        self.assertEqual(other.folder(fkey)["apps"], [a, b])
        self.assertNotIn(a, other.tiles)
        self.assertEqual(open(path).read(), saved)                   # it didn't write back
        on_disk = json.loads(saved)
        self.assertIn(fkey, on_disk["pinned"])
        self.assertEqual(on_disk["folders"][F.folder_id(fkey)]["apps"], [a, b])
        d.add_to_folder(fkey, self.apps[2])                          # changes follow too
        D.DockWindow._config_changed(win)
        self.assertEqual(other.tiles[fkey].icon.keys, [a, b, self.apps[2]])
        w2.destroy()

    def test_folder_removed_in_a_puff(self):
        """Vini: removing a folder from the Dock: the puff of smoke, like an app."""
        from unittest import mock
        d = self.dock
        fkey = d.make_folder(self.apps[:2])
        settle()
        with mock.patch.object(D.Dock, "_poof") as puff:
            F.remove_with_puff(d, d.tiles[fkey])
        puff.assert_called_once()
        self.assertNotIn(fkey, d.cfg["pinned"])

    def test_dropped_outside_puffs_too(self):
        """The puff never showed: an icon dropped on the desktop (which takes
        drops) ended the drag with "delete", a path without the puff."""
        from unittest import mock
        d, a = self.dock, self.apps[0]
        tile = d.tiles[a]
        d._drag = {"key": a, "index": 0, "left": True, "dropped": False}
        with mock.patch.object(D.Dock, "_poof") as puff:
            d._drag_end(None, None, True, tile)
        puff.assert_called_once()
        self.assertNotIn(a, d.cfg["pinned"])

    def test_poof_window_is_see_through(self):
        import inspect
        from sonata2.shell import poof
        self.assertIn("window.sonata-poof { background: none", inspect.getsource(poof))

    def test_icon_draws(self):
        icon = F.FolderIcon(self.apps[:3], 48)
        self.assertEqual(icon.do_measure(Gtk.Orientation.HORIZONTAL, -1)[0], 48)
        icon.set_size(60)
        self.assertEqual(icon.do_measure(Gtk.Orientation.HORIZONTAL, -1)[0], 60)
        snap = Gtk.Snapshot()
        icon.do_snapshot(snap)
        self.assertIsNotNone(snap.to_node())


if __name__ == "__main__":
    unittest.main()
