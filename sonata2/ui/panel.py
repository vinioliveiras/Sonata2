"""Panels: popovers with the menu material that hold custom content (menu
bar extras like Wi-Fi/Sound/Battery, Control Center, calendars).

    pop = panel.popup(anchor, content, position=Gtk.PositionType.BOTTOM)
    panel.header("Wi-Fi", trailing=controls.switch(True))
    panel.row("network-wireless-signal-good-symbolic", "Home", trailing=None, on_click=cb)
    panel.section_title("Known Networks")
    panel.module(child)                  # Control Center rounded block
    panel.toggle("bluetooth-active-symbolic", "Bluetooth", on, cb)   # round toggle + label

Open panels count in ui.menu.OPEN (auto-hiding surfaces stay put)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import menu, theme  # noqa: E402

theme.register("""
popover.sonata-panel { background: none; box-shadow: none; padding: 0; }
popover.sonata-panel > contents {
  padding: 6px; border-radius: %(r_dialog)s; min-width: 260px;
  font-family: %(font)s; font-size: %(text_body)s; color: %(label)s; background-color: %(panel_material)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
.panel-header { padding: 4px 10px 6px 10px; }
.panel-header > label { font-weight: 700; }
.panel-section { padding: 6px 10px 2px 10px; font-size: %(text_small)s; font-weight: 600;
  color: %(label_secondary)s; }
.panel-row { min-height: %(control_h)s; padding: 1px 10px; border-radius: %(r_menu_row)s;
  background: none; border: none; box-shadow: none; }
.panel-row:hover { background: alpha(%(label)s, 0.10); }   /* Big Sur status menus: soft grey */
.panel-row label { font-weight: 400; }
.panel-row.static:hover { background: none; color: inherit; }
/* just opened: no row lit until the pointer really moves in the panel (GTK
   hands a new popover the pointer's last spot: a row under it flashed) */
popover.sonata-panel.fresh .panel-row:hover { background: none; }
.panel-sep { min-height: 1px; margin: 5px 10px; background: %(separator)s; }
.panel-module { background: %(module_bg)s; border-radius: 12px; padding: 10px;
  box-shadow: 0 0 0 0.5px %(separator)s; }
.panel-module-title { font-weight: 700; }
.panel-toggle { min-width: 28px; min-height: 28px; padding: 0; border-radius: 99px; border: none;
  background: %(toggle_off)s; color: %(label)s; box-shadow: none; }
.panel-toggle.on { background: %(accent)s; color: %(label_on_accent)s; }
.panel-caption { font-size: %(text_small)s; color: %(label_secondary)s; }
popover.sonata-panel calendar { background: none; border: none; box-shadow: none; color: %(label)s;
  padding: 4px; }
popover.sonata-panel calendar > header { background: none; border: none; }
popover.sonata-panel calendar > grid > label.day-name { color: %(label_secondary)s; font-size: %(text_small)s; }
popover.sonata-panel calendar > grid > label.other-month { color: %(label_tertiary)s; }
popover.sonata-panel calendar > grid > label.today { background: %(destructive)s; color: %(label_on_accent)s;
  border-radius: 99px; box-shadow: none; }
popover.sonata-panel calendar > grid > label:selected { background: %(accent)s; color: %(label_on_accent)s;
  border-radius: 99px; }
""", key="panel")


def popup(anchor: Gtk.Widget, child: Gtk.Widget, position=Gtk.PositionType.BOTTOM, gap: int = 4,
          align_start: bool = False, width: int = None) -> Gtk.Popover:
    """width: a fixed content width (menu bar panels): long names ellipsize
    instead of stretching the panel (ui/fixed.py)."""
    pop = Gtk.Popover(css_classes=["sonata-panel"], has_arrow=False, position=position)
    if width:
        from .fixed import FixedWidth
        child = FixedWidth(child, width)
    pop.set_child(child)
    P = Gtk.PositionType
    pop.set_offset(*{P.TOP: (0, -gap), P.BOTTOM: (0, gap), P.LEFT: (-gap, 0), P.RIGHT: (gap, 0)}[position])
    pop.set_parent(anchor)
    if align_start:
        align_to_start(pop, anchor, gap)

    def closed(p):
        menu.OPEN.discard(p)
        GLib.idle_add(lambda: (p.unparent(), False)[1])
        for cb in list(menu.on_closed):
            cb()
    pop.connect("closed", closed)
    menu.OPEN.add(pop)
    hold_hover(pop)
    pop.popup()
    return pop


def hold_hover(pop: Gtk.Popover, slack: float = 2.0) -> None:
    """No hover highlight until the pointer moves inside the panel (the
    "fresh" class): a row that only seemed under the pointer as the panel
    opened (or as its rows were filled in) lit up, then went dark."""
    pop.add_css_class("fresh")
    seen = {"at": None}
    motion = Gtk.EventControllerMotion()

    def moved(_c, x, y):
        if seen["at"] is None:
            seen["at"] = (x, y)                  # the first report: where GTK thinks it is
        elif abs(x - seen["at"][0]) + abs(y - seen["at"][1]) > slack:
            pop.remove_css_class("fresh")
    motion.connect("enter", lambda _c, x, y: seen.__setitem__("at", (x, y)))
    motion.connect("motion", moved)
    pop.add_controller(motion)
    pop.hover_motion = moved                     # (tests)


def align_to_start(pop: Gtk.Popover, anchor: Gtk.Widget, gap: int = 4) -> None:
    """Left-align a popover below `anchor` (macOS menus hang from the title's
    left edge) instead of GTK's centring."""
    def fix(*_):
        pw = pop.get_width()
        if pw > 0:
            pop.set_offset(int((pw - anchor.get_width()) / 2), gap)
    pop.connect("map", lambda *_: GLib.idle_add(lambda: (fix(), False)[1]))


def column(*children, spacing: int = 0) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for c in children:
        if c is not None:
            box.append(c)
    return box


def header(title: str, trailing: Gtk.Widget = None) -> Gtk.Box:
    box = Gtk.Box(css_classes=["panel-header"])
    box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
    if trailing is not None:
        box.append(trailing)
    return box


def section_title(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0, css_classes=["panel-section"])


def separator() -> Gtk.Box:
    return Gtk.Box(css_classes=["panel-sep"])


def row(icon_name, text: str, trailing: Gtk.Widget = None, on_click=None) -> Gtk.Widget:
    """A menu-like row; `.label` / `.icon` let callers update it later."""
    content = Gtk.Box(spacing=8)
    icon = None
    if icon_name:
        icon = Gtk.Image(icon_name=icon_name, pixel_size=16)
        content.append(icon)
    label = Gtk.Label(label=text, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=30)
    content.append(label)
    if trailing is not None:
        content.append(trailing)
    if on_click is None:
        content.add_css_class("panel-row")
        content.add_css_class("static")
        content.label, content.icon = label, icon
        return content
    b = Gtk.Button(css_classes=["panel-row"], can_focus=False)
    b.set_child(content)
    b.connect("clicked", lambda _b: on_click())
    b.label, b.icon = label, icon
    return b


def module(*children, spacing: int = 8) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, css_classes=["panel-module"])
    for c in children:
        box.append(c)
    return box


def toggle(icon_name: str, title: str, on: bool, on_change, caption: str = "") -> Gtk.Box:
    """Control Center style: round icon toggle + title (+ caption)."""
    btn = Gtk.Button(icon_name=icon_name, css_classes=["panel-toggle"] + (["on"] if on else []),
                     valign=Gtk.Align.CENTER, can_focus=False)

    def clicked(_b):
        now = not btn.has_css_class("on")
        (btn.add_css_class if now else btn.remove_css_class)("on")
        on_change(now)
    btn.connect("clicked", clicked)
    box = Gtk.Box(spacing=8)
    box.append(btn)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    texts.append(Gtk.Label(label=title, xalign=0, css_classes=["panel-module-title"]))
    box.caption = Gtk.Label(label=caption, xalign=0, css_classes=["panel-caption"], visible=bool(caption),
                            ellipsize=Pango.EllipsizeMode.END, max_width_chars=16)
    texts.append(box.caption)
    box.append(texts)
    box.button = btn
    return box


def set_toggle(box, on: bool, caption: str = None) -> None:
    """Update a toggle() without calling its callback."""
    (box.button.add_css_class if on else box.button.remove_css_class)("on")
    if caption is not None:
        box.caption.set_label(caption)
        box.caption.set_visible(bool(caption))
