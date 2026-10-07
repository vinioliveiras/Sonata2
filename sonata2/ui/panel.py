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
.panel-module { background: %(module_bg)s; border-radius: %(r_dialog)s; padding: 10px;
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
    child = fit_screen(child, anchor)
    if width:
        from .fixed import FixedWidth
        child = FixedWidth(child, width)
    pop.set_child(child)
    P = Gtk.PositionType
    pop.set_offset(*{P.TOP: (0, -gap), P.BOTTOM: (0, gap), P.LEFT: (-gap, 0), P.RIGHT: (gap, 0)}[position])
    pop.set_parent(anchor)
    if align_start:
        align_to_start(pop, anchor, gap)

    # GTK 4: a widget that can't take focus keeps its descendants from taking
    # it too, and a popover is a child of its anchor -- a text field in a panel
    # hung from a toolbar button (can_focus=False) couldn't be typed in. The
    # chain may take focus while the panel is open (a click still doesn't
    # focus the button: focus_on_click).
    unlocked = []
    w = anchor
    while w is not None and not isinstance(w, Gtk.Root):
        if not w.get_can_focus():
            w.set_can_focus(True)
            unlocked.append((w, w.get_focus_on_click()))
            w.set_focus_on_click(False)
        w = w.get_parent()

    # a click anywhere else in the window closes the panel. GTK's autohide
    # relies on the compositor's popup grab, which can be lost on Wayland (a
    # keyring prompt while saving the Assistant's key): the panel then stayed
    # open over everything (Vini).
    root = anchor.get_root()
    outside = None
    if isinstance(root, Gtk.Window):
        outside = Gtk.GestureClick(button=0)
        outside.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def close_if_outside(surface):
            # a press on the window itself (not on the panel, or a pop-up
            # button's list inside it: their own surfaces)
            if pop.get_visible() and surface is not None and surface == root.get_surface():
                pop.popdown()

        def pressed(g, *_a):
            ev = g.get_current_event()
            close_if_outside(ev.get_surface() if ev is not None else None)
        outside.connect("pressed", pressed)
        root.add_controller(outside)
        pop.press_outside = close_if_outside       # (tests)

    def closed(p):
        if outside is not None and outside.get_widget() is not None:
            outside.get_widget().remove_controller(outside)
        for wid, foc in unlocked:
            wid.set_can_focus(False)
            wid.set_focus_on_click(foc)
        unlocked.clear()
        menu.OPEN.discard(p)
        GLib.idle_add(lambda: (p.unparent(), False)[1])
        for cb in list(menu.on_closed):
            cb()
    pop.connect("closed", closed)
    menu.OPEN.add(pop)
    hold_hover(pop)
    pop.popup()
    menu.watch_shown(pop)
    return pop


# a panel never reaches past its display: the menu bar above it, its own
# padding and a margin below (a popover taller than the screen is cut off)
SCREEN_MARGIN = 48


def max_height(screen_h: int) -> int:
    """The tallest a panel's content may be on a display this tall."""
    return max(200, int(screen_h) - SCREEN_MARGIN)


def fit_screen(child: Gtk.Widget, anchor: Gtk.Widget) -> Gtk.Widget:
    """`child` in a scroller that is exactly its size, until the display is
    too short for it (a long Wi-Fi list, many audio apps on a 720 px
    laptop): then it scrolls instead of running off the screen."""
    from .window import screen_size
    size = screen_size(anchor)
    if not size:
        return child
    return Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER,
                              propagate_natural_width=True, propagate_natural_height=True,
                              max_content_height=max_height(size[1]), css_classes=["panel-scroller"])


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
