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
/* menu bar menus only: glass (right-click menus stay opaque -- Vini) */
popover.menu.glass > contents { background-color: %(panel_material)s; }
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
/* rows with a small x (Dock: an app's windows) */
popover.menu .sonata-menu-row { min-height: %(control_h)s; border-radius: %(r_menu_row)s; padding: 0 4px 0 0; }
popover.menu .sonata-menu-row:hover { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
popover.menu .sonata-menu-row-label { background: none; border: none; box-shadow: none; padding: 0 10px;
  min-height: %(control_h)s; color: inherit; font-weight: normal; }
popover.menu .sonata-menu-x { min-width: 16px; min-height: 16px; padding: 0; border-radius: 99px; border: none;
  background: none; box-shadow: none; color: inherit; opacity: 0.55; -gtk-icon-size: 10px; }
popover.menu .sonata-menu-x:hover { opacity: 1; background: alpha(currentColor, 0.18); }
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
    on_close: Optional[Callable] = None         # a small x at the right (e.g. close that window)


def _build(sections, group, prefix="i", customs=None) -> Gio.Menu:
    model = Gio.Menu()
    for s_i, section in enumerate(sections):
        sec = Gio.Menu()
        for i_i, item in enumerate(section):
            name = f"{prefix}{s_i}_{i_i}"
            if item.submenu:
                sec.append_submenu(item.label, _build(item.submenu, group, name + "_", customs))
                continue
            if item.on_close is not None and customs is not None:     # a row with its own x
                mi = Gio.MenuItem.new(None, None)
                mi.set_attribute_value("custom", GLib.Variant("s", name))
                sec.append_item(mi)
                customs.append((name, item))
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


def _closable_row(pop, item) -> Gtk.Widget:
    """A menu row with a small x on the right: the label does the item's
    action (and closes the menu); the x runs on_close and removes the row."""
    row = Gtk.Box(css_classes=["sonata-menu-row"], hexpand=True)
    main = Gtk.Button(child=Gtk.Label(label=item.label, xalign=0, ellipsize=3, max_width_chars=40),
                      css_classes=["sonata-menu-row-label"], hexpand=True, can_focus=False)
    main.set_sensitive(item.enabled)
    main.connect("clicked", lambda *_: (pop.popdown(), item.on_activate and item.on_activate()))
    x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["sonata-menu-x"], valign=Gtk.Align.CENTER,
                   tooltip_text="Close", can_focus=False)
    x.connect("clicked", lambda *_: (item.on_close(), row.set_visible(False)))
    row.append(main)
    row.append(x)
    return row


def popup(widget: Gtk.Widget, sections, position=Gtk.PositionType.TOP,
          gap: int = 6, at=None, glass: bool = False) -> Gtk.PopoverMenu:
    """Show a menu anchored to `widget`; it cleans itself up when closed.
    at=(x, y) in widget coordinates: a context menu that opens at the
    pointer, its top-left corner there (macOS)."""
    group = Gio.SimpleActionGroup()
    customs = []
    model = _build(sections, group, customs=customs)
    widget.insert_action_group("m", group)
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    for name, item in customs:
        pop.add_child(_closable_row(pop, item), name)
    pop.set_has_arrow(False)
    if glass:
        pop.add_css_class("glass")
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
    _no_scroll(pop)
    pop.popup()
    return pop


def _no_scroll(pop) -> None:
    """macOS menus never scroll: every item stays visible. The compositor
    may shrink a popup that doesn't fit below its anchor (GTK then adds a
    scrollbar); when that happens the menu is moved up by the missing
    height (our own "slide") and shown again at full size."""
    def scrolled(w, out):
        w = w.get_first_child()
        while w is not None:
            if isinstance(w, Gtk.ScrolledWindow):
                out.append(w)
            scrolled(w, out)
            w = w.get_next_sibling()
        return out

    def check(*_a):
        missing = 0
        for sw in scrolled(pop, []):
            adj = sw.get_vadjustment()
            if sw.get_mapped() and adj.get_upper() - adj.get_page_size() > 1:
                missing = max(missing, int(adj.get_upper() - adj.get_page_size()))
        tries = getattr(pop, "_slides", 0)
        if missing and tries < 3:
            pop._slides = tries + 1
            x, y = pop.get_offset()
            pop.set_offset(x, y - missing)
            pop.present()
        return False
    pop.connect("map", lambda *_: GLib.timeout_add(30, check))
    for sw in scrolled(pop, []):
        sw.get_vadjustment().connect("changed", lambda *_: GLib.idle_add(check))
