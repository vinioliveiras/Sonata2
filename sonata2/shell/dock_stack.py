"""Dock stacks (macOS): a folder in the Dock, right of the divider.

Click: a panel with the folder's items (Grid or List view); click an item
to open it, drag it out to copy/move it, "Open in Files" at the bottom.
Right-click: Sort by, Display as (Folder / Stack = newest item's icon),
View content as, Remove from Dock, Open "<folder>".
A folder dropped on the Dock becomes a stack. Default: Downloads."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402

STACK_DEFAULTS = {"display": "stack", "view": "grid", "sort": "added"}
SORTS = (("name", "Name"), ("added", "Date Added"), ("modified", "Date Modified"),
         ("kind", "Kind"))
MAX_ITEMS = 60
GRID_COLS, GRID_ICON = 5, 48

ui.register("""
popover.stack-panel { background: none; box-shadow: none; padding: 0; }
popover.stack-panel > contents {
  padding: 10px 8px 8px 8px; border-radius: %(r_dialog)s;
  font-family: %(font)s; color: %(label)s; background-color: %(menu_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
.stack-title { font-size: %(text_body)s; font-weight: 700; margin: 0 6px 8px 6px; }
.stack-item { padding: 6px 4px; border-radius: %(r_button)s; background: none; border: none;
              box-shadow: none; min-width: 84px; }
.stack-item:hover { background-color: %(control_off)s; }
.stack-item:active { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
.stack-item label { font-size: %(text_small)s; }
.stack-row { min-height: %(control_h)s; padding: 0 8px; border-radius: %(r_menu_row)s;
             background: none; border: none; box-shadow: none; }
.stack-row:hover { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
.stack-row label { font-size: %(text_body)s; }
.stack-footer { margin-top: 6px; }
.stack-empty { font-size: %(text_small)s; color: %(label_secondary)s; margin: 12px; }
""", key="dock-stack")


def _downloads() -> str:
    return (GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            or os.path.expanduser("~/Downloads"))


def default_stacks() -> list:
    d = _downloads()
    return [dict(STACK_DEFAULTS, path=d)] if d and os.path.isdir(d) else []


def _items(path: str, sort: str) -> list:
    """(Gio.FileInfo, Gio.File) of the folder's visible items, sorted."""
    folder = Gio.File.new_for_path(path)
    attrs = ("standard::name,standard::display-name,standard::icon,standard::content-type,"
             "standard::is-hidden,standard::type,time::*,"
             "thumbnail::path")
    out = []
    try:
        for info in folder.enumerate_children(attrs, Gio.FileQueryInfoFlags.NONE, None):
            if info.get_is_hidden() or info.get_name().startswith("."):
                continue
            out.append((info, folder.get_child(info.get_name())))
    except GLib.Error:
        return []

    def t(info, attr):   # seconds + microseconds (files made in the same second)
        if not info.has_attribute(attr):
            return 0
        usec = info.get_attribute_uint32(attr + "-usec") if info.has_attribute(attr + "-usec") else 0
        return info.get_attribute_uint64(attr) + usec / 1e6
    key = {"name": lambda it: it[0].get_display_name().lower(),
           "kind": lambda it: (it[0].get_content_type() or "", it[0].get_display_name().lower()),
           "modified": lambda it: -t(it[0], "time::modified"),
           "added": lambda it: -max(t(it[0], "time::created"), t(it[0], "time::changed"))}[sort]
    out.sort(key=key)
    return out[:MAX_ITEMS]


def _icon(info) -> Gio.Icon:
    thumb = info.get_attribute_byte_string("thumbnail::path") if info.has_attribute("thumbnail::path") else None
    if thumb and os.path.exists(thumb):
        return Gio.FileIcon.new(Gio.File.new_for_path(thumb))
    return info.get_icon() or Gio.ThemedIcon.new("text-x-generic")


def _open(gfile: Gio.File) -> None:
    """Folders open in Sonata's Files, files in their default app."""
    from ..files import open_folder
    if gfile.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY:
        open_folder(gfile.get_uri())
        return
    from ..files import packages
    if gfile.get_path() and packages.open_path(gfile.get_path()):     # install / run / extract
        return
    Gio.AppInfo.launch_default_for_uri(gfile.get_uri(), None)


def _drag_source(widget, gfile) -> None:
    src = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
    from ..files.ops import file_content                # a plain uri-list: every receiver takes it
    src.connect("prepare", lambda *_: file_content([gfile]))
    widget.add_controller(src)


class StackRow:
    """The stack tiles of a Dock (between the divider and the Trash)."""

    def __init__(self, dock):
        self.dock = dock
        self._tiles = []
        self._monitors = []

    def tiles(self) -> list:
        return list(self._tiles)

    def load(self) -> None:
        for spec in self.dock.cfg["stacks"] or []:
            self._add_tile(dict(STACK_DEFAULTS, **spec))

    def _save(self) -> None:
        self.dock.cfg["stacks"] = [t.spec for t in self._tiles]
        config.save("dock", self.dock.cfg)

    def add(self, path: str) -> None:
        if not os.path.isdir(path) or any(t.spec["path"] == path for t in self._tiles):
            return
        self._add_tile(dict(STACK_DEFAULTS, path=path))
        self._save()

    def remove(self, tile) -> None:
        self._tiles.remove(tile)
        tile.label.unparent()
        self.dock.remove(tile)
        self._save()

    def _add_tile(self, spec) -> None:
        from .dock import DockTile
        name = os.path.basename(spec["path"].rstrip("/")) or spec["path"]
        tile = DockTile(self.dock, name, Gio.ThemedIcon.new("folder"), self.open_panel,
                        on_menu=lambda t: stack_menu(self, t))
        tile.spec = spec
        tile.key = None
        prev = (self._tiles[-1] if self._tiles else self.dock.sep)
        self.dock.insert_child_after(tile, prev)
        self._tiles.append(tile)
        self.refresh_icon(tile)
        mon = Gio.File.new_for_path(spec["path"]).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        mon.connect("changed", lambda *_: self.refresh_icon(tile))
        self._monitors.append(mon)

    def refresh_icon(self, tile) -> None:
        spec = tile.spec
        if spec["display"] == "stack":
            items = _items(spec["path"], spec["sort"])
            if items:
                tile.set_gicon(_icon(items[0][0]))
                return
        downloads = _downloads()
        tile.set_gicon(Gio.ThemedIcon.new_from_names(
            ["folder-download", "folder"] if spec["path"] == downloads else ["folder"]))

    def set_spec(self, tile, key, value) -> None:
        tile.spec[key] = value
        self._save()
        self.refresh_icon(tile)

    # -- the panel ---------------------------------------------------------------
    def open_panel(self, tile) -> Gtk.Popover:
        tile.label.popdown()
        spec = tile.spec
        pop = Gtk.Popover(css_classes=["stack-panel"], has_arrow=False, position=self.dock.away)
        P = Gtk.PositionType
        pop.set_offset(*{P.TOP: (0, -8), P.LEFT: (-8, 0), P.RIGHT: (8, 0)}[self.dock.away])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(Gtk.Label(label=tile.name, xalign=0, css_classes=["stack-title"]))
        items = _items(spec["path"], spec["sort"])
        if not items:
            col.append(Gtk.Label(label="No items", css_classes=["stack-empty"]))
        elif spec["view"] == "list":
            col.append(self._list(items, pop))
        else:
            col.append(self._grid(items, pop))
        footer = Gtk.Box(css_classes=["stack-footer"], halign=Gtk.Align.END)
        footer.append(ui.controls.push_button("Open in Files", lambda: (
            _open(Gio.File.new_for_path(spec["path"])), pop.popdown())))
        col.append(footer)
        pop.set_child(col)
        pop.set_parent(tile)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        ui.menu.OPEN.add(pop)          # keeps an auto-hiding Dock visible
        pop.connect("closed", lambda p: (ui.menu.OPEN.discard(p),
                                         [cb() for cb in list(ui.menu.on_closed)]))
        pop.popup()
        return pop

    def _grid(self, items, pop) -> Gtk.Widget:
        flow = Gtk.FlowBox(max_children_per_line=GRID_COLS, min_children_per_line=min(GRID_COLS, len(items)),
                           selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           column_spacing=2, row_spacing=2)
        for info, gfile in items:
            b = Gtk.Button(css_classes=["stack-item"])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.append(Gtk.Image(gicon=_icon(info), pixel_size=GRID_ICON))
            lbl = Gtk.Label(label=info.get_display_name(), wrap=True, lines=2, justify=Gtk.Justification.CENTER,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=12, width_chars=12)
            box.append(lbl)
            b.set_child(box)
            b.connect("clicked", lambda _b, f=gfile: (_open(f), pop.popdown()))
            _drag_source(b, gfile)
            flow.append(b)
        rows = (len(items) + GRID_COLS - 1) // GRID_COLS
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
                                    propagate_natural_height=rows <= 3, min_content_height=min(rows, 3) * 96,
                                    max_content_height=3 * 110)
        scroll.set_child(flow)
        return scroll

    def _list(self, items, pop) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for info, gfile in items:
            b = Gtk.Button(css_classes=["stack-row"])
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Image(gicon=_icon(info), pixel_size=16))
            row.append(Gtk.Label(label=info.get_display_name(), xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=36))
            if info.get_file_type() == Gio.FileType.DIRECTORY:
                row.append(Gtk.Image(icon_name="go-next-symbolic", pixel_size=12))
            b.set_child(row)
            b.connect("clicked", lambda _b, f=gfile: (_open(f), pop.popdown()))
            _drag_source(b, gfile)
            box.append(b)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
                                    propagate_natural_height=True, max_content_height=420)
        scroll.set_child(box)
        return scroll


def stack_menu(row: StackRow, tile):
    Item = ui.menu.Item
    spec = tile.spec
    tile.label.popdown()
    return ui.menu.popup(tile, [
        [Item("Sort by", submenu=[[Item(lbl, lambda on, k=k: on and row.set_spec(tile, "sort", k),
                                        checked=spec["sort"] == k) for k, lbl in SORTS]]),
         Item("Display as", submenu=[[Item(lbl, lambda on, k=k: on and row.set_spec(tile, "display", k),
                                           checked=spec["display"] == k)
                                      for k, lbl in (("folder", "Folder"), ("stack", "Stack"))]]),
         Item("View content as", submenu=[[Item(lbl, lambda on, k=k: on and row.set_spec(tile, "view", k),
                                                checked=spec["view"] == k)
                                           for k, lbl in (("grid", "Grid"), ("list", "List"))]])],
        [Item("Options", submenu=[[Item("Remove from Dock", lambda: row.remove(tile))]])],
        [Item(f'Open "{tile.name}"', lambda: _open(Gio.File.new_for_path(spec["path"])))],
    ], position=row.dock.away)
