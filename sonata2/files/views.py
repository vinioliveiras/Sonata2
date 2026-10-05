"""The Files views (Finder's View menu): Icons, List, Columns.

Each view shows a Gio.ListModel of Gio.FileInfo (see folder.py) and calls
`on_open(info)` on double-click / Return. Common API:
    widget, selected() -> [FileInfo], select_all(), unselect_all(), focus()
All views are virtualised (only visible rows/cells exist)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import icons, ui  # noqa: E402
from . import folder, tags, thumbs  # noqa: E402
from .folder import is_dir, sort_key  # noqa: E402

ui.register("""
gridview.fs-icons { background: %(content_bg)s; padding: 10px 14px; }
gridview.fs-icons > child { padding: 4px 2px 6px 2px; background: none; border-radius: 0; outline: none; }
gridview.fs-icons > child:selected, gridview.fs-icons > child:focus { background: none; }
gridview.fs-icons .fs-icon { padding: 3px; border-radius: 6px;
  transition: background-color %(t_fast)s, filter %(t_press)s; }
gridview.fs-icons > child:active .fs-icon { filter: brightness(0.75); }   /* Finder darkens a pressed icon */
gridview.fs-icons .fs-name { transition: background-color %(t_fast)s, color %(t_fast)s; }
gridview.fs-icons .fs-name { padding: 1px 4px; border-radius: 4px; font-size: 12px; color: %(label)s; }
gridview.fs-icons > child:selected .fs-icon { background: %(item_selected_bg)s; }
gridview.fs-icons > child:selected .fs-name { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop gridview.fs-icons > child:selected .fs-name { background: %(sidebar_selected)s; color: %(label)s; }
.fs-hidden { opacity: 0.5; }
entry.fs-rename { min-height: 18px; padding: 0 3px; margin: 0; font-size: 12px; border-radius: 3px;
  border: none; background: %(content_bg)s; color: %(label)s;
  box-shadow: inset 0 0 0 1px %(accent)s, 0 0 0 3px alpha(%(accent)s, 0.3); }
.fs-list entry.fs-rename, .fs-col entry.fs-rename { font-size: %(text_body)s; }
.drop-target .fs-icon, .fs-list .drop-target, .fs-col .drop-target { background: alpha(%(accent)s, 0.25);
  border-radius: 6px; }
rubberband { background: alpha(%(accent)s, 0.15); border: 1px solid alpha(%(accent)s, 0.5); }

/* List (Finder list view: zebra rows, small grey headers) */
columnview.fs-list { background: %(content_bg)s; font-size: %(text_body)s; }
columnview.fs-list > header { background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }
columnview.fs-list > header > button { padding: 3px 8px; font-size: %(text_small)s; font-weight: 400;
  color: %(label_secondary)s; background: none; border: none; box-shadow: inset -1px 0 %(separator)s;
  border-radius: 0; }
columnview.fs-list > header > button:hover { background: %(tool_hover)s; }
columnview.fs-list > header > button sort-indicator { color: %(label_secondary)s; -gtk-icon-size: 10px; }
columnview.fs-list > listview { background: %(content_bg)s; }
columnview.fs-list > listview > row { min-height: 24px; padding: 0; background: none; border-radius: 0;
  color: %(label)s; transition: background-color %(t_fast)s, color %(t_fast)s; }
columnview.fs-list > listview > row:nth-child(even) { background: %(row_alt)s; }
columnview.fs-list > listview > row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop columnview.fs-list > listview > row:selected { background: %(sidebar_selected)s; color: %(label)s; }
columnview.fs-list > listview > row > cell { padding: 0 8px; }
columnview.fs-list .fs-dim { color: %(label_secondary)s; }
columnview.fs-list > listview > row:selected .fs-dim { color: inherit; }

/* Columns (Finder column browser) */
.fs-columns { background: %(content_bg)s; }
.fs-col { border-right: 1px solid %(separator)s; }
.fs-col listview { background: %(content_bg)s; padding: 2px 4px; }
.fs-col listview > row { min-height: 22px; padding: 0 6px 0 4px; border-radius: 4px; color: %(label)s;
  background: none; transition: background-color %(t_fast)s, color %(t_fast)s; }
.fs-col listview > row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.fs-col.fs-active listview > row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop .fs-col.fs-active listview > row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.fs-col .fs-chevron { color: %(label_tertiary)s; -gtk-icon-size: 10px; }
.fs-col listview > row:selected .fs-chevron { color: inherit; }
.fs-preview { padding: 24px 16px; }
.fs-preview .fs-preview-name { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.fs-preview .fs-preview-kind { color: %(label_secondary)s; font-size: %(text_small)s; }
.fs-preview .fs-preview-key { color: %(label_secondary)s; font-size: %(text_small)s; }
.fs-preview .fs-preview-val { color: %(label)s; font-size: %(text_small)s; }
""", key="files-views")

SPRING_MS = 900        # spring-loaded folders: hover time before a folder opens
ICON_SIZE = 64
CELL_W = 96
COLUMN_W = 230

# -- formatting (Finder style) --------------------------------------------------------
_kinds = {}


def content_type(info) -> str:
    """The real type; empty files get the type their name suggests."""
    ct = info.get_content_type() or ""
    if ct == "application/x-zerosize":
        ct = Gio.content_type_guess(info.get_display_name(), None)[0] or ct
    return ct


def kind(info) -> str:
    if is_dir(info):
        return "Folder"
    ct = content_type(info)
    if ct not in _kinds:
        _kinds[ct] = Gio.content_type_get_description(ct) if ct else "Document"
        if _kinds[ct]:
            _kinds[ct] = _kinds[ct][:1].upper() + _kinds[ct][1:]
    return _kinds[ct]


def size(info) -> str:
    if is_dir(info):
        n = dir_size(info)
        return ui.fmt.size(n) if n is not None else "--"
    return ui.fmt.size(info.get_size())


# -- folder sizes (Finder: View Options > Calculate all sizes) ----------------------------------
DIR_SIZE = "sonata::dir-size"           # bytes, set once counted ("" while unknown)
_sizer = {"queue": [], "busy": False, "listeners": []}


def dir_size(info):
    """A folder's counted size, or None (not counted / not asked)."""
    v = info.get_attribute_string(DIR_SIZE) if info.has_attribute(DIR_SIZE) else ""
    return int(v) if v and v.isdigit() else None


def count_dir(info, on_done) -> None:
    """Count a folder's size in the background (one at a time, newest asked first);
    on_done(info) on the main loop."""
    f = info.get_attribute_object("sonata::file") if info.has_attribute("sonata::file") else None
    path = f.get_path() if f is not None else None
    if not path or info.has_attribute(DIR_SIZE):
        return
    info.set_attribute_string(DIR_SIZE, "")                     # asked: not again
    _sizer["queue"].append((info, path, on_done))
    if not _sizer["busy"]:
        _sizer["busy"] = True
        import threading
        threading.Thread(target=_count_all, daemon=True).start()


def _count_all():
    import os
    while _sizer["queue"]:
        info, path, on_done = _sizer["queue"].pop()
        total = 0
        for root, _dirs, files in os.walk(path, onerror=lambda e: None):
            for n in files:
                try:
                    total += os.lstat(os.path.join(root, n)).st_size
                except OSError:
                    pass
        GLib.idle_add(lambda i=info, t=total, cb=on_done: (i.set_attribute_string(DIR_SIZE, str(t)), cb(i), False)[2])
    _sizer["busy"] = False


def date(info, attr: str = "time::modified") -> str:
    """"Today at 14:32", "Yesterday at 09:10", "28 Sep 2026 at 10:00"."""
    if attr == "time::modified":
        dt = info.get_modification_date_time()
    else:
        secs = info.get_attribute_uint64(attr) if info.has_attribute(attr) else 0
        dt = GLib.DateTime.new_from_unix_utc(secs) if secs else None
    if dt is None:
        return "--"
    dt = dt.to_local()
    now = GLib.DateTime.new_now_local()
    days = (GLib.Date.new_dmy(now.get_day_of_month(), GLib.DateMonth(now.get_month()), now.get_year()).get_julian()
            - GLib.Date.new_dmy(dt.get_day_of_month(), GLib.DateMonth(dt.get_month()), dt.get_year()).get_julian())
    t = dt.format("%H:%M")
    if days == 0:
        return f"Today at {t}"
    if days == 1:
        return f"Yesterday at {t}"
    return dt.format("%-d %b %Y at %H:%M")


def set_icon(image: Gtk.Image, info, small=False) -> None:
    """The item's icon. small=True (16 px rows): drawn from the 32 px artwork,
    like Finder's full-colour small icons (themes' 16 px folders are outlines)."""
    gicon = info.get_icon()
    app = _launcher_icon(info)
    if app is not None:                         # an app shortcut (.desktop): the app's icon
        gicon = app
    elif info.get_content_type() == "application/x-zerosize":
        gicon = Gio.content_type_get_icon(content_type(info))
    if not gicon:
        gicon = Gio.ThemedIcon.new("text-x-generic")
    if small:
        image.set_from_paintable(icons.paintable(image, gicon, 32))
    else:
        icons.set_image(image, gicon)
    # Images/videos: the picture replaces the icon once its thumbnail is ready
    # (recycled rows: only if the image still shows this item).
    image.thumb_for = info
    if thumbs.wanted(info):
        thumbs.request(info, folder.file_of(info),
                       lambda tex: image.thumb_for is info and image.set_from_paintable(tex))


_launchers = {}      # .desktop path -> (mtime, gicon or None)


def label(info) -> str:
    """The name shown: an app shortcut shows its app's name (like the
    desktop and Finder), everything else its file name."""
    if info.get_name().endswith(".desktop"):
        path = folder.file_of(info).get_path() or ""
        try:
            from ..apps import DesktopAppInfo
            app = DesktopAppInfo.new_from_filename(path)
            if app is not None:
                return app.get_display_name()
        except (TypeError, GLib.Error):
            pass
    return info.get_display_name()


def _launcher_icon(info):
    """The app icon of a .desktop shortcut (like Finder's aliases to apps), or None."""
    if not info.get_name().endswith(".desktop"):
        return None
    path = folder.file_of(info).get_path() or ""
    key = info.get_modification_date_time()
    stamp = key.to_unix() if key else 0
    hit = _launchers.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    gicon = None
    try:
        from ..apps import DesktopAppInfo
        app = DesktopAppInfo.new_from_filename(path)
        gicon = icons.app_icon(app) if app else None
    except (TypeError, GLib.Error):
        pass
    _launchers[path] = (stamp, gicon)
    return gicon


def _hidden(info) -> bool:
    return info.get_is_hidden() or info.get_is_backup()


def _selected(selection, model):
    sel = selection.get_selection()
    return [model.get_item(sel.get_nth(i)) for i in range(sel.get_size())]


# -- shared view behaviour ------------------------------------------------------------------
def _fold(text: str) -> str:
    from gi.repository import GLib
    return GLib.str_to_ascii(text.casefold(), None).casefold()


_ITEM_NODES = ("child", "row", "cell")       # a grid cell, a list row, a column-view cell


def _find_info(w, depth=4):
    info = getattr(w, "info", None)
    if info is not None or depth == 0:
        return info
    c = w.get_first_child()
    while c is not None:
        info = _find_info(c, depth - 1)
        if info is not None:
            return info
        c = c.get_next_sibling()
    return None


def info_under(widget, x, y):
    """The FileInfo of the item at (x, y) of a view's `widget`, or None."""
    w = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
    while w is not None and w is not widget:
        info = getattr(w, "info", None)
        if info is not None:
            return info
        if w.get_css_name() in _ITEM_NODES:
            info = _find_info(w)
            if info is not None:
                return info
        w = w.get_parent()
    return None


class _Cells:
    """What every view offers the window besides showing items: the item
    under the pointer (context menus), selecting a file by name, and
    Finder's in-place rename. Subclasses set model, selection, and call
    _track(box, info) / _untrack(box) from bind/unbind."""

    def _init_cells(self):
        self._cells = {}          # FileInfo -> cell box currently showing it
        self.dnd = None           # the window: files_for_drag(info), drop(files, folder, copy)

    def _dnd_cell(self, box):
        """Folders accept drops, and open when a drag hovers on them
        (spring-loaded). (Dragging starts from the whole list: _dnd_list.)"""
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        tgt.connect("accept", lambda t, d: bool(getattr(box, "info", None)) and is_dir(box.info))
        tgt.connect("enter", lambda t, x, y: self._drag_enter(box))
        tgt.connect("leave", lambda t: self._drag_leave(box))
        tgt.connect("drop", lambda t, v, x, y: self._drop_on(box, t, v))
        box.add_controller(tgt)

    def _dnd_list(self, widget, rubberband=False):
        """Pressing on an item and moving drags the item (with the selection
        it belongs to) -- anywhere on it: icon, name, any column of a row.
        Only a press on empty space starts the rubber band. One drag source
        for the whole list, ahead of the list's own gestures (capture)."""
        self._drag_info = None
        src = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE,
                             propagation_phase=Gtk.PropagationPhase.CAPTURE)
        src.connect("prepare", lambda s_, x, y: self._drag_prepare(widget, x, y))
        src.connect("drag-begin", lambda s_, drag: self._drag_begin(widget, drag))
        src.connect("drag-end", lambda *_: self._drag_end())
        widget.add_controller(src)
        if rubberband:
            press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)

            def pressed(_g, _n, x, y):
                on_item = self.info_at(widget, x, y) is not None
                widget.set_enable_rubberband(not on_item)
            press.connect("pressed", pressed)
            widget.add_controller(press)

    def _drag_prepare(self, widget, x, y):
        picked = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        if picked is not None and (isinstance(picked, Gtk.Editable) or picked.get_ancestor(Gtk.Editable)):
            return None                          # selecting text in the rename field
        info = self.info_at(widget, x, y)
        if info is None or self._dnd() is None:
            return None
        files = self._dnd().files_for_drag(self, info)
        self._drag_info = info
        dnd = self._dnd()
        if hasattr(dnd, "drag_started"):
            dnd.drag_started(files)
        from .ops import file_content
        return file_content(files)

    def _drag_begin(self, widget, drag):
        """The file icon follows the pointer (ui.drag)."""
        info = self._drag_info
        if info is None or self._dnd() is None:
            return
        paint = None
        thumb = info.get_attribute_byte_string("thumbnail::path")
        if thumb:
            try:
                paint = Gdk.Texture.new_from_filename(thumb)
            except GLib.Error:
                paint = None
        if paint is None:
            paint = icons.paintable(widget, info.get_icon() or Gio.ThemedIcon.new("text-x-generic"), 64)
        self._dnd().drag_icon = ui.drag.hang(drag, paint, 64)

    def _drag_end(self):
        self._drag_info = None
        dnd = self._dnd()
        if dnd is not None:
            dnd.drag_icon = None
            if hasattr(dnd, "drag_started"):
                dnd.drag_started(None)

    def _drag_enter(self, box):
        info = getattr(box, "info", None)
        if info is None or not is_dir(info):
            return 0
        dnd = self._dnd()
        if dnd is not None and getattr(dnd, "is_dragged", None) and dnd.is_dragged(folder.file_of(info)):
            return 0                             # the dragged folder itself: no target, no spring
        box.add_css_class("drop-target")
        box._spring = GLib.timeout_add(SPRING_MS, lambda: (self._dnd() and self._dnd().spring_open(info), False)[1])
        return Gdk.DragAction.MOVE

    def _drag_leave(self, box):
        box.remove_css_class("drop-target")
        if getattr(box, "_spring", 0):
            GLib.source_remove(box._spring)
            box._spring = 0

    def _drop_on(self, box, target, value):
        self._drag_leave(box)
        info = getattr(box, "info", None)
        if info is None or self._dnd() is None or not is_dir(info):
            return False
        copy = bool(target.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK) if \
            hasattr(target, "get_current_event_state") else False
        return self._dnd().drop(list(value.get_files()), folder.file_of(info), copy)

    def _dnd(self):
        """The window's drag-and-drop handler (columns ask their browser)."""
        return self.dnd or getattr(getattr(self, "browser", None), "dnd", None)

    def _track(self, box, info):
        box.info = info
        self._cells[info] = box

    def _untrack(self, box):
        info = getattr(box, "info", None)
        if info is not None and self._cells.get(info) is box:
            del self._cells[info]
        box.info = None

    def info_at(self, widget, x, y):
        """The FileInfo under (x, y) of `widget` (None = background). Any
        point of an item counts: a grid cell's padding, a row's gaps."""
        return info_under(widget, x, y)

    def position_of(self, name: str) -> int:
        for i in range(self.model.get_n_items()):
            if self.model.get_item(i).get_name() == name:
                return i
        return -1

    def select_name(self, name: str) -> bool:
        pos = self.position_of(name)
        if pos < 0:
            return False
        self.selection.select_item(pos, True)
        self._scroll_to(pos)
        return True

    def select_prefix(self, prefix: str) -> bool:
        """Type to select (Finder): the first item, in view order, whose name
        starts with `prefix` (case- and accent-insensitive)."""
        key = _fold(prefix)
        for i in range(self.model.get_n_items()):
            if _fold(self.model.get_item(i).get_display_name()).startswith(key):
                self.selection.select_item(i, True)
                self._scroll_to(i)
                return True
        return False

    def ensure_selected(self, info) -> None:
        """Right-click on an unselected item selects just it (Finder)."""
        for i in range(self.model.get_n_items()):
            if self.model.get_item(i) is info:
                if not self.selection.is_selected(i):
                    self.selection.select_item(i, True)
                return

    def begin_rename(self, info, on_commit) -> None:
        """Edit the name in place: the label becomes a field with the name
        (without extension) selected; Return/click elsewhere commits,
        Escape cancels. on_commit(info, new_name)."""
        box = self._cells.get(info)
        if box is None:
            pos = self.position_of(info.get_name())
            if pos >= 0:
                self._scroll_to(pos)
                GLib.timeout_add(80, lambda: (self._cells.get(info) and self.begin_rename(info, on_commit),
                                              False)[1])
            return
        _inline_rename(box, info, on_commit)


def _inline_rename(box, info, on_commit, on_end=None):
    from .ops import rename_selection
    name = info.get_display_name()
    over = getattr(box, "over", None)
    if over is not None:
        # icons: the field over the name's (always two-line) place -- the cell
        # and the grid keep their size (Vini: the grid jumped while renaming)
        entry = Gtk.Entry(text=name, css_classes=["fs-rename"], halign=Gtk.Align.FILL,
                          valign=Gtk.Align.START, width_chars=1, max_width_chars=1)
        box.lbl.set_opacity(0)
        over.add_overlay(entry)
    else:
        entry = Gtk.Entry(text=name, css_classes=["fs-rename"], hexpand=True,
                          halign=box.lbl.get_halign(), width_chars=max(8, min(len(name) + 2, 24)))
        box.lbl.set_visible(False)
        box.insert_child_after(entry, box.lbl)
    done = {"v": False}

    def finish(commit):
        if done["v"]:
            return
        done["v"] = True
        new = entry.get_text().strip()
        if entry.get_parent() is box:
            box.remove(entry)
        elif over is not None and entry.get_parent() is over:
            over.remove_overlay(entry)
        box.lbl.set_visible(True)
        box.lbl.set_opacity(1)
        if on_end is not None:
            on_end()
        if commit and new and new != name:
            on_commit(info, new)
    entry.connect("activate", lambda *_: finish(True))
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", lambda _c, k, *_: (finish(False), True)[1] if k == Gdk.KEY_Escape else False)
    entry.add_controller(keys)
    focus = Gtk.EventControllerFocus()
    focus.connect("leave", lambda *_: GLib.idle_add(lambda: (finish(True), False)[1]))
    entry.add_controller(focus)
    entry.grab_focus()
    start, end = rename_selection(name, is_dir(info))
    entry.select_region(start, end)


# -- Icons --------------------------------------------------------------------------------
class IconsView(_Cells):
    """Icons in a grid, sorted by name unless View > Sort By says otherwise
    (the same order as the list's: one per folder, folderprefs.py)."""

    def __init__(self, model, on_open):
        self._init_cells()
        self._sort = ("Name", False)
        self.sorter = Gtk.CustomSorter.new(lambda a, b, _d: self._compare(a, b))
        self.model = Gtk.SortListModel(model=model, sorter=self.sorter)
        model = self.model
        self.selection = Gtk.MultiSelection(model=model)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        f.connect("unbind", lambda _f, item: self._untrack(item.get_child()))
        self.widget = Gtk.GridView(model=self.selection, factory=f, max_columns=64, min_columns=1,
                                   enable_rubberband=True, css_classes=["fs-icons"])
        self._dnd_list(self.widget, rubberband=True)
        self.widget.connect("activate", lambda _g, pos: on_open(model.get_item(pos)))

    def _setup(self, _f, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, halign=Gtk.Align.CENTER)
        box.set_size_request(CELL_W, -1)
        box.img = Gtk.Image(pixel_size=ICON_SIZE, css_classes=["fs-icon"], halign=Gtk.Align.CENTER)
        box.lbl = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, max_width_chars=12,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, justify=Gtk.Justification.CENTER,
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.START, css_classes=["fs-name"])
        # every cell the same height (Finder's grid): two lines of name are kept
        # (an invisible two-line label sets it) and the tag dots' row too, so a
        # rename or a tag never moves the grid (Vini)
        space = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, can_target=False)
        space.append(Gtk.Label(label="X\nX", css_classes=["fs-name"], opacity=0))
        space.append(tags.dots_box(reserve=True))
        name = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.START)
        box.tags = tags.dots_box()                  # right under the name, in the kept place
        box.tags.set_halign(Gtk.Align.CENTER)
        name.append(box.lbl)
        name.append(box.tags)
        box.over = Gtk.Overlay(child=space)
        box.over.add_overlay(name)
        box.append(box.img)
        box.append(box.over)
        self._dnd_cell(box)
        item.set_child(box)

    def _bind(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info)
        box.lbl.set_label(label(info))
        tags.show(box.tags, tags.of_info(info))
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")
        self._track(box, info)

    def _scroll_to(self, pos):
        self.widget.scroll_to(pos, Gtk.ListScrollFlags.FOCUS, None)

    def _compare(self, a, b) -> int:
        title, desc = self._sort
        c = SORTS.get(title, SORTS["Name"])(a, b)
        return folders_first(a, b) or (-c if desc else c)

    def sort_state(self) -> tuple:
        return self._sort

    def set_sort(self, title: str, descending: bool = False) -> None:
        title = title if title in SORTS else "Name"
        if (title, bool(descending)) != self._sort:
            self._sort = (title, bool(descending))
            self.sorter.changed(Gtk.SorterChange.DIFFERENT)

    def selected(self):
        return _selected(self.selection, self.model)

    def select_all(self):
        self.selection.select_all()

    def unselect_all(self):
        self.selection.unselect_all()

    def focus(self):
        self.widget.grab_focus()

    def scroll_top(self):
        if self.model.get_n_items():
            self.widget.scroll_to(0, Gtk.ListScrollFlags.NONE, None)


# -- List ---------------------------------------------------------------------------------
def _cmp(a, b):
    return (a > b) - (a < b)


def folders_first(a, b) -> int:
    """Folders before files whatever the sort and its direction (Vini)."""
    return _cmp(not is_dir(a), not is_dir(b))


# Sort By (icons) and the list's headers: column title -> compare(a, b), ascending
SORTS = {
    "Name": lambda a, b: _cmp(sort_key(a), sort_key(b)),
    "Kind": lambda a, b: _cmp(kind(a).casefold(), kind(b).casefold()) or _cmp(sort_key(a), sort_key(b)),
    "Date Modified": lambda a, b: _cmp(a.get_attribute_uint64("time::modified"),
                                       b.get_attribute_uint64("time::modified")),
    "Size": lambda a, b: _cmp(_size_key(a), _size_key(b)),
    "Date Created": lambda a, b: _cmp(a.get_attribute_uint64("time::created"), b.get_attribute_uint64("time::created")),
    "Date Last Opened": lambda a, b: _cmp(a.get_attribute_uint64("time::access"), b.get_attribute_uint64("time::access")),
}


def _size_key(info):
    if is_dir(info):
        n = dir_size(info)
        return -1 if n is None else n
    return info.get_size()


# the list's columns: (title, default width, shown at first); Name always shows
LIST_COLUMNS = (("Date Modified", 190, True), ("Date Created", 190, False), ("Date Last Opened", 190, False),
                ("Size", 90, True), ("Kind", 160, True))
# Sort By's direction for each (Finder: newest and biggest first)
SORT_BY = (("Name", False), ("Kind", False), ("Date Modified", True), ("Size", True))


NAME_W = 320
_widths_src = {}


def _column_widths() -> dict:
    from .. import config
    return config.load("files", {"list_columns": {}}).get("list_columns") or {}


def _save_column_width(title, width) -> None:
    """Remembered a moment after the drag (not on every pixel)."""
    if width <= 0:
        return
    if _widths_src.get(title):
        GLib.source_remove(_widths_src[title])

    def save():
        _widths_src[title] = 0
        cols = dict(_column_widths())
        cols[title] = int(width)
        from .. import config
        config.update("files", list_columns=cols)          # every other key kept as stored
        return False
    _widths_src[title] = GLib.timeout_add(400, save)


class ListView(_Cells):
    """Name / Date Modified / Size / Kind, sortable by clicking a header;
    folders always first (Vini), in either direction."""

    def __init__(self, model, on_open):
        self._init_cells()
        self.view = Gtk.ColumnView(show_column_separators=False, show_row_separators=False,
                                   enable_rubberband=True, reorderable=True, css_classes=["fs-list"])
        ui.columns.fill_last(self.view)
        # folders first, then the clicked column's order (its direction never moves them)
        both = Gtk.MultiSorter()
        both.append(Gtk.CustomSorter.new(lambda a, b, _d: folders_first(a, b)))
        both.append(self.view.get_sorter())
        self.sorted = Gtk.SortListModel(model=model, sorter=both)
        self.model = self.sorted
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.view.set_model(self.selection)
        self.view.connect("activate", lambda _v, pos: on_open(self.sorted.get_item(pos)))
        # Finder: every column keeps its width and the last one takes what's
        # left. (Name used to expand: shrinking another column grew Name, so
        # the edge being dragged stayed put, away from the pointer -- the next
        # drag then grabbed Name's header and reordered it instead.) Widths
        # are remembered.
        name = self._column("Name", self._setup_name, self._bind_name, SORTS["Name"], width=NAME_W,
                            unbind=lambda _f, it: self._untrack(it.get_child()))
        attrs = {"Date Modified": "time::modified", "Date Created": "time::created",
                 "Date Last Opened": "time::access"}
        self.columns = {}
        for title, width, _shown in LIST_COLUMNS:
            if title in attrs:
                bind = (lambda a: lambda _f, it: self._bind_text(it, date(it.get_item(), a)))(attrs[title])
                self.columns[title] = self._column(title, self._setup_text, bind, SORTS[title], width=width)
            elif title == "Size":
                self.columns[title] = self._column(title, lambda f, it: self._setup_text(f, it, xalign=1),
                                                   self._bind_size, SORTS["Size"], width=width)
            else:
                self.columns[title] = self._column(title, self._setup_text,
                                                   lambda _f, it: self._bind_text(it, kind(it.get_item())),
                                                   SORTS["Kind"], width=width)
        self.count_sizes = False                # Finder: Calculate all sizes (window: View menu)
        self.show_columns(None)
        self.view.sort_by_column(name, Gtk.SortType.ASCENDING)
        self.on_sort = None                     # callback((title, descending)) when you click a header
        self._sorting = False
        self.view.get_sorter().connect("changed", self._sort_changed)
        self.widget = self.view
        self._dnd_list(self.view, rubberband=True)

    # -- sort (remembered per folder: folderprefs.py) ----------------------------------
    def sort_state(self) -> tuple:
        """(column title, descending) of the sort in effect."""
        s = self.view.get_sorter()
        col = s.get_primary_sort_column()
        return ((col.get_title() if col else "Name"),
                s.get_primary_sort_order() == Gtk.SortType.DESCENDING)

    def set_sort(self, title: str, descending: bool = False) -> None:
        """Sort by a column (an unknown title: Name), without reporting it as your choice."""
        cols = self.view.get_columns()
        col = next((cols.get_item(i) for i in range(cols.get_n_items())
                    if cols.get_item(i).get_title() == title), None) or cols.get_item(0)
        if self.sort_state() == (col.get_title(), bool(descending)):
            return
        self._sorting = True
        try:
            self.view.sort_by_column(col, Gtk.SortType.DESCENDING if descending else Gtk.SortType.ASCENDING)
        finally:
            self._sorting = False

    def _sort_changed(self, _sorter, _change):
        if not self._sorting and self.on_sort:
            self.on_sort(self.sort_state())

    def _column(self, title, setup, bind, cmp=None, expand=False, width=-1, unbind=None):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", setup)
        f.connect("bind", bind)
        if unbind:
            f.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=True,
                                   sorter=Gtk.CustomSorter.new(lambda a, b, _d: cmp(a, b)))
        saved = _column_widths().get(title)
        width = saved if isinstance(saved, int) and saved > 0 else width
        if width > 0:
            col.set_fixed_width(width)
        col.connect("notify::fixed-width", lambda c, _p: _save_column_width(c.get_title(), c.get_fixed_width()))
        self.view.append_column(col)
        return col

    def _setup_name(self, _f, item):
        box = Gtk.Box(spacing=6)
        box.img = Gtk.Image(pixel_size=16)
        box.lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        box.tags = tags.dots_box()
        box.append(box.img)
        box.append(box.lbl)
        box.append(box.tags)
        self._dnd_cell(box)
        item.set_child(box)

    def _bind_name(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info, small=True)
        box.lbl.set_label(label(info))
        tags.show(box.tags, tags.of_info(info))
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")
        self._track(box, info)

    def _scroll_to(self, pos):
        self.view.scroll_to(pos, None, Gtk.ListScrollFlags.FOCUS, None)

    def _setup_text(self, _f, item, xalign=0):
        item.set_child(Gtk.Label(xalign=xalign, ellipsize=Pango.EllipsizeMode.END, css_classes=["fs-dim"]))

    def _bind_text(self, item, text):
        lbl = item.get_child()
        lbl.set_label(text)
        lbl.info = item.get_item()                 # right-click anywhere on the row

    def show_columns(self, shown) -> None:
        """Which columns show (None: the defaults); Name always does."""
        for title, _w, default in LIST_COLUMNS:
            self.columns[title].set_visible(default if shown is None else title in shown)

    def shown_columns(self) -> list:
        return [t for t, _w, _d in LIST_COLUMNS if self.columns[t].get_visible()]

    def _bind_size(self, _f, item):
        info = item.get_item()
        self._bind_text(item, size(info))
        if self.count_sizes and is_dir(info) and not info.has_attribute(DIR_SIZE):
            lbl = item.get_child()
            count_dir(info, lambda i: getattr(lbl, "info", None) is i and lbl.set_label(size(i)))

    def selected(self):
        return _selected(self.selection, self.sorted)

    def select_all(self):
        self.selection.select_all()

    def unselect_all(self):
        self.selection.unselect_all()

    def focus(self):
        self.view.grab_focus()

    def scroll_top(self):
        if self.sorted.get_n_items():
            self.view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)


# -- Columns ------------------------------------------------------------------------------
class _Column(Gtk.ScrolledWindow, _Cells):
    def __init__(self, browser, model, owner=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["fs-col"])
        self._init_cells()
        self.set_size_request(COLUMN_W, -1)
        self.browser, self.model, self.owner = browser, model, owner    # owner: its Folder
        self.selection = Gtk.SingleSelection(model=model, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", lambda *_: browser._picked(self))
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        f.connect("unbind", lambda _f, item: self._untrack(item.get_child()))
        self.list = Gtk.ListView(model=self.selection, factory=f)
        self.list.connect("activate", lambda _l, pos: browser.on_open(model.get_item(pos)))
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: browser._activate_column(self))
        self.list.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: browser._key(self, k))
        self.list.add_controller(keys)
        self.set_child(self.list)
        self._dnd_list(self.list)
        bg = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        bg.connect("drop", self._drop_here)         # on the column's empty space: into its folder
        self.add_controller(bg)

    def _drop_here(self, target, value, _x, _y):
        dnd = self._dnd()
        uri = self.owner.uri if self.owner else self.browser.root_uri
        if dnd is None or not uri:
            return False
        copy = bool(target.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)
        return dnd.drop(list(value.get_files()), Gio.File.new_for_uri(uri), copy)

    def _setup(self, _f, item):
        box = Gtk.Box(spacing=6)
        box.img = Gtk.Image(pixel_size=16)
        box.lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        box.chev = Gtk.Image(icon_name="go-next-symbolic", css_classes=["fs-chevron"])
        box.tags = tags.dots_box()
        for w in (box.img, box.lbl, box.tags, box.chev):
            box.append(w)
        self._dnd_cell(box)
        item.set_child(box)

    def _bind(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info, small=True)
        box.lbl.set_label(label(info))
        tags.show(box.tags, tags.of_info(info))
        box.chev.set_visible(is_dir(info))
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")
        self._track(box, info)

    def _scroll_to(self, pos):
        self.list.scroll_to(pos, Gtk.ListScrollFlags.FOCUS, None)

    def picked(self):
        return self.selection.get_selected_item()


class ColumnsView:
    """Finder column browser: the first column is the window's folder;
    picking a folder opens its column to the right, picking a file shows a
    preview column. on_location(uri) reports the deepest open folder."""

    def __init__(self, model, on_open, on_location, show_hidden):
        self.on_open, self.on_location, self.show_hidden = on_open, on_location, show_hidden
        self.box = Gtk.Box(css_classes=["fs-columns"])
        self.scroller = Gtk.ScrolledWindow(child=self.box, vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.widget = self.scroller
        self.columns = [_Column(self, model)]
        self.box.append(self.columns[0])
        self.preview = None
        self.root_uri = None

    def reset(self, root_uri):
        """The window's folder changed: back to one column."""
        self.root_uri = root_uri
        self._truncate(0)
        self.columns[0].selection.unselect_all()

    def _truncate(self, keep_index):
        for col in self.columns[keep_index + 1:]:
            if col.owner:
                col.owner.cancel()
            self.box.remove(col)
        del self.columns[keep_index + 1:]
        if self.preview:
            self.box.remove(self.preview)
            self.preview = None

    def _picked(self, col):
        i = self.columns.index(col)
        self._truncate(i)
        self._activate_column(col)
        info = col.picked()
        if info is None:
            self.on_location(self.columns[i].owner.uri if i else self.root_uri)
            return
        if is_dir(info):
            uri = info.get_attribute_string("standard::target-uri") or folder.file_of(info).get_uri()
            f = folder.Folder(lambda _u: None, lambda _u, _e: None)
            f.show_hidden = self.show_hidden()
            f.load(uri)
            new = _Column(self, f.store, f)
            self.columns.append(new)
            self.box.append(new)
            self.on_location(uri)
        else:
            self.preview = _preview(info)
            self.box.append(self.preview)
            self.on_location(self.columns[i].owner.uri if i else self.root_uri)
        GLib.idle_add(self._scroll_end)

    def _key(self, col, keyval) -> bool:
        """Right: into the open folder's column; Left: back to the parent column."""
        i = self.columns.index(col)
        if keyval == Gdk.KEY_Right and i + 1 < len(self.columns):
            nxt = self.columns[i + 1]
            if nxt.model.get_n_items() and nxt.selection.get_selected() == Gtk.INVALID_LIST_POSITION:
                nxt.selection.set_selected(0)
            nxt.list.grab_focus()
            return True
        if keyval == Gdk.KEY_Left and i > 0:
            col.selection.unselect_all()
            self.columns[i - 1].list.grab_focus()
            return True
        return False

    def _scroll_end(self):
        adj = self.scroller.get_hadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _activate_column(self, col):
        for c in self.columns:
            (c.add_css_class if c is col else c.remove_css_class)("fs-active")

    def _active(self):
        return next((c for c in self.columns if c.has_css_class("fs-active")), self.columns[0])

    def selected(self):
        info = self._active().picked()
        return [info] if info else []

    # the window's item actions act on the active column
    def info_at(self, widget, x, y):
        return _Cells.info_at(self, widget, x, y)

    def ensure_selected(self, info):
        for c in self.columns:
            if info in c._cells:
                c.ensure_selected(info)
                self._activate_column(c)
                return

    def select_name(self, name):
        return self._active().select_name(name)

    def select_prefix(self, prefix):
        return self._active().select_prefix(prefix)

    def begin_rename(self, info, on_commit):
        for c in self.columns:
            if info in c._cells:
                c.begin_rename(info, on_commit)
                return
        self._active().begin_rename(info, on_commit)

    def location(self):
        """Folder the active column shows (paste / new folder go there)."""
        col = self._active()
        return col.owner.uri if col.owner else self.root_uri

    def select_all(self):
        pass

    def unselect_all(self):
        self.columns[0].selection.unselect_all()

    def focus(self):
        self._active().list.grab_focus()

    def scroll_top(self):
        pass


def _preview(info) -> Gtk.Widget:
    """Finder's preview column: big icon, name, kind + size, dates."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["fs-preview"])
    box.set_size_request(COLUMN_W + 40, -1)
    img = Gtk.Image(pixel_size=128, margin_bottom=10)
    set_icon(img, info)
    box.append(img)
    box.append(Gtk.Label(label=label(info), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                         justify=Gtk.Justification.CENTER, css_classes=["fs-preview-name"]))
    box.append(Gtk.Label(label=f"{kind(info)} – {size(info)}", css_classes=["fs-preview-kind"]))
    grid = Gtk.Grid(column_spacing=8, row_spacing=2, margin_top=14, halign=Gtk.Align.CENTER)
    for r, (k, v) in enumerate((("Modified", date(info)),)):
        grid.attach(Gtk.Label(label=k, xalign=1, css_classes=["fs-preview-key"]), 0, r, 1, 1)
        grid.attach(Gtk.Label(label=v, xalign=0, css_classes=["fs-preview-val"]), 1, r, 1, 1)
    box.append(grid)
    return box
