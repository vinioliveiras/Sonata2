"""Control Center you arrange yourself (Vini): modules on a 4-column grid
with their sizes; Edit Controls… (or holding a module) makes them jiggle,
like apps in Launchpad; drag to move -- the others glide --, the x takes one
out, Add Controls puts it back. The order is kept in controlcenter.json."""
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import controlcenter as C  # noqa: E402

Adw.init()


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TempConfig(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)


class PackTest(unittest.TestCase):
    def test_default_layout_is_the_big_sur_one(self):
        """Wi-Fi & Bluetooth 2x2 left; Do Not Disturb over Dark Mode + Screenshot right; rows below."""
        p = C.pack(C.DEFAULT_ORDER)
        self.assertEqual(p["connectivity"], (0, 0, 2, 2))
        self.assertEqual(p["dnd"], (2, 0, 2, 1))
        self.assertEqual(p["darkmode"], (2, 1, 1, 1))
        self.assertEqual(p["screenshot"], (3, 1, 1, 1))
        self.assertEqual([p[m][1] for m in ("display", "sound", "nowplaying")], [2, 3, 4])

    def test_small_modules_fill_holes_first(self):
        """First free place where it fits, left to right, top to bottom."""
        p = C.pack(["darkmode", "display", "screenshot"])
        self.assertEqual(p["darkmode"], (0, 0, 1, 1))
        self.assertEqual(p["display"], (0, 1, 4, 1))               # doesn't fit beside: next row
        self.assertEqual(p["screenshot"], (1, 0, 1, 1))            # back into the hole on row 0

    def test_never_overlaps(self):
        import itertools
        for order in itertools.permutations(["connectivity", "dnd", "darkmode", "screenshot"]):
            cells = []
            for col, row, w, h in C.pack(order).values():
                cells += [(col + x, row + y) for x in range(w) for y in range(h)]
            self.assertEqual(len(cells), len(set(cells)))


class OrderTest(TempConfig):
    def test_load_defaults_and_cleans_up(self):
        self.assertEqual(C.load(), C.DEFAULT_ORDER)
        config.save("controlcenter", {"modules": ["sound", "nope", "sound", 5, "dnd"]})
        self.assertEqual(C.load(), ["sound", "dnd"])               # unknown and repeated ones dropped
        config.save("controlcenter", {"modules": "junk"})
        self.assertEqual(C.load(), C.DEFAULT_ORDER)

    def test_hidden_is_what_add_controls_offers(self):
        self.assertEqual(C.hidden(C.DEFAULT_ORDER), [m for m in C.CATALOG if m.startswith("stat_")])
        self.assertEqual(C.hidden(["dnd"]), [m for m in C.CATALOG if m != "dnd"])


class GridTest(TempConfig):
    def grid(self, order=None):
        widgets = {m: Gtk.Button(label=m, width_request=20, height_request=20) for m in C.CATALOG}
        changes = []
        g = C.ModuleGrid(widgets, order or list(C.DEFAULT_ORDER), on_change=lambda o: changes.append(list(o)))
        win = Gtk.Window(default_width=320)
        win.set_child(g)
        win.present()
        self.addCleanup(win.destroy)
        settle(80)
        return g, changes

    def test_edit_mode_jiggles_and_shows_the_x(self):
        g, _ = self.grid()
        self.assertFalse(any(s.badge.get_visible() for s in g.slots.values()))
        g.set_editing(True)
        self.assertTrue(g.has_css_class("jiggle"))
        self.assertTrue(all(s.badge.get_visible() and s.cover.get_visible() for s in g.slots.values()))
        self.assertTrue(all(s.has_css_class("sonata-jiggle") for s in g.slots.values()))
        self.assertTrue(g.slots["display"].has_css_class("wide"))         # rows wiggle less
        g.set_editing(False)
        self.assertFalse(g.has_css_class("jiggle"))
        self.assertFalse(any(s.cover.get_visible() for s in g.slots.values()))   # modules work again

    def test_x_takes_a_module_out_and_saves(self):
        g, changes = self.grid()
        g.set_editing(True)
        g.slots["darkmode"].badge.emit("clicked")
        self.assertTrue(g.slots["darkmode"].has_css_class("cc-slot-leaving"))   # shrinks away first
        settle(400)
        self.assertNotIn("darkmode", g.order)
        self.assertNotIn("darkmode", C.load())
        self.assertEqual(C.pack(g.order)["screenshot"], (2, 1, 1, 1))          # the next one moves in
        self.assertNotIn("darkmode", changes[-1])

    def test_add_controls_puts_it_back_at_the_end(self):
        g, _ = self.grid(["dnd", "sound"])
        g.add_module("darkmode")
        self.assertEqual(g.order, ["dnd", "sound", "darkmode"])
        self.assertTrue(g.slots["darkmode"].has_css_class("cc-slot-arriving"))
        self.assertEqual(C.load(), ["dnd", "sound", "darkmode"])

    def test_dragging_reorders_live_and_saves_on_drop(self):
        g, _ = self.grid()
        g.set_editing(True)
        g._dragging = "nowplaying"
        ok, b = g.slots["display"].compute_bounds(g)
        self.assertTrue(ok)
        g._drag_over(b.get_x() + 5, b.get_y() + 5)                 # over Display: takes its place
        self.assertEqual(g.order.index("nowplaying"), C.DEFAULT_ORDER.index("display"))
        self.assertEqual(C.load(), C.DEFAULT_ORDER)                # not saved until dropped
        g._drop()
        self.assertEqual(C.load(), g.order)

    def test_drag_only_in_edit_mode(self):
        """Outside edit mode a press-and-drag belongs to the module (a slider)."""
        g, _ = self.grid()
        self.assertIsNone(g._drag_prepare(g.slots["sound"]))
        g.set_editing(True)
        self.assertIsNotNone(g._drag_prepare(g.slots["sound"]))


class AnimationTest(TempConfig):
    def test_reordering_glides_the_others(self):
        """Moved modules slide from their old place (ui.transition glide), not jump."""
        widgets = {m: Gtk.Button(label=m) for m in C.CATALOG}
        g = C.ModuleGrid(widgets, list(C.DEFAULT_ORDER))
        win = Gtk.Window(default_width=320)
        win.set_child(g)
        win.present()
        settle(100)
        with mock.patch.object(ui.transition, "_start") as start:
            g.move("nowplaying", 0)
            settle(100)
        self.assertTrue(start.called)
        win.destroy()

    def test_launchpad_shares_the_edit_mode(self):
        """One jiggle and one x for Launchpad and Control Center (ui.edit)."""
        import inspect
        from sonata2.shell import launchpad
        src = inspect.getsource(launchpad)
        self.assertIn("ui.edit.badge(", src)
        self.assertIn("ui.edit.hold(", src)
        self.assertIn("sonata-jiggle", src)
        self.assertNotIn("@keyframes lp-jiggle", src)


if __name__ == "__main__":
    unittest.main()


class FixedSizeTest(TempConfig):
    """Vini: adding CPU and GPU made Control Center huge -- its size is fixed,
    whatever the modules (more of them scroll inside it)."""

    def test_same_size_whatever_the_modules(self):
        import types
        from sonata2.shell import topbar as T
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None)
        sizes = set()
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"):
            for order in (C.DEFAULT_ORDER, list(C.CATALOG), ["dnd"], ["stat_net", "stat_cpu", "stat_gpu"]):
                config.save("controlcenter", {"modules": order})
                cc = T.ControlCenter(bar)
                win = Gtk.Window()
                win.set_child(cc)
                win.present()
                settle(150)
                sizes.add((cc.get_width(), cc.get_height()))
                cc.grid.set_editing(True)                            # edit mode doesn't resize it either
                settle(80)
                sizes.add((cc.get_width(), cc.get_height()))
                win.destroy()
        self.assertEqual(len(sizes), 1, sizes)
        w, h = sizes.pop()
        self.assertEqual(w, T.CC_GRID_W)
