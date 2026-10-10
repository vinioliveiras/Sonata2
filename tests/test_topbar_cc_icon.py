"""Bug 7 (Vini): "When I opened and closed the Feedbacker, the Control Center
icon (menu bar, top right) disappeared, and came back when I clicked it."

Opening an app the Dock doesn't pin writes dock.json (its recents); every
Sonata process watches that file for appearance changes, and the first write
ran ui.theme's picture cross-fade over every window -- the menu bar too,
moved into a new Gtk.Overlay under the Control Center button. The menu bar
now opts out of that fade (it blends its own colours), and a menu bar item
always gets its icon drawn anew when its panel closes.

xvfb-run -a python3 -m unittest tests.test_topbar_cc_icon"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import topbar  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class _NoManager:
    available = False
    listeners = []
    toplevels = []


def _image(btn):
    c = btn.get_child().get_first_child()
    while not isinstance(c, Gtk.Image):
        c = c.get_next_sibling()
    return c


class ControlCenterIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def _open_and_close_cc(self, bar):
        bar.open_menu(bar.items.index(bar.cc))
        settle(300)
        self.assertTrue(ui.menu.OPEN, "the Control Center panel opened")
        for pop in list(ui.menu.OPEN):
            pop.popdown()
        settle(300)

    def test_menu_bar_window_is_not_cross_faded(self):
        win = topbar.TopBarWindow(None, secondary=True, manager=_NoManager())
        self.assertTrue(getattr(win, "sonata_no_fade", False))
        win.present()
        settle(400)
        self._open_and_close_cc(win.bar)
        # what the first dock.json write (an app opened) did: the appearance
        # cross-fade over every window. The bar stays where it is (it was
        # re-rooted under an overlay -- and GTK crashed here before)
        ui.theme._start_fade(ui.theme.values())
        settle(500)
        self.assertIs(win.get_child(), win.bar)
        self.assertEqual(_image(win.bar.cc).get_icon_name(), "sonata-control-center-symbolic")
        self.assertEqual(_image(win.bar.cc).get_storage_type(), Gtk.ImageType.ICON_NAME)
        win.bar.stop()
        win.destroy()

    def test_icon_drawn_anew_when_its_panel_closes(self):
        win = Gtk.Window()
        bar = topbar.Bar(None)
        win.set_child(bar)
        win.present()
        settle(300)
        img = _image(bar.cc)
        changes = []
        img.connect("notify::storage-type", lambda i, _p: changes.append(i.get_storage_type()))
        self._open_and_close_cc(bar)
        self.assertNotIn("open", bar.cc.get_css_classes())
        self.assertTrue(changes, "the icon was looked up again")
        self.assertEqual(img.get_icon_name(), "sonata-control-center-symbolic")
        self.assertEqual(img.get_storage_type(), Gtk.ImageType.ICON_NAME)
        bar.stop()
        win.destroy()

    def test_refresh_keeps_items_without_an_icon_alone(self):
        bar = topbar.Bar(None)
        topbar.Bar._refresh_icon(bar.clock)                 # a text item: nothing to do
        topbar.Bar._refresh_icon(bar.inuse_btn)             # dots only
        img = _image(bar.cc)
        topbar.Bar._refresh_icon(bar.cc)
        self.assertEqual(img.get_icon_name(), "sonata-control-center-symbolic")
        bar.stop()


if __name__ == "__main__":
    unittest.main()
