"""Files tab strip (macOS Finder tabs): shown under the toolbar once a
window has two or more tabs. Equal-width tabs with the folder's name, the
close x on the left on hover, "+" at the end (a new tab on the same
folder). Click selects, middle-click closes, dragging a tab sideways
reorders it (live). Files dragged onto a tab go into that tab's folder;
holding them over a tab brings it to the front (spring-loaded).

    strip = TabStrip(win)       # win: select_tab(t), close_tab(t), new_tab(),
                                #      move_tab(t, i), drop(files, Gio.File, copy)
    strip.add(tab, index) / strip.remove(tab) / strip.set_active(tab) / strip.retitle(tab)
The tab objects only need `.uri` and `.title()`."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

SPRING_MS = 700            # a drag held over a tab brings it to the front

ui.register("""
.fs-tabs { background: %(window_bg)s; min-height: 26px;
  box-shadow: inset 0 -1px %(separator)s; }
/* like the Terminal's: the tab in front is the folder's own background, the
   others sit in the bar (no grey of their own, Vini) */
.fs-tab { background: none; box-shadow: inset -1px 0 %(separator)s;
  transition: background-color %(t_fast)s; }
.fs-tab:hover { background: %(tool_hover)s; }
.fs-tab.active, .fs-tab.active:hover { background: %(content_bg)s; }
.fs-tab.drop-target { background: alpha(%(accent)s, 0.25); }
.fs-tab-label { font-size: %(text_small)s; color: %(label_secondary)s; margin: 0 24px; }
.fs-tab.active .fs-tab-label { color: %(label)s; font-weight: 500; }
.fs-tab-icon { color: %(label_secondary)s; -gtk-icon-size: 12px; }
.fs-tab.active .fs-tab-icon { color: %(accent_ink)s; }
button.fs-tab-close, button.fs-tab-add { min-width: 16px; min-height: 16px; padding: 0; margin: 0 5px;
  border-radius: 4px; background: none; box-shadow: none; border: none;
  color: %(label_secondary)s; -gtk-icon-size: 10px; }
button.fs-tab-close { opacity: 0; transition: opacity %(t_fast)s, background-color %(t_fast)s; }
.fs-tab:hover button.fs-tab-close { opacity: 1; }
button.fs-tab-close:hover, button.fs-tab-add:hover { background: %(tool_hover)s; color: %(label)s; }
button.fs-tab-add { margin: 0 6px; -gtk-icon-size: 12px; }
""", key="files-tabs")


class _TabButton(Gtk.Box):
    """A tab: close x on the left (on hover), centred icon + title."""

    def __init__(self, strip, tab):
        super().__init__(css_classes=["fs-tab"], hexpand=True)
        self.strip, self.tab = strip, tab
        over = Gtk.Overlay(hexpand=True)
        mid = Gtk.Box(spacing=5, halign=Gtk.Align.CENTER, css_classes=["fs-tab-label"])
        self.icon = Gtk.Image(icon_name="folder-symbolic", css_classes=["fs-tab-icon"])
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, width_chars=1)
        mid.append(self.icon)
        mid.append(self.label)
        over.set_child(mid)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["fs-tab-close"],
                           tooltip_text="Close Tab", halign=Gtk.Align.START, valign=Gtk.Align.CENTER,
                           focusable=False)
        close.connect("clicked", lambda *_: strip.win.close_tab(tab))
        over.add_overlay(close)
        self.append(over)
        press = Gtk.GestureClick(button=0)
        press.connect("pressed", self._pressed)
        self.add_controller(press)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._dragged)
        self.add_controller(drag)
        self._start_x = None
        self._drop_target()
        self.retitle()

    def retitle(self) -> None:
        title = self.tab.title()
        self.label.set_label(title)
        self.set_tooltip_text(title)

    def _pressed(self, gesture, _n, _x, _y) -> None:
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_MIDDLE:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.strip.win.close_tab(self.tab)
        elif button == Gdk.BUTTON_PRIMARY:
            self.strip.win.select_tab(self.tab)

    def _drag_begin(self, _g, x, y) -> None:
        pt = Graphene.Point()
        pt.x, pt.y = x, y
        ok, p = self.compute_point(self.strip.row, pt)
        self._start_x = p.x if ok else None

    def _dragged(self, _g, dx, _dy) -> None:
        """Live reorder: the tab moves to the slot under the pointer."""
        row = self.strip.row
        n = len(self.strip.buttons)
        width = row.get_width() / max(1, n)
        if self._start_x is None or abs(dx) < 4 or width <= 0:
            return
        self.strip.win.move_tab(self.tab, min(n - 1, int(max(0.0, self._start_x + dx) // width)))

    # files dropped on a tab go to its folder; hovering brings it to the front
    def _drop_target(self) -> None:
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self._spring = 0

        def enter(*_a):
            self.add_css_class("drop-target")
            self._cancel_spring()
            self._spring = GLib.timeout_add(SPRING_MS, self._spring_now)
            return Gdk.DragAction.MOVE

        def drop(t, value, *_a):
            leave()
            copy = bool(t.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)
            return self.strip.win.drop(list(value.get_files()), Gio.File.new_for_uri(self.tab.uri), copy)

        def leave(*_a):
            self.remove_css_class("drop-target")
            self._cancel_spring()
        tgt.connect("enter", enter)
        tgt.connect("leave", leave)
        tgt.connect("drop", drop)
        self.add_controller(tgt)

    def _spring_now(self) -> bool:
        self._spring = 0
        self.strip.win.select_tab(self.tab)
        return False

    def _cancel_spring(self) -> None:
        if self._spring:
            GLib.source_remove(self._spring)
            self._spring = 0


class TabStrip(Gtk.Revealer):
    """The strip; hidden (collapsed) while there is a single tab."""

    def __init__(self, win):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        self.win = win
        self.buttons = []              # in tab order
        bar = Gtk.Box(css_classes=["fs-tabs"])
        self.row = Gtk.Box(homogeneous=True, hexpand=True)
        bar.append(self.row)
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["fs-tab-add"], tooltip_text="New Tab",
                         valign=Gtk.Align.CENTER, focusable=False)
        add.connect("clicked", lambda *_: win.new_tab())
        bar.append(add)
        self.set_child(bar)
        ui.window.follow_tab_bar(self, self._show)

    def add(self, tab, index: int) -> None:
        tab.button = _TabButton(self, tab)
        self.buttons.insert(index, tab.button)
        self._relayout()

    def remove(self, tab) -> None:
        if tab.button in self.buttons:
            self.buttons.remove(tab.button)
            self.row.remove(tab.button)
            tab.button._cancel_spring()
        self._relayout()

    def move(self, tab, index: int) -> None:
        self.buttons.remove(tab.button)
        self.buttons.insert(index, tab.button)
        self._relayout()

    def _relayout(self) -> None:
        prev = None
        for b in self.buttons:
            if b.get_parent() is None:
                self.row.append(b)
            self.row.reorder_child_after(b, prev)
            prev = b
        self._show()

    def _show(self) -> None:
        self.set_reveal_child(ui.window.tab_bar_shown(len(self.buttons)))

    def set_active(self, tab) -> None:
        for b in self.buttons:
            (b.add_css_class if b.tab is tab else b.remove_css_class)("active")

    def retitle(self, tab) -> None:
        if getattr(tab, "button", None) is not None:
            tab.button.retitle()
