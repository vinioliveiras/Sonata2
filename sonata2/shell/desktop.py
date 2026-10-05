"""The desktop (macOS Big Sur): the files of ~/Desktop as icons on the
wallpaper, in a grid that starts at the top right and fills columns
downwards. Drag icons anywhere (they snap to the grid and stay there),
drop files from Files or other apps to move them to the Desktop, drag
icons out to Files, the Dock or the Trash. Double-click opens, right-click
menus like Finder's, rubber-band selection, Delete / Return / Space /
Ctrl+A / Ctrl+C / Ctrl+V. Lives in the wallpaper process (the background
layer); reuses Files' folder model, icons, thumbnails and operations.

Every display has a desktop (macOS): icons start on the main one; one
dragged onto another display, a folder made or a file dropped there, stays
there. Its spot remembers the display ([col, row, "HDMI-A-1"]); with that
display unplugged it shows on the main one.

desktop.json: {"positions": {name: [col, row] | [col, row, connector]},
               "sort": "none"|"name"|"kind"|"date"}"""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..files import folder, ops, packages  # noqa: E402
from ..files.folder import file_of, is_dir  # noqa: E402

CELL_W, CELL_H = 96, 104          # Big Sur's default desktop grid (64 px icons)
ICON = 64
from ..shell import menubar_size  # noqa: E402
TOP, EDGE = menubar_size.height() + 10, 14        # below the menu bar; off the screen edges
DEFAULTS = {"positions": {}, "sort": "none"}
SORTS = (("none", "None"), ("name", "Name"), ("kind", "Kind"), ("date", "Date Modified"))   # Sort By (and Settings)

ui.register("""
.desk-item { padding: 4px 2px; }
.desk-item .desk-icon { padding: 3px; border-radius: 6px; }
.desk-item label { color: white; font-family: %(font)s; font-size: %(text_small)s; font-weight: 500;
  text-shadow: 0 1px 2px rgba(0,0,0,0.75); padding: 1px 4px; border-radius: 4px; }
.desk-item.selected .desk-icon { background: rgba(0,0,0,0.28); box-shadow: inset 0 0 0 1px rgba(255,255,255,0.25); }
.desk-item.selected label { background: %(accent)s; text-shadow: none; }
.desk-item.dragging { opacity: 0.4; }
.desk-item.drop-target .desk-icon { background: rgba(0,0,0,0.35); }
.desk-band { background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.55); border-radius: 2px; }
.desk-item entry.fs-rename { min-height: 0; padding: 1px 4px; font-size: %(text_small)s; }
""", key="desktop")


def desktop_dir() -> Gio.File:
    """~/Desktop (the XDG desktop folder); created when missing, like macOS."""
    p = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    if not p or os.path.realpath(p) == os.path.realpath(GLib.get_home_dir()):
        p = os.path.join(GLib.get_home_dir(), "Desktop")
    os.makedirs(p, exist_ok=True)
    return Gio.File.new_for_path(p)


class DesktopItem(Gtk.Box):
    def __init__(self, desk, info):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["desk-item"],
                         width_request=CELL_W, halign=Gtk.Align.CENTER)
        from ..files.views import set_icon
        self.desk, self.info = desk, info
        self.img = Gtk.Image(pixel_size=ICON, css_classes=["desk-icon"], halign=Gtk.Align.CENTER)
        self.app = _launcher(info)                     # an app shortcut (.desktop): its icon and name
        if self.app is not None:
            from .. import icons
            icons.set_image(self.img, icons.app_icon(self.app))
        else:
            set_icon(self.img, info)
        self.append(self.img)
        self.lbl = Gtk.Label(label=self.app.get_display_name() if self.app else info.get_display_name(), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                             lines=2, ellipsize=Pango.EllipsizeMode.MIDDLE, justify=Gtk.Justification.CENTER,
                             max_width_chars=13, halign=Gtk.Align.CENTER)
        self.append(self.lbl)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        src.connect("prepare", lambda *_: desk.drag_prepare(self))
        src.connect("drag-begin", lambda _s, drag: desk.drag_begin(self, drag))
        src.connect("drag-end", lambda *_: desk.drag_end())
        self.add_controller(src)
        if is_dir(info):                               # folders take drops
            tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
            tgt.connect("enter", lambda *_: (self.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
            tgt.connect("leave", lambda *_: self.remove_css_class("drop-target"))
            tgt.connect("drop", lambda t, v, *_: (self.remove_css_class("drop-target"),
                                                   desk.drop_into(list(v.get_files()), file_of(info),
                                                                  _copy(t)))[1])
            self.add_controller(tgt)

    @property
    def name(self) -> str:
        return self.info.get_name()

    def _pressed(self, g, n, x, y):
        button = g.get_current_button()
        state = g.get_current_event_state()
        if button == Gdk.BUTTON_SECONDARY:
            if self not in self.desk.selection:
                self.desk.select([self])
            self.desk.item_menu(self, x, y)
        elif n == 2:
            self.desk.open_selection()
        elif state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            self.desk.toggle(self)
        elif self not in self.desk.selection:
            self.desk.select([self])
        # a plain press stays unclaimed: claiming it here cancelled the drag
        # source on the same icon, so icons couldn't be dragged anywhere
        if button == Gdk.BUTTON_SECONDARY or n == 2:
            g.set_state(Gtk.EventSequenceState.CLAIMED)


def _previewable(info) -> bool:
    from ..files.quicklook import previewable
    return previewable(info)


def _launcher(info):
    """The app of a .desktop shortcut on the Desktop, or None."""
    if not info.get_name().endswith(".desktop"):
        return None
    from ..apps import DesktopAppInfo
    try:
        return DesktopAppInfo.new_from_filename(file_of(info).get_path() or "")
    except (TypeError, GLib.Error):
        return None


def _copy(target) -> bool:
    return bool(target.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)


class Desktop(Gtk.Fixed):
    """Icon layer over the wallpaper."""

    def __init__(self, screen: str = "", main: bool = True):
        super().__init__(focusable=True, hexpand=True, vexpand=True)
        self.screen, self.main = screen, main         # the display's connector; the main display's desktop?
        self.dir = desktop_dir()
        self.cfg = config.load("desktop", DEFAULTS)
        self.items = {}                   # name -> DesktopItem
        self.selection = []
        self._drag_names = None
        self._drag_from = None
        self._placed = {}
        self._size = (0, 0)
        self._margins = self._dock_margins()
        self.folder = folder.Folder(lambda _u: self._sync(), lambda _u, _e: None)
        self.folder.store.connect("items-changed", lambda *_: self._sync())
        self.folder.load(self.dir.get_uri())
        self.band = Gtk.Box(css_classes=["desk-band"], can_target=False, visible=False)
        self.put(self.band, 0, 0)
        # background: deselect, rubber band, menu, drops
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._band_begin)
        drag.connect("drag-update", self._band_update)
        drag.connect("drag-end", lambda *_: self.band.set_visible(False))
        self.add_controller(drag)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._background_menu)
        self.add_controller(menu)
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        tgt.connect("drop", self._drop)
        self.add_controller(tgt)
        self.drag_icon = None
        ui.drag.follow(self, lambda: self.drag_icon)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._mon = config.watch("desktop", self._config_changed)
        self._dock_mon = config.watch("dock", self._dock_changed)      # the Dock moved / resized

    # -- layout -------------------------------------------------------------------------------
    def resized(self, w, h) -> None:
        """From the window's size_allocate (GtkFixed allocates through its
        layout manager, so its own vfunc never runs)."""
        if (w, h) != self._size:
            self._size = (w, h)
            GLib.idle_add(lambda: (self._layout(), False)[1])

    def _dock_margins(self):
        """(left, right, bottom) kept free: the Dock's edge gets its height
        (it can sit left, right or bottom; auto-hide leaves only the edge gap)."""
        from .dock import DEFAULTS as DOCK, plate_height
        d = config.load("dock", DOCK)
        thick = 4 if d.get("autohide") else plate_height(d) + d.get("edge_gap", 4) + 8
        side = d.get("position", "bottom")
        return (EDGE + (thick if side == "left" else 0), EDGE + (thick if side == "right" else 0),
                EDGE + (thick if side == "bottom" else 0))

    def rows(self) -> int:
        return max(1, (self._size[1] - TOP - self._margins[2]) // CELL_H)

    def cols(self) -> int:
        return max(1, (self._size[0] - self._margins[0] - self._margins[1]) // CELL_W)

    def cell_xy(self, col, row):
        return self._size[0] - self._margins[1] - (col + 1) * CELL_W, TOP + row * CELL_H

    def cell_at(self, x, y):
        col = int((self._size[0] - self._margins[1] - x) // CELL_W)
        row = int((y - TOP) // CELL_H)
        return max(0, min(self.cols() - 1, col)), max(0, min(self.rows() - 1, row))

    def _sorted_names(self):
        infos = [self.folder.store.get_item(i) for i in range(self.folder.store.get_n_items())]
        sort = self.cfg.get("sort", "none")
        if sort == "kind":
            infos.sort(key=lambda i: (not is_dir(i), i.get_content_type() or "", folder.sort_key(i)))
        elif sort == "date":
            infos.sort(key=lambda i: -(i.get_modification_date_time().to_unix()
                                       if i.get_modification_date_time() else 0))
        return [i.get_name() for i in infos]      # "name"/"none": the model's Finder order

    def _layout(self) -> None:
        if not self._size[0]:
            return
        rows = self.rows()
        free_order = [(c, r) for c in range(self.cols()) for r in range(rows)]
        pos = {} if self.cfg.get("sort", "none") != "none" else dict(self.cfg.get("positions", {}))
        taken = set()
        placed = {}
        mine = [n for n in self._sorted_names() if n in self.items]
        for name in mine:                          # remembered spots first
            p = pos.get(name)
            if p and (p[0], p[1]) not in taken and p[0] < self.cols() and p[1] < rows:
                placed[name] = (p[0], p[1])
                taken.add((p[0], p[1]))
        slots = iter(c for c in free_order if c not in taken)
        for name in mine:                          # then the first free cells
            if name not in placed:
                placed[name] = next(slots, (self.cols() - 1, rows - 1))
        # icons that already had a spot slide to their new one (new ones just appear)
        old = getattr(self, "_placed", {}) or {}
        # (only icons already drawn at a spot: one just added sits at 0,0 until
        # now -- it glided in from the top-left corner (Vini))
        before = ui.transition.glide_record([it for n, it in self.items.items()
                                             if n in old and getattr(it, "_laid", False)], self)
        for name, (c, r) in placed.items():
            item = self.items.get(name)
            if item:
                x, y = self.cell_xy(c, r)
                self.move(item, x, y)
                item._laid = True
        self._placed = placed
        ui.transition.glide_play(before, self)

    def do_snapshot(self, snap) -> None:
        ui.transition.snapshot_children(self, snap)

    def _display_of(self, name: str) -> str:
        """The display an icon was put on ("" = the main one)."""
        if self.cfg.get("sort", "none") != "none":
            return ""                                # sorted: all on the main display
        p = self.cfg.get("positions", {}).get(name)
        return p[2] if isinstance(p, list) and len(p) > 2 and isinstance(p[2], str) else ""

    def mine(self, name: str) -> bool:
        """Is this icon shown on this display's desktop?"""
        where = self._display_of(name)
        if self.main:
            return where in ("", self.screen) or where not in _connected()
        return bool(self.screen) and where == self.screen

    def _sync(self) -> None:
        """Items follow the folder (live monitor), the ones on this display."""
        store = self.folder.store
        infos = {store.get_item(i).get_name(): store.get_item(i) for i in range(store.get_n_items())}
        infos = {n: i for n, i in infos.items() if self.mine(n)}
        for name in [n for n in self.items if n not in infos]:
            item = self.items.pop(name)
            if item in self.selection:
                self.selection.remove(item)
            self.remove(item)
        for name, info in infos.items():
            old = self.items.get(name)
            if old is not None and old.info is info:
                continue
            if old is not None and not old.lbl.get_visible():   # its name being typed: keep the field
                old.info = info
                continue
            at = (0, 0)
            if old is not None:                       # the same file, fresh info: same spot
                spot = (getattr(self, "_placed", None) or {}).get(name)
                at = self.cell_xy(*spot) if spot and getattr(old, "_laid", False) else at
                self.remove(old)
            item = DesktopItem(self, info)
            item._laid = old is not None and getattr(old, "_laid", False)
            self.items[name] = item
            self.put(item, *at)
        GLib.idle_add(lambda: (self._layout(), False)[1])

    def _config_changed(self) -> None:
        self.cfg = config.load("desktop", DEFAULTS)
        self._sync()                                 # icons moved to / from another display

    def _dock_changed(self) -> None:
        m = self._dock_margins()
        if m != self._margins:
            self._margins = m
            self._layout()

    def _save_positions(self, moved: dict) -> None:
        cfg = config.load("desktop", DEFAULTS)
        tag = [] if self.main else [self.screen]          # spots on another display name it
        cfg["positions"].update({k: [v[0], v[1]] + tag for k, v in moved.items()})
        # files gone from ~/Desktop lose their spot (not icons of another
        # display, nor a folder that is being made right now)
        base = self.dir.get_path()
        cfg["positions"] = {k: v for k, v in cfg["positions"].items()
                            if k in moved or os.path.lexists(os.path.join(base, k))}
        if cfg.get("sort", "none") != "none":
            cfg["sort"] = "none"                    # moving an icon by hand (Finder does the same)
        config.save("desktop", cfg)

    # -- selection ----------------------------------------------------------------------------
    def select(self, items) -> None:
        for it in self.selection:
            it.remove_css_class("selected")
        self.selection = list(items)
        for it in self.selection:
            it.add_css_class("selected")
        self.grab_focus()

    def toggle(self, item) -> None:
        self.select([i for i in self.selection if i is not item] +
                    ([] if item in self.selection else [item]))

    def _band_begin(self, g, x, y):
        if self.pick(x, y, Gtk.PickFlags.DEFAULT) not in (self, None):
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.select([])
        self._band_origin = (x, y)

    def _band_update(self, _g, dx, dy):
        x0, y0 = self._band_origin
        x, y, w, h = min(x0, x0 + dx), min(y0, y0 + dy), abs(dx), abs(dy)
        if w < 3 and h < 3:
            return
        self.band.set_size_request(int(w), int(h))
        self.move(self.band, x, y)
        self.band.set_visible(True)
        rect = Graphene.Rect().init(x, y, w, h)
        hit = []
        for item in self.items.values():
            ok, b = item.compute_bounds(self)
            if ok and b.intersection(rect)[0]:
                hit.append(item)
        if hit != self.selection:
            self.select(hit)

    # -- actions ------------------------------------------------------------------------------
    def selected_files(self):
        return [file_of(i.info) for i in self.selection]

    def open_selection(self) -> None:
        from ..files import open_folder
        for item in self.selection:
            f = file_of(item.info)
            if item.app is not None:
                try:
                    item.app.launch([], None)
                except GLib.Error:
                    pass
            elif is_dir(item.info):
                open_folder(f.get_uri())
            elif _previewable(item.info) and len(self.selection) == 1 and not (
                    item.info.get_content_type() and
                    Gio.AppInfo.get_default_for_type(item.info.get_content_type(), False)):
                self.quick_look()             # Quick Look only when no app opens it (like Files)
            elif not (f.get_path() and packages.open_path(f.get_path())):   # packages: install / run / extract
                try:
                    Gio.AppInfo.launch_default_for_uri(f.get_uri(), None)
                except GLib.Error:
                    pass

    def trash_selection(self) -> None:
        ops.trash(self.selected_files())

    def rename_selection(self) -> None:
        if len(self.selection) != 1:
            return
        from ..files.views import _inline_rename
        item = self.selection[0]

        def commit(info, new):
            old = info.get_name()
            ops.rename(file_of(info), new, lambda _f: self._renamed(old, new), lambda _e: None)
        # the desktop sits under the windows and gets the keyboard only when
        # clicked: it takes it while the name is typed (Vini: a new folder's
        # name had to be clicked before typing), then gives it back
        from . import layer
        win = self.get_root()
        layer.take_keyboard(win, True, rest="on_demand")
        _inline_rename(item, item.info, commit, on_end=lambda: layer.take_keyboard(win, False, rest="on_demand"))

    def _renamed(self, old, new) -> None:
        p = self._placed.get(old)
        if p:
            self._save_positions({new: p})

    def get_info(self) -> None:
        from ..files.quicklook import GetInfo
        for item in self.selection[:5]:
            w = GetInfo(None, item.info)
            w.set_application(self.get_root().get_application())
            w.present()

    def quick_look(self) -> None:
        from ..files.quicklook import QuickLook
        if self.selection:
            ql = QuickLook(None)
            ql.set_application(self.get_root().get_application())
            ql.show_item(self.selection[0].info)
            ql.present()

    def duplicate_selection(self) -> None:
        files = self.selected_files()
        if files:
            ops.Transfer(files, self.dir, duplicate=True)

    def copy_selection(self) -> None:
        if self.selection:
            ops.copy_to_clipboard(self, self.selected_files())

    def paste(self) -> None:
        def got(files, cut):
            if files:
                ops.Transfer(files, self.dir, move=cut)
                if cut:
                    self.get_clipboard().set_content(None)
            else:
                ops.paste_image(self, self.dir,                 # a copied picture becomes a file
                                on_error=lambda e: ui.dialog.alert(
                                    "The picture couldn’t be pasted.", getattr(e, "message", str(e)),
                                    [("ok", "OK", "suggested")]))
        ops.read_clipboard(self, got)

    def new_folder(self, at=None) -> None:
        def spot(child):
            # its spot is saved before it exists: it appears where it was asked
            # for (Vini: it showed in the first free cell, top right, and glided over)
            if at is not None:
                self._save_positions({child.get_basename(): at})

        def done(child):
            GLib.timeout_add(300, lambda: (self._rename_new(child.get_basename()), False)[1])
        ops.new_folder(self.dir, done, lambda _e: None, before=spot)

    def _rename_new(self, name) -> None:
        item = self.items.get(name)
        if item:
            self.select([item])
            self.rename_selection()

    def clean_up(self) -> None:
        """Snap everything into the grid in name order (Finder's Clean Up By Name)."""
        cfg = config.load("desktop", DEFAULTS)
        cfg["positions"], cfg["sort"] = {}, "none"
        config.save("desktop", cfg)

    def set_sort(self, sort: str) -> None:
        cfg = config.load("desktop", DEFAULTS)
        cfg["sort"] = sort
        config.save("desktop", cfg)

    # -- menus ----------------------------------------------------------------------------------
    def item_menu(self, item, x, y) -> None:
        Item = ui.menu.Item
        n = len(self.selection)
        what = f"“{item.info.get_display_name()}”" if n == 1 else f"{n} Items"
        pkg = packages.menu_items(file_of(item.info).get_path()) if n == 1 and item.app is None else []
        # anchored to the desktop, not the icon: the selected icon's styles
        # (blue label...) would otherwise reach into the menu's rows
        ok, pt = item.compute_point(self, Graphene.Point().init(x, y))
        from .. import prefs
        first = pkg + [Item("Open", self.open_selection)]
        if n == 1 and item.app is None and prefs.is_picture(item.info.get_content_type() or ""):
            first.append(Item("Set Desktop Picture", lambda f=file_of(item.info): prefs.set_wallpaper(f.get_uri())))
        ui.menu.popup(self, [
            first,
            [Item("Move to Trash", self.trash_selection)],
            [Item("Get Info", self.get_info), Item("Rename", self.rename_selection, enabled=n == 1),
             Item("Duplicate", self.duplicate_selection)],
            [Item(f"Quick Look {what}", self.quick_look)],
            [Item(f"Copy {what}", self.copy_selection)],
        ], at=(pt.x, pt.y) if ok else (x, y))

    def _background_menu(self, g, _n, x, y) -> None:
        if self.pick(x, y, Gtk.PickFlags.DEFAULT) not in (self, None):
            return
        g.set_state(Gtk.EventSequenceState.CLAIMED)
        self.select([])
        Item = ui.menu.Item
        sort = self.cfg.get("sort", "none")
        cell = self.cell_at(x, y)
        ui.menu.popup(self, [
            [Item("New Folder", lambda: self.new_folder(cell))],      # (New Web App…: Apps' menu only, Vini)
            [Item("Paste Item", self.paste, enabled=ops.clipboard_has_files(self))],
            [Item("Change Desktop Background…", lambda: _open_settings("wallpaper"))],
            [Item("Clean Up", self.clean_up),
             Item("Sort By", submenu=[[Item(label, lambda s=key: self.set_sort(s), checked=sort == key)
                                       for key, label in SORTS]])],
        ], at=(x, y))

    # -- drag and drop --------------------------------------------------------------------------
    def drag_prepare(self, item):
        if item not in self.selection:
            self.select([item])
        self._drag_names = [i.name for i in self.selection]
        files = self.selected_files()
        return ops.file_content(files)            # file list, uri-list and (one picture) its image

    def drag_begin(self, item, drag) -> None:
        for i in self.selection:
            i.add_css_class("dragging")
        paint = item.img.get_paintable() or Gtk.WidgetPaintable.new(item.img)
        self.drag_icon = ui.drag.hang(drag, paint, ICON)
        self._drag_from = self._placed.get(item.name)

    def drag_end(self) -> None:
        for i in self.items.values():
            i.remove_css_class("dragging")
        self.drag_icon = None
        self._drag_names = None

    def _drop(self, target, value, x, y) -> bool:
        files = list(value.get_files())
        col, row = self.cell_at(x, y)
        if self._drag_names:                        # moving our own icons: new spots
            names = self._drag_names
            anchor = self._drag_from or (col, row)
            moved, taken = {}, {v for k, v in self._placed.items() if k not in names}
            for name in names:
                p = self._placed.get(name, anchor)
                c = max(0, min(self.cols() - 1, p[0] + (col - anchor[0])))
                r = max(0, min(self.rows() - 1, p[1] + (row - anchor[1])))
                while (c, r) in taken:                  # occupied: next free cell below
                    r += 1
                    if r >= self.rows():
                        r, c = 0, c + 1
                moved[name] = (c, r)
                taken.add((c, r))
            self._save_positions(moved)
            return True
        here = [f for f in files if f.get_parent() is not None and f.get_parent().equal(self.dir)]
        if here and len(here) == len(files):          # icons from another display's desktop: just move them
            moved, taken = {}, set(self._placed.values())
            c, r = col, row
            for f in here:
                while (c, r) in taken:
                    r += 1
                    if r >= self.rows():
                        r, c = 0, c + 1
                moved[f.get_basename()] = (c, r)
                taken.add((c, r))
            self._save_positions(moved)
            return True
        self._save_positions({f.get_basename(): (col, row) for f in files[:1]})
        return self.drop_into(files, self.dir, _copy(target))

    def drop_into(self, files, dest: Gio.File, copy=False) -> bool:
        files = ops.drop_plan(files, dest)        # same folder / into itself: silently nothing
        if not files:
            return False
        from ..files.window import _same_disk
        move = not copy and all(_same_disk(f, dest) for f in files)
        ops.Transfer(files, dest, move=move)
        return True

    # -- keys -------------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) or (ctrl and keyval == Gdk.KEY_BackSpace):
            self.trash_selection()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_F2):
            self.rename_selection()
        elif keyval == Gdk.KEY_space:
            self.quick_look()
        elif ctrl and keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.select(list(self.items.values()))
        elif ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_selection()
        elif ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.paste()
        elif ctrl and keyval in (Gdk.KEY_o, Gdk.KEY_O, Gdk.KEY_Down):
            self.open_selection()
        elif keyval == Gdk.KEY_Escape:
            self.select([])
        else:
            return False
        return True


def _connected() -> set:
    """Connectors of the displays plugged in now."""
    try:
        from . import monitors
        return {monitors.connector(m) for m in monitors._list()}
    except Exception:                               # no display list (tests, X11): just the main one
        return set()


def _open_settings(page: str) -> None:
    from .topbar import open_settings
    open_settings(page)
