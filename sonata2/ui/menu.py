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
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
popover.menu { background: none; box-shadow: none; padding: 0; }
popover.menu > contents {
  padding: 5px; border-radius: %(r_menu)s; min-width: %(menu_min_w)s;
  font-family: %(font)s; font-size: %(text_body)s;
  color: %(label)s; background-color: %(panel_material)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
/* every menu -- menu bar, right-click, and their nested submenus (which
   don't inherit our classes) -- is the same glass (Vini) */
popover.menu modelbutton {
  min-height: %(control_h)s; padding: 0 10px; border-radius: %(r_menu_row)s;
  color: inherit; background: none;
}
popover.menu modelbutton:hover, popover.menu modelbutton:selected,
popover.menu modelbutton:focus-visible {
  background-color: %(accent_selected)s; color: %(label_on_accent)s;
}
/* the pointer left the menu: no row stays highlighted (macOS); the
   keyboard brings the selection back */
popover.menu.pointer-out modelbutton:selected:not(:hover):not(:focus-visible) {
  background: none; color: inherit; }
popover.menu modelbutton:disabled { color: %(label_tertiary)s; }
/* GTK's own check/radio sits before the text and pushes every row's text
   right (its column is shared): hidden, with no width -- _checks_after_text
   shows a check after the text instead (Vini) */
popover.menu modelbutton check, popover.menu modelbutton radio { min-width: 0; min-height: 0; margin: 0;
  padding: 0; border: none; background: none; box-shadow: none; opacity: 0; -gtk-icon-size: 0; }
popover.menu modelbutton image.sonata-menu-check { -gtk-icon-size: 12px; margin-left: 12px; color: inherit; }
popover.menu modelbutton arrow { -gtk-icon-size: 12px; color: inherit; }
popover.menu separator { margin: 5px 10px; min-height: 1px; background-color: %(separator)s; }
/* pop-up buttons' lists (Gtk.DropDown: Settings, Control Center's sound
   devices...) are the same glass menu: no opaque list behind the rows */
popover.menu listview, popover.menu listview.view, popover.menu scrolledwindow { background: none; }
popover.menu listview > row { min-height: %(control_h)s; padding: 0 10px; margin: 0; border-radius: %(r_menu_row)s;
  color: inherit; background: none; }
popover.menu listview > row:hover, popover.menu listview > row:selected:hover,
popover.menu listview > row:focus-visible { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
popover.menu listview > row:selected { background: none; }
/* a hairline between the options (Vini), inset like the menus' separators;
   gone under the highlighted row and the one after it */
popover.menu listview > row:not(:first-child) {
  background-image: linear-gradient(%(separator)s, %(separator)s);
  background-size: calc(100%% - 20px) 1px; background-position: center top; background-repeat: no-repeat; }
popover.menu listview > row:hover, popover.menu listview > row:hover + row,
popover.menu listview > row:focus-visible, popover.menu listview > row:focus-visible + row { background-image: none; }
popover.menu listview > row image { -gtk-icon-size: 12px; color: inherit; }
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
    _one_click(main, lambda: (pop.popdown(), item.on_activate and item.on_activate()))
    x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["sonata-menu-x"], valign=Gtk.Align.CENTER,
                   tooltip_text="Close", can_focus=False)
    _one_click(x, lambda: (item.on_close(), row.set_visible(False)))
    row.append(main)
    row.append(x)
    return row


def _one_click(button: Gtk.Button, action) -> None:
    """Inside a PopoverMenu the menu's own gestures see the press first, so a
    plain "clicked" needed a second click: take the click in the capture
    phase and act on release over the button."""
    g = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def pressed(gest, *_a):
        gest.set_state(Gtk.EventSequenceState.CLAIMED)

    def released(gest, _n, x, y):
        if button.get_sensitive() and button.contains(x, y):
            action()
    g.connect("pressed", pressed)
    g.connect("released", released)
    button.add_controller(g)


def popup(widget: Gtk.Widget, sections, position=Gtk.PositionType.TOP,
          gap: int = 6, at=None, glass: bool = False, passthrough: bool = False) -> Gtk.PopoverMenu:
    """Show a menu anchored to `widget`; it cleans itself up when closed.
    at=(x, y) in widget coordinates: a context menu that opens at the
    pointer, its top-left corner there (macOS).
    passthrough: (menus inside an app window, e.g. Files' right-click) a
    click elsewhere in the window closes the menu *and* does its own thing
    -- selects, opens, right-clicks another file -- instead of only
    closing the menu."""
    group = Gio.SimpleActionGroup()
    customs = []
    model = _build(sections, group, customs=customs)
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    # on the menu, not the anchor: the anchor kept the last menu's actions
    # (and their closures) for good, and two menus on one anchor clashed
    pop.insert_action_group("m", group)
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
    if passthrough:
        _pass_clicks(pop, widget.get_root())
    OPEN.add(pop)
    _no_scroll(pop)
    _hover_only(pop)
    pop.connect("map", _checks_after_text)
    pop.popup()
    return pop


def _checks_after_text(pop) -> None:
    """Checkmark rows: the text starts where every other row's does and the
    check follows it at the right edge (Vini). GTK's own indicator (hidden
    by CSS) still carries the state; our image mirrors it."""
    def walk(w):
        w = w.get_first_child()
        while w is not None:
            if w.get_css_name() == "modelbutton":
                row(w)
                if getattr(w, "_sonata_check", None) is None and not getattr(w, "_sonata_watch", False):
                    # (an indicator GTK only makes when the row shows)
                    w._sonata_watch = True
                    w.connect("map", lambda b: GLib.idle_add(lambda: (row(b), False)[1]))
            # also inside a row: a nested submenu (the Dock's Options) is a popover of
            # its own under the row that opens it (Vini: "Keep in Dock" lost its check)
            walk(w)
            w = w.get_next_sibling()

    def indicator(btn):
        box = btn.get_first_child()
        c = box.get_first_child() if box is not None else None
        while c is not None:
            if c.get_css_name() in ("check", "radio"):
                return c
            c = c.get_next_sibling()
        return None

    def row(btn):
        ind = indicator(btn)
        if ind is None or getattr(btn, "_sonata_check", None) is not None:
            return
        img = Gtk.Image(icon_name="object-select-symbolic", css_classes=["sonata-menu-check"],
                        halign=Gtk.Align.END, hexpand=True)
        img.insert_before(btn, None)                  # last: after the text (and its shortcut)
        btn._sonata_check = img
        sync = lambda *_a: img.set_opacity(1 if ind.get_state_flags() & Gtk.StateFlags.CHECKED else 0)
        sync()
        ind.connect("state-flags-changed", sync)
        btn.connect("destroy", lambda *_a: img.get_parent() is not None and img.unparent())
    walk(pop)


def _pass_clicks(pop, root) -> None:
    if not isinstance(root, Gtk.Window):
        return
    pop.set_autohide(False)                     # no grab: clicks reach the window
    click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    def pressed(gest, *_a):
        # popovers are children of the window in the widget tree, so clicks on
        # the menu itself pass here too: only the window's own surface counts
        ev = gest.get_current_event()
        if ev is not None and ev.get_surface() == root.get_surface():
            pop.popdown()                        # not claimed: the click goes on
    click.connect("pressed", pressed)
    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", lambda _c, k, *_a: (pop.popdown(), True)[1] if k == Gdk.KEY_Escape else False)
    root.add_controller(click)
    root.add_controller(keys)
    # another window took over (a click in another app): close. The pointer
    # moving onto the menu itself can briefly take focus from the window on
    # some compositors -- that must not close it, so check a moment later
    # and only when the pointer isn't on the menu.
    inside = {"on": False}
    m = Gtk.EventControllerMotion()
    m.connect("enter", lambda *_a: inside.update(on=True))
    m.connect("leave", lambda *_a: inside.update(on=False))
    pop.add_controller(m)

    def deactivated(w, _p):
        if w.is_active():
            return

        def check():
            if not w.is_active() and not inside["on"] and pop.get_visible():
                pop.popdown()
            return False
        GLib.timeout_add(250, check)
    active = root.connect("notify::is-active", deactivated)

    def cleanup(_p):
        root.remove_controller(click)
        root.remove_controller(keys)
        root.disconnect(active)
    pop.connect("closed", cleanup)


def _hover_only(pop) -> None:
    """GTK keeps the last hovered row selected after the pointer leaves the
    menu; macOS clears it. Nested submenus are descendants of `pop`, so
    moving into one isn't a leave."""
    def popovers(w, out):
        if isinstance(w, Gtk.Popover):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            popovers(c, out)
            c = c.get_next_sibling()
        return out
    for p in popovers(pop, []):
        m = Gtk.EventControllerMotion()
        m.connect("leave", lambda _c, p=p: p.add_css_class("pointer-out"))
        m.connect("enter", lambda _c, _x, _y, p=p: p.remove_css_class("pointer-out"))
        p.add_controller(m)
    keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", lambda *_a: ([q.remove_css_class("pointer-out") for q in popovers(pop, [])], False)[1])
    pop.add_controller(keys)


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
