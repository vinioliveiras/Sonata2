"""Controls (macOS Big Sur): push button, pop-up button (dropdown), switch,
slider (menu and Control Center styles).

    controls.push_button("Cancel", on_click)
    controls.push_button("Empty Trash", on_click, style="destructive")
    controls.push_button("OK", on_click, style="default")      # accent, Return
    controls.popup_button(["Automatic", "Light", "Dark"], selected=0, on_change=cb)
    controls.switch(active=True, on_change=cb)
    controls.slider(50, on_change=cb, style="menu" | "module")

Widgets are plain GTK widgets with Sonata classes, so Adw/GTK containers
work with them unchanged."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
/* libadwaita widgets (switches, sliders, suggested buttons in Adw rows)
   follow Sonata's accent colour too */
@define-color accent_bg_color %(accent)s;
@define-color accent_color %(accent)s;
@define-color accent_fg_color %(label_on_accent)s;
""", key="adw-accent")
theme.register("""
:root { --accent-bg-color: %(accent)s; --accent-color: %(accent)s; --accent-fg-color: %(label_on_accent)s; }
""", key="adw-accent-vars")

theme.register("""
/* push button */
button.sonata-button {
  min-height: %(control_h)s; min-width: 64px; padding: 0 12px;
  border-radius: %(r_button)s; border: none;
  font-family: %(font)s; font-size: %(text_body)s; font-weight: 400;
  color: %(label)s; background: %(control_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_control)s;
  transition: background-color %(t_press)s ease-out;
}
button.sonata-button:active { background: %(control_pressed)s; }
button.sonata-button.default { color: %(label_on_accent)s;
  background: linear-gradient(to bottom, alpha(%(accent)s, 0.92), %(accent)s); }
button.sonata-button.default:active { background: %(accent_selected)s; }
button.sonata-button.destructive { color: %(destructive)s; }
button.sonata-button:disabled { color: %(label_tertiary)s; box-shadow: 0 0 0 0.5px %(separator)s; }

/* pop-up button (Gtk.DropDown) with the Big Sur accent chevron cap */
dropdown.sonata-popup > button {
  min-height: %(control_h)s; padding: 0 2px 0 9px; border-radius: %(r_button)s; border: none;
  font-family: %(font)s; font-size: %(text_body)s;
  color: %(label)s; background: %(control_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_control)s;
}
dropdown.sonata-popup > button > box { border-spacing: 8px; }
dropdown.sonata-popup > button label { font-weight: 400; }
dropdown.sonata-popup > button arrow {
  min-width: 16px; min-height: 16px; margin: 3px 0; border-radius: 4px; -gtk-icon-size: 12px;
  -gtk-icon-source: -gtk-icontheme("sonata-updown-symbolic");
  color: %(label_on_accent)s; background: %(accent)s;
}
dropdown.sonata-popup popover > contents {
  padding: 5px; border-radius: %(r_menu)s;
  font-family: %(font)s; font-size: %(text_body)s;
  color: %(label)s; background-color: %(menu_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
dropdown.sonata-popup popover listview { background: none; }
dropdown.sonata-popup popover row {
  min-height: %(control_h)s; padding: 0 8px; border-radius: %(r_menu_row)s; background: none;
}
dropdown.sonata-popup popover row:hover, dropdown.sonata-popup popover row:selected {
  background-color: %(accent_selected)s; color: %(label_on_accent)s;
}

/* switch */
switch.sonata-switch {
  min-width: %(switch_w)s; min-height: %(switch_h)s; padding: 0; border: none;
  border-radius: 99px; background: %(control_off)s;
  box-shadow: inset 0 0 0 0.5px %(separator)s;
  transition: background-color %(t_fast)s ease-out;
}
switch.sonata-switch:checked { background: %(accent)s; }
switch.sonata-switch > slider {
  min-width: 16px; min-height: 16px; margin: 1px; border-radius: 99px; border: none;
  background: %(knob)s; box-shadow: %(shadow_knob)s;
}
switch.sonata-switch image { opacity: 0; }   /* no I/O glyphs */

/* slider (Big Sur menu slider: thin track, accent fill, white knob) */
scale.sonata-slider { padding: 6px 0; min-width: 160px; }
scale.sonata-slider trough { min-height: 4px; border-radius: 99px; background: %(control_off)s; border: none; }
scale.sonata-slider highlight { border-radius: 99px; background: %(accent)s; border: none; }
scale.sonata-slider slider { min-width: 18px; min-height: 18px; margin: -7px; border-radius: 99px;
  border: none; background: #ffffff; box-shadow: 0 0 0 0.5px rgba(0,0,0,0.16), 0 0 2px rgba(0,0,0,0.22); }

/* module slider (Control Center: thick capsule, white fill, knob at the end) */
scale.sonata-module-slider { padding: 0; min-width: 240px; }
scale.sonata-module-slider trough { min-height: 22px; border-radius: 99px; border: none;
  background: %(module_track)s; box-shadow: inset 0 0 0 0.5px %(separator)s; }
/* GTK ends the fill at the knob's centre; Big Sur's fill runs under the whole
   knob, so the capsule reads as one piece: extend it by the knob's radius. */
scale.sonata-module-slider highlight { min-height: 22px; border-radius: 99px; border: none; background: %(module_fill)s;
  margin-right: -11px; }
scale.sonata-module-slider slider { min-width: 22px; min-height: 22px; margin: 0; border-radius: 99px; border: none;
  background: #ffffff; box-shadow: 0 0 0 0.5px rgba(0,0,0,0.14), 0 0 3px rgba(0,0,0,0.22); }   /* even: no downward drop */
scale.sonata-module-slider:disabled { opacity: 0.45; }     /* dimmed as a whole: no see-through knob */
""")


def push_button(label: str, on_click=None, style: str = "") -> Gtk.Button:
    """style: "" (plain), "default" (accent), "destructive"."""
    b = Gtk.Button(label=label, css_classes=["sonata-button"] + ([style] if style else []))
    if on_click:
        b.connect("clicked", lambda _b: on_click())
    return b


def popup_button(options, selected: int = 0, on_change=None) -> Gtk.DropDown:
    dd = Gtk.DropDown.new_from_strings(list(options))
    dd.add_css_class("sonata-popup")
    dd.set_selected(selected)
    if on_change:
        dd.connect("notify::selected", lambda d, _p: on_change(d.get_selected()))
    return dd


class ModuleSlider(Gtk.Widget):
    """Control Center slider, drawn by hand so the knob and the fill line up
    exactly (Gtk.Scale ends its fill at the knob's centre): a 22 px capsule,
    the fill runs under the whole knob. Scale-like API: get_value,
    set_value, "value-changed"."""

    __gsignals__ = {"value-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}
    H = 22

    def __init__(self, value=0.0, lower=0.0, upper=100.0):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER, cursor=Gdk.Cursor.new_from_name("default"))
        self.lower, self.upper, self.value = lower, upper, float(value)
        self.set_size_request(120, self.H)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda _g, x, _y: self._to(x))
        drag.connect("drag-update", lambda g, dx, _dy: self._to(g.get_start_point()[1] + dx))
        self.add_controller(drag)
        self.connect("notify::sensitive", lambda *_: self.queue_draw())
        theme.on_change(self.queue_draw)

    def get_value(self) -> float:
        return self.value

    def set_value(self, v) -> None:
        v = max(self.lower, min(self.upper, float(v)))
        if v != self.value:
            self.value = v
            self.queue_draw()
            self.emit("value-changed")

    def _to(self, x) -> None:
        if not self.is_sensitive():
            return
        span = max(1, self.get_width() - self.H)
        frac = max(0.0, min(1.0, (x - self.H / 2) / span))           # the knob's centre follows the pointer
        self.set_value(self.lower + frac * (self.upper - self.lower))

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.H
        y = (self.get_height() - h) / 2
        r = h / 2
        frac = (self.value - self.lower) / max(1e-9, self.upper - self.lower)
        kx = (w - h) * frac                                             # knob's left edge
        if not self.is_sensitive():
            snap.push_opacity(0.45)
        track = Gsk.RoundedRect()
        track.init_from_rect(Graphene.Rect().init(0, y, w, h), r)
        snap.push_rounded_clip(track)
        snap.append_color(theme.rgba("module_track"), Graphene.Rect().init(0, y, w, h))
        fill = Gsk.RoundedRect()
        fill.init_from_rect(Graphene.Rect().init(0, y, kx + h, h), r)
        snap.push_rounded_clip(fill)
        snap.append_color(theme.rgba("module_fill"), Graphene.Rect().init(0, y, kx + h, h))
        snap.pop()
        snap.pop()
        knob = Gsk.RoundedRect()
        knob.init_from_rect(Graphene.Rect().init(kx, y, h, h), r)
        shadow = Gdk.RGBA()
        shadow.parse("rgba(0,0,0,0.28)")
        snap.append_outset_shadow(knob, shadow, 0, 0, 0, 2.5)
        white = Gdk.RGBA()
        white.parse("#ffffff")
        snap.push_rounded_clip(knob)
        snap.append_color(white, Graphene.Rect().init(kx, y, h, h))
        snap.pop()
        edge = Gdk.RGBA()
        edge.parse("rgba(0,0,0,0.12)")
        snap.append_border(knob, [0.5] * 4, [edge] * 4)
        if not self.is_sensitive():
            snap.pop()


def slider(value: float = 0, on_change=None, style: str = "menu", lower: float = 0,
           upper: float = 100, default: float = None):
    """style: "menu" (thin, accent fill) or "module" (Control Center capsule).
    on_change(value) fires while dragging. default: a double-click puts the
    slider back to it (and on_change applies it)."""
    if style == "module":
        m = ModuleSlider(value, lower, upper)
        if on_change:
            m.connect("value-changed", lambda sl: on_change(sl.get_value()))
        return m
    s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, 1)
    s.add_css_class("sonata-slider" if style == "menu" else "sonata-module-slider")
    s.set_draw_value(False)
    s.set_value(value)
    s.set_hexpand(True)
    if on_change:
        s.connect("value-changed", lambda sc: on_change(sc.get_value()))
    if default is not None:
        reset_on_double_click(s, default)
    return s


def reset_on_double_click(scale: Gtk.Range, default: float) -> None:
    """Double-click a slider: back to its default value."""
    g = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def pressed(gest, n, _x, _y):
        if n == 2:
            GLib.idle_add(lambda: (scale.set_value(default), False)[1])   # after the scale's own jump
    g.connect("pressed", pressed)
    scale.add_controller(g)


def switch(active: bool = False, on_change=None) -> Gtk.Switch:
    sw = Gtk.Switch(active=active, css_classes=["sonata-switch"], valign=Gtk.Align.CENTER)
    if on_change:
        sw.connect("notify::active", lambda s, _p: on_change(s.get_active()))
    return sw
