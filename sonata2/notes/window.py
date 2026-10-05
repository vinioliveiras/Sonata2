"""Notes (macOS Ventura Notes, with Reminders in the same window).

Three panes: the sidebar (translucent) lists the Notes folders -- All
Notes, Notes, your folders, Recently Deleted -- and the Reminders lists
(Today, Scheduled, All, Completed, your lists); the middle pane lists the
folder's notes (pinned first, then by date edited, in date sections:
title, date and first line); the editor shows the selected note (the
first line is its title; see editor.py). A reminders list takes both
panes (reminders.py).

Notes save themselves half a second after you stop typing; an empty note
goes away when you leave it. Deleted notes stay in Recently Deleted for
30 days (right-click: Recover). Right-click a folder or list: New,
Rename, Delete. Search (toolbar) looks through every note (or reminder).

Keyboard (⌘ is Ctrl or Super): N new note (new reminder in Reminders),
Shift+N new folder, F search, B / I / U bold / italic / underline,
Shift+T title, Shift+H heading, Shift+J subheading, Shift+B body,
Shift+L checklist, Delete deletes the selected note (or reminder), Z
after a delete brings the note back, W closes.

Data: notes.json and reminders.json in ~/.local/share/sonata2-data/notes
(store.py)."""
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gio, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import markup  # noqa: E402
from . import store as S  # noqa: E402
from .editor import NoteEditor, format_date  # noqa: E402
from .reminders import SMART_INFO, Notifier, RemindersView  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.notes"
DEFAULTS = {"sidebar_width": 200, "list_width": 270, "selected": "n:all"}
SAVE_DELAY = 500      # ms after the last keystroke

ui.register("""
window.sonata-notes { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.nt-paned > separator { min-width: 1px; background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.nt-sidebar list { background: none; padding: 4px 10px 10px 10px; }
.nt-sidebar list row { min-height: 28px; padding: 0 6px; border-radius: %(r_menu)s; background: none;
  color: %(label)s; transition: background-color %(t_fast)s; }
.nt-sidebar list row:hover { background: none; }
.nt-sidebar list row:active { background: %(tool_hover)s; }
.nt-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.nt-sidebar list row.nt-head { min-height: 22px; margin-top: 10px; }
.nt-sidebar list row.nt-head:first-child { margin-top: 0; }
.nt-sidebar .nt-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.nt-sidebar image.nt-folder { color: %(sys_orange)s; }
.nt-sidebar .nt-count { color: %(label_secondary)s; font-size: %(text_body)s; }
.nt-sidebar .nt-badge { min-width: 20px; min-height: 20px; border-radius: 99px; color: %(label_on_accent)s; }
.nt-sidebar editablelabel text { background: %(content_bg)s; border-radius: 3px;
  box-shadow: 0 0 0 2px alpha(%(accent)s, 0.5); }
.nt-sidebar-bottom { padding: 6px 12px 10px 12px; }
.nt-sidebar-bottom button { background: none; border: none; box-shadow: none; padding: 2px 4px;
  color: %(label_secondary)s; border-radius: %(r_button)s; transition: background-color %(t_fast)s; }
.nt-sidebar-bottom button:hover { color: %(label)s; }
.nt-sidebar-bottom button:active { background: %(tool_hover)s; transition: background-color %(t_press)s; }
.nt-list-pane, .nt-editor-pane { background: %(content_bg)s; }
.nt-notes { background: none; padding: 0 8px 10px 8px; }
.nt-notes > row { padding: 0; border-radius: %(r_menu)s; background: none; margin: 0;
  transition: background-color %(t_fast)s; }
.nt-notes > row:selected { background: %(sidebar_selected)s; }
.nt-notes:focus-within > row:selected { background: alpha(%(sys_orange)s, 0.38); }
.nt-notes > header { padding: 14px 10px 4px 10px; font-weight: 700; font-size: %(text_title)s;
  color: %(label)s; }
.nt-note-row { padding: 9px 10px 10px 12px; box-shadow: inset 0 -1px %(separator)s; }
.nt-notes > row:selected .nt-note-row { box-shadow: none; }
.nt-note-title { font-weight: 700; }
.nt-note-date { color: %(label)s; }
.nt-note-preview { color: %(label_secondary)s; }
.nt-note-folder, .nt-note-folder image { color: %(label_secondary)s; font-size: %(text_small)s; }
.nt-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
.nt-fmt-row { padding: 2px 6px 6px 6px; }
.nt-fmt-row button { min-width: 44px; min-height: 26px; padding: 0; border-radius: %(r_button)s; border: none;
  background: %(control_off)s; box-shadow: none; color: %(label)s; transition: background-color %(t_fast)s; }
.nt-fmt-row button:checked { background: %(sys_orange)s; color: %(label_on_accent)s; }
.nt-fmt-row button:active { filter: brightness(0.9); transition: filter %(t_press)s; }
.panel-row label.nt-fmt-title { font-weight: 700; font-size: calc(%(text_title)s * 1.4); }
.panel-row label.nt-fmt-heading { font-weight: 700; font-size: calc(%(text_title)s * 1.15); }
.panel-row label.nt-fmt-subheading { font-weight: 700; }
.sonata-toolbar entry.nt-search { min-height: 24px; min-width: 180px; border-radius: 6px; margin-left: 6px; }
""", key="notes-window")


class NoteItem(GObject.Object):
    """A row of the notes list."""
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, note: dict):
        super().__init__()
        self.note = note
        self.refresh()

    def refresh(self, now: float = None) -> None:
        n = self.note
        self.title, self.preview = markup.title_and_preview(n.get("body", ""))
        self.modified = n.get("modified", 0)
        self.pinned = bool(n.get("pinned")) and not n.get("deleted")
        self.section = section_of(self.modified, self.pinned, now)
        self.date = short_date(self.modified, now)
        self.emit("changed")


# moved to ui.fmt (shared with the Assistant's conversation list)
section_of, short_date = ui.fmt.date_section, ui.fmt.short_date


def _cmp_sections(a, b, *_u) -> int:
    x, y = a.section[:2], b.section[:2]
    return (x > y) - (x < y)


def _cmp_items(a, b, *_u) -> int:
    x, y = (a.section[:2], -a.modified), (b.section[:2], -b.modified)
    return (x > y) - (x < y)


class SideRow(Gtk.ListBoxRow):
    """A sidebar row: a folder, a smart list or a list (key "n:<id>" / "r:<id>")."""

    def __init__(self, key: str, name: str, icon: str, badge: str = None, editable: bool = False):
        super().__init__()
        self.key = key
        box = Gtk.Box(spacing=8)
        if badge:
            img = Gtk.Image(icon_name=icon, pixel_size=12, css_classes=["nt-badge", f"bg-{badge}"],
                            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        else:
            img = Gtk.Image(icon_name=icon, pixel_size=16, css_classes=["nt-folder"])
        box.append(img)
        if editable:
            self.name = Gtk.EditableLabel(text=name, hexpand=True)
        else:
            self.name = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.append(self.name)
        self.count = Gtk.Label(css_classes=["nt-count"])
        box.append(self.count)
        self.set_child(box)

    def set_count(self, n: int) -> None:
        self.count.set_label(str(n) if n else "")


def _head(text: str) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["nt-head"], can_focus=False)
    row.key = None
    row.set_child(Gtk.Label(label=text, xalign=0))
    return row


class NotesWindow(Gtk.ApplicationWindow):
    def __init__(self, app, store: S.Store = None):
        super().__init__(application=app, title="Notes")
        ui.window.remember_size(self, "notes", 1060, 640)  # its last size (never bigger than the display)
        self.add_css_class("sonata-notes")
        self.set_size_request(640, 380)
        ui.window.standard(self)
        self.cfg = config.load("notes", DEFAULTS)
        self.store = store or S.Store()
        self.notifier = Notifier(self.store)
        self.key = None              # sidebar selection
        self.query = ""
        self.note = None             # the note in the editor
        self._save_id = 0
        self._undo = None            # the last deleted note (⌘Z)
        self._items = {}             # note id -> NoteItem
        self._syncing = False

        self.toolbar = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Show Sidebar", self.toggle_sidebar),), end=(
            ("user-trash-symbolic", "Delete", self.delete_selected),
            ("document-edit-symbolic", "New Note", self.new_note),
            ("format-text-rich-symbolic", "Format", self.format_panel),
            ("checkbox-checked-symbolic", "Make a Checklist", lambda: self._style("check")),
            ("list-add-symbolic", "New Reminder", self.new_reminder)))
        tools = self.toolbar.get_child().get_end_widget()
        btns = []
        child = tools.get_first_child()
        while child is not None:
            btns.append(child)
            child = child.get_next_sibling()
        self.delete_btn, self.new_btn, self.format_btn, self.check_btn, self.rem_btn = btns
        self.search = Gtk.SearchEntry(placeholder_text="Search", css_classes=["nt-search"])
        self.search.connect("search-changed", lambda e: self.set_query(e.get_text()))
        self.search.connect("stop-search", lambda e: (e.set_text(""), self._focus_content()))
        tools.append(self.search)

        self.sidebar = self._sidebar()
        self.outer = Gtk.Paned(start_child=self.sidebar, shrink_start_child=False, resize_start_child=False,
                               css_classes=["nt-paned"], vexpand=True)
        self.outer.set_position(ui.window.SIDEBAR_W)       # the apps' one sidebar width (not remembered)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.inner = Gtk.Paned(start_child=self._list_pane(), end_child=self._editor_pane(),
                               shrink_start_child=False, resize_start_child=False, shrink_end_child=False,
                               css_classes=["nt-paned"])
        self.inner.set_position(self.cfg["list_width"])
        self.stack.add_named(self.inner, "notes")
        self.reminders = RemindersView(self.store, self._reminders_changed)
        self.stack.add_named(self.reminders, "reminders")
        self.outer.set_end_child(self.stack)
        self.outer.set_shrink_end_child(False)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        col.append(self.outer)
        self.set_child(col)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)
        self.rebuild_sidebar()
        self.select(self.cfg["selected"] if self._row(self.cfg["selected"]) else "n:all")
        GLib.idle_add(lambda: (self._focus_content(), False)[1])

    # -- sidebar ------------------------------------------------------------------------------------
    def _sidebar(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["nt-sidebar", "sonata-sidebar"])
        box.set_size_request(160, -1)
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.connect("row-selected", self._side_selected)
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", self._side_menu)
        self.side.add_controller(menu)
        box.append(Gtk.ScrolledWindow(child=self.side, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        bottom = Gtk.Box(css_classes=["nt-sidebar-bottom"])
        self.add_btn = Gtk.Button(can_focus=False)
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image(icon_name="list-add-symbolic"))
        self.add_label = Gtk.Label(label="New Folder")
        inner.append(self.add_label)
        self.add_btn.set_child(inner)
        self.add_btn.connect("clicked", lambda *_: self.new_folder() if self._notes_mode() else self.new_list())
        bottom.append(self.add_btn)
        box.append(bottom)
        return box

    def rebuild_sidebar(self) -> None:
        self._syncing = True
        self.side.remove_all()
        st = self.store
        self.side.append(_head("Notes"))
        self.side.append(SideRow("n:" + S.ALL, "All Notes", "folder-symbolic"))
        for f in st.folders:
            self.side.append(SideRow("n:" + f["id"], f["name"], "folder-symbolic",
                                     editable=f["id"] != S.DEFAULT_FOLDER))
        if any(n.get("deleted") for n in st.notes):
            self.side.append(SideRow("n:" + S.DELETED, "Recently Deleted", "user-trash-symbolic"))
        self.side.append(_head("Reminders"))
        for k in S.SMART:
            name, icon, color = SMART_INFO[k]
            self.side.append(SideRow("r:" + k, name, icon, badge=color))
        for x in st.lists:
            self.side.append(SideRow("r:" + x["id"], x["name"], "view-list-bullet-symbolic",
                                     badge=x.get("color", "blue"), editable=True))
        for row in self._side_rows():
            if isinstance(row.name, Gtk.EditableLabel):
                row.name.connect("notify::editing", self._renamed, row)
        self.update_counts()
        if self.key and self._row(self.key):
            self.side.select_row(self._row(self.key))
        self._syncing = False

    def _side_rows(self):
        child = self.side.get_first_child()
        while child is not None:
            if isinstance(child, SideRow):
                yield child
            child = child.get_next_sibling()

    def _row(self, key):
        return next((r for r in self._side_rows() if r.key == key), None)

    def update_counts(self) -> None:
        st = self.store
        for row in self._side_rows():
            kind, ident = row.key.split(":", 1)
            row.set_count(st.folder_count(ident) if kind == "n" else len(st.reminders_in(ident)))

    def select(self, key: str) -> None:
        row = self._row(key)
        if row is not None:
            self.side.select_row(row)

    def _side_selected(self, _lb, row) -> None:
        if row is None or self._syncing or row.key == self.key:
            return
        self._flush()
        self.key = row.key
        config.update("notes", selected=row.key)
        notes = self._notes_mode()
        self.stack.set_visible_child_name("notes" if notes else "reminders")
        for b in (self.format_btn, self.check_btn, self.new_btn):
            b.set_visible(notes)
        self.rem_btn.set_visible(not notes)
        self.add_label.set_label("New Folder" if notes else "Add List")
        self.search.set_placeholder_text("Search" if notes else "Search Reminders")
        if notes:
            self.reload_notes()
        else:
            self.reminders.show(self.key[2:], self.query)

    def _notes_mode(self) -> bool:
        return (self.key or "n:").startswith("n:")

    def _folder(self) -> str:
        return self.key[2:] if self._notes_mode() else S.ALL

    def _side_menu(self, gest, _n, x, y) -> None:
        row = self.side.get_row_at_y(int(y))
        key = getattr(row, "key", None) if row else None
        if key is None:
            in_notes = row is None or row.get_index() < (self._row("r:today").get_index() if self._row("r:today")
                                                          else 1 << 30)
            sections = [[ui.menu.Item("New Folder", self.new_folder)]] if in_notes else \
                [[ui.menu.Item("Add List", self.new_list)]]
            ui.menu.popup(self.side, sections, at=(x, y), glass=True, passthrough=True)
            return
        kind, ident = key.split(":", 1)
        st = self.store
        if kind == "n":
            f = st.folder(ident)
            deleted = ident == S.DELETED
            sections = [[ui.menu.Item("New Folder", self.new_folder)],
                        [ui.menu.Item("Rename Folder", lambda: row.name.start_editing(),
                                      enabled=bool(f) and ident != S.DEFAULT_FOLDER),
                         ui.menu.Item("Delete Folder", lambda: self.delete_folder(f),
                                      enabled=bool(f) and ident != S.DEFAULT_FOLDER)]]
            if deleted:
                sections = [[ui.menu.Item("Empty Recently Deleted", self.empty_deleted)]]
        else:
            x_ = st.rlist(ident)
            sections = [[ui.menu.Item("Add List", self.new_list)],
                        [ui.menu.Item("Rename List", lambda: row.name.start_editing(), enabled=bool(x_)),
                         ui.menu.Item("Delete List", lambda: self.delete_list(x_),
                                      enabled=bool(x_) and ident != S.DEFAULT_LIST)]]
        ui.menu.popup(self.side, sections, at=(x, y), glass=True, passthrough=True)

    def _renamed(self, label, _p, row) -> None:
        if label.get_editing():
            return
        kind, ident = row.key.split(":", 1)
        if kind == "n" and self.store.folder(ident):
            f = self.store.folder(ident)
            self.store.rename_folder(f, label.get_text())
            name = f["name"]
        elif kind == "r" and self.store.rlist(ident):
            x = self.store.rlist(ident)
            self.store.rename_list(x, label.get_text())
            name = x["name"]
            if self.key == row.key:
                self.reminders.refresh()
        else:
            return
        if label.get_text() != name:
            label.set_text(name)

    def new_folder(self) -> None:
        f = self.store.add_folder()
        self.rebuild_sidebar()
        self.select("n:" + f["id"])
        row = self._row("n:" + f["id"])
        GLib.idle_add(lambda: (row.name.start_editing(), False)[1])

    def new_list(self) -> None:
        x = self.store.add_list()
        self.rebuild_sidebar()
        self.select("r:" + x["id"])
        row = self._row("r:" + x["id"])
        GLib.idle_add(lambda: (row.name.start_editing(), False)[1])

    def delete_folder(self, f: dict) -> None:
        count = self.store.folder_count(f["id"])

        def answer(rid):
            if rid != "delete":
                return
            if self.key == "n:" + f["id"]:
                self._flush()
                self.key = None
            self.store.delete_folder(f)
            self.rebuild_sidebar()
            if self.key is None:
                self.select("n:" + S.ALL)
        if not count:
            answer("delete")
            return
        ui.dialog.alert(f"Are you sure you want to delete the folder “{f['name']}”?",
                        f"Its {count} note{'s' if count != 1 else ''} will be moved to Recently Deleted.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    def delete_list(self, x: dict) -> None:
        def answer(rid):
            if rid != "delete":
                return
            was = self.key == "r:" + x["id"]
            self.store.delete_list(x)
            self.rebuild_sidebar()
            self.notifier.schedule()
            if was:
                self.key = None
                self.select("r:today")
        ui.dialog.alert(f"Delete “{x['name']}”?", "This will delete all reminders in this list.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    def empty_deleted(self) -> None:
        def answer(rid):
            if rid == "delete":
                for n in self.store.notes_in(S.DELETED):
                    self.store.destroy_note(n)
                self.after_notes_change()
        ui.dialog.alert("Are you sure you want to permanently delete these notes?",
                        "You can't undo this action.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    def toggle_sidebar(self) -> None:
        self.sidebar.set_visible(not self.sidebar.get_visible())

    # -- notes list ------------------------------------------------------------------------------------
    def _list_pane(self) -> Gtk.Widget:
        self.items = Gio.ListStore(item_type=NoteItem)
        self.sorter = Gtk.CustomSorter.new(_cmp_items, None)
        self.sorted = Gtk.SortListModel(model=self.items, sorter=self.sorter,
                                        section_sorter=Gtk.CustomSorter.new(_cmp_sections, None))
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self._note_selected)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._row_setup)
        factory.connect("bind", self._row_bind)
        factory.connect("unbind", self._row_unbind)
        header = Gtk.SignalListItemFactory()
        header.connect("setup", lambda _f, h: h.set_child(Gtk.Label(xalign=0)))
        header.connect("bind", lambda _f, h: h.get_child().set_label(h.get_item().section[2]))
        self.listview = Gtk.ListView(model=self.selection, factory=factory, header_factory=header,
                                     css_classes=["nt-notes"])
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", self._note_menu)
        self.listview.add_controller(menu)
        over = Gtk.Overlay(css_classes=["nt-list-pane"])
        over.set_child(Gtk.ScrolledWindow(child=self.listview, vexpand=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.list_empty = Gtk.Label(label="No Notes", css_classes=["nt-empty"], can_target=False, visible=False)
        over.add_overlay(self.list_empty)
        over.set_size_request(200, -1)
        return over

    def _row_setup(self, _f, li) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["nt-note-row"])
        title = Gtk.Label(xalign=0, css_classes=["nt-note-title"], ellipsize=Pango.EllipsizeMode.END)
        line = Gtk.Box(spacing=8)
        date = Gtk.Label(xalign=0, css_classes=["nt-note-date"])
        prev = Gtk.Label(xalign=0, hexpand=True, css_classes=["nt-note-preview"], ellipsize=Pango.EllipsizeMode.END)
        line.append(date)
        line.append(prev)
        folder = Gtk.Box(spacing=4, css_classes=["nt-note-folder"])
        folder.append(Gtk.Image(icon_name="folder-symbolic", pixel_size=12))
        fname = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        folder.append(fname)
        for w in (title, line, folder):
            box.append(w)
        box.parts = (title, date, prev, folder, fname)
        li.set_child(box)

    def _row_bind(self, _f, li) -> None:
        item = li.get_item()
        box = li.get_child()
        box.item = item

        def fill(*_a):
            title, date, prev, folder, fname = box.parts
            title.set_label(item.title)
            date.set_label(item.date)
            prev.set_label(item.preview)
            show = self._folder() in (S.ALL, S.DELETED) or bool(self.query.strip())
            f = self.store.folder(item.note.get("folder"))
            folder.set_visible(show and f is not None)
            fname.set_label(f["name"] if f else "")
        fill()
        box.handler = item.connect("changed", fill)

    def _row_unbind(self, _f, li) -> None:
        box = li.get_child()
        if getattr(box, "handler", 0):
            li.get_item().disconnect(box.handler)
            box.handler = 0

    def reload_notes(self, select_id: str = None) -> None:
        """Fill the list with this folder's notes (or the search results)."""
        st = self.store
        if self.query.strip():
            notes = S.search(st.notes_in(S.ALL), self.query)
        else:
            notes = st.notes_in(self._folder())
        keep = select_id or (self.note["id"] if self.note else None)
        items = []
        for n in notes:
            it = self._items.get(n["id"])
            if it is None or it.note is not n:
                it = self._items[n["id"]] = NoteItem(n)
            else:
                it.refresh()
            items.append(it)
        self._syncing = True
        self.items.splice(0, self.items.get_n_items(), items)
        self._syncing = False
        pos = self._position(keep)
        if pos is None and items:
            pos = 0
        if pos is None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
            self._open(None)
        else:
            if self.selection.get_selected() == pos and self.note is self.sorted.get_item(pos).note:
                pass
            else:
                self.selection.set_selected(pos)
            self._note_selected()
        self.list_empty.set_label("No Results" if self.query.strip() else "No Notes")
        self.list_empty.set_visible(not items)
        self.delete_btn.set_sensitive(bool(items))

    def _position(self, nid):
        if nid is None:
            return None
        for i in range(self.sorted.get_n_items()):
            if self.sorted.get_item(i).note["id"] == nid:
                return i
        return None

    def _note_selected(self, *_a) -> None:
        if self._syncing:
            return
        item = self.selection.get_selected_item()
        note = item.note if item else None
        if note is not self.note:
            self._open(note)

    def _open(self, note) -> None:
        """Show a note in the editor (the one we leave is saved; if empty, it goes)."""
        prev = self.note
        self._flush()
        self.note = note
        if prev is not None and prev is not note and not prev.get("deleted") \
                and not "".join(markup.plain(prev.get("body", ""))).strip() and prev in self.store.notes:
            self.store.destroy_note(prev)
            GLib.idle_add(lambda: (self.after_notes_change(), False)[1])
        if note is None:
            self.editor.load("", editable=False)
            self.editor.set_visible(False)
            self.editor_empty.set_visible(True)
            return
        self.editor_empty.set_visible(False)
        self.editor.set_visible(True)
        self.editor.load(note.get("body", ""), editable=not note.get("deleted"))
        self._show_dates(note)

    def _show_dates(self, note) -> None:
        self.editor.set_dates(format_date(note.get("modified") or note.get("created") or time.time()))

    def _note_menu(self, gest, _n, x, y) -> None:
        picked = self.listview.pick(x, y, Gtk.PickFlags.DEFAULT)
        # the row under the pointer: select it first (macOS)
        while picked is not None and not (hasattr(picked, "parts")):
            picked = picked.get_parent() if picked is not self.listview else None
        if picked is None:
            ui.menu.popup(self.listview, [[ui.menu.Item("New Note", self.new_note,
                                                        enabled=self._folder() != S.DELETED)]],
                          at=(x, y), glass=True, passthrough=True)
            return
        note = picked.item.note
        pos = self._position(note["id"])
        if pos is not None:
            self.selection.set_selected(pos)
        if note is None:
            return
        if note.get("deleted"):
            sections = [[ui.menu.Item("Recover", lambda: self.recover(note))],
                        [ui.menu.Item("Delete Immediately", lambda: self.delete_note(note))]]
        else:
            moves = [[ui.menu.Item(f["name"], (lambda fid=f["id"]: self.move_note(note, fid)),
                                   checked=note.get("folder") == f["id"]) for f in self.store.folders]]
            sections = [[ui.menu.Item("Unpin Note" if note.get("pinned") else "Pin Note",
                                      lambda: self.pin(note, not note.get("pinned")))],
                        [ui.menu.Item("Move to", submenu=moves)],
                        [ui.menu.Item("Delete", lambda: self.delete_note(note))]]
        ui.menu.popup(self.listview, sections, at=(x, y), glass=True, passthrough=True)

    # -- editor ------------------------------------------------------------------------------------------
    def _editor_pane(self) -> Gtk.Widget:
        self.editor = NoteEditor()
        self.editor.connect("edited", self._edited)
        over = Gtk.Overlay(css_classes=["nt-editor-pane"])
        over.set_child(self.editor)
        self.editor_empty = Gtk.Label(label="No Note Selected", css_classes=["nt-empty"], visible=False,
                                      can_target=False)
        over.add_overlay(self.editor_empty)
        over.set_size_request(280, -1)
        return over

    def _edited(self, *_a) -> None:
        if self.note is None or self.note.get("deleted"):
            return
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(SAVE_DELAY, self._save)

    def _save(self) -> bool:
        self._save_id = 0
        note = self.note
        if note is None or note.get("deleted") or note not in self.store.notes:
            return False
        before = note.get("modified")
        self.store.update_note(note, self.editor.text())
        if note.get("modified") != before:
            it = self._items.get(note["id"])
            if it is not None:
                it.refresh()
                self.sorter.changed(Gtk.SorterChange.DIFFERENT)
            self._show_dates(note)
        return False

    def _flush(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save()

    def _style(self, kind: str) -> None:
        if self._notes_mode() and self.note is not None and not self.note.get("deleted"):
            self.editor.set_style(kind)

    def _inline(self, name: str) -> None:
        if self._notes_mode() and self.note is not None and not self.note.get("deleted"):
            self.editor.toggle_inline(name)

    def format_panel(self) -> None:
        if self.note is None or self.note.get("deleted"):
            return
        ed = self.editor
        active = ed.active_styles()
        kind = ed.line_kind(ed.selected_lines()[0])[0]
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pop = None
        row = Gtk.Box(spacing=4, homogeneous=True, css_classes=["nt-fmt-row"])
        for name, label, tip in (("bold", "B", "Bold"), ("italic", "I", "Italic"),
                                 ("underline", "U", "Underline"), ("strike", "S", "Strikethrough")):
            b = Gtk.ToggleButton(active=name in active, tooltip_text=tip, can_focus=False)
            lab = Gtk.Label()
            lab.set_markup({"bold": "<b>B</b>", "italic": "<i>I</i>", "underline": "<u>U</u>",
                            "strike": "<s>S</s>"}[name])
            b.set_child(lab)
            b.connect("toggled", lambda _b, n=name: self._inline(n))
            row.append(b)
        col.append(row)
        for k, label in (("title", "Title"), ("heading", "Heading"), ("subheading", "Subheading"),
                         ("body", "Body"), (None, None), ("bullet", "•  Bulleted List"),
                         ("number", "1.  Numbered List"), ("check", "Checklist")):
            if k is None:
                col.append(ui.panel.separator())
                continue
            mark = Gtk.Image(icon_name="object-select-symbolic", opacity=1 if k == kind else 0)
            r = ui.panel.row(None, label, mark, on_click=lambda k=k: (pop.popdown(), self._style(k)))
            r.label.add_css_class(f"nt-fmt-{k}")
            col.append(r)
        pop = ui.panel.popup(self.format_btn, col)

    # -- note commands -----------------------------------------------------------------------------------
    def new_note(self) -> None:
        if not self._notes_mode() or self._folder() == S.DELETED:
            self.select("n:" + S.DEFAULT_FOLDER if self._folder() == S.DELETED or not self._notes_mode()
                        else self.key)
        self._flush()
        if self.query:
            self.search.set_text("")
            self.query = ""
        note = self.store.new_note(self._folder())
        self.update_counts()
        self.reload_notes(select_id=note["id"])
        self.editor.view.grab_focus()

    def delete_selected(self) -> None:
        if not self._notes_mode():
            self.reminders.delete_selected()
            return
        if self.note is not None:
            self.delete_note(self.note)

    def delete_note(self, note: dict) -> None:
        if note.get("deleted"):
            def answer(rid):
                if rid == "delete":
                    self._do_delete(note)
            ui.dialog.alert("Are you sure you want to delete this note?", "You can't undo this action.",
                            [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)
            return
        self._do_delete(note)

    def _do_delete(self, note: dict) -> None:
        self._flush()
        pos = self._position(note["id"])
        was_deleted = bool(note.get("deleted"))
        if note is self.note:
            self.note = None                     # not "left empty": it's going anyway
        self.store.delete_note(note)
        self._undo = None if was_deleted else note
        n = self.sorted.get_n_items()
        nxt = None
        if pos is not None and n > 1:
            nxt = self.sorted.get_item(pos + 1 if pos + 1 < n else pos - 1).note["id"]
        self.after_notes_change(select_id=nxt)

    def undo_delete(self) -> bool:
        note = self._undo
        if note is None or note not in self.store.notes or not note.get("deleted"):
            return False
        self._undo = None
        self.recover(note)
        return True

    def recover(self, note: dict) -> None:
        self.store.recover_note(note)
        folder = note.get("folder")
        self.rebuild_sidebar()
        if self._folder() not in (S.ALL, folder):
            self.select("n:" + folder)
        self.after_notes_change(select_id=note["id"])

    def pin(self, note: dict, on: bool) -> None:
        self.store.set_pinned(note, on)
        self.reload_notes(select_id=note["id"])

    def move_note(self, note: dict, folder: str) -> None:
        self.store.move_note(note, folder)
        self.after_notes_change()

    def after_notes_change(self, select_id: str = None) -> None:
        had_deleted = self._row("n:" + S.DELETED) is not None
        if had_deleted != any(n.get("deleted") for n in self.store.notes):
            if self.key == "n:" + S.DELETED and had_deleted:
                self.key = None
                self.rebuild_sidebar()
                self.select("n:" + S.ALL)
                return
            self.rebuild_sidebar()
        self.update_counts()
        if self._notes_mode():
            self.reload_notes(select_id=select_id)

    # -- reminders ----------------------------------------------------------------------------------------
    def new_reminder(self) -> None:
        if self._notes_mode():
            self.select("r:today")
        self.reminders.new_reminder()

    def _reminders_changed(self) -> None:
        self.update_counts()
        self.notifier.schedule()

    # -- search ---------------------------------------------------------------------------------------------
    def set_query(self, q: str) -> None:
        if q == self.query:
            return
        self._flush()
        self.query = q
        if self._notes_mode():
            self.reload_notes()
        else:
            self.reminders.show(self.key[2:], q)

    def _focus_content(self) -> None:
        (self.listview if self._notes_mode() else self.reminders.list).grab_focus()

    # -- keys ------------------------------------------------------------------------------------------------
    def _typing(self) -> bool:
        w = self.get_focus()
        return isinstance(w, (Gtk.Text, Gtk.TextView, Gtk.Entry)) or \
            (w is not None and w.is_ancestor(self.editor))

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if not cmd:
            if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and not self._typing():
                self.delete_selected()
                return True
            return False
        in_editor = self.get_focus() is self.editor.view
        if k == Gdk.KEY_z and not shift:
            if in_editor and self.editor.buffer.get_can_undo():
                return False
            return self.undo_delete()
        if shift:
            styles = {Gdk.KEY_t: "title", Gdk.KEY_h: "heading", Gdk.KEY_j: "subheading", Gdk.KEY_b: "body",
                      Gdk.KEY_l: "check"}
            if k in styles:
                self._style(styles[k])
                return True
            if k == Gdk.KEY_n:
                self.new_folder()
                return True
            return False
        if k == Gdk.KEY_n:
            (self.new_note if self._notes_mode() else self.new_reminder)()
            return True
        if k == Gdk.KEY_f:
            self.search.grab_focus()
            return True
        if k == Gdk.KEY_w:
            self.close()
            return True
        if k in (Gdk.KEY_b, Gdk.KEY_i, Gdk.KEY_u) and in_editor:
            self._inline({Gdk.KEY_b: "bold", Gdk.KEY_i: "italic", Gdk.KEY_u: "underline"}[k])
            return True
        return False

    def _close_request(self, _w) -> bool:
        self._flush()
        if self.note is not None and not self.note.get("deleted") \
                and not "".join(markup.plain(self.note.get("body", ""))).strip() and self.note in self.store.notes:
            self.store.destroy_note(self.note)
        self.notifier.stop()
        cfg = {"sidebar_width": self.outer.get_position(), "list_width": self.inner.get_position()}
        if any(self.cfg.get(k) != v for k, v in cfg.items()):
            config.update("notes", **cfg)
        return False


def open_windows(app, paths=None) -> None:
    """One Notes window: opening Notes again brings it forward."""
    wins = [w for w in app.get_windows() if isinstance(w, NotesWindow)]
    (wins[0] if wins else NotesWindow(app)).present()


def notes_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Notes\n"
                              "Comment=Write notes, make checklists and keep reminders\n"
                              "Icon=sonata-notes\nCategories=Office;Utility;\n"
                              "Keywords=note;reminder;todo;checklist;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} notes\n")
