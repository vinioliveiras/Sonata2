"""Vini: every alert in glass -- also the ones asked from a window (Files'
"couldn't be opened"), which were drawn inside it, opaque."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402

Adw.init()


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipUnless(hasattr(Adw, "AlertDialog"), "libadwaita < 1.5")
class AlertGlassTest(unittest.TestCase):
    def test_alert_from_a_window_is_its_own_glass_window(self):
        ui.setup()
        parent = Gtk.Window()
        parent.present()
        settle()
        d = ui.dialog.alert("“DATA” couldn't be opened.", "Error mounting", [("ok", "OK", "default")],
                            parent=parent)
        settle()
        root = d.get_root()
        self.assertIsNot(root, parent)                              # not drawn inside the window
        self.assertTrue(root.has_css_class("sonata-glass-window"))
        self.assertTrue(d.has_css_class("glass"))
        self.assertIs(root.get_transient_for(), parent)             # still in front of it
        self.assertTrue(root.get_modal())
        d.close()
        parent.destroy()

    def test_alert_from_a_widget_uses_its_window(self):
        parent = Gtk.Window()
        button = Gtk.Button()
        parent.set_child(button)
        parent.present()
        settle()
        d = ui.dialog.alert("H", "B", [("ok", "OK", "default")], parent=button)
        settle()
        self.assertIs(d.get_root().get_transient_for(), parent)
        d.close()
        parent.destroy()


if __name__ == "__main__":
    unittest.main()
