"""Task Manager's building blocks: its CSS, the history graph (big graph
and sidebar sparklines), the memory composition bar, table columns with
live cells and heat-map tint, toolbar buttons with a label."""
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

HISTORY = 31                      # graph points: the last ~60 s at 2 s a sample

ui.register("""
/* sidebar (translucent, like Files) */
.tm-sidebar { padding: 8px 10px; }
.tm-sidebar list { background: none; }
.tm-sidebar list row { min-height: 28px; padding: 0 6px; border-radius: %(r_menu)s; background: none;
  color: %(label)s; transition: background-color %(t_fast)s; }
.tm-sidebar list row:active { background: %(tool_hover)s; transition: none; }
.tm-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.tm-sidebar list row image { color: %(accent_ink)s; }
.tm-sidebar list row label { font-family: %(font)s; font-size: %(text_body)s; }
.tm-side-head { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; padding: 4px 6px; }

/* toolbar */
.tm-toolbar label.tm-title { font-family: %(font)s; font-size: %(text_title)s; font-weight: 700; color: %(label)s;
  margin-left: 6px; }
.tm-toolbar entry { min-height: 24px; border-radius: %(r_button)s; }
.tm-toolbar button.tool { color: %(label)s; }
.tm-toolbar button.tool image { color: %(tool_icon)s; }
.tm-toolbar button.tool:active { background: %(control_pressed)s; transition: none; }
.tm-toolbar button.tool:disabled, .tm-toolbar button.tool:disabled image { color: %(label_tertiary)s; background: none; }
.tm-toolbar button.tool label { font-family: %(font)s; font-size: %(text_body)s; font-weight: 400; }

/* content */
.tm-content { background: %(content_bg)s; }
.tm-note { padding: 8px 14px; font-family: %(font)s; font-size: %(text_small)s; color: %(label_secondary)s;
  background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }

/* tables: zebra rows, small grey headers, numbers right-aligned */
columnview.tm-list { background: %(content_bg)s; font-family: %(font)s; font-size: %(text_body)s; }
columnview.tm-list > header { background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }
columnview.tm-list > header > button { padding: 3px 8px; font-size: %(text_small)s; background: none; border: none;
  border-radius: 0; box-shadow: inset -1px 0 %(separator)s; transition: background-color %(t_fast)s; }
columnview.tm-list > header > button > box, columnview.tm-list > header > button label {
  color: %(label_secondary)s; font-weight: 500; opacity: 1; }
columnview.tm-list > header > button:hover { background: %(tool_hover)s; }
columnview.tm-list > header > button:active { background: %(control_pressed)s; transition: none; }
columnview.tm-list > header > button sort-indicator { color: %(label_secondary)s; -gtk-icon-size: 10px; }
columnview.tm-list > listview { background: %(content_bg)s; }
columnview.tm-list > listview > row { min-height: 24px; padding: 0; border-radius: 0; background: none;
  color: %(label)s; transition: background-color %(t_fast)s, color %(t_fast)s; }
columnview.tm-list > listview > row:nth-child(even) { background: %(row_alt)s; }
columnview.tm-list > listview > row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop columnview.tm-list > listview > row:selected { background: %(sidebar_selected)s; color: %(label)s; }
columnview.tm-list > listview > row > cell { padding: 0; }
columnview.tm-list label.tm-cell { padding: 0 8px; min-height: 24px; }
columnview.tm-list .tm-name { padding: 0 8px; }
columnview.tm-list label.tm-num { font-feature-settings: "tnum"; }
columnview.tm-list label.tm-dim { color: %(label_secondary)s; }
columnview.tm-list > listview > row:selected label.tm-dim { color: inherit; }
columnview.tm-list > listview > header { padding: 10px 8px 4px 8px; background: %(content_bg)s; }
columnview.tm-list > listview > header label { font-family: %(font)s; font-size: %(text_small)s; font-weight: 700;
  color: %(label_secondary)s; }
columnview.tm-list switch { margin: 2px 0; }
/* heat map (Windows): usage tints the cell, stronger with more use; accent only */
columnview.tm-list label.heat-0 { background: alpha(%(accent)s, 0.05); }
columnview.tm-list label.heat-1 { background: alpha(%(accent)s, 0.10); }
columnview.tm-list label.heat-2 { background: alpha(%(accent)s, 0.17); }
columnview.tm-list label.heat-3 { background: alpha(%(accent)s, 0.26); }
columnview.tm-list label.heat-4 { background: alpha(%(accent)s, 0.36); }
columnview.tm-list label.heat-5 { background: alpha(%(accent)s, 0.48); }
columnview.tm-list > listview > row:selected label.heat-0, columnview.tm-list > listview > row:selected label.heat-1,
columnview.tm-list > listview > row:selected label.heat-2, columnview.tm-list > listview > row:selected label.heat-3,
columnview.tm-list > listview > row:selected label.heat-4, columnview.tm-list > listview > row:selected label.heat-5 {
  background: none; }
columnview.tm-list label.tm-running { color: %(sys_green)s; }
columnview.tm-list label.tm-failed { color: %(destructive)s; }

/* Performance */
.tm-perf-list { background: none; padding: 8px; }
.tm-perf-list row { padding: 6px 8px; border-radius: %(r_menu)s; background: none; color: %(label)s;
  transition: background-color %(t_fast)s; }
.tm-perf-list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.tm-perf-list row:active { background: %(tool_hover)s; transition: none; }
.tm-perf-list label.tm-res-title { font-family: %(font)s; font-size: %(text_body)s; font-weight: 600; }
.tm-perf-list label.tm-res-sub { font-family: %(font)s; font-size: %(text_small)s; color: %(label_secondary)s;
  font-feature-settings: "tnum"; }
.tm-perf-pane { background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; padding: 16px 20px; }
.tm-perf-pane label.tm-perf-title { font-family: %(font_display)s; font-size: 24px; font-weight: 600; color: %(label)s; }
.tm-perf-pane label.tm-perf-model { font-family: %(font)s; font-size: %(text_body)s; color: %(label_secondary)s; }
.tm-perf-pane label.tm-caption { font-family: %(font)s; font-size: %(text_small)s; color: %(label_secondary)s; }
.tm-perf-pane label.tm-stat-key { font-family: %(font)s; font-size: %(text_small)s; color: %(label_secondary)s; }
.tm-perf-pane label.tm-stat-big { font-family: %(font_display)s; font-size: 18px; color: %(label)s;
  font-feature-settings: "tnum"; }
.tm-perf-pane label.tm-stat-val { font-family: %(font)s; font-size: %(text_body)s; color: %(label)s;
  font-feature-settings: "tnum"; }
.tm-legend { min-width: 10px; min-height: 10px; border-radius: 2px; }

/* Properties panel */
.tm-info { padding: 2px 6px 8px 6px; }
.tm-info label.tm-info-name { font-weight: 700; font-size: %(text_title)s; }
.tm-info label.tm-key { color: %(label_secondary)s; font-size: %(text_small)s; }
.tm-info label.tm-val { font-size: %(text_small)s; font-feature-settings: "tnum"; }
.tm-info label.tm-cmd { font-family: %(font_mono)s; font-size: %(text_small)s; color: %(label)s; }
""", key="taskmanager")


# -- toolbar ------------------------------------------------------------------------------------
def tool_button(icon: str, label: str, callback, tooltip: str = None) -> Gtk.Button:
    """A glass-toolbar button with a glyph and a word (Windows' command bar)."""
    b = Gtk.Button(css_classes=["tool"], can_focus=False, tooltip_text=tooltip or label, valign=Gtk.Align.CENTER)
    box = Gtk.Box(spacing=5)
    box.append(Gtk.Image(icon_name=icon))
    if label:
        box.append(Gtk.Label(label=label))
    b.set_child(box)
    b.connect("clicked", lambda *_: callback())
    return b


# -- graphs -------------------------------------------------------------------------------------
class Series:
    """A value history shared by a sparkline and the big graph."""

    def __init__(self):
        self.values = deque(maxlen=HISTORY)

    def push(self, v: float) -> None:
        self.values.append(float(v))


class Graph(Gtk.Widget):
    """A live history graph drawn in do_snapshot with tokens.
    lines: [(colour token or callable(last value) -> token, Series)];
    maximum: fixed top (100 for %) or None to follow the data; stacked:
    each line on top of the previous one; grid: quarter lines (big graph);
    fill: the area under a line, tinted."""

    def __init__(self, lines, maximum=None, stacked=False, grid=True, width=220, height=78, radius=4):
        super().__init__(hexpand=True, vexpand=False)
        self.lines, self.maximum, self.stacked, self.grid, self.radius = lines, maximum, stacked, grid, radius
        self.set_size_request(width, height)
        ui.on_change(self.queue_draw)

    def set_lines(self, lines, maximum=None) -> None:
        self.lines, self.maximum = lines, maximum
        self.queue_draw()

    def top(self) -> float:
        if self.maximum:
            return self.maximum
        vals = [list(s.values) for _t, s in self.lines]
        if self.stacked:
            peak = max((sum(v) for v in zip(*vals)), default=0)
        else:
            peak = max((max(v, default=0) for v in vals), default=0)
        return max(1.0, peak * 1.15)

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        rect = Graphene.Rect().init(0, 0, w, h)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, self.radius)
        snap.push_rounded_clip(rr)
        snap.append_color(ui.rgba("content_bg"), rect)
        if self.grid:
            grid = ui.rgba("separator")
            for i in (1, 2, 3):
                snap.append_color(grid, Graphene.Rect().init(0, round(h * i / 4), w, 1))
            for i in range(1, 6):
                snap.append_color(grid, Graphene.Rect().init(round(w * i / 6), 0, 1, h))
        top = self.top()
        step = w / (HISTORY - 1)
        base = [0.0] * HISTORY
        for token, series in self.lines:
            vals = list(series.values)
            if len(vals) < 2:
                continue
            n = len(vals)
            off = HISTORY - n
            lows = [base[off + i] if self.stacked else 0.0 for i in range(n)]
            ys = [lo + v for lo, v in zip(lows, vals)]
            col = ui.rgba(token(vals[-1]) if callable(token) else token)

            def y_of(v):
                return h - min(1.0, v / top) * (h - 2) - 1
            pts = [(w - (n - 1 - i) * step, y_of(y)) for i, y in enumerate(ys)]
            area = Gsk.PathBuilder.new()
            area.move_to(*pts[0])
            for x, y in pts[1:]:
                area.line_to(x, y)
            for i in range(n - 1, -1, -1):
                area.line_to(pts[i][0], y_of(lows[i]))
            area.close()
            fill = col.copy()
            fill.alpha *= 0.22
            snap.append_fill(area.to_path(), Gsk.FillRule.WINDING, fill)
            line = Gsk.PathBuilder.new()
            line.move_to(*pts[0])
            for x, y in pts[1:]:
                line.line_to(x, y)
            snap.append_stroke(line.to_path(), Gsk.Stroke.new(1.5 if self.grid else 1.2), col)
            if self.stacked:
                for i, y in enumerate(ys):
                    base[off + i] = y
        snap.pop()
        edge = ui.rgba("separator")
        snap.append_border(rr, [1] * 4, [edge] * 4)


class CompositionBar(Gtk.Widget):
    """Windows' "Memory composition": in use | modified | standby | free,
    as proportional blocks (tokens)."""
    PARTS = (("in_use", "sys_purple", 1.0), ("modified", "sys_orange", 0.8), ("standby", "sys_purple", 0.35),
             ("free", "separator", 1.0))

    def __init__(self):
        super().__init__(hexpand=True)
        self.set_size_request(200, 34)
        self.values = {}
        ui.on_change(self.queue_draw)

    def set_values(self, values: dict) -> None:
        if values != self.values:
            self.values = dict(values)
            self.queue_draw()

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        total = sum(max(0, self.values.get(k, 0)) for k, _t, _a in self.PARTS)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(Graphene.Rect().init(0, 0, w, h), 4)
        snap.push_rounded_clip(rr)
        snap.append_color(ui.rgba("content_bg"), Graphene.Rect().init(0, 0, w, h))
        x = 0.0
        if total > 0:
            for key, token, alpha in self.PARTS:
                part = w * max(0, self.values.get(key, 0)) / total
                c = ui.rgba(token).copy()
                c.alpha *= alpha
                snap.append_color(c, Graphene.Rect().init(x, 0, part, h))
                x += part
                if key != "free":
                    snap.append_color(ui.rgba("content_bg"), Graphene.Rect().init(x - 1, 0, 1, h))
        snap.pop()
        snap.append_border(rr, [1] * 4, [ui.rgba("sys_purple")] * 4)


# -- table columns ------------------------------------------------------------------------------
HEAT_CLASSES = tuple(f"heat-{i}" for i in range(6))


def _unwrap(obj):
    return obj.get_item() if isinstance(obj, Gtk.TreeListRow) else obj


def text_column(view, title, prop, fmt=None, width=90, numeric=True, heat=None, sorter=None, expand=False,
                dim=False) -> Gtk.ColumnViewColumn:
    """A column whose label follows the row's property `prop` (formatted by
    fmt); heat(row, value) -> 0..5 tints the cell, None leaves it plain."""
    f = Gtk.SignalListItemFactory()

    def setup(_f, item):
        css = ["tm-cell"] + (["tm-num"] if numeric else []) + (["tm-dim"] if dim else [])
        item.set_child(Gtk.Label(xalign=1 if numeric else 0, ellipsize=Pango.EllipsizeMode.END, css_classes=css,
                                 single_line_mode=True))

    def refresh(row, lbl):
        sv = getattr(row, "sv", None)
        v = sv[prop] if sv is not None and prop in sv else row.get_property(prop)
        lbl.set_label(fmt(v) if fmt else str(v))
        level = heat(row, v) if heat else None
        old = getattr(lbl, "tm_level", None)
        if level != old:                              # restyle only when the tint changes
            if old is not None:
                lbl.remove_css_class(HEAT_CLASSES[old])
            if level is not None:
                lbl.add_css_class(HEAT_CLASSES[level])
            lbl.tm_level = level

    def bind(_f, item):
        row, lbl = _unwrap(item.get_item()), item.get_child()
        lbl.tm_row = row
        refresh(row, lbl)
        lbl.tm_handler = (row, row.connect(f"notify::{prop.replace('_', '-')}", lambda r, _p: refresh(r, lbl)))

    def unbind(_f, item):
        lbl = item.get_child()
        h = getattr(lbl, "tm_handler", None)
        if h:
            h[0].disconnect(h[1])
            lbl.tm_handler = None
        lbl.tm_row = None
    f.connect("setup", setup)
    f.connect("bind", bind)
    f.connect("unbind", unbind)
    c = Gtk.ColumnViewColumn(title=title, factory=f, resizable=True, sorter=sorter, expand=expand)
    c.set_fixed_width(width)
    view.append_column(c)
    return c


def name_column(view, title="Name", width=260, tree=False, sorter=None) -> Gtk.ColumnViewColumn:
    """Icon + name (row.icon: a Gio.Icon or None; row.name), inside a tree
    expander when the model is a Gtk.TreeListModel."""
    f = Gtk.SignalListItemFactory()

    def setup(_f, item):
        box = Gtk.Box(spacing=6, css_classes=["tm-name"])
        box.img = Gtk.Image(pixel_size=16)
        box.lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        box.append(box.img)
        box.append(box.lbl)
        if tree:
            item.set_child(Gtk.TreeExpander(child=box, indent_for_icon=True))
        else:
            item.set_child(box)

    def bind(_f, item):
        obj = item.get_item()
        child = item.get_child()
        if tree:
            child.set_list_row(obj)
            box = child.get_child()
        else:
            box = child
        row = _unwrap(obj)
        icon = getattr(row, "icon", None)
        if icon is not None:
            box.img.set_from_gicon(icon)
        else:
            box.img.clear()                  # macOS/Windows: no icon for processes that aren't apps
        box.lbl.set_label(row.name)
        box.tm_row = row
        child.tm_row = row
        child.tm_handler = (row, row.connect("notify::name", lambda r, _p: box.lbl.set_label(r.name)))

    def unbind(_f, item):
        child = item.get_child()
        if tree:
            child.set_list_row(None)
        h = getattr(child, "tm_handler", None)
        if h:
            h[0].disconnect(h[1])
            child.tm_handler = None
        child.tm_row = None
    f.connect("setup", setup)
    f.connect("bind", bind)
    f.connect("unbind", unbind)
    c = Gtk.ColumnViewColumn(title=title, factory=f, resizable=True, sorter=sorter, expand=True)
    c.set_fixed_width(width)
    view.append_column(c)
    return c


def row_at(view, x, y):
    """The row object under (x, y) of a ColumnView (cells carry .tm_row)."""
    w = view.pick(x, y, Gtk.PickFlags.DEFAULT)
    while w is not None and w is not view:
        row = getattr(w, "tm_row", None)
        if row is not None:
            return row
        w = w.get_parent()
    return None


def table(css=("tm-list",)) -> Gtk.ColumnView:
    view = Gtk.ColumnView(show_column_separators=False, show_row_separators=False, reorderable=False,
                          css_classes=list(css))
    from .. import ui
    ui.columns.fill_last(view)          # columns keep their widths; the last one fills
    return view
