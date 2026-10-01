"""A colour picker in Sonata's own style (no GTK/libadwaita colour dialog:
its buttons and header came out in another theme). A panel popover: a grid
of swatches (macOS-like hues, lighter to darker), a hex field with a
preview, Cancel / Select.

    ui.colorpicker.popup(anchor, "#ff6a00", on_pick)    # on_pick("#rrggbb")
    ColorDot(color)                                      # the dot that opens it"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import controls, panel, theme  # noqa: E402

BASES = ("#0a84ff", "#30d158", "#ffd60a", "#ff9f0a", "#ff453a", "#bf5af2", "#a2845e")
LIGHTS = ("#ffffff", "#f2f2f7", "#d1d1d6", "#aeaeb2", "#8e8e93")
DARKS = ("#636366", "#48484a", "#3a3a3c", "#2c2c2e", "#000000")
ROWS = (0.45, 0.22, 0.0, -0.18, -0.36)       # + tint towards white, - shade towards black
SWATCH = 28

theme.register("""
.cp-panel { padding: 2px 2px 4px 2px; }
.cp-grid { margin: 0 10px; }
button.cp-swatch { min-width: %(sw)dpx; min-height: %(sw)dpx; padding: 0; margin: 0; border: none;
  border-radius: 0; background: none; box-shadow: none; }
.cp-grid > button.cp-swatch:first-child { border-top-left-radius: 6px; }
button.cp-swatch.selected { outline: 2px solid white; outline-offset: -4px; }   /* over the swatch (drawn on top) */
.cp-row { margin: 10px 10px 2px 10px; }
entry.cp-hex { min-height: %(control_h)s; font-family: monospace; }
.cp-buttons { margin: 10px 10px 4px 10px; }
button.cp-dot { min-width: 16px; min-height: 16px; padding: 2px; margin: 0 3px; border-radius: 99px; border: none;
  background: conic-gradient(#ff3b30, #ffcc00, #34c759, #0a84ff, #af52de, #ff3b30);
  box-shadow: none; transition: box-shadow %(t_fast)s; }
button.cp-dot.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 3.5px alpha(%(label)s, 0.45); }
""", key="colorpicker", sw=SWATCH)


def _mix(hex_color: str, k: float) -> str:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    target = 255 if k > 0 else 0
    rgb = [round(c + (target - c) * abs(k)) for c in rgb]
    return "#%02x%02x%02x" % tuple(rgb)


def palette() -> list:
    """Columns of five, light to dark: the hues, then greys."""
    cols = [[_mix(b, k) for k in ROWS] for b in BASES]
    return cols + [list(LIGHTS), list(DARKS)]


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

    def __init__(self, color: str, tooltip: str = "Other colour…"):
        self.swatch = Swatch(color, 12, round_=True)
        super().__init__(child=self.swatch, css_classes=["cp-dot"], valign=Gtk.Align.CENTER, tooltip_text=tooltip)

    def set_color(self, color: str) -> None:
        self.swatch.set_color(color)


def popup(anchor: Gtk.Widget, current: str, on_pick, title: str = "Accent Colour") -> Gtk.Popover:
    """The picker under `anchor`; on_pick("#rrggbb") when Select is pressed."""
    state = {"color": parse_hex(current) or BASES[0]}
    grid = Gtk.Grid(css_classes=["cp-grid"], halign=Gtk.Align.CENTER)
    buttons = {}
    preview = Swatch(state["color"], 22, round_=True)
    hexe = Gtk.Entry(text=state["color"], css_classes=["cp-hex"], max_length=7, width_chars=8, hexpand=True)

    def choose(c, from_entry=False):
        state["color"] = c
        preview.set_color(c)
        for k, b in buttons.items():
            (b.add_css_class if k == c else b.remove_css_class)("selected")
        if not from_entry and hexe.get_text() != c:
            hexe.set_text(c)
    for x, col in enumerate(palette()):
        for y, c in enumerate(col):
            b = Gtk.Button(child=Swatch(c, SWATCH), css_classes=["cp-swatch"], tooltip_text=c)
            b.connect("clicked", lambda _b, c=c: choose(c))
            buttons.setdefault(c, b)
            grid.attach(b, x, y, 1, 1)
    hexe.connect("changed", lambda e: (parse_hex(e.get_text()) and choose(parse_hex(e.get_text()), True)))
    row = Gtk.Box(spacing=8, css_classes=["cp-row"])
    row.append(preview)
    row.append(hexe)
    pop = None

    def select():
        on_pick(state["color"])
        pop.popdown()
    btns = Gtk.Box(spacing=8, css_classes=["cp-buttons"], homogeneous=True)
    btns.append(controls.push_button("Cancel", lambda: pop.popdown()))
    sel = controls.push_button("Select", select, style="default")
    btns.append(sel)
    hexe.connect("activate", lambda _e: select())
    col = panel.column(panel.header(title), grid, row, btns)
    col.add_css_class("cp-panel")
    pop = panel.popup(anchor, col, gap=6)
    pop.grid, pop.hex, pop.select_button, pop.state, pop.swatches = grid, hexe, sel, state, buttons   # (tests)
    choose(state["color"])
    # its window closing while it's open: let go of the anchor first (a
    # popover still attached when the anchor is freed crashes GTK)
    hid = anchor.connect("unrealize", lambda *_: pop.get_parent() is not None and pop.unparent())
    pop.connect("closed", lambda *_: anchor.disconnect(hid) if anchor.handler_is_connected(hid) else None)
    GLib.idle_add(lambda: (hexe.grab_focus(), False)[1])
    return pop
