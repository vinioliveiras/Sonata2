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

    def _w(self) -> int:
        """The fixed width, or the child's minimum when that is wider: GTK
        warns (and clips) when a child gets less than its minimum (Control
        Center needed 328 px in a 320 px panel). The minimum never follows
        long text (labels ellipsize), so the panel still doesn't grow."""
        mn = self.get_first_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        return max(self.width, mn)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            w = self._w()
            return w, w, -1, -1
        mn, nat, _b, _nb = self.get_first_child().measure(orientation, max(for_size, self._w()))
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


class MaxWidth(Gtk.Widget):
    """Centres its child at most `width` wide (a reading column: chat,
    documents); narrower windows give it all their width.

        column = fixed.MaxWidth(child, 720)"""
    __gtype_name__ = "SonataMaxWidth"
    width = GObject.Property(type=int, default=0)

    def __init__(self, child: Gtk.Widget, width: int, **kw):
        super().__init__(hexpand=True, **kw)
        self.width = int(width)
        child.set_parent(self)
        self.connect("destroy", FixedWidth._unparent)

    def do_measure(self, orientation, for_size):
        child = self.get_first_child()
        if orientation == Gtk.Orientation.VERTICAL and for_size > self.width:
            for_size = self.width
        mn, nat, _b, _nb = child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            nat = max(mn, min(nat, self.width))
        return mn, nat, -1, -1

    def do_size_allocate(self, width, height, baseline):
        child = self.get_first_child()
        w = max(min(width, self.width), child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        from gi.repository import Graphene, Gsk
        child.allocate(w, height, -1, Gsk.Transform().translate(Graphene.Point().init((width - w) / 2, 0)))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH
