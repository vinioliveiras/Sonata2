"""Hover label: the small name bubble macOS shows above Dock icons (also
usable as a tooltip for any Sonata surface).

    lbl = label.HoverLabel(widget, "Firefox")   # shows on hover
    lbl.popup() / lbl.popdown() / lbl.set_text("...")"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
popover.hover-label { background: none; box-shadow: none; padding: 0; }
popover.hover-label > contents {
  padding: 3px 10px; border-radius: %(r_label)s; min-height: 0;
  font-family: %(font)s; font-size: %(text_body)s; font-weight: 400;
  color: %(label)s; background-color: %(panel_material)s;     /* glass, like menus (Vini) */
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_label)s;
}
""")


class HoverLabel(Gtk.Popover):
    def __init__(self, widget: Gtk.Widget, text: str, position=Gtk.PositionType.TOP,
                 gap: int = 8, hover: bool = True):
        super().__init__(css_classes=["hover-label"], has_arrow=False, autohide=False,
                         can_target=False, position=position)
        self._label = Gtk.Label(label=text)
        self.set_child(self._label)
        self.set_position_gap(position, gap)
        self.set_parent(widget)
        if hover:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self.popup())
            motion.connect("leave", lambda *_: self.popdown())
            widget.add_controller(motion)

    def set_position_gap(self, position, gap: int = 8) -> None:
        self.set_position(position)
        P = Gtk.PositionType
        self.set_offset(*{P.TOP: (0, -gap), P.BOTTOM: (0, gap),
                          P.LEFT: (-gap, 0), P.RIGHT: (gap, 0)}[position])

    def set_text(self, text: str) -> None:
        self._label.set_label(text)
