"""Files window (macOS Big Sur Finder): sidebar | unified toolbar + view.

Icons / List / Columns views (views.py), navigation (back/forward/
enclosing folder), open with the default app, filter-as-you-type search of
the current folder, hidden files toggle, live folder updates. Context
menus, rename, drag and drop come next (ROADMAP M7)."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import folder, ops  # noqa: E402
from .folder import RECENTS, file_of, is_dir  # noqa: E402
from .views import ColumnsView, IconsView, ListView  # noqa: E402
from .sidebar import Sidebar  # noqa: E402

VIEWS = (("icons", "view-grid-symbolic", "as Icons"), ("list", "view-list-symbolic", "as List"),
         ("columns", "view-dual-symbolic", "as Columns"))
DEFAULTS = {"view": "icons"}

ui.register("""
window.sonata-files { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.fs-content { background: %(content_bg)s; }
.fs-paned > separator { min-width: 1px; background: %(separator)s; }
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
.fs-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
""", key="files-window")


def _icon_button(icon, tip, cb, css=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tip, focus_on_click=False, valign=Gtk.Align.CENTER,
                   css_classes=css or [])
    b.connect("clicked", lambda *_: cb())
    return b


class FilesWindow(Adw.ApplicationWindow):
    def __init__(self, app, uri: str = None):
        super().__init__(application=app, title="Files", css_classes=["sonata-files", "sonata-glass"],
                         default_width=920, default_height=560)
        self.set_size_request(560, 320)
        self.history, self.pos = [], -1
        self.folder = folder.Folder(self._loaded, self._load_failed)

        self.sidebar = Sidebar(self.go, ui.window.traffic_lights(self.close, self.minimize, self._zoom))
        paned = Gtk.Paned(start_child=self.sidebar, shrink_start_child=False, resize_start_child=False,
                          css_classes=["fs-paned"])
        paned.set_position(200)
        self.sidebar.set_size_request(150, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["fs-content"])
        content.append(Gtk.WindowHandle(child=self._toolbar()))
        overlay = Gtk.Overlay(vexpand=True)
        overlay.set_child(self._views())
        self.empty = Gtk.Label(css_classes=["fs-empty"], visible=False, can_target=False)
        overlay.add_overlay(self.empty)
        content.append(overlay)
        paned.set_end_child(content)
        paned.set_shrink_end_child(False)
        self.set_content(paned)
        self._shortcuts()
        self.connect("notify::is-active", lambda w, _p: w.is_active() and self.sidebar.refresh_space())
        self.set_view(config.load("files", DEFAULTS)["view"], save=False)
        self.go(uri or Gio.File.new_for_path(GLib.get_home_dir()).get_uri())

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
        self.search.connect("search-changed", lambda *_: self._refilter())
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

    def _close_search(self):
        self.search.set_text("")
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
            menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu.connect("pressed", lambda g, _n, x, y, v=v: self._context_menu(v, g.get_widget(), x, y))
            v.widget.add_controller(menu)
            w = v.widget
            if vid != "columns":
                w = Gtk.ScrolledWindow(child=w, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self.stack.add_named(w, vid)
        self.view = self.views["icons"]
        return self.stack

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
        q = self.search.get_text().strip().casefold() if hasattr(self, "search") else ""
        return not q or q in info.get_display_name().casefold()

    def _refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_empty()

    def _update_empty(self):
        empty = self.filtered.get_n_items() == 0
        searching = bool(self.search.get_text().strip())
        self.empty.set_label("No Results" if searching else "")
        self.empty.set_visible(empty and searching)

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
        if not uri or uri == RECENTS:
            return
        parent = Gio.File.new_for_uri(uri).get_parent()
        if parent:
            self.go(parent.get_uri())

    def _update_nav(self):
        self.back.set_sensitive(self.pos > 0)
        self.fwd.set_sensitive(self.pos < len(self.history) - 1)

    def _loaded(self, uri):
        name = folder.display_name(uri)
        self.title.set_label(name)
        self.set_title(name)
        self.sidebar.select(uri)
        self._update_empty()
        for v in self.views.values():
            v.unselect_all()
            v.scroll_top()
        self.views["columns"].reset(uri)

    def _load_failed(self, uri, err):
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
        return self.location() != RECENTS

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
            sections = [[Item("Open", self.open_selection)]]
            apps = self._open_with_items(sel[0]) if n == 1 and not is_dir(sel[0]) else []
            if apps:
                sections[0].append(Item("Open With", submenu=[apps]))
            sections.append([Item("Move to Trash", self.trash_selection, enabled=self._writable_sel(sel))])
            sections.append([Item("Rename", lambda: self.rename_selection(), enabled=n == 1 and
                                  self._writable_sel(sel)),
                             Item("Duplicate", self.duplicate_selection, enabled=self._writable_here())])
            sections.append([Item(f"Copy {what}", self.copy_selection)])
            if self.history[self.pos] == RECENTS or self.search.get_text():
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
            if here:
                sections.append([Item("New Terminal at Folder",
                                      lambda: self._terminal(Gio.File.new_for_uri(self.location())))])
        ui.menu.popup(widget, sections, at=(x, y))

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
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_F2) and not ctrl:
            if len(self.view.selected()) == 1:
                self.rename_selection()
                return True
            return False
        if keyval == Gdk.KEY_Delete or (keyval == Gdk.KEY_BackSpace and ctrl):
            if self.view.selected():
                self.trash_selection()
                return True
        return False
