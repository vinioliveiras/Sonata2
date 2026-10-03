"""Control Center's layout: which modules it shows, in which order, on a
grid of 4 columns where each module has its size (macOS' newer Control
Center). Edited like Launchpad (Vini): hold a module (or Edit Controls…)
and they jiggle; drag one to move it -- the others glide out of its way --,
its x takes it out, "Add Controls" brings back what was taken out.

    order = load()                     # module ids, from controlcenter.json
    pack(order) -> {id: (col, row, w, h)}
    ModuleGrid(widgets, order, on_change)

The modules themselves are built by topbar.ControlCenter."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GObject, Graphene, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402

COLS = 4
SPACING = 8
# id -> (title in Add Controls, (columns, rows))
CATALOG = {
    "connectivity": ("Wi-Fi & Bluetooth", (2, 2)),
    "dnd": ("Do Not Disturb", (2, 1)),
    "darkmode": ("Dark Mode", (1, 1)),
    "screenshot": ("Screenshot", (1, 1)),
    "display": ("Display", (4, 1)),
    "sound": ("Sound", (4, 1)),
    "nowplaying": ("Now Playing", (4, 1)),
    # performance (statsui.py): not in the default layout, offered by Add Controls
    "stat_cpu": ("CPU", (2, 1)),
    "stat_gpu": ("GPU", (2, 1)),
    "stat_ram": ("Memory", (2, 1)),
    "stat_net": ("Network", (2, 1)),
    "stat_fps": ("FPS", (2, 1)),
}
DEFAULT_ORDER = ["connectivity", "dnd", "darkmode", "screenshot", "display", "sound", "nowplaying"]
DEFAULTS = {"modules": None}            # None: DEFAULT_ORDER (new modules join it in later versions)


def load() -> list:
    """The modules shown, in order: known ids, each once."""
    saved = config.load("controlcenter", DEFAULTS)["modules"]
    order = saved if isinstance(saved, list) else DEFAULT_ORDER
    out = []
    for m in order:
        if isinstance(m, str) and m in CATALOG and m not in out:
            out.append(m)
    return out


def save(order) -> None:
    config.update("controlcenter", modules=list(order))


def hidden(order) -> list:
    """What Add Controls offers: the catalog's modules that aren't shown."""
    return [m for m in CATALOG if m not in order]


def pack(order, cols: int = COLS) -> dict:
    """{id: (col, row, w, h)}: each module in order at the first free place
    where it fits (left to right, top to bottom), like the macOS grid."""
    taken = set()
    out = {}
    for m in order:
        w, h = CATALOG[m][1]
        w = min(w, cols)
        row = 0
        while m not in out:
            for col in range(cols - w + 1):
                cells = {(col + dx, row + dy) for dx in range(w) for dy in range(h)}
                if not cells & taken:
                    taken |= cells
                    out[m] = (col, row, w, h)
                    break
            row += 1
    return out


class _Slot(Gtk.Overlay):
    """One module on the grid: the module, a cover that takes its clicks
    while editing, and its x."""

    def __init__(self, grid, mid: str, child: Gtk.Widget, odd: bool):
        super().__init__(css_classes=["cc-slot", "sonata-jiggle"] + (["odd"] if odd else [])
                         + (["wide"] if CATALOG[mid][1][0] > 2 else []))
        self.mid = mid
        self.set_child(child)
        self.cover = Gtk.Box(hexpand=True, vexpand=True, visible=False)    # the module doesn't react
        self.add_overlay(self.cover)
        self.badge = ui.edit.badge(lambda: grid.remove_module(mid), corner=True)
        self.add_overlay(self.badge)
        ui.edit.hold(self, lambda: grid.set_editing(True))
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda *_a: grid._drag_prepare(self))
        drag.connect("drag-begin", lambda _s, d: grid._drag_begin(self, d))
        drag.connect("drag-end", lambda *_a: grid._drag_end(self))
        self.add_controller(drag)

    def set_editing(self, on: bool) -> None:
        self.cover.set_visible(on)
        self.badge.set_visible(on)


class ModuleGrid(Gtk.Grid):
    """The modules on the 4-column grid; edit mode reorders and removes."""

    def __init__(self, widgets: dict, order: list, on_change=None):
        super().__init__(column_homogeneous=True, row_spacing=SPACING, column_spacing=SPACING,
                         css_classes=["cc-grid"])
        self.widgets = widgets              # id -> module widget (built by ControlCenter)
        self.order = [m for m in order if m in widgets]
        self.on_change = on_change
        self.editing = False
        self.slots = {}
        self._dragging = None
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", lambda _t, x, y: self._drag_over(x, y))
        target.connect("drop", lambda *_a: self._drop())
        self.add_controller(target)
        self._layout()

    def do_snapshot(self, snap) -> None:
        ui.transition.snapshot_children(self, snap)      # modules glide when re-ordered

    def _slot(self, mid: str) -> _Slot:
        if mid not in self.slots:
            self.slots[mid] = _Slot(self, mid, self.widgets[mid], len(self.slots) % 2 == 1)
            self.slots[mid].set_editing(self.editing)
        return self.slots[mid]

    def _layout(self, glide: bool = False) -> None:
        before = ui.transition.glide_record(list(self.slots.values()), self) if glide else {}
        places = pack(self.order)
        for mid, slot in list(self.slots.items()):
            if mid not in places and slot.get_parent() is self:
                self.remove(slot)
        for mid in self.order:
            col, row, w, h = places[mid]
            slot = self._slot(mid)
            if slot.get_parent() is self:              # moved in place: it stays mapped (it glides)
                lc = self.get_layout_manager().get_layout_child(slot)
                lc.set_column(col)
                lc.set_row(row)
                lc.set_column_span(w)
                lc.set_row_span(h)
            else:
                self.attach(slot, col, row, w, h)
        if before:
            ui.transition.glide_play(before, self)

    def _changed(self) -> None:
        save(self.order)
        if self.on_change:
            self.on_change(self.order)

    # -- edit mode -----------------------------------------------------------------------
    def set_editing(self, on: bool) -> None:
        if on == self.editing:
            return
        self.editing = on
        (self.add_css_class if on else self.remove_css_class)("jiggle")
        for slot in self.slots.values():
            slot.set_editing(on)
        if self.on_change:
            self.on_change(self.order)

    def remove_module(self, mid: str) -> None:
        if mid not in self.order:
            return
        slot = self.slots.get(mid)
        if slot is not None:
            # it shrinks away first (Launchpad's delete), then the others glide in
            slot.add_css_class("cc-slot-leaving")
            from gi.repository import GLib
            GLib.timeout_add(ui.tokens.ms(160), lambda: (self._take_out(mid), False)[1])
        else:
            self._take_out(mid)

    def _take_out(self, mid: str) -> None:
        if mid not in self.order:
            return
        self.order.remove(mid)
        slot = self.slots.pop(mid, None)
        if slot is not None and slot.get_parent() is self:
            self.remove(slot)
        self._layout(glide=True)
        self._changed()

    def add_module(self, mid: str) -> None:
        if mid in self.order or mid not in self.widgets:
            return
        self.order.append(mid)
        self._layout(glide=True)
        self.slots[mid].add_css_class("cc-slot-arriving")
        self._changed()

    def move(self, mid: str, index: int) -> None:
        if mid not in self.order:
            return
        index = max(0, min(index, len(self.order) - 1))
        if self.order.index(mid) == index:
            return
        self.order.remove(mid)
        self.order.insert(index, mid)
        self._layout(glide=True)

    # -- drag to move ------------------------------------------------------------------------
    def _drag_prepare(self, slot):
        if not self.editing:
            return None                              # outside edit mode a drag is the module's own
        return Gdk.ContentProvider.new_for_value(slot.mid)

    def _drag_begin(self, slot, drag) -> None:
        self._dragging = slot.mid
        icon = Gtk.DragIcon.get_for_drag(drag)
        pic = Gtk.Picture(paintable=Gtk.WidgetPaintable.new(slot.get_child()), can_shrink=False,
                          css_classes=["cc-drag-icon"])
        icon.set_child(pic)
        slot.add_css_class("cc-slot-dragged")

    def _drag_over(self, x, y):
        mid = self._dragging
        if mid is None:
            return Gdk.DragAction.MOVE
        over = self.slot_at(x, y)
        if over is not None and over != mid:
            self.move(mid, self.order.index(over))
        return Gdk.DragAction.MOVE

    def slot_at(self, x, y):
        """The module under (x, y) in the grid's coordinates, or None."""
        for mid in self.order:
            slot = self.slots.get(mid)
            if slot is None or slot.get_parent() is not self:
                continue
            ok, b = slot.compute_bounds(self)
            if ok and b.contains_point(Graphene.Point().init(x, y)):
                return mid
        return None

    def _drop(self) -> bool:
        self._changed()
        return True

    def _drag_end(self, slot) -> None:
        slot.remove_css_class("cc-slot-dragged")
        self._dragging = None


ui.register("""
.cc-slot-dragged { opacity: 0.25; transition: opacity 150ms ease; }
@keyframes cc-slot-out { to { opacity: 0; transform: scale(0.6); } }
.cc-slot-leaving { animation: cc-slot-out 160ms cubic-bezier(0.4, 0, 1, 1) forwards; }
@keyframes cc-slot-in { from { opacity: 0; transform: scale(0.7); } to { opacity: 1; transform: none; } }
.cc-slot-arriving { animation: cc-slot-in 240ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.cc-drag-icon { opacity: 0.92; }
.cc-edit-bar { margin-top: 2px; }
""", key="controlcenter")
