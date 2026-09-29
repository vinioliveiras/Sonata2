"""Files window (macOS Big Sur Finder): sidebar | unified toolbar + view.

Icons / List / Columns views (views.py), navigation (back/forward/
enclosing folder), open with the default app, search (This Mac or the
current folder, recursive, search.py), hidden files toggle, live folder updates. Context
menus, rename, drag and drop come next (ROADMAP M7)."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import folder, ops, packages  # noqa: E402
from .search import Search  # noqa: E402
from .folder import APPS, RECENTS, VIRTUAL, file_of, is_dir  # noqa: E402
from .views import ColumnsView, IconsView, ListView  # noqa: E402
from .sidebar import Sidebar  # noqa: E402

VIEWS = (("icons", "view-grid-symbolic", "as Icons"), ("list", "view-list-symbolic", "as List"),
         ("columns", "view-dual-symbolic", "as Columns"))
DEFAULTS = {"view": "icons", "sidebar_width": 200}

ui.register("""
window.sonata-files { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.fs-content { background: %(content_bg)s; }
.fs-paned > separator { min-width: 1px; background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.fs-toolbar { min-height: 52px; padding: 0 10px 0 8px; background: %(content_bg)s;
  box-shadow: inset 0 -1px %(separator)s; }
.fs-toolbar .fs-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.fs-toolbar button { min-width: 28px; min-height: 26px; padding: 0 4px; border-radius: %(r_button)s;
  background: none; box-shadow: none; border: none; color: %(tool_icon)s;
  transition: background-color %(t_fast)s, color %(t_fast)s; }
.fs-toolbar button:active { background: %(sidebar_selected)s; transition: background-color %(t_press)s; }
.fs-toolbar button:hover { background: %(tool_hover)s; }
.fs-toolbar button:disabled { color: %(label_tertiary)s; background: none; }
.fs-toolbar button:checked { background: %(tool_hover)s; color: %(label)s; }
.fs-toolbar .fs-seg { border-radius: %(r_button)s; }
.fs-toolbar .fs-seg button { border-radius: 0; min-width: 30px; }
.fs-toolbar .fs-seg button:first-child { border-radius: %(r_button)s 0 0 %(r_button)s; }
.fs-toolbar .fs-seg button:last-child { border-radius: 0 %(r_button)s %(r_button)s 0; }
.fs-toolbar entry { min-height: 24px; border-radius: %(r_button)s; }
.fs-scope { padding: 4px 10px; background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.fs-scope label.fs-scope-title { color: %(label_secondary)s; font-weight: 600; margin-right: 6px; }
.fs-scope button { min-height: 20px; padding: 0 8px; border-radius: 5px; border: none; box-shadow: none;
  background: none; color: %(label)s; font-weight: 500; }
.fs-scope button:checked { background: %(tool_hover)s; }
.fs-scope .fs-scope-status { color: %(label_tertiary)s; font-size: %(text_small)s; }
.fs-toolbar button.fs-text-btn { padding: 0 10px; color: %(label)s; background: %(tool_hover)s; }
.fs-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
""", key="files-window")


def _icon_button(icon, tip, cb, css=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, focus_on_click=False, valign=Gtk.Align.CENTER,
                   css_classes=css or [])
    b.connect("clicked", lambda *_: cb())
    return b


def _same_disk(a: Gio.File, b: Gio.File) -> bool:
    try:
        fa = a.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None).get_attribute_string("id::filesystem")
        fb = b.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None).get_attribute_string("id::filesystem")
        return bool(fa) and fa == fb
    except GLib.Error:
        return False


class FilesWindow(Adw.ApplicationWindow):
    def __init__(self, app, uri: str = None):
        # (classes added, not passed: passing css_classes drops GTK's "csd"
        # class, and with it the rounded corners and the shadow)
        super().__init__(application=app, title="Files", default_width=920, default_height=560)
        for c in ("sonata-files", "sonata-glass"):
            self.add_css_class(c)
        self.set_size_request(560, 320)
        ui.window.standard(self)
        self.history, self.pos = [], -1
        self.folder = folder.Folder(self._loaded, self._load_failed)

        self.sidebar = Sidebar(self.go, ui.window.traffic_lights(self.close, self.minimize, self._zoom))
        self.sidebar.on_drop = lambda files, dest, copy: self.drop(files, dest, copy)
        paned = Gtk.Paned(start_child=self.sidebar, shrink_start_child=False, resize_start_child=False,
                          css_classes=["fs-paned"])
        paned.set_position(config.load("files", DEFAULTS)["sidebar_width"])
        self.sidebar.set_size_request(150, -1)
        self._sidebar_width(paned)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["fs-content"])
        content.append(Gtk.WindowHandle(child=self._toolbar()))
        content.append(self._scope_bar())
        overlay = Gtk.Overlay(vexpand=True)
        overlay.set_child(self._views())
        self.empty = Gtk.Label(css_classes=["fs-empty"], visible=False, can_target=False)
        overlay.add_overlay(self.empty)
        content.append(overlay)
        paned.set_end_child(content)
        paned.set_shrink_end_child(False)
        self.set_content(paned)
        self._shortcuts()
        self.drag_icon = None                # set by the views while a file drag runs
        self._typed = ""                     # type to select
        ui.drag.follow(self, lambda: self.drag_icon)
        self.connect("notify::is-active", lambda w, _p: w.is_active() and self.sidebar.refresh_space())
        self.set_view(config.load("files", DEFAULTS)["view"], save=False)
        self.go(uri or Gio.File.new_for_path(GLib.get_home_dir()).get_uri())

    def _sidebar_width(self, paned) -> None:
        """The sidebar keeps the width you drag it to (all windows, saved);
        a double-click on the divider puts the standard width back."""
        pending = {"src": 0}

        def save():
            pending["src"] = 0
            cfg = config.load("files", DEFAULTS)
            if cfg["sidebar_width"] != paned.get_position():
                config.save("files", {**cfg, "sidebar_width": paned.get_position()})
            return False

        def moved(*_a):
            if pending["src"]:
                GLib.source_remove(pending["src"])
            pending["src"] = GLib.timeout_add(400, save)
        paned.connect("notify::position", moved)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def pressed(_g, n, x, _y):
            if n == 2 and abs(x - paned.get_position()) <= 8:          # on the divider
                paned.set_position(DEFAULTS["sidebar_width"])
        click.connect("pressed", pressed)
        paned.add_controller(click)

    # -- toolbar ---------------------------------------------------------------------
    def _toolbar(self):
        bar = Gtk.Box(spacing=4, css_classes=["fs-toolbar"])
        self.back = _icon_button("go-previous-symbolic", "Back", self.go_back)
        self.fwd = _icon_button("go-next-symbolic", "Forward", self.go_forward)
        bar.append(self.back)
        bar.append(self.fwd)
        self.title = Gtk.Label(css_classes=["fs-title"], margin_start=6, ellipsize=Pango.EllipsizeMode.END,
                               xalign=0, hexpand=True)
        bar.append(self.title)
        # Trash: Finder's "Empty" button
        self.empty_btn = Gtk.Button(label="Empty", valign=Gtk.Align.CENTER, visible=False,
                                    css_classes=["fs-text-btn"])
        self.empty_btn.connect("clicked", lambda *_: self.empty_trash())
        bar.append(self.empty_btn)
        seg = Gtk.Box(css_classes=["fs-seg"], valign=Gtk.Align.CENTER)
        self.view_buttons = {}
        first = None
        for vid, icon, tip in VIEWS:
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip, focus_on_click=False, group=first)
            b.connect("toggled", lambda b, v=vid: b.get_active() and self.set_view(v))
            first = first or b
            seg.append(b)
            self.view_buttons[vid] = b
        bar.append(seg)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=16, valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("stop-search", lambda *_: self._close_search())
        self.search_rev = Gtk.Revealer(child=self.search, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                       transition_duration=150)
        self.search_btn = _icon_button("system-search-symbolic", "Search", self._open_search)
        bar.append(self.search_btn)
        bar.append(self.search_rev)
        return bar

    def _open_search(self):
        self.search_btn.set_visible(False)
        self.search_rev.set_reveal_child(True)
        self.search.grab_focus()

    # -- search (Finder: This Mac / the current folder, recursive) -------------------------
    def _scope_bar(self):
        bar = Gtk.Box(spacing=2, css_classes=["fs-scope"])
        bar.append(Gtk.Label(label="Search:", css_classes=["fs-scope-title"]))
        self.scope_home = Gtk.ToggleButton(label="This Mac", active=True, focus_on_click=False)
        self.scope_here = Gtk.ToggleButton(group=self.scope_home, focus_on_click=False)
        for b in (self.scope_home, self.scope_here):
            b.connect("toggled", lambda b: b.get_active() and self._run_search())
            bar.append(b)
        self.search_status = Gtk.Label(css_classes=["fs-scope-status"], hexpand=True, xalign=1)
        bar.append(self.search_status)
        self.scope_rev = Gtk.Revealer(child=bar, transition_duration=150)
        self.searcher = Search()
        self.results = Gio.ListStore(item_type=Gio.FileInfo)
        self._search_src = 0
        self._in_results = False
        return self.scope_rev

    def _search_changed(self):
        """Filter the folder at once; the recursive search follows after a pause."""
        q = self.search.get_text().strip()
        if self._search_src:
            GLib.source_remove(self._search_src)
            self._search_src = 0
        if not q:
            self._leave_results()
            return
        here = self.history[self.pos] if self.pos >= 0 else ""
        self.scope_here.set_label(f"“{folder.display_name(here)}”")
        self.scope_here.set_visible(here not in VIRTUAL)
        self.scope_rev.set_reveal_child(True)
        if not self._in_results:
            self._refilter()

        def later():
            self._search_src = 0
            self._run_search()
            return False
        self._search_src = GLib.timeout_add(250, later)

    def _run_search(self):
        q = self.search.get_text().strip()
        if not q:
            return
        here = self.history[self.pos] if self.pos >= 0 else RECENTS
        root_uri = here if self.scope_here.get_active() and here not in VIRTUAL else \
            Gio.File.new_for_path(GLib.get_home_dir()).get_uri()
        root = Gio.File.new_for_uri(root_uri).get_path()
        if not root:
            return
        self.results.remove_all()
        if not self._in_results:
            self._in_results = True
            if self.view is self.views["columns"]:
                self._view_before_search = "columns"
                self.set_view("list", save=False)
            self.filtered.set_model(self.results)
            self._refilter()
        self.search_status.set_label("Searching…")
        scope = "This Mac" if not self.scope_here.get_active() else folder.display_name(here)
        self.title.set_label(f"Searching “{scope}”")
        self.searcher.start(root, q, self._got_results, self._search_done)

    def _got_results(self, items):
        self.results.splice(self.results.get_n_items(), 0, items)
        self._update_empty()

    def _search_done(self, truncated):
        n = self.results.get_n_items()
        self.search_status.set_label(f"{n}+ items" if truncated else f"{n} item{'s' if n != 1 else ''}")
        self._update_empty()

    def _leave_results(self):
        self.searcher.cancel()
        self.scope_rev.set_reveal_child(False)
        if self._in_results:
            self._in_results = False
            self.filtered.set_model(self.folder.store)
            self.results.remove_all()
            if self.pos >= 0:
                self.title.set_label(folder.display_name(self.history[self.pos]))
            if getattr(self, "_view_before_search", None):
                self.set_view(self._view_before_search, save=False)
                self._view_before_search = None
        self._refilter()

    def _close_search(self):
        self.search.set_text("")
        self._leave_results()
        self.search_rev.set_reveal_child(False)
        self.search_btn.set_visible(True)
        self.view.focus()

    # -- views -----------------------------------------------------------------------
    def _views(self):
        self.filter = Gtk.CustomFilter.new(self._match)
        self.filtered = Gtk.FilterListModel(model=self.folder.store, filter=self.filter)
        self.views = {
            "icons": IconsView(self.filtered, self.open_item),
            "list": ListView(self.filtered, self.open_item),
            "columns": ColumnsView(self.filtered, self.open_item, self._column_location,
                                   lambda: self.folder.show_hidden),
        }
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        for vid, v in self.views.items():
            if hasattr(v, "selection"):              # Quick Look follows the selection
                v.selection.connect("selection-changed", lambda *_: self._follow_quicklook())
            v.dnd = self
            bg = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            bg.connect("drop", lambda t, val, x, y: self.drop(
                list(val.get_files()), Gio.File.new_for_uri(self.location()),
                bool(t.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)))
            v.widget.add_controller(bg)
            menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu.connect("pressed", lambda g, _n, x, y, v=v: self._context_menu(v, g.get_widget(), x, y))
            v.widget.add_controller(menu)
            w = v.widget
            if vid != "columns":
                w = Gtk.ScrolledWindow(child=w, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self.stack.add_named(w, vid)
        self.view = self.views["icons"]
        self.fade = ui.transition.CrossFade(self.stack)    # folder changes cross-fade
        return self.fade

    def set_view(self, vid, save=True):
        if vid not in self.views:
            vid = "icons"
        self.view = self.views[vid]
        self.stack.set_visible_child_name(vid)
        if not self.view_buttons[vid].get_active():
            self.view_buttons[vid].set_active(True)
        if vid == "columns" and self.pos >= 0:
            self.views["columns"].reset(self.history[self.pos])
        if save and config.load("files", DEFAULTS)["view"] != vid:
            config.save("files", {**config.load("files", DEFAULTS), "view": vid})
        self.view.focus()

    def _column_location(self, uri):
        """Columns view: the title follows the deepest open folder."""
        name = folder.display_name(uri)
        self.title.set_label(name)
        self.set_title(name)

    def _match(self, info) -> bool:
        if getattr(self, "_in_results", False):
            return True                         # the search already matched them
        q = self.search.get_text().strip().casefold() if hasattr(self, "search") else ""
        return not q or q in info.get_display_name().casefold()

    def _refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_empty()

    def _update_empty(self):
        empty = self.filtered.get_n_items() == 0
        searching = bool(self.search.get_text().strip())
        busy = getattr(self, "_in_results", False) and self.search_status.get_label() == "Searching…"
        self.empty.set_label("No Results" if searching else "")
        self.empty.set_visible(empty and searching and not busy)

    # -- navigation ------------------------------------------------------------------
    def go(self, uri: str, record=True) -> None:
        if record:
            if self.pos >= 0 and self.history[self.pos] == uri:
                return
            del self.history[self.pos + 1:]
            self.history.append(uri)
            self.pos = len(self.history) - 1
        if self.search.get_text():
            self._close_search()
        if self.view is not self.views["columns"]:        # columns slide on their own
            self.fade.capture()
        self.folder.load(uri)
        self._update_nav()

    def go_back(self):
        if self.pos > 0:
            self.pos -= 1
            self.go(self.history[self.pos], record=False)

    def go_forward(self):
        if self.pos < len(self.history) - 1:
            self.pos += 1
            self.go(self.history[self.pos], record=False)

    def go_up(self):
        uri = self.history[self.pos] if self.pos >= 0 else None
        if not uri or uri in VIRTUAL:
            return
        parent = Gio.File.new_for_uri(uri).get_parent()
        if parent:
            self.go(parent.get_uri())

    def _update_nav(self):
        self.back.set_sensitive(self.pos > 0)
        self.fwd.set_sensitive(self.pos < len(self.history) - 1)

    def _loaded(self, uri):
        self.empty_btn.set_visible(ops.is_trash(uri))
        name = folder.display_name(uri)
        self.title.set_label(name)
        self.set_title(name)
        self.sidebar.select(uri)
        self._update_empty()
        for v in self.views.values():
            v.unselect_all()
            v.scroll_top()
        self.views["columns"].reset(uri)
        self.fade.play()

    def _load_failed(self, uri, err):
        self.fade.play()
        name = folder.display_name(uri)
        # stay where we were (drop the failed step from the history)
        if self.history and self.history[self.pos] == uri:
            del self.history[self.pos]
            self.pos -= 1
            self._update_nav()
        if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED):
            body = "You don't have permission to see its contents."
        else:
            body = err.message
        ui.dialog.alert(f"The folder “{name}” can’t be opened.", body, [("ok", "OK", "default")], parent=self)

    # -- opening -----------------------------------------------------------------------
    def open_item(self, info) -> None:
        if info is None:
            return
        f = file_of(info)
        target = info.get_attribute_string("standard::target-uri")
        if is_dir(info):
            self.go(target or f.get_uri())
            return
        if info.get_name().endswith(".desktop") and f.get_path():        # an app (Applications, shortcuts)
            from ..apps import DesktopAppInfo
            try:
                app = DesktopAppInfo.new_from_filename(f.get_path())
                if app is not None:
                    app.launch([], self.get_display().get_app_launch_context())
                    return
            except (TypeError, GLib.Error):
                pass
        if f.get_path() and packages.open_path(f.get_path(), self):     # install / run / extract
            return
        # macOS: double-click opens the file in its app (pictures in Preview);
        # Quick Look (Space) is only the fallback when no app opens it
        from .quicklook import previewable
        ct = info.get_content_type() or ""
        if previewable(info) and not (ct and Gio.AppInfo.get_default_for_type(ct, False)):
            self.quick_look_item(info)
            return
        ctx = self.get_display().get_app_launch_context()
        Gio.AppInfo.launch_default_for_uri_async(target or f.get_uri(), ctx, None, self._launched, info)

    def _launched(self, _src, res, info):
        try:
            Gio.AppInfo.launch_default_for_uri_finish(res)
        except GLib.Error as e:
            ui.dialog.alert(f"There is no application set to open “{info.get_display_name()}”.", e.message,
                            [("ok", "OK", "default")], parent=self)

    def open_selection(self):
        for info in self.view.selected():
            self.open_item(info)

    # -- file actions (Finder's File/Edit menus and context menus) ----------------------
    def location(self) -> str:
        """The folder new items / pasted items go to."""
        if self.view is self.views["columns"]:
            return self.views["columns"].location() or self.history[self.pos]
        return self.history[self.pos]

    def _writable_here(self) -> bool:
        return self.location() not in VIRTUAL and not ops.is_trash(self.location())

    def _in_trash(self) -> bool:
        return self.pos >= 0 and ops.is_trash(self.history[self.pos])

    def empty_trash(self):
        def answer(rid):
            if rid == "empty":
                ops.empty_trash(lambda: self.folder.reload(),
                                lambda f, e: self._error("The Trash can’t be emptied.", e))
        ui.dialog.alert("Are you sure you want to permanently erase the items in the Trash?",
                        "You can’t undo this action.",
                        [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")], answer, parent=self)

    def delete_selection_now(self):
        files = self._selected_files()
        if not files:
            return
        what = f"“{files[0].get_basename()}”" if len(files) == 1 else f"these {len(files)} items"

        def answer(rid):
            if rid == "delete":
                ops.delete_now(files, None, lambda f, e: self._error(f"“{f.get_basename()}” can’t be deleted.", e))
        ui.dialog.alert(f"Are you sure you want to delete {what} immediately?", "You can’t undo this action.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    def put_back_selection(self):
        ops.put_back(self._selected_files(),
                     lambda f, e: self._error(f"“{f.get_basename()}” can’t be put back.", e))

    def _selected_files(self):
        return [file_of(i) for i in self.view.selected()]

    def _context_menu(self, view, widget, x, y):
        Item = ui.menu.Item
        info = view.info_at(widget, x, y)
        if info is not None:
            view.ensure_selected(info)
            sel = view.selected() or [info]
            n = len(sel)
            what = f"“{sel[0].get_display_name()}”" if n == 1 else f"{n} Items"
            if self._in_trash():                 # Finder's Trash menu
                ui.menu.popup(widget, [[Item("Put Back", self.put_back_selection)],
                                       [Item("Delete Immediately…", self.delete_selection_now)],
                                       [Item("Get Info", self.get_info),
                                        Item(f"Quick Look {what}", self.toggle_quicklook)],
                                       [Item("Empty Trash", self.empty_trash)]], at=(x, y), passthrough=True)
                return
            pkg = packages.menu_items(file_of(sel[0]).get_path(), self) if n == 1 and not is_dir(sel[0]) else []
            sections = [pkg + [Item("Open", self.open_selection)]] if pkg else [[Item("Open", self.open_selection)]]
            apps = self._open_with_items(sel[0]) if n == 1 and not is_dir(sel[0]) else []
            if apps:
                sections[0].append(Item("Open With", submenu=[apps]))
            from .. import prefs
            if n == 1 and prefs.is_picture(sel[0].get_content_type() or ""):     # Finder's Quick Action
                sections[0].append(Item("Set Desktop Picture",
                                        lambda f=file_of(sel[0]): prefs.set_wallpaper(f.get_uri())))
            sections.append([Item("Move to Trash", self.trash_selection, enabled=self._writable_sel(sel))])
            sections.append([Item("Get Info", self.get_info),
                             Item(f"Quick Look {what}", self.toggle_quicklook)])
            sections.append([Item("Rename", lambda: self.rename_selection(), enabled=n == 1 and
                                  self._writable_sel(sel)),
                             Item("Duplicate", self.duplicate_selection, enabled=self._writable_here())])
            sections.append([Item(f"Copy {what}", self.copy_selection)])
            if self.history[self.pos] in VIRTUAL or self.search.get_text():
                sections.append([Item("Show in Enclosing Folder", lambda: self._reveal(sel[0]))])
            if n == 1 and is_dir(sel[0]):
                sections.append([Item("New Terminal at Folder", lambda: self._terminal(file_of(sel[0])))])
        else:
            self.view.unselect_all()
            here = self._writable_here()
            sections = [[Item("New Folder", self.new_folder, enabled=here)],
                        [Item("Paste", self.paste,
                              enabled=here and ops.clipboard_has_files(self))],
                        [Item("View", submenu=[[Item(label, lambda v=vid: self.set_view(v),
                                                     checked=self.view is self.views[vid])
                                                for vid, _i, label in VIEWS]])]]
            if self._in_trash():
                sections.insert(0, [Item("Empty Trash", self.empty_trash)])
            if here:
                sections.append([Item("New Terminal at Folder",
                                      lambda: self._terminal(Gio.File.new_for_uri(self.location())))])
        ui.menu.popup(widget, sections, at=(x, y), passthrough=True)

    def _writable_sel(self, sel) -> bool:
        return all(i.get_attribute_boolean("access::can-write") or not i.has_attribute("access::can-write")
                   for i in sel)

    def _open_with_items(self, info):
        Item = ui.menu.Item
        ct = info.get_content_type() or ""
        default = Gio.AppInfo.get_default_for_type(ct, False) if ct else None
        items = []
        for app in Gio.AppInfo.get_all_for_type(ct) if ct else []:
            label = app.get_display_name() + (" (default)" if default and app.equal(default) else "")
            items.append(Item(label, lambda a=app: self._open_with(a, info)))
        return items

    def _open_with(self, app, info):
        try:
            app.launch([file_of(info)], self.get_display().get_app_launch_context())
        except GLib.Error as e:
            ui.dialog.alert(f"“{app.get_display_name()}” can’t be opened.", e.message,
                            [("ok", "OK", "default")], parent=self)

    def _reveal(self, info):
        f = file_of(info)
        parent = f.get_parent()
        if parent:
            self.go(parent.get_uri())
            self._select_when_listed(f.get_basename())

    def _terminal(self, gfile):
        path = gfile.get_path()
        if not path:
            return
        for term in (os.environ.get("TERMINAL"), "kgx", "gnome-terminal", "konsole", "kitty", "alacritty",
                     "foot", "ghostty", "wezterm", "xfce4-terminal", "xterm"):
            if term and GLib.find_program_in_path(term):
                try:
                    GLib.spawn_async([term], working_directory=path, flags=GLib.SpawnFlags.SEARCH_PATH)
                except GLib.Error:
                    continue
                return

    # new folder / rename
    def new_folder(self):
        if not self._writable_here():
            return
        where = Gio.File.new_for_uri(self.location())
        ops.new_folder(where, lambda f: self._select_when_listed(f.get_basename(), rename=True),
                       lambda e: self._error("The folder can’t be created.", e))

    def _select_when_listed(self, name, rename=False, tries=40):
        """Select (and maybe rename) `name` once the live listing shows it."""
        view = self.view if self.view is not self.views["columns"] else self.views["columns"]._active()
        if view.select_name(name):
            if rename:
                info = view.model.get_item(view.position_of(name))
                GLib.timeout_add(60, lambda: (self.view.begin_rename(info, self._commit_rename), False)[1])
            return
        if tries:
            GLib.timeout_add(50, lambda: (self._select_when_listed(name, rename, tries - 1), False)[1])

    def rename_selection(self):
        sel = self.view.selected()
        if len(sel) == 1:
            self.view.begin_rename(sel[0], self._commit_rename)

    def _commit_rename(self, info, new_name):
        if "/" in new_name:
            self._error(f"The name “{new_name}” can’t be used.", None,
                        "Try using a name with fewer characters, or with no punctuation marks.")
            return
        ops.rename(file_of(info), new_name, lambda f: self._select_when_listed(f.get_basename()),
                   lambda e: self._error(f"The name “{new_name}” can’t be used.", e))

    # drag and drop (Finder: same disk moves, another disk copies, Ctrl copies)
    def files_for_drag(self, view, info):
        sel = view.selected()
        return [file_of(i) for i in (sel if info in sel else [info])]

    def spring_open(self, info):
        """A drag hovering on a folder opens it (spring-loaded folders)."""
        target = info.get_attribute_string("standard::target-uri") or file_of(info).get_uri()
        self.go(target)

    def drop(self, files, dest, copy=False) -> bool:
        if dest.get_uri() in VIRTUAL or not files:
            return False
        if ops.is_trash(dest.get_uri()):                  # dropped on Trash: move to the Trash
            ops.trash(files)
            return True
        files = [f for f in files if not f.equal(dest) and not dest.has_prefix(f)]   # not into itself
        if all(f.get_parent() and f.get_parent().equal(dest) for f in files):
            return False                                  # dropped where they already are
        move = not copy and all(_same_disk(f, dest) for f in files)
        ops.Transfer(files, dest, move=move, parent=self, on_done=self.sidebar.refresh_space)
        return True

    # Quick Look / Get Info
    def toggle_quicklook(self):
        from .quicklook import QuickLook
        ql = getattr(self, "_ql", None)
        if ql is not None:
            ql.close()
            return
        sel = self.view.selected()
        if not sel:
            return
        self._ql = QuickLook(self, on_close=lambda: setattr(self, "_ql", None))
        self._ql.show_item(sel[0])

    def quick_look_item(self, info):
        from .quicklook import QuickLook
        if getattr(self, "_ql", None) is None:
            self._ql = QuickLook(self, on_close=lambda: setattr(self, "_ql", None))
        self._ql.show_item(info)

    def _follow_quicklook(self):
        ql = getattr(self, "_ql", None)
        sel = self.view.selected()
        if ql is not None and sel and sel[0] is not ql.info:
            ql.show_item(sel[0])

    def get_info(self):
        from .quicklook import GetInfo
        for info in self.view.selected()[:10]:
            GetInfo(self, info).present()

    # trash / duplicate / clipboard
    def trash_selection(self):
        files = self._selected_files()
        if files:
            ops.trash(files, lambda f, e: self._error(f"“{f.get_basename()}” can’t be moved to the Trash.", e))
            GLib.timeout_add(600, lambda: (self.sidebar.refresh_space(), False)[1])

    def duplicate_selection(self):
        files = self._selected_files()
        if files and self._writable_here():
            groups = {}
            for f in files:                    # duplicates go next to each original
                groups.setdefault(f.get_parent().get_uri(), []).append(f)
            for parent, fs in groups.items():
                ops.Transfer(fs, Gio.File.new_for_uri(parent), duplicate=True, parent=self,
                             on_done=self.sidebar.refresh_space)

    def copy_selection(self, cut=False):
        files = self._selected_files()
        if files:
            ops.copy_to_clipboard(self, files, cut=cut)

    def paste(self, move=False):
        if not self._writable_here():
            return
        dest = Gio.File.new_for_uri(self.location())

        def got(files, cut):
            if files:
                ops.Transfer(files, dest, move=move or cut, parent=self, on_done=self.sidebar.refresh_space)
                if cut:
                    self.get_clipboard().set_content(None)     # a cut is pasted once
            else:
                ops.paste_image(self, dest)                     # a copied picture becomes a file
        ops.read_clipboard(self, got)

    def _error(self, heading, err, body=None):
        ui.dialog.alert(heading, body or (err.message if err else ""), [("ok", "OK", "default")], parent=self)

    # -- window ------------------------------------------------------------------------
    def _zoom(self):
        self.unmaximize() if self.is_maximized() else self.maximize()

    def toggle_hidden(self):
        self.folder.show_hidden = not self.folder.show_hidden
        self.folder.reload()

    def _home_dir(self, kind=None):
        if kind is None:
            self.go(Gio.File.new_for_path(GLib.get_home_dir()).get_uri())
        else:
            uri = folder.xdg_uri(kind)
            if uri:
                self.go(uri)

    def _shortcuts(self):
        """macOS Finder keys with Ctrl standing in for Cmd (Super belongs to
        the compositor); Alt+arrows as the usual Linux alternates."""
        U = GLib.UserDirectory
        keys = [
            ("<Control>bracketleft|<Alt>Left", self.go_back),
            ("<Control>bracketright|<Alt>Right", self.go_forward),
            ("<Control>Up|<Alt>Up", self.go_up),
            ("<Control>Down|<Control>o", self.open_selection),
            ("<Control><Shift>h", self._home_dir),
            ("<Control><Shift>d", lambda: self._home_dir(U.DIRECTORY_DESKTOP)),
            ("<Control><Shift>o", lambda: self._home_dir(U.DIRECTORY_DOCUMENTS)),
            ("<Control><Alt>l", lambda: self._home_dir(U.DIRECTORY_DOWNLOAD)),
            ("<Control><Shift>f", lambda: self.go(RECENTS)),
            ("<Control><Shift>a", lambda: self.go(APPS)),
            ("<Control><Shift>period", self.toggle_hidden),
            ("<Control>f", self._open_search),
            ("<Control>w", self.close),
            ("<Control>n", lambda: FilesWindow(self.get_application(), self.history[self.pos]).present()),
            ("<Control>a", lambda: self.view.select_all()),
            ("<Control>1", lambda: self.set_view("icons")),
            ("<Control>2", lambda: self.set_view("list")),
            ("<Control>3", lambda: self.set_view("columns")),
            ("Escape", lambda: self._close_search() if self.search_rev.get_reveal_child()
             else self.view.unselect_all()),
            ("<Control><Shift>n", self.new_folder),
            ("<Control>c", self.copy_selection),
            ("<Control>x", lambda: self.copy_selection(cut=True)),
            ("<Control>v", self.paste),
            ("<Control><Alt>v", lambda: self.paste(move=True)),        # Finder: Move Item Here
            ("<Control>d", self.duplicate_selection),
            ("<Control>i", self.get_info),
            ("<Control>y", self.toggle_quicklook),
        ]
        ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for trig, cb in keys:
            ctl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                          action=Gtk.CallbackAction.new(lambda *_a, f=cb: (f(), True)[1])))
        self.add_controller(ctl)
        # Return renames (Finder), F2 too; Delete / Ctrl+Backspace move to the
        # Trash. Captured before the views (Return would open), never while
        # typing in a field.
        keys2 = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys2.connect("key-pressed", self._file_keys)
        self.add_controller(keys2)

    def _file_keys(self, _c, keyval, _code, state) -> bool:
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable) or (focus and focus.get_ancestor(Gtk.Entry)):
            return False
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_F2 and not ctrl:
            if len(self.view.selected()) == 1:
                self.rename_selection()
                return True
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not ctrl:
            return self._enter()
        if keyval == Gdk.KEY_BackSpace and not ctrl:
            if self.pos > 0:
                self.go_back()
            else:
                self.go_up()
            return True
        if keyval == Gdk.KEY_space and not ctrl:
            if self.view.selected() or getattr(self, "_ql", None):
                self.toggle_quicklook()
                return True
        if keyval == Gdk.KEY_Delete or (keyval == Gdk.KEY_BackSpace and ctrl):
            if self.view.selected():
                self.trash_selection()
                return True
        ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
        alt = bool(state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK))
        if ch.isprintable() and ch != "\x00" and not ctrl and not alt and (ch != " " or self._typed):
            return self._type_select(ch)
        return False

    ENTER_TWICE_MS = 350

    def _enter(self) -> bool:
        """Enter opens the selection; Enter twice (quickly) renames it. The
        open waits out the double-press window."""
        if not self.view.selected():
            return False
        if getattr(self, "_enter_src", 0):
            GLib.source_remove(self._enter_src)
            self._enter_src = 0
            if len(self.view.selected()) == 1:
                self.rename_selection()
            return True

        def open_now():
            self._enter_src = 0
            self.open_selection()
            return False
        self._enter_src = GLib.timeout_add(self.ENTER_TWICE_MS, open_now)
        return True

    def _type_select(self, ch) -> bool:
        """Letters jump to the first item starting with what was typed; typing
        on within a second extends the name ("do" -> "Documents")."""
        if getattr(self, "_typed_src", 0):
            GLib.source_remove(self._typed_src)

        def reset():
            self._typed, self._typed_src = "", 0
            return False
        self._typed = getattr(self, "_typed", "") + ch
        self._typed_src = GLib.timeout_add(1000, reset)
        if not self.view.select_prefix(self._typed):
            self.view.select_prefix(ch)            # a new word: start again from this letter
            self._typed = ch
        return True
