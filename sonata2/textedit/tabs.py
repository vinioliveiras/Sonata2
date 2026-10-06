"""macOS-style tab bar (Safari / TextEdit / Finder): equal-width tabs under the
glass toolbar, the selected one in the document's own background, the
others in the bar's (like Terminal's and Files'); a close button fades in at a tab's left edge on
hover, where an edited document shows a dot; "+" at the right. Tabs are
dragged sideways to reorder (they glide into place), middle-click closes,
right-click asks the owner for a menu."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Graphene, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

ui.register("""
.te-tabs { box-shadow: inset 0 -1px %(separator)s; }
/* like Terminal's and Files': the tab in front is the document's own
   background, the others sit in the bar, no grey of their own (Vini) */
.te-tab, .te-tabs button.te-tab-new {
  min-height: 28px; background-color: %(titlebar_bg)s; background-image: none;
  box-shadow: inset -1px 0 %(separator)s, inset 0 -1px %(separator)s;
  transition: background-image %(t_fast)s %(ease_out)s, color %(t_fast)s; }
window:backdrop .te-tab, window:backdrop .te-tabs button.te-tab-new { background-color: %(titlebar_bg_inactive)s; }
.te-tab { min-width: 64px; color: %(label_secondary)s; font-size: %(text_body)s; }
.te-tab:hover { background-image: linear-gradient(%(tool_hover)s, %(tool_hover)s); color: %(label)s; }
.te-tab.selected, window:backdrop .te-tab.selected { background-color: %(content_bg)s; background-image: none;
  color: %(label)s; box-shadow: inset -1px 0 %(separator)s; }
.te-tab.dragging { background-image: none; box-shadow: inset 1px 0 %(separator)s, inset -1px 0 %(separator)s,
  %(shadow_control)s; }
.te-tab label { padding: 0 26px; }
.te-tab button.te-tab-close { min-width: 16px; min-height: 16px; padding: 0; margin-left: 6px;
  border-radius: %(r_menu_row)s; border: none; background: none; box-shadow: none;
  color: %(label_secondary)s; opacity: 0; transition: opacity %(t_fast)s, background %(t_fast)s; }
.te-tab button.te-tab-close image { -gtk-icon-size: 10px; }
.te-tab:hover button.te-tab-close { opacity: 1; }
.te-tab button.te-tab-close:hover { background: alpha(%(label)s, 0.10); color: %(label)s; }
.te-tab button.te-tab-close:active { background: alpha(%(label)s, 0.18); }
.te-tab .te-tab-dot { min-width: 7px; min-height: 7px; margin-left: 10px; border-radius: 99px;
  background: %(label_secondary)s; opacity: 0; transition: opacity %(t_fast)s; }
.te-tab.modified .te-tab-dot { opacity: 1; }
.te-tab.modified:hover .te-tab-dot { opacity: 0; }
.te-tabs button.te-tab-new { min-width: 30px; padding: 0; border: none; border-radius: 0;
  box-shadow: inset 0 -1px %(separator)s; color: %(label_secondary)s; }
.te-tabs button.te-tab-new:hover { color: %(label)s;
  background-image: linear-gradient(alpha(%(label)s, 0.11), alpha(%(label)s, 0.11)); }
.te-tabs button.te-tab-new image { -gtk-icon-size: 12px; }
""", key="textedit-tabs")


class Tab(Gtk.Overlay):
    def __init__(self, doc, strip):
        super().__init__(css_classes=["te-tab"], hexpand=True)
        self.doc = doc
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, width_chars=1, max_width_chars=40,
                               hexpand=True, xalign=0.5)
        self.set_child(self.label)
        self.dot = Gtk.Box(css_classes=["te-tab-dot"], halign=Gtk.Align.START, valign=Gtk.Align.CENTER,
                           can_target=False)
        self.add_overlay(self.dot)
        self.close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["te-tab-close"],
                                halign=Gtk.Align.START, valign=Gtk.Align.CENTER, can_focus=False,
                                tooltip_text="Close Tab")
        self.close.connect("clicked", lambda *_: strip.on_close(self.doc))
        self.add_overlay(self.close)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._pressed, strip)
        click.connect("released", self._released, strip)
        self.add_controller(click)

    def _pressed(self, g, _n, x, y, strip):
        b = g.get_current_button()
        if b == Gdk.BUTTON_PRIMARY:
            strip.on_select(self.doc)
        elif b == Gdk.BUTTON_SECONDARY and strip.on_menu:
            strip.on_menu(self.doc, self, x, y)

    def _released(self, g, _n, _x, _y, strip):
        if g.get_current_button() == Gdk.BUTTON_MIDDLE:
            strip.on_close(self.doc)

    def set_title(self, title: str, modified: bool, tooltip: str = None) -> None:
        self.label.set_label(title)
        self.set_tooltip_text(tooltip or title)
        (self.add_css_class if modified else self.remove_css_class)("modified")


class TabStrip(Gtk.Box):
    """on_select(doc), on_close(doc), on_new(), on_reorder([doc, ...]),
    on_menu(doc, tab_widget, x, y)."""

    def __init__(self, on_select, on_close, on_new, on_reorder, on_menu=None):
        super().__init__(css_classes=["te-tabs"])
        self.on_select, self.on_close, self.on_new = on_select, on_close, on_new
        self.on_reorder, self.on_menu = on_reorder, on_menu
        self.row = Gtk.Box(homogeneous=True, hexpand=True)
        self.append(self.row)
        plus = Gtk.Button(icon_name="list-add-symbolic", css_classes=["te-tab-new"], can_focus=False,
                          tooltip_text="New Tab")
        plus.connect("clicked", lambda *_: self.on_new())
        self.append(plus)
        self._drag = None
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.row.add_controller(drag)

    # -- model -----------------------------------------------------------------------------
    def tabs(self) -> list:
        out, c = [], self.row.get_first_child()
        while c is not None:
            out.append(c)
            c = c.get_next_sibling()
        return out

    def tab_of(self, doc):
        return next((t for t in self.tabs() if t.doc is doc), None)

    def add(self, doc, index: int = None) -> Tab:
        tab = Tab(doc, self)
        tabs = self.tabs()
        if index is None or index >= len(tabs):
            self.row.append(tab)
        else:
            self.row.insert_child_after(tab, tabs[index - 1] if index > 0 else None)
        return tab

    def remove(self, doc) -> None:
        tab = self.tab_of(doc)
        if tab is not None:
            self.row.remove(tab)

    def select(self, doc) -> None:
        for t in self.tabs():
            (t.add_css_class if t.doc is doc else t.remove_css_class)("selected")

    def move(self, doc, index: int) -> None:
        tab = self.tab_of(doc)
        tabs = [t for t in self.tabs() if t is not tab]
        if tab is None:
            return
        index = max(0, min(index, len(tabs)))
        before = ui.transition.glide_record(self.tabs(), self.row)
        self.row.reorder_child_after(tab, tabs[index - 1] if index > 0 else None)
        ui.transition.glide_play({w: v for w, v in before.items() if w is not tab}, self.row)

    # -- drag to reorder ---------------------------------------------------------------------
    def _tab_at(self, x: float):
        for t in self.tabs():
            ok, p = t.compute_point(self.row, Graphene.Point().init(0, 0))
            if ok and p.x <= x < p.x + t.get_width():
                return t
        return None

    def _drag_begin(self, g, x, y):
        tab = self._tab_at(x)
        picked = self.row.pick(x, y, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not tab:
            if isinstance(picked, Gtk.Button):          # the close button keeps its click
                g.set_state(Gtk.EventSequenceState.DENIED)
                return
            picked = picked.get_parent()
        self._drag = (tab, x) if tab is not None else None

    def _drag_update(self, g, dx, _dy):
        if not self._drag:
            return
        tab, x0 = self._drag
        if abs(dx) < 6 and not tab.has_css_class("dragging"):
            return
        tab.add_css_class("dragging")
        tabs = self.tabs()
        if len(tabs) < 2:
            return
        w = max(1, self.row.get_width() / len(tabs))
        target = int(max(0, min(len(tabs) - 1, (x0 + dx) // w)))
        cur = tabs.index(tab)
        if target != cur:
            self.move(tab.doc, target)
            self.on_reorder([t.doc for t in self.tabs()])

    def _drag_end(self, *_a):
        if self._drag:
            self._drag[0].remove_css_class("dragging")
        self._drag = None
