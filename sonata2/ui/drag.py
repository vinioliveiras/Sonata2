"""Drag icon, macOS style: the item's icon, slightly translucent, held
steady under the pointer. (It used to swing like a pendulum; Vini asked
for the macOS look instead.)

    icon = ui.drag.hang(drag, paintable, size)      # in "drag-begin"
    icon.feed(x, surface_key)                        # DropControllerMotion "motion"
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gtk  # noqa: E402


class HangingIcon(Gtk.Widget):
    """The dragged item as macOS shows it: the icon itself, a little
    translucent, held steady under the pointer (Vini: no pendulum). The
    feed() API stays so callers don't change."""
    OPACITY = 0.78

    def __init__(self, paintable, size: int):
        super().__init__(can_target=False)
        self.paintable, self.size = paintable, size
        self.box = size
        self.set_size_request(size, size)

    @property
    def hotspot(self) -> int:
        return self.box // 2

    def feed(self, x: float, key=None) -> None:
        pass                                              # steady: nothing follows the motion

    def do_snapshot(self, snap) -> None:
        ar = self.paintable.get_intrinsic_aspect_ratio() or 1.0     # photos keep their shape
        w, h = (self.size, self.size / ar) if ar > 1 else (self.size * ar, self.size)
        snap.push_opacity(self.OPACITY)
        snap.save()
        snap.translate(Graphene.Point().init((self.box - w) / 2, (self.box - h) / 2))
        self.paintable.snapshot(snap, w, h)
        snap.restore()
        snap.pop()


def hang(drag, paintable, size: int) -> HangingIcon:
    icon = HangingIcon(paintable, size)
    Gtk.DragIcon.get_for_drag(drag).set_child(icon)
    drag.set_hotspot(icon.hotspot, icon.hotspot)
    return icon


def follow(widget, get_icon) -> Gtk.DropControllerMotion:
    """Feed the hanging icon from every drag motion over `widget` (its whole
    area, drop targets included). get_icon() returns the current icon or None."""
    ctl = Gtk.DropControllerMotion()

    def motion(_c, x, _y):
        icon = get_icon()
        if icon is not None:
            icon.feed(x, widget)
    ctl.connect("motion", motion)
    ctl.connect("enter", motion)
    widget.add_controller(ctl)
    return ctl
