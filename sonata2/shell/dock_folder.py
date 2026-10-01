"""App folders in the Dock (like Launchpad's folders, kept in the Dock).

A folder is a pinned key "folder:<id>"; its name and apps live in the
Dock's config, cfg["folders"][id] = {"name": ..., "apps": [desktop ids]}
(the same shape as a Launchpad folder: {"folder": name, "apps": [...]}).
Its icon is a rounded plate with up to nine of its apps in a 3 x 3 grid.
Click: a panel with its apps opens from the icon (zoom + fade); click an
app to open it. An app's menu has "Add to New Folder" and "Move to
<folder>"; a folder's menu has "Ungroup" and "Remove from Dock". A folder
left with one app turns back into that app (Launchpad does the same)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import apps, icons, ui  # noqa: E402

PREFIX = "folder:"
GRID = 3                  # mini icons per row on the folder's icon
PANEL_COLS = 4            # apps per row in the open folder
PANEL_ICON = 56
OPEN_MS, CLOSE_MS = 220, 140

ui.register("""
popover.dock-folder-panel { background: none; box-shadow: none; padding: 0; }
popover.dock-folder-panel > contents {
  padding: 12px 10px 10px 10px; border-radius: calc(%(r_dialog)s * 1.6);
  font-family: %(font)s; color: %(label)s; background-color: %(menu_bg)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
@keyframes dock-folder-in { from { opacity: 0; transform: scale(0.82); } to { opacity: 1; transform: none; } }
@keyframes dock-folder-out { from { opacity: 1; transform: none; } to { opacity: 0; transform: scale(0.9); } }
.dock-folder-view { animation: dock-folder-in %(open_ms)dms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.dock-folder-view.closing { animation: dock-folder-out %(close_ms)dms ease-in both; }
.dock-folder-title { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700;
                     margin: 0 6px 10px 6px; }
.dock-folder-app { padding: 6px 4px; border-radius: %(r_button)s; background: none; border: none;
                   box-shadow: none; min-width: 84px; }
.dock-folder-app:hover { background-color: %(control_off)s; }
.dock-folder-app:active { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
.dock-folder-app label { font-size: %(text_small)s; }
""", key="dock-folder", open_ms=OPEN_MS, close_ms=CLOSE_MS)


# -- the model ---------------------------------------------------------------------------------
def is_folder(key) -> bool:
    return isinstance(key, str) and key.startswith(PREFIX)


def folder_id(key: str) -> str:
    return key[len(PREFIX):]


def new_id(folders: dict) -> str:
    n = 1
    while str(n) in folders:
        n += 1
    return str(n)


def default_name(keys: list) -> str:
    """Launchpad's rule: the apps' shared category, else the first's."""
    from ..launchpad_model import folder_name

    def cats(k):
        info = apps.lookup(k)
        try:
            text = info.get_string("Categories") if info else ""
        except (AttributeError, TypeError):
            text = ""
        return [c for c in (text or "").split(";") if c]
    a = cats(keys[0]) if keys else []
    b = cats(keys[1]) if len(keys) > 1 else a
    return folder_name(a, b)


def as_launchpad(folder: dict) -> dict:
    """The same folder in Launchpad's shape."""
    return {"folder": folder["name"], "apps": list(folder["apps"])}


def mini_rects(size: float, n: int) -> list:
    """Where the first n (<= 9) mini icons go on a folder icon of `size`:
    [(x, y, side)], row by row, inside a padded 3 x 3 grid."""
    pad = size * 0.12
    cell = (size - 2 * pad) / GRID
    side = cell * 0.84
    off = (cell - side) / 2
    return [(pad + (i % GRID) * cell + off, pad + (i // GRID) * cell + off, side)
            for i in range(min(n, GRID * GRID))]


# -- the icon ----------------------------------------------------------------------------------
def _rgba(spec: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(spec)
    return c


class FolderIcon(Gtk.Widget):
    """A rounded translucent plate with the folder's first nine apps
    (sized like a DockIcon, so magnification and fitting work the same)."""

    def __init__(self, keys: list, size: int):
        super().__init__(css_classes=["dock-icon", "dock-folder-icon"])
        self._size = size
        self.set_apps(keys)

    def set_apps(self, keys: list) -> None:
        self.keys = list(keys)
        self._gicons = []
        for k in self.keys[:GRID * GRID]:
            info = apps.lookup(k)
            self._gicons.append(icons.app_icon(info) if info else None)
        self._paint = {}
        self.queue_draw()

    # DockIcon's interface (the Dock sizes and badges its icons)
    badge = ""

    def set_size(self, size: float) -> None:
        size = int(round(size))
        if size != self._size:
            self._size = size
            self.queue_resize()

    def set_gicon(self, _gicon) -> None:
        pass

    def get_gicon(self):
        return None

    def set_badge(self, _text: str) -> None:
        pass

    def do_measure(self, _orientation, _for_size):
        return self._size, self._size, -1, -1

    def _mini(self, gicon, px: int):
        key = (id(gicon), px, ui.is_dark())
        if key not in self._paint:
            if len(self._paint) > 40:
                self._paint.clear()
            self._paint[key] = icons.paintable(self, gicon, px)
        return self._paint[key]

    def do_snapshot(self, snap) -> None:
        s = self._size
        rect = Graphene.Rect().init(0, 0, s, s)
        rr = Gsk.RoundedRect()
        rr.init_from_rect(rect, s * 0.225)
        dark = ui.is_dark()
        snap.push_rounded_clip(rr)
        snap.append_color(_rgba("rgba(120,120,128,0.42)" if dark else "rgba(255,255,255,0.55)"), rect)
        snap.pop()
        snap.append_border(rr, [0.5] * 4, [_rgba("rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.12)")] * 4)
        for (x, y, side), gicon in zip(mini_rects(s, len(self._gicons)), self._gicons):
            if gicon is None:
                continue
            px = max(8, int(round(side)))
            snap.save()
            snap.translate(Graphene.Point().init(x, y))
            self._mini(gicon, px).snapshot(snap, side, side)
            snap.restore()


# -- the open folder ---------------------------------------------------------------------------
def open_panel(dock, tile) -> Gtk.Popover:
    """The folder's apps in a panel over its icon, zooming in from it."""
    tile.label.popdown()
    folder = dock.folder(tile.key)
    pop = Gtk.Popover(css_classes=["dock-folder-panel"], has_arrow=False, position=dock.away)
    P = Gtk.PositionType
    pop.set_offset(*{P.TOP: (0, -8), P.LEFT: (-8, 0), P.RIGHT: (8, 0)}[dock.away])
    view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["dock-folder-view"])
    view.append(Gtk.Label(label=folder["name"], css_classes=["dock-folder-title"],
                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=28))
    keys = [k for k in folder["apps"] if apps.lookup(k)]
    cols = max(1, min(PANEL_COLS, len(keys)))
    flow = Gtk.FlowBox(max_children_per_line=cols, min_children_per_line=cols,
                       selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                       column_spacing=2, row_spacing=2)
    for k in keys:
        flow.append(_app_button(dock, tile, k, pop))
    rows = (len(keys) + cols - 1) // cols
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True,
                                propagate_natural_height=rows <= 4, min_content_height=min(rows, 4) * 100,
                                max_content_height=4 * 112)
    scroll.set_child(flow)
    view.append(scroll)
    pop.set_child(view)
    pop.set_parent(tile)
    pop.view, pop.flow = view, flow             # (tests)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    ui.menu.OPEN.add(pop)                       # keeps an auto-hiding Dock visible
    pop.connect("closed", lambda p: (ui.menu.OPEN.discard(p), [cb() for cb in list(ui.menu.on_closed)]))
    pop.popup()
    return pop


def close_panel(pop, then=None) -> None:
    """Fade the panel out, then close it (then(): after it's gone)."""
    if pop.view.has_css_class("closing"):
        return
    pop.view.add_css_class("closing")

    def done():
        pop.popdown()
        if then:
            then()
        return False
    GLib.timeout_add(CLOSE_MS, done)


def _app_button(dock, tile, key, pop) -> Gtk.Button:
    info = apps.lookup(key)
    b = Gtk.Button(css_classes=["dock-folder-app"])
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.append(Gtk.Image(gicon=icons.app_icon(info), pixel_size=PANEL_ICON))
    box.append(Gtk.Label(label=info.get_display_name(), wrap=True, lines=2, justify=Gtk.Justification.CENTER,
                         ellipsize=Pango.EllipsizeMode.END, max_width_chars=12, width_chars=12))
    b.set_child(box)
    b.key = key
    b.connect("clicked", lambda _b: close_panel(pop, lambda: dock.open_app(key, tile)))
    menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
    menu.connect("pressed", lambda g, _n, x, y: ui.menu.popup(b, [[
        ui.menu.Item("Open", lambda: close_panel(pop, lambda: dock.open_app(key, tile))),
        ui.menu.Item("Remove from Folder", lambda: (pop.popdown(), dock.remove_from_folder(tile.key, key)))]],
        at=(x, y)))
    b.add_controller(menu)
    return b


# -- menus -------------------------------------------------------------------------------------
def folder_menu(dock, tile):
    Item = ui.menu.Item
    tile.label.popdown()
    return ui.menu.popup(tile, [[Item("Open", lambda: open_panel(dock, tile))],
                                [Item("Ungroup", lambda: dock.ungroup(tile.key)),
                                 Item("Remove from Dock", lambda: dock.set_pinned(tile.key, False))]],
                         position=dock.away)


def app_items(dock, key) -> list:
    """For an app's Dock menu: [Add to New Folder, Move to <folder>...]
    (pinned, movable apps only)."""
    from .dock import PERMANENT
    if is_folder(key) or key in PERMANENT or key not in dock.cfg["pinned"] or not apps.lookup(key):
        return []
    Item = ui.menu.Item
    items = [Item("Add to New Folder", lambda: dock.make_folder([key]))]
    for fkey in [k for k in dock.cfg["pinned"] if is_folder(k) and dock.folder(k)]:
        items.append(Item(f"Move to “{dock.folder(fkey)['name']}”",
                          lambda f=fkey: dock.add_to_folder(f, key)))
    return items
