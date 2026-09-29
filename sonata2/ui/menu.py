"""Context menus and menu popovers (macOS Big Sur menus).

    from sonata2.ui import menu
    menu.popup(widget, [
        [menu.Item("Window title", on_activate)],                 # a section
        [menu.Item("Options", submenu=[[menu.Item("Keep", cb, checked=True)]])],
        [menu.Item("Quit", quit_cb, enabled=running)],
    ])

Sections are separated by a line. Submenus open to the side (NESTED), like
macOS. The style targets every `popover.menu` of the process, so GTK's own
nested submenus (which don't inherit our classes) match too."""
from dataclasses import dataclass, field
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
popover.menu { background: none; box-shadow: none; padding: 0; }
popover.menu > contents {
  padding: 5px; border-radius: %(r_menu)s; min-width: %(menu_min_w)s;
  font-family: %(font)s; font-size: %(text_body)s;
  color: %(label)s; background-color: %(menu_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
popover.menu modelbutton {
  min-height: %(control_h)s; padding: 0 10px; border-radius: %(r_menu_row)s;
  color: inherit; background: none;
}
popover.menu modelbutton:hover, popover.menu modelbutton:selected,
popover.menu modelbutton:focus-visible {
  background-color: %(accent_selected)s; color: %(label_on_accent)s;
}
popover.menu modelbutton:disabled { color: %(label_tertiary)s; }
popover.menu modelbutton check { min-width: 12px; min-height: 12px; margin-right: 4px;
  border: none; background: none; box-shadow: none; color: inherit; -gtk-icon-size: 12px; }
popover.menu modelbutton arrow { -gtk-icon-size: 12px; color: inherit; }
popover.menu separator { margin: 5px 10px; min-height: 1px; background-color: %(separator)s; }
""")


# Open menus of this process, so surfaces can stay put while one is open
# (e.g. an auto-hiding Dock). `on_closed` callbacks run after any closes.
OPEN = set()
on_closed = []


@dataclass
class Item:
    label: str
    on_activate: Optional[Callable] = None     # called with no args (checked: with new state)
    checked: Optional[bool] = None              # None = plain item, bool = checkmark item
    enabled: bool = True
    submenu: list = field(default_factory=list)  # list of sections


def _build(sections, group, prefix="i") -> Gio.Menu:
    model = Gio.Menu()
    for s_i, section in enumerate(sections):
        sec = Gio.Menu()
        for i_i, item in enumerate(section):
            name = f"{prefix}{s_i}_{i_i}"
            if item.submenu:
                sec.append_submenu(item.label, _build(item.submenu, group, name + "_"))
                continue
            if item.checked is None:
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", lambda _a, _p, f=item.on_activate: f and f())
            else:
                act = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", item.checked))
                act.connect("activate", lambda a, _p, f=item.on_activate:
                            f and f(not a.get_state().get_boolean()))
            act.set_enabled(item.enabled)
            group.add_action(act)
            sec.append(item.label, "m." + name)
        model.append_section(None, sec)
    return model


def popup(widget: Gtk.Widget, sections, position=Gtk.PositionType.TOP,
          gap: int = 6, at=None) -> Gtk.PopoverMenu:
    """Show a menu anchored to `widget`; it cleans itself up when closed.
    at=(x, y) in widget coordinates: a context menu that opens at the
    pointer, its top-left corner there (macOS)."""
    group = Gio.SimpleActionGroup()
    model = _build(sections, group)
    widget.insert_action_group("m", group)
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    pop.set_has_arrow(False)
    pop.set_position(position)
    P = Gtk.PositionType
    pop.set_offset(*{P.TOP: (0, -gap), P.BOTTOM: (0, gap), P.LEFT: (-gap, 0), P.RIGHT: (gap, 0)}[position])
    pop.set_parent(widget)
    if at is not None:
        from gi.repository import Gdk
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(at[0]), int(at[1]), 1, 1
        pop.set_pointing_to(r)
        pop.set_position(Gtk.PositionType.BOTTOM)
        pop.set_halign(Gtk.Align.START)
        pop.set_offset(0, 2)

    def closed(p):
        OPEN.discard(p)
        GLib.idle_add(lambda: (p.unparent(), False)[1])
        for cb in list(on_closed):
            cb()
    pop.connect("closed", closed)
    OPEN.add(pop)
    pop.popup()
    return pop


def open_submenu(pop: Gtk.PopoverMenu, label: str) -> bool:
    """Open the submenu item called `label` (screenshots, tests)."""
    def walk(w):
        while w is not None:
            if type(w).__gtype__.name == "GtkModelButton" and w.get_property("text") == label:
                return w.activate()
            if walk(w.get_first_child()):
                return True
            w = w.get_next_sibling()
        return False
    return walk(pop.get_first_child())
