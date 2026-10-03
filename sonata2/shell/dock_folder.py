"""App folders in the Dock (like Launchpad's folders, kept in the Dock).

A folder is a pinned key "folder:<id>"; its name and apps live in the
Dock's config, cfg["folders"][id] = {"name": ..., "apps": [desktop ids]}
(the same shape as a Launchpad folder: {"folder": name, "apps": [...]}).
Its icon is a rounded plate with up to nine of its apps in a 3 x 3 grid.
Click: a panel with its apps opens from the icon (zoom + fade); click an
app to open it. An app's menu has "Add to New Folder" and "Move to
<folder>"; a folder's menu has "Ungroup" and "Remove from Dock". A folder
left with one app turns back into that app (Launchpad does the same).

Locked folders ("Lock Folder"): the icon shows blank tiles and a lock,
nothing of what's inside; opening it (or unlocking it, or ungrouping it)
asks for the login password (PAM, like the lock screen) every time.
Apps can still be dropped in without it."""
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
  /* the Dock's own glass (Vini): same tint, same Appearance setting */
  font-family: %(font)s; color: %(label)s; background-color: %(dock_material)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
}
@keyframes dock-folder-in { from { opacity: 0; transform: scale(0.82); } to { opacity: 1; transform: none; } }
@keyframes dock-folder-out { from { opacity: 1; transform: none; } to { opacity: 0; transform: scale(0.9); } }
.dock-folder-view { animation: dock-folder-in %(open_ms)dms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.dock-folder-view.closing { animation: dock-folder-out %(close_ms)dms ease-in both; }
.dock-folder-title { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700;
                     margin: 0 6px 10px 6px; padding: 1px 8px; border-radius: %(r_button)s; }
.dock-folder-title.editable:hover { background-color: %(control_off)s; }
.dock-folder-app { padding: 6px 4px; border-radius: %(r_button)s; background: none; border: none;
                   box-shadow: none; min-width: 84px; }
.dock-folder-app:hover { background-color: %(control_off)s; }
.dock-folder-app:active { background-color: %(accent_selected)s; color: %(label_on_accent)s; }
.dock-folder-app label { font-size: %(text_small)s; }
.dock-folder-lock { margin: 4px 16px 6px 16px; }
.dock-folder-lock label { font-size: %(text_body)s; color: %(label_secondary)s; }
.dock-folder-lock .hint { font-size: %(text_small)s; color: %(label_tertiary)s; }
.dock-folder-lock passwordentry { min-width: 220px; }
@keyframes dock-folder-shake {   /* one selector per step: GTK's CSS has no "0%%, 100%%" lists */
  0%% { transform: none; } 20%% { transform: translateX(-8px); } 40%% { transform: translateX(8px); }
  60%% { transform: translateX(-8px); } 80%% { transform: translateX(8px); } 100%% { transform: none; } }
.dock-folder-lock.shake { animation: dock-folder-shake 360ms ease-in-out; }
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
    # inside the app-sized frame; a round one: inside the square that fits the circle
    extra = 0.08 if icons.frame_shape() != "circle" else (1 - 2 * icons.PLATE_INSET) * (1 - 0.7071) / 2 + 0.02
    pad = size * (icons.PLATE_INSET + extra)
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


def _sheen() -> list:
    """The app frame's sheen (icons.PLATE_SHEEN) as colour stops."""
    return [icons._stop(off, spec) for off, spec in zip((0.0, 1.0), icons.PLATE_SHEEN)]


class FolderIcon(Gtk.Widget):
    """A rounded translucent plate with the folder's first nine apps
    (sized like a DockIcon, so magnification and fitting work the same)."""

    def __init__(self, keys: list, size: int, locked: bool = False, on_scrim: bool = False, css: tuple = ()):
        """on_scrim: over Launchpad's dimmed desktop (light and dark alike)."""
        super().__init__(css_classes=["dock-icon", "dock-folder-icon", *css])
        self._size = size
        self.locked = locked
        self.on_scrim = on_scrim
        self.set_apps(keys)

    def set_locked(self, on: bool) -> None:
        self.locked = bool(on)
        self._nodes = {}
        self.queue_draw()

    def set_apps(self, keys: list) -> None:
        self.keys = list(keys)
        self._gicons = []
        for k in self.keys[:GRID * GRID]:
            info = apps.lookup(k)
            self._gicons.append(icons.app_icon(info) if info else None)
        self._paint = {}
        self._nodes = {}            # (size step, dark, locked, apps) -> render node
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
        """The icon is drawn once per 8 px size step and kept as a render node;
        magnification (a new size every frame) only scales that node."""
        s = self._size
        if s <= 0:
            return
        b = max(8, -(-s // 8) * 8)
        key = (b, ui.is_dark(), self.locked, tuple(self.keys), icons.frame_shape())
        node = self._nodes.get(key)
        if node is None:
            if len(self._nodes) > 6:
                self._nodes.clear()
            sub = Gtk.Snapshot()
            self._draw(sub, b)
            node = self._nodes[key] = sub.to_node()
        if node is None:
            return
        snap.save()
        if b != s:
            snap.scale(s / b, s / b)
        snap.append_node(node)
        snap.restore()

    def texture(self, size: int):
        """The icon as a picture (the drag icon: a folder has no gicon, and
        without one GTK showed the drag's text -- the folder's encoded name
        and apps, in large letters by the pointer)."""
        native = self.get_native()
        renderer = native.get_renderer() if native is not None else None
        snap = Gtk.Snapshot()
        self._draw(snap, size)
        node = snap.to_node()
        if renderer is None or node is None:
            return None
        return renderer.render_texture(node, Graphene.Rect().init(0, 0, size, size))

    def _draw(self, snap, s) -> None:
        # exactly an app's frame (icons._Plate): the same squircle, inset,
        # shadow, sheen and edge -- only see-through (Vini: they must match)
        inset = s * icons.PLATE_INSET
        p = s - 2 * inset
        rect = Graphene.Rect().init(inset, inset, p, p)
        dark = ui.is_dark() or self.on_scrim
        shadow = Gsk.RoundedRect()
        shadow.init_from_rect(rect, p * (0.5 if icons.frame_shape() == "circle" else 0.3))
        snap.append_outset_shadow(shadow, _rgba("rgba(0,0,0,0.22)"), 0, s * 0.012, 0, s * 0.02)
        shape = icons.frame_shape()                      # Settings > App Icons: folders follow apps
        path = icons.shape_path(shape, inset, inset, p, p)
        snap.push_fill(path, Gsk.FillRule.WINDING)
        fill = ("rgba(255,255,255,0.22)" if self.on_scrim          # Launchpad: its tile_on_scrim, a bit stronger
                else "rgba(120,120,128,0.55)" if dark else "rgba(255,255,255,0.62)")
        snap.append_color(_rgba(fill), rect)
        snap.append_linear_gradient(rect, Graphene.Point().init(0, inset), Graphene.Point().init(0, s - inset),
                                    _sheen())
        snap.pop()
        snap.append_stroke(path, Gsk.Stroke.new(max(0.5, s / 256)), _rgba("rgba(0,0,0,0.10)"))
        if self.locked:
            self._draw_locked(snap, s, dark)
            return
        for (x, y, side), gicon in zip(mini_rects(s, len(self._gicons)), self._gicons):
            if gicon is None:
                continue
            px = max(8, int(round(side)))      # (drawn at an 8 px size step: do_snapshot)
            snap.save()
            snap.translate(Graphene.Point().init(x, y))
            self._mini(gicon, px).snapshot(snap, side, side)
            snap.restore()


    def _draw_locked(self, snap, s, dark) -> None:
        """Blank tiles (how many apps, not which) and a padlock over them."""
        tile = _rgba("rgba(255,255,255,0.16)" if dark else "rgba(0,0,0,0.08)")
        for x, y, side in mini_rects(s, len(self.keys)):
            rr = Gsk.RoundedRect()
            rr.init_from_rect(Graphene.Rect().init(x, y, side, side), side * 0.225)
            snap.push_rounded_clip(rr)
            snap.append_color(tile, Graphene.Rect().init(x, y, side, side))
            snap.pop()
        ink = _rgba("rgba(255,255,255,0.92)" if dark else "rgba(40,40,46,0.85)")
        for path, fill in lock_paths(s):
            if fill:
                snap.append_fill(path, Gsk.FillRule.WINDING, ink)
            else:
                snap.append_stroke(path, Gsk.Stroke.new(s * 0.055), ink)


def lock_paths(s: float) -> list:
    """A padlock centred on an s x s icon: [(path, filled?)] -- the body
    (filled) and the shackle (stroked)."""
    bw, bh = s * 0.34, s * 0.26
    bx, by = (s - bw) / 2, s * 0.47
    body = Gsk.PathBuilder.new()
    body.add_rounded_rect(_rounded_rect(bx, by, bw, bh, s * 0.05))
    r = bw * 0.30
    cx, top = s / 2, by - r * 1.15
    sh = Gsk.PathBuilder.new()
    sh.move_to(cx - r, by)
    sh.line_to(cx - r, top + r)
    sh.conic_to(cx - r, top, cx, top, 0.70710678)
    sh.conic_to(cx + r, top, cx + r, top + r, 0.70710678)
    sh.line_to(cx + r, by)
    return [(body.to_path(), True), (sh.to_path(), False)]


def _rounded_rect(x, y, w, h, r) -> Gsk.RoundedRect:
    rr = Gsk.RoundedRect()
    rr.init_from_rect(Graphene.Rect().init(x, y, w, h), r)
    return rr


def check_password(password: str, done) -> None:
    """The login password (PAM) in a thread; done(ok) on the main loop."""
    import threading
    from .. import pam
    user = GLib.get_user_name()
    def work():
        ok = pam.authenticate(user, password)         # PAM (and its fail delay) off the main loop
        GLib.idle_add(lambda: (done(ok), False)[1])
    threading.Thread(target=work, daemon=True).start()


# -- the open folder ---------------------------------------------------------------------------
def _signature(folder: dict) -> tuple:
    return folder["name"], tuple(folder["apps"])


def open_panel(dock, tile, then=None, rename=False) -> Gtk.Popover:
    """The folder's apps in a panel over its icon, zooming in from it (a
    locked folder asks for the password first; `then`: ask, then run it
    instead of showing the apps). An unlocked folder's panel is built once
    and opened again as it is while its apps don't change."""
    tile.label.popdown()
    folder = dock.folder(tile.key)
    cached = getattr(tile, "folder_pop", None)
    reuse = then is None and not folder.get("locked")
    if cached is not None:
        if reuse and cached.sig == _signature(folder) and not cached.get_visible():
            _replay(cached)
            if rename:
                _edit_title(cached)
            return cached
        if not cached.get_visible():
            cached.unparent()
        tile.folder_pop = None
    pop = Gtk.Popover(css_classes=["dock-folder-panel"], has_arrow=False, position=dock.away)
    P = Gtk.PositionType
    pop.set_offset(*{P.TOP: (0, -8), P.LEFT: (-8, 0), P.RIGHT: (8, 0)}[dock.away])
    view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["dock-folder-view"])
    # the name, centred: click it to rename (Vini) -- once the apps show (a
    # locked folder: after its password). Typed in an alert of its own: the
    # Dock's panel never got the keys (editing in place did nothing there)
    title = Gtk.Label(label=folder["name"], css_classes=["dock-folder-title"], halign=Gtk.Align.CENTER,
                      xalign=0.5, justify=Gtk.Justification.CENTER, max_width_chars=28,
                      ellipsize=Pango.EllipsizeMode.END, tooltip_text="Rename")
    title.editable = False
    click = Gtk.GestureClick()
    click.connect("released", lambda *_a: title.editable and ask_rename(dock, tile, pop))
    title.add_controller(click)
    view.append(title)
    pop.view, pop.flow, pop.lock, pop.sig, pop.title = view, None, None, _signature(folder), title
    pop.rename = rename
    pop.dock, pop.tile = dock, tile
    pop.set_child(view)
    pop.set_parent(tile)
    if reuse:
        tile.folder_pop = pop                   # kept (unparented with the tile: Dock._remove_tile)
    else:                                       # a password panel: never kept
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    ui.menu.OPEN.add(pop)                       # keeps an auto-hiding Dock visible
    pop.connect("closed", lambda p: (ui.menu.OPEN.discard(p), [cb() for cb in list(ui.menu.on_closed)]))
    # the keyboard while it's open (the Dock takes none): its name can be
    # typed (Vini: clicking it did nothing), Esc closes it, a password too
    pop.connect("closed", lambda _p: _keyboard(tile, False))
    if not reuse:
        _lock_view(dock, tile, pop, then)
    else:
        _apps_view(dock, tile, pop)
    _keyboard(tile, True)
    pop.popup()
    return pop


def _keyboard(tile, on: bool) -> None:
    from . import layer
    win = tile.get_root()
    if win is not None:
        layer.take_keyboard(win, on)


def _replay(pop) -> None:
    """Open a kept panel with its zoom-in again (the class comes back on the
    next frame, so the animation starts over; hidden until then)."""
    _keyboard(pop.get_parent(), True)
    v = pop.view
    for c in ("closing", "dock-folder-view"):
        v.remove_css_class(c)
    v.set_opacity(0)
    ui.menu.OPEN.add(pop)
    pop.popup()

    def start():
        v.set_opacity(1)
        v.add_css_class("dock-folder-view")
        return False
    GLib.idle_add(start)


def _lock_view(dock, tile, pop, then=None) -> None:
    """Password first; right: the apps (or then(), e.g. unlock / ungroup)."""
    from .. import pam
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["dock-folder-lock"])
    box.append(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=32))
    box.append(Gtk.Label(label="Enter your password to open this folder" if then is None
                         else "Enter your password to change this folder", wrap=True,
                         justify=Gtk.Justification.CENTER, max_width_chars=30))
    entry = Gtk.PasswordEntry(show_peek_icon=True, halign=Gtk.Align.CENTER)
    box.append(entry)
    hint = Gtk.Label(label="" if pam.available() else "Passwords can't be checked (PAM missing)",
                     css_classes=["hint"])
    box.append(hint)
    pop.view.append(box)
    pop.lock, pop.entry, pop.hint = box, entry, hint      # (tests)

    def done(ok):
        if pop.lock is not box:
            return
        entry.set_sensitive(True)
        if ok:
            pop.view.remove(box)
            pop.lock = None
            if then is not None:
                pop.popdown()
                then()
                return
            _apps_view(dock, tile, pop)
            pop.view.remove_css_class("dock-folder-view")         # zoom in again, now with the apps
            GLib.idle_add(lambda: (pop.view.add_css_class("dock-folder-view"), False)[1])
        else:
            hint.set_label("Wrong password")
            entry.set_text("")
            entry.grab_focus()
            box.remove_css_class("shake")
            GLib.idle_add(lambda: (box.add_css_class("shake"), False)[1])

    def check(_e):
        pw = entry.get_text()
        if pw:
            entry.set_sensitive(False)
            check_password(pw, done)
    entry.connect("activate", check)
    GLib.idle_add(lambda: (entry.grab_focus(), False)[1])


def _apps_view(dock, tile, pop) -> None:
    folder = dock.folder(tile.key)
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
    pop.view.append(scroll)
    pop.flow = flow                             # (tests)
    pop.title.editable = True
    pop.title.add_css_class("editable")
    if pop.rename:
        pop.rename = False
        _edit_title(pop)


def _edit_title(pop) -> None:
    """"Rename…" for a locked folder: asked once its apps show."""
    def start():
        if pop.get_visible() and pop.title.editable:
            ask_rename(pop.dock, pop.tile, pop)
        return False
    GLib.idle_add(start)


def ask_rename(dock, tile, pop=None) -> None:
    """The folder's new name (ui.dialog.ask_text); the panel closes first."""
    f = dock.folder(tile.key)
    if f is None:
        return

    def ask():
        ui.dialog.ask_text("Rename Folder", f["name"], "Rename", lambda name: dock.rename_folder(tile.key, name))
    if pop is not None and pop.get_visible():
        close_panel(pop, then=ask)
    else:
        ask()


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
    # dragged out of the panel: out of the folder, into the Dock (Vini)
    src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
    src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(key))
    src.connect("drag-begin", lambda _s, drag: _drag_out(dock, tile, key, pop, drag))
    src.connect("drag-cancel", lambda *_: True)          # no snap back into a closed panel
    src.connect("drag-end", lambda *_: dock.folder_app_drag_end(tile.key, key))
    b.add_controller(src)
    return b


def _drag_out(dock, tile, key, pop, drag) -> None:
    pop.popdown()                                        # the Dock is reachable under it
    if not dock.folder_app_drag_begin(tile.key, key, drag):
        drag.drop_done(False)


# -- menus -------------------------------------------------------------------------------------
def folder_menu(dock, tile):
    Item = ui.menu.Item
    tile.label.popdown()
    locked = bool((dock.folder(tile.key) or {}).get("locked"))
    if locked:
        lock = Item("Unlock Folder\u2026", lambda: open_panel(
            dock, tile, then=lambda: dock.set_folder_locked(tile.key, False)))
        ungroup = Item("Ungroup\u2026", lambda: open_panel(dock, tile, then=lambda: dock.ungroup(tile.key)))
    else:
        lock = Item("Lock Folder", lambda: dock.set_folder_locked(tile.key, True))
        ungroup = Item("Ungroup", lambda: dock.ungroup(tile.key))
    return ui.menu.popup(tile, [[Item("Open", lambda: open_panel(dock, tile)),
                                 Item("Rename\u2026", lambda: open_panel(dock, tile, rename=True)
                                      if dock.folder(tile.key).get("locked") else ask_rename(dock, tile))],
                                [lock],
                                [ungroup, Item("Remove from Dock", lambda: remove_with_puff(dock, tile))]],
                         position=dock.away)


def remove_with_puff(dock, tile) -> None:
    """Remove from Dock, in a puff of smoke like an app dragged out (Vini)."""
    dock.poof_at_tile(tile)
    dock.set_pinned(tile.key, False)


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
