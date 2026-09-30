"""A container whose width never follows its content: panels, cards and
widgets keep one width however long a title, a network name or a button
label is (they ellipsize or wrap instead).

    box = fixed.FixedWidth(child, 320)

GTK sizes a box by its children's natural widths, and an ellipsized label
still asks for its whole text as its natural width, so a long notification
title used to stretch Notification Center (and the calendar under it).
Here the width is fixed; the height follows the content at that width."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402


class FixedWidth(Gtk.Widget):
    __gtype_name__ = "SonataFixedWidth"
    # a GObject property, not a Python attribute: kept with the widget itself
    width = GObject.Property(type=int, default=0)

    def __init__(self, child: Gtk.Widget, width: int, **kw):
        super().__init__(**kw)
        self.width = int(width)
        child.set_parent(self)
        self.connect("destroy", FixedWidth._unparent)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.width, self.width, -1, -1
        mn, nat, _b, _nb = self.get_first_child().measure(orientation, self.width)
        return mn, nat, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.get_first_child().allocate(width, height, baseline, None)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    @staticmethod
    def _unparent(self):
        try:
            child = self.get_first_child()
            if child is not None:
                child.unparent()
        except Exception:                       # Python itself shutting down
            pass
