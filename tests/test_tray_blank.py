"""Vini: Claude's icon in the menu bar went invisible for a while, then came
back. An app that sends a blank picture (all see-through) keeps showing
its last icon -- or a generic one -- never an empty gap."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sonata2.shell import tray  # noqa: E402


def texture(alpha):
    w = h = 16
    data = bytes([255, 255, 255, alpha] * (w * h))
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), w * 4)


class BlankTrayIconTest(unittest.TestCase):
    def test_blank_has_no_shape(self):
        self.assertIsNone(tray._silhouette(texture(0)))
        self.assertIsNotNone(tray._silhouette(texture(255)))

    def test_last_icon_stays(self):
        w = Gtk.Window()
        icon = tray.MonoIcon()
        w.set_child(icon)
        icon.set_icon(("paintable", texture(255)), 1)
        shown = icon.paintable
        self.assertIsNotNone(shown)
        icon.set_icon(("paintable", texture(0)), 1)               # a blank frame
        self.assertIs(icon.paintable, shown)
        fresh = tray.MonoIcon()
        w.set_child(fresh)
        fresh.set_icon(("paintable", texture(0)), 1)              # blank from the start: a generic icon
        self.assertIsNotNone(fresh.paintable)


if __name__ == "__main__":
    unittest.main()
