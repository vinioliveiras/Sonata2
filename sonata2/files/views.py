"""The Files views (Finder's View menu): Icons, List, Columns.

Each view shows a Gio.ListModel of Gio.FileInfo (see folder.py) and calls
`on_open(info)` on double-click / Return. Common API:
    widget, selected() -> [FileInfo], select_all(), unselect_all(), focus()
All views are virtualised (only visible rows/cells exist)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from .. import icons, ui  # noqa: E402
from . import folder  # noqa: E402
from .folder import is_dir, sort_key  # noqa: E402

ui.register("""
gridview.fs-icons { background: %(content_bg)s; padding: 10px 14px; }
gridview.fs-icons > child { padding: 4px 2px 6px 2px; background: none; border-radius: 0; outline: none; }
gridview.fs-icons > child:selected, gridview.fs-icons > child:focus { background: none; }
gridview.fs-icons .fs-icon { padding: 3px; border-radius: 6px; }
gridview.fs-icons .fs-name { padding: 1px 4px; border-radius: 4px; font-size: 12px; color: %(label)s; }
gridview.fs-icons > child:selected .fs-icon { background: %(item_selected_bg)s; }
gridview.fs-icons > child:selected .fs-name { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop gridview.fs-icons > child:selected .fs-name { background: %(sidebar_selected)s; color: %(label)s; }
.fs-hidden { opacity: 0.5; }
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
  color: %(label)s; }
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
  background: none; }
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
    """Finder style: "Zero bytes", "653 bytes", "12 KB", "1.4 MB" (decimal units)."""
    if is_dir(info):
        return "--"
    n = info.get_size()
    if n == 0:
        return "Zero bytes"
    if n < 1000:
        return f"{n} bytes"
    if n < 1000 ** 2:
        return f"{round(n / 1000)} KB"
    for unit, p in (("MB", 2), ("GB", 3), ("TB", 4)):
        if n < 1000 ** (p + 1) or unit == "TB":
            return f"{n / 1000 ** p:.1f} {unit}"


def date(info) -> str:
    """"Today at 14:32", "Yesterday at 09:10", "28 Sep 2026 at 10:00"."""
    dt = info.get_modification_date_time()
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
    if info.get_content_type() == "application/x-zerosize":
        gicon = Gio.content_type_get_icon(content_type(info))
    if not gicon:
        gicon = Gio.ThemedIcon.new("text-x-generic")
    if small:
        image.set_from_paintable(icons.paintable(image, gicon, 32))
    else:
        icons.set_image(image, gicon)


def _hidden(info) -> bool:
    return info.get_is_hidden() or info.get_is_backup()


def _selected(selection, model):
    sel = selection.get_selection()
    return [model.get_item(sel.get_nth(i)) for i in range(sel.get_size())]


# -- Icons --------------------------------------------------------------------------------
class IconsView:
    def __init__(self, model, on_open):
        self.model = model
        self.selection = Gtk.MultiSelection(model=model)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        self.widget = Gtk.GridView(model=self.selection, factory=f, max_columns=64, min_columns=1,
                                   enable_rubberband=True, css_classes=["fs-icons"])
        self.widget.connect("activate", lambda _g, pos: on_open(model.get_item(pos)))

    def _setup(self, _f, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, halign=Gtk.Align.CENTER)
        box.set_size_request(CELL_W, -1)
        box.img = Gtk.Image(pixel_size=ICON_SIZE, css_classes=["fs-icon"], halign=Gtk.Align.CENTER)
        box.lbl = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, max_width_chars=12,
                            ellipsize=Pango.EllipsizeMode.MIDDLE, justify=Gtk.Justification.CENTER,
                            halign=Gtk.Align.CENTER, css_classes=["fs-name"])
        box.append(box.img)
        box.append(box.lbl)
        item.set_child(box)

    def _bind(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info)
        box.lbl.set_label(info.get_display_name())
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")

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


class ListView:
    """Name / Date Modified / Size / Kind, sortable by clicking a header
    (Finder: folders are sorted with the files)."""

    def __init__(self, model, on_open):
        self.view = Gtk.ColumnView(show_column_separators=False, show_row_separators=False,
                                   enable_rubberband=True, reorderable=True, css_classes=["fs-list"])
        self.sorted = Gtk.SortListModel(model=model, sorter=self.view.get_sorter())
        self.model = self.sorted
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.view.set_model(self.selection)
        self.view.connect("activate", lambda _v, pos: on_open(self.sorted.get_item(pos)))
        name = self._column("Name", self._setup_name, self._bind_name,
                            lambda a, b: _cmp(sort_key(a), sort_key(b)), expand=True)
        self._column("Date Modified", self._setup_text, lambda _f, it: self._bind_text(it, date(it.get_item())),
                     lambda a, b: _cmp(a.get_attribute_uint64("time::modified"),
                                       b.get_attribute_uint64("time::modified")), width=190)
        self._column("Size", lambda f, it: self._setup_text(f, it, xalign=1),
                     lambda _f, it: self._bind_text(it, size(it.get_item())),
                     lambda a, b: _cmp(-1 if is_dir(a) else a.get_size(), -1 if is_dir(b) else b.get_size()),
                     width=90)
        self._column("Kind", self._setup_text, lambda _f, it: self._bind_text(it, kind(it.get_item())),
                     lambda a, b: _cmp(kind(a).casefold(), kind(b).casefold()) or _cmp(sort_key(a), sort_key(b)),
                     width=160)
        self.view.sort_by_column(name, Gtk.SortType.ASCENDING)
        self.widget = self.view

    def _column(self, title, setup, bind, cmp, expand=False, width=-1):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", setup)
        f.connect("bind", bind)
        col = Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=True,
                                   sorter=Gtk.CustomSorter.new(lambda a, b, _d: cmp(a, b)))
        if width > 0:
            col.set_fixed_width(width)
        self.view.append_column(col)
        return col

    def _setup_name(self, _f, item):
        box = Gtk.Box(spacing=6)
        box.img = Gtk.Image(pixel_size=16)
        box.lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        box.append(box.img)
        box.append(box.lbl)
        item.set_child(box)

    def _bind_name(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info, small=True)
        box.lbl.set_label(info.get_display_name())
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")

    def _setup_text(self, _f, item, xalign=0):
        item.set_child(Gtk.Label(xalign=xalign, ellipsize=Pango.EllipsizeMode.END, css_classes=["fs-dim"]))

    def _bind_text(self, item, text):
        item.get_child().set_label(text)

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
class _Column(Gtk.ScrolledWindow):
    def __init__(self, browser, model, owner=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["fs-col"])
        self.set_size_request(COLUMN_W, -1)
        self.browser, self.model, self.owner = browser, model, owner    # owner: its Folder
        self.selection = Gtk.SingleSelection(model=model, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", lambda *_: browser._picked(self))
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        self.list = Gtk.ListView(model=self.selection, factory=f)
        self.list.connect("activate", lambda _l, pos: browser.on_open(model.get_item(pos)))
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: browser._activate_column(self))
        self.list.add_controller(focus)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: browser._key(self, k))
        self.list.add_controller(keys)
        self.set_child(self.list)

    def _setup(self, _f, item):
        box = Gtk.Box(spacing=6)
        box.img = Gtk.Image(pixel_size=16)
        box.lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        box.chev = Gtk.Image(icon_name="go-next-symbolic", css_classes=["fs-chevron"])
        for w in (box.img, box.lbl, box.chev):
            box.append(w)
        item.set_child(box)

    def _bind(self, _f, item):
        info, box = item.get_item(), item.get_child()
        set_icon(box.img, info, small=True)
        box.lbl.set_label(info.get_display_name())
        box.chev.set_visible(is_dir(info))
        (box.add_css_class if _hidden(info) else box.remove_css_class)("fs-hidden")

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
        from gi.repository import Gdk
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
    box.append(Gtk.Label(label=info.get_display_name(), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                         justify=Gtk.Justification.CENTER, css_classes=["fs-preview-name"]))
    box.append(Gtk.Label(label=f"{kind(info)} – {size(info)}", css_classes=["fs-preview-kind"]))
    grid = Gtk.Grid(column_spacing=8, row_spacing=2, margin_top=14, halign=Gtk.Align.CENTER)
    for r, (k, v) in enumerate((("Modified", date(info)),)):
        grid.attach(Gtk.Label(label=k, xalign=1, css_classes=["fs-preview-key"]), 0, r, 1, 1)
        grid.attach(Gtk.Label(label=v, xalign=0, css_classes=["fs-preview-val"]), 1, r, 1, 1)
    box.append(grid)
    return box
