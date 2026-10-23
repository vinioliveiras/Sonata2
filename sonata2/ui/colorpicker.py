"""A colour picker in Sonata's own style (no GTK/libadwaita colour dialog:
its buttons and header came out in another theme). A panel popover: a
colour wheel (hue around, saturation outwards; drag the knob), a
brightness slider, a hex field with a preview, Cancel / Select.

    ui.colorpicker.popup(anchor, "#ff6a00", on_pick)    # on_pick("#rrggbb")
    ColorDot(color)                                      # the dot that opens it"""
import colorsys
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import controls, panel, theme, tokens, transition  # noqa: E402
from ..i18n import _  # noqa: E402

DEFAULT = "#0a84ff"
WHEEL = 200                                   # the wheel's diameter
KNOB = 9                                      # the knob's radius (11 while held)

theme.register("""
.cp-panel { padding: 2px 2px 4px 2px; }
.cp-wheel { margin: 6px 14px 4px 14px; }
.cp-bright { margin: 10px 14px 2px 14px; }
.cp-row { margin: 10px 10px 2px 10px; }
entry.cp-hex { min-height: %(control_h)s; font-family: monospace; }
.cp-buttons { margin: 10px 10px 4px 10px; }
button.cp-dot { min-width: 16px; min-height: 16px; padding: 2px; margin: 0 3px; border-radius: 99px; border: none;
  background: conic-gradient(#ff3b30, #ffcc00, #34c759, #0a84ff, #af52de, #ff3b30);
  box-shadow: none; transition: box-shadow %(t_fast)s; }
button.cp-dot.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 3.5px alpha(%(label)s, 0.45); }
""", key="colorpicker")


def to_hex(h: float, s: float, v: float) -> str:
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def to_hsv(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))


def parse_hex(text: str):
    """'#abc', 'abc', '#aabbcc' -> '#aabbcc'; None when not a colour."""
    t = (text or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        return None
    try:
        int(t, 16)
    except ValueError:
        return None
    return "#" + t.lower()


def _rgba(hexc: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(hexc)
    return c


class Swatch(Gtk.Widget):
    """A filled shape of one colour (square, or round for dots)."""

    def __init__(self, color: str, size: int, round_: bool = False):
        super().__init__(can_target=False)
        self.color, self.size, self.round = color, size, round_
        self.set_size_request(size, size)

    def set_color(self, color: str) -> None:
        self.color = color
        self.queue_draw()

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        rect = Graphene.Rect().init(0, 0, w, h)
        if self.round:
            rr = Gsk.RoundedRect()
            rr.init_from_rect(rect, min(w, h) / 2)
            snap.push_rounded_clip(rr)
        snap.append_color(_rgba(self.color), rect)
        if self.round:
            snap.pop()


class ColorDot(Gtk.Button):
    """The "other colour" dot: a rainbow ring around the colour picked."""

    def __init__(self, color: str, tooltip: str = None):
        tooltip = _("Other colour…") if tooltip is None else tooltip
        self.swatch = Swatch(color, 12, round_=True)
        super().__init__(child=self.swatch, css_classes=["cp-dot"], valign=Gtk.Align.CENTER, tooltip_text=tooltip)

    def set_color(self, color: str) -> None:
        self.swatch.set_color(color)


class ColorWheel(Gtk.Widget):
    """Hue around the circle (red on the right, counter-clockwise, as macOS),
    saturation from the white centre out, darkened by the brightness. Press
    and the knob glides to the spot; drag and it follows. "changed" when the
    hue or saturation moves (h, s, v: 0..1)."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, h=0.0, s=0.0, v=1.0, size=WHEEL):
        super().__init__(css_classes=["cp-wheel"], halign=Gtk.Align.CENTER,
                         cursor=Gdk.Cursor.new_from_name("crosshair"))
        self.h, self.s, self.v, self.size = h, s, v, size
        self.knob = KNOB
        self.set_size_request(size, size)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", lambda g, dx, dy: self._at(*(a + d for a, d in zip(g.get_start_point()[1:], (dx, dy)))))
        drag.connect("drag-end", lambda *_a: self._grow(False))
        self.add_controller(drag)

    # -- geometry ---------------------------------------------------------------
    def _centre(self):
        return self.get_width() / 2, self.get_height() / 2, min(self.get_width(), self.get_height()) / 2

    def point_of(self, h, s):
        """(x, y) of a hue and saturation."""
        cx, cy, r = self._centre()
        a = h * 2 * math.pi
        return cx + r * s * math.cos(a), cy - r * s * math.sin(a)

    def hs_at(self, x, y):
        """Hue and saturation under (x, y); outside the circle, its edge."""
        cx, cy, r = self._centre()
        dx, dy = x - cx, cy - y
        return (math.atan2(dy, dx) / (2 * math.pi)) % 1.0, min(1.0, math.hypot(dx, dy) / max(1, r))

    # -- input ------------------------------------------------------------------
    def _begin(self, _g, x, y):
        self._grow(True)
        h, s = self.hs_at(x, y)
        self.glide(h, s, self.v)

    def _at(self, x, y):
        self._stop()
        self.h, self.s = self.hs_at(x, y)
        self.queue_draw()
        self.emit("changed")

    def _stop(self):
        a = getattr(self, "_tweens", {}).pop("glide", None)
        if a:
            a[0].pause()
            a[1].stop()

    def _grow(self, held: bool):
        start, end = self.knob, KNOB + (2 if held else 0)
        transition.tween(self, "knob", start, end, tokens.ms(150),
                         lambda k: (setattr(self, "knob", k), self.queue_draw()), "colour-knob")

    def set_hsv(self, h, s, v) -> None:
        """Quietly (no "changed"): a colour read from elsewhere."""
        self._stop()
        self.h, self.s, self.v = h, s, v
        self.queue_draw()

    def glide(self, h, s, v, quiet=False) -> None:
        """The knob slides to (h, s) on a straight line, the colour with it."""
        a0 = self.h * 2 * math.pi
        x0, y0, v0 = self.s * math.cos(a0), self.s * math.sin(a0), self.v
        a1 = h * 2 * math.pi
        x1, y1 = s * math.cos(a1), s * math.sin(a1)

        def step(t):
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            if t >= 1:
                self.h, self.s = h, s                  # exact at the end (the centre has no hue)
            else:
                self.h, self.s = (math.atan2(y, x) / (2 * math.pi)) % 1.0, min(1.0, math.hypot(x, y))
            self.v = v0 + (v - v0) * t
            self.queue_draw()
            if not quiet:
                self.emit("changed")
        transition.tween(self, "glide", 0.0, 1.0, tokens.ms(180), step, "colour-glide")

    # -- drawing ----------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        cx, cy, r = self._centre()
        rect = Graphene.Rect().init(cx - r, cy - r, 2 * r, 2 * r)
        circle = Gsk.RoundedRect()
        circle.init_from_rect(rect, r)
        snap.push_rounded_clip(circle)
        # conic: clockwise from the top; hue: counter-clockwise from the right
        hues = [to_hex(((90 - 30 * i) % 360) / 360, 1, 1) for i in range(13)]
        snap.append_conic_gradient(rect, Graphene.Point().init(cx, cy), 0, controls.color_stops(*hues))
        snap.append_radial_gradient(rect, Graphene.Point().init(cx, cy), r, r, 0, 1,
                                    controls.color_stops("#ffffff", "rgba(255,255,255,0)"))
        if self.v < 1:
            dark = Gdk.RGBA()
            dark.parse(f"rgba(0,0,0,{1 - self.v:.3f})")
            snap.append_color(dark, rect)
        snap.pop()
        edge = Gdk.RGBA()
        edge.parse("rgba(0,0,0,0.12)")
        snap.append_border(circle, [0.5] * 4, [edge] * 4)
        # the knob: the colour, in a white ring
        x, y = self.point_of(self.h, self.s)
        k = self.knob
        ring = Gsk.RoundedRect()
        ring.init_from_rect(Graphene.Rect().init(x - k, y - k, 2 * k, 2 * k), k)
        shadow = Gdk.RGBA()
        shadow.parse("rgba(0,0,0,0.35)")
        snap.append_outset_shadow(ring, shadow, 0, 0.5, 0, 2)
        white = Gdk.RGBA()
        white.parse("#ffffff")
        snap.append_border(ring, [3] * 4, [white] * 4)
        inner = Gsk.RoundedRect()
        inner.init_from_rect(Graphene.Rect().init(x - k + 3, y - k + 3, 2 * k - 6, 2 * k - 6), k - 3)
        snap.push_rounded_clip(inner)
        snap.append_color(_rgba(to_hex(self.h, self.s, self.v)), inner.bounds)
        snap.pop()


def popup(anchor: Gtk.Widget, current: str, on_pick, title: str = None) -> Gtk.Popover:
    """The picker under `anchor`; on_pick("#rrggbb") when Select is pressed."""
    title = _("Accent Colour") if title is None else title
    state = {"color": parse_hex(current) or DEFAULT}
    h, s, v = to_hsv(state["color"])
    wheel = ColorWheel(h, s, v)
    bright = controls.ModuleSlider(v * 100, 0, 100, gradient=("#000000", to_hex(h, s, 1)))
    bright.set_tooltip_text(_("Brightness"))
    bright.add_css_class("cp-bright")
    preview = Swatch(state["color"], 22, round_=True)
    hexe = Gtk.Entry(text=state["color"], css_classes=["cp-hex"], max_length=7, width_chars=8, hexpand=True)
    busy = []                                     # (a change made here: not echoed back)

    def show(c, from_entry=False):
        state["color"] = c
        preview.set_color(c)
        bright.set_gradient("#000000", to_hex(wheel.h, wheel.s, 1))
        if not from_entry and hexe.get_text() != c:
            busy.append(1)
            hexe.set_text(c)
            busy.pop()

    def from_wheel(_w):
        show(to_hex(wheel.h, wheel.s, wheel.v))
        if abs(bright.get_value() - wheel.v * 100) > 0.5:
            busy.append(1)
            bright.set_value(wheel.v * 100)
            busy.pop()
    wheel.connect("changed", from_wheel)

    def from_slider(sl):
        if busy:
            return
        wheel.set_hsv(wheel.h, wheel.s, sl.get_value() / 100)
        show(to_hex(wheel.h, wheel.s, wheel.v))
    bright.connect("value-changed", from_slider)

    def from_entry(e):
        c = parse_hex(e.get_text())
        if busy or not c:
            return                                # ignored until it's a colour
        nh, ns, nv = to_hsv(c)
        if ns == 0:
            nh = wheel.h                          # a grey: keep the hue (no swing round)
        state["color"] = c
        wheel.glide(nh, ns, nv, quiet=True)
        busy.append(1)
        bright.set_value(nv * 100)
        busy.pop()
        preview.set_color(c)
        bright.set_gradient("#000000", to_hex(nh, ns, 1))
    hexe.connect("changed", from_entry)
    row = Gtk.Box(spacing=8, css_classes=["cp-row"])
    row.append(preview)
    row.append(hexe)
    pop = None

    def select():
        on_pick(state["color"])
        pop.popdown()
    btns = Gtk.Box(spacing=8, css_classes=["cp-buttons"], homogeneous=True)
    btns.append(controls.push_button(_("Cancel"), lambda: pop.popdown()))
    sel = controls.push_button(_("Select"), select, style="default")
    btns.append(sel)
    hexe.connect("activate", lambda _e: select())
    col = panel.column(panel.header(title), wheel, bright, row, btns)
    col.add_css_class("cp-panel")
    pop = panel.popup(anchor, col, gap=6)
    pop.wheel, pop.brightness, pop.hex, pop.select_button, pop.state = wheel, bright, hexe, sel, state   # (tests)
    # its window closing while it's open: let go of the anchor first (a
    # popover still attached when the anchor is freed crashes GTK)
    hid = anchor.connect("unrealize", lambda *_: pop.get_parent() is not None and pop.unparent())
    pop.connect("closed", lambda *_: anchor.disconnect(hid) if anchor.handler_is_connected(hid) else None)
    GLib.idle_add(lambda: (hexe.grab_focus(), False)[1])
    return pop
