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
from gi.repository import Gdk, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402

COLS = 4
SPACING = 8
UNIT_H = 56                    # one grid row: every module is a whole number of these, always
WIDTH_MIN, WIDTH_MAX, WIDTH_SHARE = 344, 400, 0.18   # Control Center's width from the display's
# id -> (title in Add Controls, (columns, rows))
CATALOG = {
    "connectivity": ("Wi-Fi & Bluetooth", (2, 2)),
    "dnd": ("Do Not Disturb", (2, 1)),
    "darkmode": ("Dark Mode", (1, 1)),
    "screenshot": ("Screenshot", (1, 1)),
    "display": ("Display", (4, 2)),
    "sound": ("Sound", (4, 2)),
    "nowplaying": ("Now Playing", (4, 1)),
    "mixer": ("Volume Mixer", (4, 2)),            # each app's volume; grows a row per app (set_rows)
    "fpslimit": ("FPS Limit", (4, 2)),            # games' frame rate + frame-time graph (fpsmodule.py; Add Controls)
    "keyboard": ("Keyboard", (4, 1)),             # input sources, a click switches (kbdmodule.py; Add Controls)
    # performance (statsui.py): not in the default layout, offered by Add Controls
}
from ..backend import stats as _stats  # noqa: E402
_GPU_TITLES = {k: f"GPU ({m})" for k, m in _stats.GPU_MAKERS.items()}
for _k in _stats.KINDS:                    # one GPU, or one module per card
    CATALOG["stat_" + _k] = ({"cpu": "CPU", "gpu": "GPU", "ram": "Memory", "net": "Network",
                              "fps": "FPS", "vram": "Video Memory", "temp_cpu": "CPU Temperature",
                              "temp_gpu": "GPU Temperature"}.get(_k) or _GPU_TITLES.get(_k)
                             or (f"GPU Temperature ({_stats.TEMP_MAKERS_BY_KIND[_k]})"
                                 if _k in _stats.TEMP_MAKERS_BY_KIND else None)
                             or f"Video Memory ({_stats.VRAM_MAKERS_BY_KIND.get(_k, _k)})", (2, 2))
DEFAULT_ORDER = ["connectivity", "dnd", "darkmode", "screenshot", "display", "sound", "nowplaying"]
DEFAULTS = {"modules": None}            # None: DEFAULT_ORDER (new modules join it in later versions)


def width_for(screen_width: int) -> int:
    """Control Center's width on a display this wide (logical px): the same
    share of the screen, never narrower than the default layout needs."""
    if not screen_width:
        return WIDTH_MIN
    return max(WIDTH_MIN, min(WIDTH_MAX, round(screen_width * WIDTH_SHARE)))


def span_height(rows: int) -> int:
    return rows * UNIT_H + (rows - 1) * SPACING


def rows_for(px: float, most: int = 6) -> int:
    """The fewest whole grid rows (1..most) that hold `px` of content."""
    rows = 1
    while rows < most and span_height(rows) < px:
        rows += 1
    return rows


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


def pack(order, cols: int = COLS, rows: dict = None) -> dict:
    """{id: (col, row, w, h)}: each module in order at the first free place
    where it fits (left to right, top to bottom), like the macOS grid.
    rows: {id: h} for modules whose height follows their content (the mixer)."""
    taken = set()
    out = {}
    for m in order:
        w, h = CATALOG[m][1]
        h = (rows or {}).get(m, h)
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
        # the module fills its cells: the same size for the same span, always
        child.set_vexpand(True)
        child.set_valign(Gtk.Align.FILL)
        self.set_child(child)
        self.cover = Gtk.Box(hexpand=True, vexpand=True, visible=False)    # the module doesn't react
        self.add_overlay(self.cover)
        self.badge = ui.edit.badge(lambda: grid.remove_module(mid))      # inside: the scroller would clip it outside
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


class ModuleGrid(Gtk.Widget):
    """The modules on the 4-column grid; edit mode reorders and removes.
    Each module gets exactly its cells (its span x the column width and
    UNIT_H): the same size in any layout, whatever is around it."""

    def __init__(self, widgets: dict, order: list, on_change=None, width: int = WIDTH_MIN):
        super().__init__(css_classes=["cc-grid"])
        self.width = width
        self.col_w = (width - (COLS - 1) * SPACING) / COLS
        self.widgets = widgets              # id -> module widget (built by ControlCenter)
        self.order = [m for m in order if m in widgets]
        self.on_change = on_change
        self.on_height = None                # each frame of a size change (Control Center's height)
        self._height = None                  # the height drawn while it changes
        self.editing = False
        self.slots = {}
        self._places = {}
        self.rows = {}                      # id -> rows, for modules sized by their content
        self._grow = {}                     # id -> drawn height while it grows or shrinks
        self._dragging = None
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", lambda _t, x, y: self._drag_over(x, y))
        target.connect("drop", lambda *_a: self._drop())
        self.add_controller(target)
        self.connect("destroy", lambda *_a: [s.unparent() for s in self.slots.values() if s.get_parent() is self])
        self._layout()

    # -- layout: every module in its cells ----------------------------------------------------
    def rect(self, mid: str) -> tuple:
        col, row, w, h = self._places[mid]
        return (round(col * (self.col_w + SPACING)), row * (UNIT_H + SPACING),
                round(w * self.col_w + (w - 1) * SPACING), round(self._grow.get(mid, span_height(h))))

    def set_rows(self, mid: str, rows: int) -> None:
        """A module sized by its content (the mixer: a row per app playing)
        takes `rows` grid rows: it grows or shrinks smoothly, the modules
        below glide along; the grid never gets wider."""
        old = self._places.get(mid)
        if self.rows.get(mid) == rows and (old is None or old[3] == rows):
            return
        self.rows[mid] = rows
        if old is None or old[3] == rows:
            return
        start, end = span_height(old[3]), span_height(rows)
        self._layout(glide=True)

        def step(v):
            self._grow[mid] = v
            if v == end:
                self._grow.pop(mid, None)
            self.queue_allocate()
        self._grow[mid] = start
        ui.transition.tween(self, "rows-" + mid, start, end, 220, step, "control center module size")
        if self.on_change:
            self.on_change(self.order)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.width, self.width, -1, -1
        size = self.target_height() if getattr(self, "_height", None) is None else round(self._height)
        return size, size, -1, -1

    def target_height(self) -> int:
        rows = max([r + h for _c, r, _w, h in self._places.values()] or [0])
        return span_height(rows) if rows else 0

    def _resize_to(self, old: int) -> None:
        """Control Center grows or shrinks to the modules smoothly (Vini: a
        module taken out made it jump); on_height() follows each frame."""
        new = self.target_height()
        if old == new or not self.get_mapped():
            self._height = None
            return

        def step(v):
            self._height = None if v == new else v
            self.queue_resize()
            if self.on_height:
                self.on_height()
        self._height = float(old)
        ui.transition.tween(self, "height", float(old), float(new), 240, step, "control center size")

    def do_size_allocate(self, width, height, baseline):
        for mid in self.order:
            slot = self.slots.get(mid)
            if slot is None or slot.get_parent() is not self:
                continue
            x, y, w, h = self.rect(mid)
            slot.measure(Gtk.Orientation.HORIZONTAL, -1)      # (GTK wants a measure before allocate)
            t = Gsk.Transform.new().translate(Graphene.Point().init(x, y))
            slot.allocate(w, h, -1, t)

    def do_snapshot(self, snap) -> None:
        ui.transition.snapshot_children(self, snap)      # modules glide when re-ordered

    def _slot(self, mid: str) -> _Slot:
        if mid not in self.slots:
            self.slots[mid] = _Slot(self, mid, self.widgets[mid], len(self.slots) % 2 == 1)
            self.slots[mid].set_editing(self.editing)
        return self.slots[mid]

    def _layout(self, glide: bool = False) -> None:
        before = ui.transition.glide_record(list(self.slots.values()), self) if glide else {}
        old_h = self.measure(Gtk.Orientation.VERTICAL, -1)[1] if self._places else None
        self._places = pack(self.order, rows=self.rows)
        for mid, slot in list(self.slots.items()):
            if mid not in self._places and slot.get_parent() is self:
                slot.unparent()
        for mid in self.order:
            slot = self._slot(mid)
            if slot.get_parent() is not self:
                slot.set_parent(self)
        self.queue_resize()
        if glide and old_h is not None:
            self._resize_to(old_h)
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
        if slot is not None:
            if slot.get_parent() is self:
                slot.unparent()
            # let go of the module: put back (Add Controls) it goes into a new slot --
            # still held by this one, it came back empty (Vini)
            slot.set_child(None)
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
.panel-module.cc-conn { padding-top: 8px; padding-bottom: 8px; }
""", key="controlcenter")
