"""Vini: a right-click on the Dock's own space -- the strip under it, its
plate between the icons -- opens a menu like Windows' taskbar: Task
Manager, Show Desktop, the Dock's options and its preferences. An icon or
the divider keeps its own menu.

    xvfb-run -a python3 -m unittest tests.test_dock_area_menu
"""
import os
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import dock as D, dock_menu as DM  # noqa: E402


def labels(sections):
    return [[i.label for i in sec] for sec in sections]


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class AreaMenuTest(unittest.TestCase):
    def menu(self, cfg=None):
        dock = types.SimpleNamespace(cfg=dict(cfg or {"autohide": False, "position": "bottom"}),
                                     away=Gtk.PositionType.TOP, set_option=mock.Mock())
        window = types.SimpleNamespace(manager=object())
        with mock.patch.object(DM.ui.menu, "popup") as popup:
            DM.area_menu(dock, window, (10, 20))
        self.assertIs(popup.call_args[0][0], dock)        # on the Dock: a menu on the window never showed
        sections = popup.call_args[0][1]
        self.assertEqual(popup.call_args[1]["at"], (10, 20))          # where it was clicked
        return dock, window, sections

    def test_items(self):
        from sonata2.i18n import _
        _dock, _w, sections = self.menu()
        self.assertEqual(labels(sections), [[_("Task Manager"), _("Show Desktop")],
                                            [_("Turn Hiding On"), _("Position on Screen")],
                                            [_("Dock Preferences…")]])

    def test_actions(self):
        dock, window, sections = self.menu({"autohide": True, "position": "bottom"})
        (task, desk), (hiding, _pos), (prefs,) = sections
        from sonata2 import __main__ as M
        with mock.patch.object(DM.GLib, "spawn_async") as spawn:
            task.on_activate()
        self.assertEqual(spawn.call_args[0][0][-1], "activity")            # Task Manager
        with mock.patch.object(D, "toggle_show_desktop") as show:
            desk.on_activate()
        show.assert_called_once_with(window.manager)
        hiding.on_activate()
        dock.set_option.assert_called_once_with("autohide", False)          # "Turn Hiding Off"
        from sonata2.shell import topbar
        with mock.patch.object(topbar, "open_settings") as settings:
            prefs.on_activate()
        settings.assert_called_once_with("dock")
        self.assertTrue(M.self_argv())

    def test_translated(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for loc, want in (("pt_BR", "Preferências do Dock…"), ("pt_PT", "Preferências da Dock…")):
            with open(os.path.join(root, "sonata2", "locale", loc, "shell_dock.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["Dock Preferences…"], want)

    def test_icons_and_divider_keep_their_own_menus(self):
        box = Gtk.Box()
        tile = D.DockTile.__new__(D.DockTile)
        Gtk.Button.__init__(tile)
        inner = Gtk.Label()
        tile.set_child(inner)
        box.append(tile)
        self.assertTrue(D.on_an_item(inner))
        self.assertTrue(D.on_an_item(tile))
        self.assertFalse(D.on_an_item(box))                                  # the plate between icons
        self.assertFalse(D.on_an_item(None))                                 # the strip under the Dock

    def test_window_right_click_opens_it_off_the_icons(self):
        w = D.DockWindow.__new__(D.DockWindow)
        w.dock = types.SimpleNamespace()
        pt = types.SimpleNamespace(x=3.0, y=-2.0)                        # (in the Dock: under its plate)
        with mock.patch.object(D.DockWindow, "pick", return_value=None, create=True), \
                mock.patch.object(D.DockWindow, "compute_point", return_value=(True, pt), create=True), \
                mock.patch.object(DM, "area_menu") as menu:
            g = mock.Mock()
            D.DockWindow._area_pressed(w, g, 1, 5.0, 7.0)
        menu.assert_called_once_with(w.dock, w, (3.0, -2.0))
        with mock.patch.object(D.DockWindow, "pick", return_value="icon", create=True), \
                mock.patch.object(D, "on_an_item", return_value=True), \
                mock.patch.object(DM, "area_menu") as menu:
            D.DockWindow._area_pressed(w, mock.Mock(), 1, 5.0, 7.0)
        menu.assert_not_called()


if __name__ == "__main__":
    unittest.main()
