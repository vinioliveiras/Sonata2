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
        self.assertEqual([p[m][1] for m in ("display", "sound", "nowplaying")], [2, 4, 6])

    def test_small_modules_fill_holes_first(self):
        """First free place where it fits, left to right, top to bottom."""
        p = C.pack(["darkmode", "display", "screenshot"])
        self.assertEqual(p["darkmode"], (0, 0, 1, 1))
        self.assertEqual(p["display"], (0, 1, 4, 2))               # doesn't fit beside: next row
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
        # FPS Limit: off by default, only through Add Controls (Vini)
        self.assertEqual(C.hidden(C.DEFAULT_ORDER), [m for m in C.CATALOG if m.startswith("stat_") or m in ("mixer", "fpslimit")])
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
    """Vini: adding CPU and GPU made Control Center huge -- its width is fixed,
    whatever the modules; more modules make it taller, never wider."""

    def test_width_fixed_height_follows(self):
        import types
        from sonata2.shell import topbar as T
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None)
        sizes = {}
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"):
            for name, order in (("default", C.DEFAULT_ORDER), ("all", list(C.CATALOG)), ("one", ["dnd"]),
                                ("stats", ["stat_net", "stat_cpu", "stat_gpu"])):
                config.save("controlcenter", {"modules": order})
                cc = T.ControlCenter(bar)
                win = Gtk.Window()
                win.set_child(cc)
                win.present()
                settle(150)
                sizes[name] = (cc.get_width(), cc.get_height())
                cc.grid.set_editing(True)                            # edit mode doesn't resize it either
                settle(80)
                self.assertEqual((cc.get_width(), cc.get_height()), sizes[name])
                win.destroy()
        self.assertEqual({w for w, _h in sizes.values()}, {cc.width})          # never wider
        self.assertGreater(sizes["all"][1], sizes["default"][1])              # taller with more
        self.assertLess(sizes["one"][1], sizes["default"][1])


class AddControlsInPanelTest(TempConfig):
    """Vini: after Add Controls, Control Center couldn't be closed (neither
    its icon nor the other menu bar icons). The choices were a menu -- a
    pop-up inside the panel's pop-up -- and once it closed, Wayfire stopped
    telling GTK about clicks elsewhere. They are a list in the panel now."""

    def test_add_controls_is_a_list_in_the_panel(self):
        import types
        from sonata2.shell import topbar as T
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None)
        config.save("controlcenter", {"modules": list(C.DEFAULT_ORDER)})
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"), \
                mock.patch.object(T.ui.menu, "popup", side_effect=AssertionError("no menu inside the panel")):
            cc = T.ControlCenter(bar)
            win = Gtk.Window(child=cc)
            win.present()
            settle(100)
            self.assertFalse(cc.add_btn.get_visible())
            cc.grid.set_editing(True)
            cc.add_btn.emit("clicked")
            self.assertTrue(cc.add_reveal.get_reveal_child())
            labels = [c.get_child().get_label() for c in _children(cc.add_list)]
            self.assertIn("CPU", labels)
            cpu = [c for c in _children(cc.add_list) if c.get_child().get_label() == "CPU"][0]
            cpu.get_child().emit("clicked")
            self.assertIn("stat_cpu", cc.grid.order)
            labels = [c.get_child().get_label() for c in _children(cc.add_list)]
            self.assertNotIn("CPU", labels)                         # gone from the list
            cc.grid.set_editing(False)                              # Done: the list goes
            self.assertFalse(cc.add_reveal.get_reveal_child())
            win.destroy()


class ConnectionDetailsTest(TempConfig):
    """Vini: a click beside the Wi-Fi / Bluetooth toggle shows the networks /
    devices (in the panel, macOS); the round button still turns it on/off."""

    def test_details_in_the_panel(self):
        import types
        from sonata2.shell import topbar as T
        built = []

        def column(kind):
            def build(close):
                built.append((kind, close))
                return Gtk.Label(label=kind)
            return build
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None,
                                    wifi_column=column("wifi"), bluetooth_column=column("bluetooth"))
        with mock.patch.object(T.system, "run_async") as run, mock.patch.object(T, "cached"), \
                mock.patch.object(T.ui.panel, "popup", side_effect=AssertionError("no pop-up inside the panel")):
            cc = T.ControlCenter(bar)
            win = Gtk.Window(child=cc)
            win.present()
            settle(100)
            cc.wifi.details.emit("released", 1, 5, 5)           # beside the toggle
            self.assertEqual(cc.pages.get_visible_child_name(), "details")
            self.assertEqual(built[-1][0], "wifi")
            self.assertEqual(cc.pages.get_visible_child().kind, "wifi")
            cc.hide_details()
            self.assertEqual(cc.pages.get_visible_child_name(), "main")
            cc.bt.details.emit("released", 1, 5, 5)
            self.assertEqual(cc.pages.get_visible_child().kind, "bluetooth")
            run.reset_mock()
            cc.wifi.button.emit("clicked")                      # the round button: on/off, no list
            self.assertTrue(run.called)
            self.assertEqual(cc.pages.get_visible_child().kind, "bluetooth")
            win.destroy()

    def test_menu_bar_menus_share_the_lists(self):
        import inspect
        from sonata2.shell import topbar as T
        self.assertIn("self.wifi_column(", inspect.getsource(T.Bar._wifi_panel))
        self.assertIn("self.bluetooth_column(", inspect.getsource(T.Bar._bluetooth_panel))


def _children(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


class ResponsiveTest(unittest.TestCase):
    def test_width_follows_the_display(self):
        """A share of the display's width, never narrower than the default layout, never huge."""
        self.assertEqual(C.width_for(0), C.WIDTH_MIN)
        self.assertEqual(C.width_for(1366), C.WIDTH_MIN)
        self.assertEqual(C.width_for(2048), round(2048 * C.WIDTH_SHARE))
        self.assertEqual(C.width_for(3840), C.WIDTH_MAX)
        self.assertLessEqual(C.width_for(1920), C.width_for(2560))


class SameSizeModulesTest(TempConfig):
    """Vini: modules keep their standard size always -- a module is a whole
    number of grid rows, whatever else is in Control Center."""

    def test_each_module_is_its_span(self):
        import types
        from sonata2.shell import topbar as T
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None)
        seen = {}
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"):
            for order in (C.DEFAULT_ORDER, list(C.CATALOG), ["stat_cpu", "darkmode"]):
                config.save("controlcenter", {"modules": order})
                cc = T.ControlCenter(bar)
                win = Gtk.Window()
                win.set_child(cc)
                win.present()
                settle(150)
                col = (cc.width - (C.COLS - 1) * C.SPACING) / C.COLS
                for mid in order:
                    slot = cc.grid.slots[mid]
                    w, h = C.CATALOG[mid][1]
                    self.assertEqual(slot.get_height(), C.span_height(h), mid)
                    self.assertAlmostEqual(slot.get_width(), w * col + (w - 1) * C.SPACING, delta=2)
                    seen.setdefault(mid, set()).add((slot.get_width(), slot.get_height()))
                win.destroy()
        self.assertTrue(all(len(v) == 1 for v in seen.values()), seen)      # the same in every layout


class MixerModuleTest(TempConfig):
    """Vini: the volume mixer (each app's volume) as a Control Center module."""

    def build(self, available=True):
        import types
        from sonata2.backend import mixer
        from sonata2.shell import topbar as T
        service = types.SimpleNamespace(listeners=[])
        bar = types.SimpleNamespace(_poll=lambda: None, _set_volume=lambda v: None, notifications=None,
                                    monitor=None, get_native=lambda: None, mixer=service)
        with mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"), \
                mock.patch.object(mixer, "available", return_value=available):
            cc = T.ControlCenter(bar)
        return cc, service

    def test_add_it_and_it_follows_the_apps_while_shown(self):
        cc, service = self.build()
        self.assertIn("mixer", C.hidden(cc.grid.order))           # off by default: Add Controls
        win = Gtk.Window()
        win.set_child(cc)
        win.present()
        settle(100)
        with mock.patch("sonata2.backend.system.run_async"):
            cc.grid.add_module("mixer")
            settle(150)
        self.assertIn(cc.modules["mixer"]._streams, service.listeners)  # live while on screen
        win.destroy()
        settle(50)
        self.assertNotIn(cc.modules["mixer"]._streams, service.listeners)

    def test_its_height_follows_the_apps_playing(self):
        """Vini: the mixer grows (a grid row at a time) with the apps playing
        and shrinks back -- never wider, the modules below glide along."""
        from sonata2.backend import mixer
        cc, _service = self.build()
        win = Gtk.Window()
        win.set_child(cc)
        win.present()
        settle(100)
        with mock.patch("sonata2.backend.system.run_async"):
            cc.grid.add_module("mixer")
            settle(100)
        feed = cc.modules["mixer"]._streams

        def play(n):
            feed([mixer.Stream(index=i, key=f"app{i}", name=f"App {i}", icon="", volume=50, muted=False)
                  for i in range(n)])
            settle(400)
            return cc.grid._places["mixer"][3]
        width = cc.grid.slots["mixer"].get_width()
        none = play(0)
        one, four, many = play(1), play(4), play(12)
        self.assertLessEqual(none, one)
        self.assertLess(one, four)
        self.assertLessEqual(four, many)
        self.assertEqual(many, 6)                                  # past that it scrolls inside
        self.assertEqual(play(1), one)                             # and shrinks back
        self.assertEqual(cc.grid.slots["mixer"].get_height(), C.span_height(one))
        self.assertEqual(cc.grid.slots["mixer"].get_width(), width)  # never wider
        self.assertEqual(cc.grid.measure(Gtk.Orientation.HORIZONTAL, -1)[1], cc.width)
        win.destroy()

    def test_size_change_animates(self):
        """Vini: taking a module out made Control Center jump to its new size;
        it shrinks smoothly now, and the panel follows every frame."""
        cc, _service = self.build()
        win = Gtk.Window()
        win.set_child(cc)
        win.present()
        settle(150)
        grid = cc.grid
        before = grid.measure(Gtk.Orientation.VERTICAL, -1)[1]
        seen = []
        real = cc._fit_height
        with mock.patch.object(cc, "_fit_height", side_effect=lambda: (seen.append(
                grid.measure(Gtk.Orientation.VERTICAL, -1)[1]), real())):
            grid.on_height = cc._fit_height
            with mock.patch.object(C.ui.transition, "tween") as tween:
                grid._take_out("nowplaying")
            self.assertEqual(tween.call_args[0][1], "height")
            start, end = tween.call_args[0][2:4]
            self.assertEqual(start, before)
            self.assertEqual(end, grid.target_height())
            self.assertLess(end, start)
            step = tween.call_args[0][5]
            step((start + end) / 2)                      # mid-way: the grid (and the panel) in between
            self.assertEqual(grid.measure(Gtk.Orientation.VERTICAL, -1)[1], round((start + end) / 2))
            step(end)
            self.assertEqual(grid.measure(Gtk.Orientation.VERTICAL, -1)[1], end)
        self.assertGreaterEqual(len(seen), 2)                # every frame
        win.destroy()

    def test_rows_for(self):
        self.assertEqual(C.rows_for(10), 1)
        self.assertEqual(C.rows_for(C.span_height(2)), 2)
        self.assertEqual(C.rows_for(C.span_height(2) + 1), 3)
        self.assertEqual(C.rows_for(10_000), 6)

    def test_not_offered_without_a_sound_server(self):
        cc, _ = self.build(available=False)
        self.assertNotIn("mixer", cc.modules)


class PutBackTest(TempConfig):
    def test_removed_then_added_again_is_not_empty(self):
        """Vini: a module taken out with its x and put back with Add Controls came
        back empty (its content was still held by the old slot)."""
        widgets = {m: Gtk.Button(label=m) for m in C.CATALOG}
        g = C.ModuleGrid(widgets, list(C.DEFAULT_ORDER))
        win = Gtk.Window(default_width=344)
        win.set_child(g)
        win.present()
        settle(80)
        g.set_editing(True)
        g._take_out("darkmode")
        g.add_module("darkmode")
        settle(80)
        self.assertIs(g.slots["darkmode"].get_child(), widgets["darkmode"])
        self.assertTrue(widgets["darkmode"].get_mapped())
        win.destroy()
