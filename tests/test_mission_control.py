"""Mission Control (Super+Tab), what Vini asked of it and what went wrong:
a click on a window brings it forward, a click where no window is closes
it, a right click shows the app's Dock menu -- gone again when Mission
Control closes --, a window dragged onto a desktop goes there, Launchpad
closes when it opens, a full-screen window comes back without cut corners.
The desktops bar itself: tests/test_spaces.py.

    xvfb-run -a python3 -m unittest tests.test_mission_control
"""
import os
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GDK_BACKEND", "x11")


def read(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class WayfirePatchesTest(unittest.TestCase):
    """Sonata's Wayfire (wayfire-plugin/wayfire-*.patch, built by install.sh)."""

    def test_a_click_finds_the_window_under_it(self):
        """Only minimized windows came forward on a click."""
        patch = read("wayfire-plugin", "wayfire-scale-click.patch")
        self.assertIn("wayfire_toplevel_view scaled_view_at(wf::pointf_t at)", patch)
        self.assertIn("get_transformed_node()->get_bounding_box() & local", patch)
        self.assertIn("auto view = last_selected_view ? last_selected_view : scaled_view_at(input_position);", patch)

    def test_a_click_where_no_window_is_closes_it(self):
        patch = read("wayfire-plugin", "wayfire-scale-sonata-events.patch")
        self.assertIn("pressed_empty = !view && (button == BTN_LEFT);", patch)
        self.assertIn("if (pressed_empty && (button == BTN_LEFT) && !scaled_view_at(input_position))", patch)

    def test_right_click_and_drag_are_told(self):
        patch = read("wayfire-plugin", "wayfire-scale-sonata-events.patch")
        for part in ("case BTN_RIGHT:", '"sonata-scale-menu#"', '"sonata-scale-drag#"', '"start"', '"drop"'):
            self.assertIn(part, patch)

    def test_patches_are_built(self):
        for tool in ("build-wayfire.sh", "build-wayfire-stack.sh"):
            self.assertIn('"$SRC"/wayfire-plugin/wayfire-*.patch', read("tools", tool))


class MenuTest(unittest.TestCase):
    """The right click's menu (the Dock's own, at the pointer)."""

    def watch_dock_events(self):
        from sonata2.shell import dock
        got = {}

        class IPC:
            def watch(self, events, cb):
                got["events"], got["cb"] = events, cb
                return True
        with mock.patch.dict(dock._WATCH, {"on": False}), mock.patch("sonata2.wl.wfipc.WayfireIPC", IPC):
            dock._watch_outputs()
        return dock, got

    def test_closes_with_mission_control(self):
        """Vini: right-click a window, close Mission Control -- the menu's
        items stayed on the screen."""
        dock, got = self.watch_dock_events()
        self.assertIn("plugin-activation-state-changed", got["events"])
        with mock.patch.object(dock.layer, "close_menu_at") as close:
            got["cb"]({"event": "plugin-activation-state-changed", "plugin": "scale", "state": True})
            close.assert_not_called()                                   # opening: nothing
            got["cb"]({"event": "plugin-activation-state-changed", "plugin": "expo", "state": False})
            close.assert_not_called()
            got["cb"]({"event": "plugin-activation-state-changed", "plugin": "scale", "state": False})
            close.assert_called_once()

    def test_right_click_opens_the_docks_menu(self):
        dock, got = self.watch_dock_events()
        self.assertIn("sonata-scale-menu#", got["events"])
        msg = {"event": "sonata-scale-menu#", "app-id": "org.gnome.Calculator", "output": "DP-1", "x": 1, "y": 2}
        with mock.patch.object(dock, "_DOCKS", ["the dock"]), mock.patch.object(dock.dock_menu, "scale_menu") as menu:
            got["cb"](msg)
        menu.assert_called_once_with("the dock", msg)

    def test_close_menu_at(self):
        from sonata2.shell import layer
        pop = mock.Mock()
        with mock.patch.dict(layer._MENU_HOLDER, {"pop": pop}, clear=True):
            layer.close_menu_at()
        pop.popdown.assert_called_once()
        with mock.patch.dict(layer._MENU_HOLDER, {}, clear=True):
            layer.close_menu_at()                                       # none open: nothing

    def test_same_items_as_the_dock(self):
        src = read("sonata2", "shell", "dock_menu.py")
        self.assertIn("return ui.menu.popup(tile, app_sections(dock, key, tile), position=dock.away)", src)
        self.assertIn("msg.get(\"y\", 0), app_sections(dock, tile.key, tile))", src)


class DragTest(unittest.TestCase):
    def test_events_reach_the_bar_of_that_display(self):
        from sonata2.shell import mission
        m = mission.MissionBackdrop.__new__(mission.MissionBackdrop)
        bar, other = mock.Mock(), mock.Mock()
        bar.get_visible.return_value = other.get_visible.return_value = True
        m.bars = {"DP-1": bar, "HDMI-A-1": other}
        ev = {"event": "sonata-scale-drag#", "state": "drop", "output": "DP-1", "view-id": 3, "x": 5, "y": 6}
        m._event(ev)
        bar.dragging.assert_called_once_with(ev)
        other.dragging.assert_not_called()
        self.assertIn('"sonata-scale-drag#"', read("sonata2", "shell", "mission.py"))


class LaunchpadTest(unittest.TestCase):
    """Vini: with the Apps menu open, Super+Tab should close it."""

    def pad(self, visible=True, mode="fullscreen", menu_open=True):
        from sonata2.shell import launchpad
        pad = types.SimpleNamespace(mode=mode, menu=types.SimpleNamespace(is_open=lambda: menu_open),
                                    get_visible=lambda: visible, close_launchpad=mock.Mock())
        return launchpad.Launchpad._plugin_changed, pad

    def test_closes_when_mission_control_opens(self):
        changed, pad = self.pad()
        changed(pad, {"plugin": "scale", "state": True})
        pad.close_launchpad.assert_called_once()

    def test_not_when_closed_or_for_other_plugins(self):
        for kw, ev in (({"visible": False}, {"plugin": "scale", "state": True}),
                       ({}, {"plugin": "scale", "state": False}),
                       ({}, {"plugin": "move", "state": True}),
                       ({"mode": "menu", "menu_open": False}, {"plugin": "scale", "state": True})):
            changed, pad = self.pad(**kw)
            changed(pad, ev)
            pad.close_launchpad.assert_not_called()
        changed, pad = self.pad()
        changed(pad, {"plugin": "expo", "state": True})                 # all Spaces too
        pad.close_launchpad.assert_called_once()


class PluginTest(unittest.TestCase):
    """Sonata's own Wayfire plugin (wayfire-plugin/src/sonata-corners.cpp)."""

    def test_full_screen_window_never_cut(self):
        """Vini: a video in full screen, Super+Tab twice -- blue in its four
        corners (the rounded corners cut and filled with the glass colour)."""
        src = read("wayfire-plugin", "src", "sonata-corners.cpp")
        start = src.index("void corners_node_t::gen_render_instances")
        body = src[start:src.index("\n}\n", start)]
        self.assertLess(body.index("if (drawn_full)"), body.index("corners_render_instance_t"))
        self.assertIn("return view->pending_fullscreen() || view->toplevel()->current().fullscreen;", src)
        self.assertIn("if (wanted(view) && mine && mine->drawn_full)", src)        # out of it: corners again
        self.assertIn("wf::get_core().connect(&on_focus_check);", src)

    def test_window_pictures_read_by_the_renderer(self):
        """Vini: the Dock's previews were empty frames -- pixels read straight
        with GL came back transparent; now the renderer reads them."""
        src = read("wayfire-plugin", "src", "sonata-corners.cpp")
        start = src.index("ipc_window_shot = [=]")
        body = src[start:src.index('response["height"]', start)]
        self.assertIn("wlr_texture_read_pixels(tex, &opts)", body)
        self.assertIn("shrink_into(*src, *steps.back(), next)", body)
        self.assertNotIn("glReadPixels", body)
        self.assertNotIn("glBlitFramebuffer", body)


if __name__ == "__main__":
    unittest.main()
