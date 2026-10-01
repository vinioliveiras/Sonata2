"""Volume / brightness HUD, macOS Big Sur style: a translucent rounded
square in the lower middle of the screen with the symbol and 16 blocks;
fades after a moment. Shown by the menu bar process when the media keys
are pressed (`sonata2 key volume-up` ... asks it over D-Bus)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402

SIZE = 200
BLOCKS = 16
SHOW_MS = 1400

ui.register("""
window.sonata-osd, window.sonata-osd > contents { background: none; box-shadow: none; }
.osd-card { background: %(glass_tint)s; border-radius: calc(%(r_dialog)s * 1.5);
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s; color: %(label)s; }
.osd-card.solid { background: %(menu_bg)s; }
.osd-icon { color: alpha(%(label)s, 0.8); }
.osd-block { min-width: 9px; min-height: 6px; background: alpha(%(label)s, 0.85); }
.osd-block.off { background: alpha(%(label)s, 0.15); }
""", key="osd")

ICONS = {"volume": ("sonata-volume-3-symbolic", "sonata-volume-muted-symbolic"),
         "brightness": ("display-brightness-symbolic", "display-brightness-symbolic")}


class OSD(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title="OSD", decorated=False, resizable=False)
        self.add_css_class("sonata-osd")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["osd-card"] +
                       ([] if ui.theme.glass() else ["solid"]))
        ui.theme.glass_class(card)
        card.set_size_request(SIZE, SIZE)
        self.icon = Gtk.Image(pixel_size=96, css_classes=["osd-icon"], vexpand=True, valign=Gtk.Align.CENTER)
        card.append(self.icon)
        bar = Gtk.Box(spacing=1, halign=Gtk.Align.CENTER, margin_bottom=22)
        self.blocks = [Gtk.Box(css_classes=["osd-block"]) for _ in range(BLOCKS)]
        for b in self.blocks:
            bar.append(b)
        card.append(bar)
        self.rev = Gtk.Revealer(child=card, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                transition_duration=180)
        self.set_child(self.rev)
        self._src = 0
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-osd")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.BOTTOM, True)
            LS.set_margin(self, LS.Edge.BOTTOM, 140)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            LS.set_exclusive_zone(self, -1)
        self.set_can_target(False)

    def show_level(self, kind: str, percent: int, muted: bool = False) -> None:
        on, off = ICONS.get(kind, ICONS["volume"])
        self.icon.set_from_icon_name(off if muted else on)
        lit = 0 if muted else round(max(0, min(100, percent)) * BLOCKS / 100)
        for i, b in enumerate(self.blocks):
            (b.remove_css_class if i < lit else b.add_css_class)("off")
        self.present()
        self.rev.set_reveal_child(True)
        if self._src:
            GLib.source_remove(self._src)
        self._src = GLib.timeout_add(SHOW_MS, self._fade)

    def _fade(self):
        self._src = 0
        self.rev.set_reveal_child(False)
        GLib.timeout_add(200, lambda: (not self.rev.get_reveal_child() and self.set_visible(False), False)[1])
        return False
