"""Files window (macOS Finder): sidebar | unified toolbar + tabs + view.

Icons / List / Columns views (views.py), navigation (back/forward/
enclosing folder), open with the default app, search (This Mac or the
current folder, recursive, search.py), hidden files toggle, live folder
updates, context menus, rename, drag and drop, clipboard, Quick Look.

Tabs (Finder): ⌘T opens a tab on the same folder, ⌘W closes the tab (the
window with the last one), Ctrl+Tab / Ctrl+Shift+Tab and ⌘⇧[ / ⌘⇧]
switch tabs (⌘ = Ctrl or Super). A folder's context menu has "Open in New
Tab"; a middle-click on a folder opens it in a new tab behind. Each tab
(Tab) keeps its folder, view mode, selection and back/forward history;
the sidebar, toolbar and window title follow the tab in front. The tab
strip (tabs.py) shows once there are two tabs."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, names, ui  # noqa: E402
from . import actions, folder, folderprefs, ops, packages, tags, undo  # noqa: E402
from .search import Search  # noqa: E402
from .folder import APPS, RECENTS, VIRTUAL, file_of, is_dir  # noqa: E402
from .views import SORT_BY, ColumnsView, IconsView, ListView  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .tabs import TabStrip  # noqa: E402
from .pathbar import PathBar  # noqa: E402

VIEWS = (("icons", "view-grid-symbolic", "as Icons"), ("list", "view-list-symbolic", "as List"),
         ("columns", "view-dual-symbolic", "as Columns"))
DEFAULTS = {"view": "icons", "list_columns": {}, "path_bar": True,    # list_columns: views.py
            "list_shown": None, "count_sizes": False,           # the list's columns; folder sizes
            "show_hidden": False}               # hidden files: off, except in folders where the eye is on (Vini)

ui.register("""
window.sonata-files { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.fs-content { background: %(content_bg)s; }
/* the sidebar's edge (not draggable): an opaque base under the hairline -- the
   separator colour alone is see-through and the wallpaper showed through as a
   bright gap beside the glass sidebar (like Settings' .st-divider) */
.fs-divider { min-width: 1px; background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.fs-toolbar { min-height: 52px; padding: 0 10px 0 8px; background: %(content_bg)s;
  box-shadow: inset 0 -1px %(separator)s; }
.fs-toolbar .fs-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.fs-toolbar entry.fs-title-rename { font-weight: 700; font-size: %(text_title)s; min-height: 24px; margin-left: 2px; }
.fs-toolbar button { min-width: 28px; min-height: 26px; padding: 0 4px; border-radius: %(r_button)s;
  background: none; box-shadow: none; border: none; color: %(tool_icon)s;
  transition: background-color %(t_fast)s, color %(t_fast)s; }
.fs-toolbar button:active { background: %(sidebar_selected)s; transition: background-color %(t_press)s; }
.fs-toolbar button:hover { background: %(tool_hover)s; }
.fs-toolbar button:disabled { color: %(label_tertiary)s; background: none; }
.fs-toolbar button:checked { background: %(tool_hover)s; color: %(label)s; }
/* the window buttons on the right (Settings > Appearance): dots, not toolbar buttons */
.fs-toolbar .traffic button, .fs-toolbar .traffic button:hover, .fs-toolbar .traffic button:active {
  min-width: 12px; min-height: 12px; padding: 0; margin: 0 4px; border-radius: 999px;
  background-color: transparent; }
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
.fs-scope .fs-scope-sep { min-width: 1px; background: %(separator)s; margin-top: 3px; margin-bottom: 3px; }
.fs-scope dropdown button { min-height: 20px; padding: 0 6px; }
.fs-toolbar button.fs-text-btn { padding: 0 10px; color: %(label)s; background: %(tool_hover)s; }
.fs-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
""", key="files-window")

# window state that belongs to the tab in front (FilesWindow reads/writes it through)
_TAB_STATE = ("history", "pos", "folder", "views", "view", "filtered", "filter", "stack", "fade", "empty")


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


class Tab:
    """One Finder tab: its folder (history, back/forward), its views with
    their selection, its view mode. `widget` is what the window shows."""

    def __init__(self, win, view_id):
        self.win = win
        self.history, self.pos = [], -1
        self.folder = folder.Folder(lambda u: win._tab_loaded(self, u), lambda u, e: win._tab_failed(self, u, e))
        self.folder.show_hidden = win.show_hidden
        self.name = ""                           # what the tab shows (columns: the deepest folder)
        self.button = None                       # its tab in the strip
        self.filter = Gtk.CustomFilter.new(lambda info: win._match(info) if win.tab is self else True)
        self.filtered = Gtk.FilterListModel(model=self.folder.store, filter=self.filter)
        self.views = win._make_views(self)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        for vid, v in self.views.items():
            w = v.widget
            if vid != "columns":
                # the list's columns scroll sideways when the window is narrow (Finder):
                # their widths no longer set the window's minimum (it was 1040 px)
                w = Gtk.ScrolledWindow(child=w, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC if vid == "list"
                                       else Gtk.PolicyType.NEVER)
            self.stack.add_named(w, vid)
        self.fade = ui.transition.CrossFade(self.stack)    # folder changes cross-fade
        self.widget = Gtk.Overlay(vexpand=True)
        self.widget.set_child(self.fade)
        self.empty = Gtk.Label(css_classes=["fs-empty"], visible=False, can_target=False)
        self.widget.add_overlay(self.empty)
        self.view = self.views.get(view_id) or self.views["icons"]
        self.stack.set_visible_child_name(self.view_id)

    @property
    def uri(self) -> str:
        return self.history[self.pos] if self.pos >= 0 else ""

    @property
    def view_id(self) -> str:
        return next(k for k, v in self.views.items() if v is self.view)

    def title(self) -> str:
        return self.name or (folder.display_name(self.uri) if self.uri else "Files")

    def after_load(self, uri) -> None:
        """A folder finished loading: fresh views, back at the top."""
        self.name = folder.display_name(uri)
        for v in self.views.values():
            v.unselect_all()
            v.scroll_top()
        self.views["columns"].reset(uri)
        self.fade.play()

    def dispose(self) -> None:
        self.folder.cancel()
        self.views["columns"].reset(None)


def _tab_state(name):
    return property(lambda self: getattr(self.tab, name),
                    lambda self, value: setattr(self.tab, name, value))


# a terminal app -> how it's told the folder to start in (the process' own
# working directory isn't enough for every one: gnome-terminal's server and
# wezterm's mux keep theirs)
TERMINAL_DIR_FLAGS = (("kgx", lambda p: [f"--working-directory={p}"]),
                      ("gnome-terminal", lambda p: [f"--working-directory={p}"]),
                      ("konsole", lambda p: ["--workdir", p]),
                      ("kitty", lambda p: ["--directory", p]),
                      ("alacritty", lambda p: ["--working-directory", p]),
                      ("foot", lambda p: [f"--working-directory={p}"]),
                      ("ghostty", lambda p: [f"--working-directory={p}"]),
                      ("wezterm", lambda p: ["start", "--cwd", p]),
                      ("xfce4-terminal", lambda p: [f"--working-directory={p}"]),
                      ("tilix", lambda p: [f"--working-directory={p}"]),
                      ("terminator", lambda p: [f"--working-directory={p}"]),
                      ("x-terminal-emulator", lambda p: []),
                      ("xterm", lambda p: []))


def _sonata_terminal_ok() -> bool:
    """Sonata's Terminal needs VTE for GTK 4 (asked for, not imported: it'd
    load libvte into Files for good)."""
    try:
        gi.require_version("Vte", "3.91")
        return True
    except ValueError:
        return False


def terminal_commands(path: str) -> list:
    """Every way to open a terminal at `path`, best first (each also spawned
    with `path` as its working directory)."""
    out = []
    if _sonata_terminal_ok():
        from ..__main__ import self_argv
        out.append(self_argv() + ["terminal", path])      # a tab at the folder (terminal.window.open_windows)
    flags = dict(TERMINAL_DIR_FLAGS)
    env = os.environ.get("TERMINAL", "").strip()
    names = ([env] if env else []) + [t for t, _f in TERMINAL_DIR_FLAGS if t != env]
    for name in names:
        exe = GLib.find_program_in_path(name)
        if exe:
            out.append([exe] + flags.get(os.path.basename(name), lambda p: [])(path))
    return out


class FilesWindow(Adw.ApplicationWindow):
    TABS = True              # the Open/Save panel (chooser.py) has no tabs

    def __init__(self, app, uri: str = None):
        # (classes added, not passed: passing css_classes drops GTK's "csd"
        # class, and with it the rounded corners and the shadow)
        super().__init__(application=app, title="Files")
        ui.window.remember_size(self, "files", 920, 560)        # its last size
        for c in ("sonata-files", "sonata-glass"):
            self.add_css_class(c)
        self.set_size_request(560, 320)
        ui.window.standard(self)
        self.tabs, self.tab = [], None
        self.show_hidden = bool(config.load("files", DEFAULTS)["show_hidden"])
        self._syncing = False                # toolbar being set to the tab in front
        self._dragged = []                   # files of a drag started here (spring-load guard)

        lights = ui.window.traffic_lights(self.close, self.minimize, self._zoom)
        right = ui.window.buttons_side() == "right"          # Settings > Appearance > Window buttons
        self.sidebar = Sidebar(lambda uri: self.go(uri, fade=False), Gtk.Box() if right else lights)
        self._right_lights = lights if right else None       # (at the toolbar's end: _toolbar)
        self.sidebar.on_drop = lambda files, dest, copy: self.drop(files, dest, copy)
        # one sidebar width, no divider to drag (Vini: resizing it got in
        # the way of the list's columns)
        split = Gtk.Box(css_classes=["fs-split"])
        self.sidebar.set_size_request(ui.window.SIDEBAR_W, -1)      # the same as Settings'
        self.sidebar.set_hexpand(False)
        split.append(self.sidebar)
        split.append(Gtk.Box(css_classes=["fs-divider"]))

        content = self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["fs-content"],
                                             hexpand=True)              # the Open/Save panel adds to it
        content.append(Gtk.WindowHandle(child=self._toolbar()))
        self.strip = TabStrip(self)
        content.append(self.strip)
        content.append(self._scope_bar())
        self.tab_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.NONE)
        content.append(self.tab_stack)
        # where you are, at the bottom (Finder's path bar; on by default, like Windows' address bar)
        self.pathbar = PathBar(self.go, lambda files, dest, copy: self.drop(files, dest, copy))
        self.pathbar.set_visible(config.load("files", DEFAULTS)["path_bar"])
        self.pathbar.on_edit = self.edit_address
        content.append(self.pathbar)
        split.append(content)
        self.set_content(split)
        self._shortcuts()
        self.drag_icon = None                # set by the views while a file drag runs
        self._typed = ""                     # type to select
        ui.drag.follow(self, lambda: self.drag_icon)
        self.connect("notify::is-active", lambda w, _p: w.is_active() and self.sidebar.refresh_space())
        uri = folder.canonical(uri)
        if uri == folder.CONNECT:            # Go > Connect to Server from the menu bar (via gvfs: run_files missed it)
            uri = None
            self._connect_once = self.connect("map", self._connect_on_map)
        self._add_tab(uri or Gio.File.new_for_path(GLib.get_home_dir()).get_uri(),
                      config.load("files", DEFAULTS)["view"], select=True)

    def _connect_on_map(self, *_a):
        self.disconnect(self._connect_once)
        GLib.idle_add(lambda: (self.connect_to_server(), False)[1])

    # -- toolbar ---------------------------------------------------------------------
    def _toolbar(self):
        bar = Gtk.Box(spacing=4, css_classes=["fs-toolbar"])
        self.back = _icon_button("go-previous-symbolic", "Back", self.go_back)
        self.fwd = _icon_button("go-next-symbolic", "Forward", self.go_forward)
        bar.append(self.back)
        bar.append(self.fwd)
        self.title = Gtk.Label(css_classes=["fs-title"], margin_start=6, ellipsize=Pango.EllipsizeMode.END,
                               xalign=0, hexpand=False)
        bar.append(self.title)
        # a click on the folder's name renames it (Vini); the space after it still drags the window
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda g, n, x, y: n == 1 and self.title.contains(x, y) and self.rename_folder())
        self.title.add_controller(click)
        bar.append(Gtk.Box(hexpand=True))
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
            b.connect("toggled", lambda b, v=vid: b.get_active() and not self._syncing and self.set_view(v))
            first = first or b
            seg.append(b)
            self.view_buttons[vid] = b
        bar.append(seg)
        # hidden files in this folder, on or off (Vini): remembered per folder
        eye = Gtk.Box(css_classes=["fs-seg"], valign=Gtk.Align.CENTER)
        self.hidden_btn = Gtk.ToggleButton(icon_name="view-reveal-symbolic", tooltip_text="Show Hidden Files",
                                           focus_on_click=False)
        self.hidden_btn.connect("toggled", lambda b: not self._syncing and
                                b.get_active() != self.tab.folder.show_hidden and self.toggle_hidden())
        eye.append(self.hidden_btn)
        bar.insert_child_after(eye, self.empty_btn)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=16, valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("stop-search", lambda *_: self._close_search())
        self.search_rev = Gtk.Revealer(child=self.search, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                       transition_duration=150)
        self.search_btn = _icon_button("system-search-symbolic", "Search", self._open_search)
        bar.append(self.search_btn)
        bar.append(self.search_rev)
        if getattr(self, "_right_lights", None) is not None:          # the window buttons on the right
            self._right_lights.set_valign(Gtk.Align.CENTER)
            self._right_lights.set_margin_start(8)
            bar.append(self._right_lights)
        return bar

    def _open_search(self):
        self.search_btn.set_visible(False)
        self.search_rev.set_reveal_child(True)
        self.search.grab_focus()

    # -- search (Finder: This Mac / the current folder, recursive) -------------------------
    def _scope_bar(self):
        bar = Gtk.Box(spacing=2, css_classes=["fs-scope"])
        bar.append(Gtk.Label(label="Search:", css_classes=["fs-scope-title"]))
        self.scope_home = Gtk.ToggleButton(label=names.THIS_COMPUTER, active=True, focus_on_click=False)
        self.scope_here = Gtk.ToggleButton(group=self.scope_home, focus_on_click=False)
        for b in (self.scope_home, self.scope_here):
            b.connect("toggled", lambda b: b.get_active() and self._run_search())
            bar.append(b)
        # Finder's search criteria: the words in names or contents, a kind, a date
        from .search import DATES, KINDS
        bar.append(Gtk.Box(css_classes=["fs-scope-sep"], margin_start=6, margin_end=6))
        self.match_name = Gtk.ToggleButton(label="Name", active=True, focus_on_click=False)
        self.match_contents = Gtk.ToggleButton(label="Contents", group=self.match_name, focus_on_click=False)
        for b in (self.match_name, self.match_contents):
            b.connect("toggled", lambda b: b.get_active() and self._run_search())
            bar.append(b)
        self.search_kind = ui.controls.popup_button([label for _k, label in KINDS], 0,
                                                    lambda _i: self._run_search())
        self.search_date = ui.controls.popup_button([label for _k, label, _d in DATES], 0,
                                                    lambda _i: self._run_search())
        for w in (self.search_kind, self.search_date):
            w.set_valign(Gtk.Align.CENTER)
            w.set_margin_start(6)
            bar.append(w)
        self.search_status = Gtk.Label(css_classes=["fs-scope-status"], hexpand=True, xalign=1)
        bar.append(self.search_status)
        # the criteria never set the window's minimum width: a narrow window scrolls them
        scroll = Gtk.ScrolledWindow(child=bar, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                    vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.scope_rev = Gtk.Revealer(child=scroll, transition_duration=150)
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
        scope = names.THIS_COMPUTER if not self.scope_here.get_active() else folder.display_name(here)
        self.title.set_label(f"Searching “{scope}”")
        from .search import DATES, KINDS
        self.searcher.start(root, q, self._got_results, self._search_done,
                            kind=KINDS[self.search_kind.get_selected()][0],
                            date=DATES[self.search_date.get_selected()][0],
                            contents=self.match_contents.get_active())

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
    def _make_views(self, tab):
        """A tab's three views, wired to the window (open, menus, drops)."""
        views = {
            "icons": IconsView(tab.filtered, self.open_item),
            "list": ListView(tab.filtered, self.open_item),
            "columns": ColumnsView(tab.filtered, self.open_item, lambda uri: self._column_location(tab, uri),
                                   lambda: tab.folder.show_hidden),
        }
        cfg = config.load("files", DEFAULTS)
        views["list"].show_columns(cfg.get("list_shown"))
        views["list"].count_sizes = bool(cfg.get("count_sizes"))
        # a header click sorts this folder that way from now on
        views["list"].on_sort = lambda state: (views["icons"].set_sort(*state),
                                                tab.uri and folderprefs.remember(tab.uri, sort=state))
        for v in views.values():
            if hasattr(v, "selection"):              # Quick Look follows the selection
                v.selection.connect("selection-changed", lambda *_: tab is self.tab and self._follow_quicklook())
            v.dnd = self
            bg = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            bg.connect("drop", lambda t, val, x, y: self.drop(
                list(val.get_files()), Gio.File.new_for_uri(self.location()),
                bool(t.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)))
            v.widget.add_controller(bg)
            menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu.connect("pressed", lambda g, _n, x, y, v=v: self._context_menu(v, g.get_widget(), x, y))
            v.widget.add_controller(menu)
            middle = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)       # a folder in a new tab (behind)
            middle.connect("pressed", lambda g, _n, x, y, v=v: self._middle_click(v, g, x, y))
            v.widget.add_controller(middle)
        return views

    def set_view(self, vid, save=True):
        if vid not in self.views:
            vid = "icons"
        self.view = self.views[vid]
        self.stack.set_visible_child_name(vid)
        self._sync_view_buttons()
        if vid == "columns" and self.pos >= 0:
            self.views["columns"].reset(self.history[self.pos])
        if save:                                           # your choice: this folder, and the default
            if self.tab.uri:
                folderprefs.remember(self.tab.uri, view=vid)
            if config.load("files", DEFAULTS)["view"] != vid:
                config.update("files", view=vid)
        self.view.focus()

    def _apply_folder_prefs(self, tab, uri) -> None:
        """Before a folder shows: its own view and sort, if you chose them
        there; else the default view and Name, ascending."""
        p = folderprefs.get(uri)
        tab.folder.show_hidden = p.get("hidden", self.show_hidden)      # before it lists
        if tab is self.tab:
            self._sync_hidden_button()
        vid = p.get("view") or config.load("files", DEFAULTS)["view"]
        if vid in tab.views and vid != tab.view_id and not getattr(self, "_in_results", False):
            if tab is self.tab:
                self.set_view(vid, save=False)
            else:
                tab.view = tab.views[vid]
                tab.stack.set_visible_child_name(vid)
        for vid in ("list", "icons"):
            tab.views[vid].set_sort(*p.get("sort", folderprefs.DEFAULT_SORT))

    def sort_by(self, title: str) -> None:
        """View > Sort By: this folder, both icons and list (Finder's direction for each)."""
        state = (title, dict(SORT_BY).get(title, False))
        for vid in ("list", "icons"):
            self.tab.views[vid].set_sort(*state)
        if self.tab.uri:
            folderprefs.remember(self.tab.uri, sort=state)

    def _sync_view_buttons(self):
        b = self.view_buttons[self.tab.view_id]
        if not b.get_active():
            self._syncing = True
            b.set_active(True)
            self._syncing = False

    def _column_location(self, tab, uri):
        """Columns view: the title (and the tab) follow the deepest open folder."""
        if not uri:
            return
        tab.name = folder.display_name(uri)
        self.strip.retitle(tab)
        if tab is self.tab:
            self.title.set_label(tab.name)
            self.set_title(tab.name)
            self.pathbar.set_uri(uri)

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
    def go(self, uri: str, record=True, fade=True) -> None:
        """fade: cross-fade into the folder (not from the sidebar: at once, Vini)."""
        uri = folder.canonical(uri)
        if record:
            if self.pos >= 0 and self.history[self.pos] == uri:
                return
            del self.history[self.pos + 1:]
            self.history.append(uri)
            self.pos = len(self.history) - 1
        if self.search.get_text():
            self._close_search()
        if fade and self.view is not self.views["columns"]:        # columns slide on their own
            self.fade.capture()
        self._apply_folder_prefs(self.tab, uri)
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

    def _tab_loaded(self, tab, uri):
        tab.after_load(uri)
        self.strip.retitle(tab)
        if tab is self.tab:
            self._loaded(uri)

    def _loaded(self, uri):
        """The tab in front shows `uri`: toolbar, title and sidebar follow."""
        self.empty_btn.set_visible(ops.is_trash(uri))
        name = self.tab.title()
        self.title.set_label(name)
        self.set_title(name)
        self.sidebar.select(uri)
        self.pathbar.set_uri(uri)
        self._update_empty()

    def _tab_failed(self, tab, uri, err):
        tab.fade.play()
        # stay where we were (drop the failed step from the history)
        if tab.history and tab.history[tab.pos] == uri:
            del tab.history[tab.pos]
            tab.pos -= 1
        if tab is self.tab:
            self._update_nav()
        if tab.pos < 0 and len(self.tabs) > 1:     # a new tab that never opened
            self.close_tab(tab)
        self._load_failed(uri, err)

    def _load_failed(self, uri, err):
        name = folder.display_name(uri)
        if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.PERMISSION_DENIED):
            body = "You don't have permission to see its contents."
        else:
            body = err.message
        ui.dialog.alert(f"The folder “{name}” can’t be opened.", body, [("ok", "OK", "default")], parent=self)

    # -- tabs (Finder) -----------------------------------------------------------------
    def _add_tab(self, uri, view_id=None, index=None, select=True):
        uri = folder.canonical(uri)
        tab = Tab(self, view_id or (self.tab.view_id if self.tab else "icons"))
        if index is None:
            index = self.tabs.index(self.tab) + 1 if self.tab in self.tabs else len(self.tabs)
        self.tabs.insert(index, tab)
        self.tab_stack.add_child(tab.widget)
        self.strip.add(tab, index)
        if select or self.tab is None:
            self.select_tab(tab)
        tab.history, tab.pos = [uri], 0
        if folderprefs.get(uri):
            self._apply_folder_prefs(tab, uri)
        if tab is self.tab:
            self._update_nav()
        tab.folder.load(uri)
        return tab

    def new_tab(self, uri=None, select=True):
        """⌘T: a tab on the same folder (Finder), after the current one."""
        if not self.TABS:
            return None
        return self._add_tab(uri or self.tab.uri, select=select)

    def open_in_new_tab(self, info, select=True):
        if info is not None and is_dir(info):
            self.new_tab(info.get_attribute_string("standard::target-uri") or file_of(info).get_uri(), select)

    def _middle_click(self, view, gesture, x, y):
        if not self.TABS:
            return
        info = view.info_at(gesture.get_widget(), x, y)
        if info is not None and is_dir(info):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.open_in_new_tab(info, select=False)

    def select_tab(self, tab):
        if tab not in self.tabs or tab is self.tab:
            return
        if self.tab is not None and (self.search.get_text() or self.search_rev.get_reveal_child()):
            self._close_search()                  # the search belongs to the tab it ran in
        self.tab = tab
        self.tab_stack.set_visible_child(tab.widget)
        self.strip.set_active(tab)
        self._sync_view_buttons()
        self._sync_hidden_button()
        self._update_nav()
        if tab.uri:
            self.empty_btn.set_visible(ops.is_trash(tab.uri))
            self.sidebar.select(tab.uri)
            self.pathbar.set_uri(tab.uri)
        self.title.set_label(tab.title())
        self.set_title(tab.title())
        self._update_empty()
        tab.view.focus()

    def close_tab(self, tab=None):
        """⌘W: closes the tab; the window with its last tab."""
        tab = tab or self.tab
        if len(self.tabs) <= 1:
            self.close()
            return
        i = self.tabs.index(tab)
        if tab is self.tab:
            self.select_tab(self.tabs[i + 1] if i + 1 < len(self.tabs) else self.tabs[i - 1])
        self.tabs.remove(tab)
        self.strip.remove(tab)
        self.tab_stack.remove(tab.widget)
        tab.dispose()

    def move_tab(self, tab, index):
        index = max(0, min(index, len(self.tabs) - 1))
        if self.tabs.index(tab) != index:
            self.tabs.remove(tab)
            self.tabs.insert(index, tab)
            self.strip.move(tab, index)

    def cycle_tab(self, step):
        if len(self.tabs) > 1:
            self.select_tab(self.tabs[(self.tabs.index(self.tab) + step) % len(self.tabs)])

    def _tab_keys(self, _c, keyval, _code, state) -> bool:
        """⌘T, ⌘W, Ctrl+Tab / Ctrl+Shift+Tab, ⌘⇧[ / ⌘⇧] (⌘: Ctrl or Super)."""
        if not self.TABS:
            return False
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        cmd = ctrl or bool(state & Gdk.ModifierType.SUPER_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and k in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab):
            self.cycle_tab(-1 if shift or k == Gdk.KEY_ISO_Left_Tab else 1)
            return True
        if not cmd:
            return False
        if shift and k in (Gdk.KEY_bracketleft, Gdk.KEY_braceleft, Gdk.KEY_bracketright, Gdk.KEY_braceright):
            self.cycle_tab(-1 if k in (Gdk.KEY_bracketleft, Gdk.KEY_braceleft) else 1)
            return True
        if not shift and k == Gdk.KEY_t:
            self.new_tab()
            return True
        if not shift and k == Gdk.KEY_w:
            self.close_tab()
            return True
        return False

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
                        [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")], answer, parent=self,
                        default="empty")            # Return empties (macOS)

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

    def shortcut_selection(self, desktop: bool = False) -> list:
        """Shortcuts to the selection, here or on the desktop (Vini, like Windows)."""
        from ..shell.desktop import desktop_dir
        dest = desktop_dir() if desktop else Gio.File.new_for_uri(self.history[self.pos])
        return ops.make_shortcuts(self._selected_files(), dest)

    def _context_menu(self, view, widget, x, y):
        Item = ui.menu.Item
        info = view.info_at(widget, x, y)
        if info is not None:
            view.ensure_selected(info)
            sel = view.selected() or [info]
            n = len(sel)
            what = f"“{folder.short_name(sel[0].get_display_name())}”" if n == 1 else f"{n} Items"
            if self._in_trash():                 # Finder's Trash menu
                ui.menu.popup(widget, [[Item("Put Back", self.put_back_selection)],
                                       [Item("Delete Immediately…", self.delete_selection_now)],
                                       [Item("Get Info", self.get_info),
                                        Item(f"Quick Look {what}", self.toggle_quicklook)],
                                       [Item("Empty Trash", self.empty_trash)]], at=(x, y), passthrough=True)
                return
            pkg = packages.menu_items(file_of(sel[0]).get_path(), self) if n == 1 and not is_dir(sel[0]) else []
            sections = [pkg + [Item("Open", self.open_selection)]] if pkg else [[Item("Open", self.open_selection)]]
            if self.TABS and n == 1 and is_dir(sel[0]):
                sections[0].append(Item("Open in New Tab", lambda i=sel[0]: self.open_in_new_tab(i)))
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
            sections.append([Item("Rename" if n == 1 else f"Rename {n} Items…", lambda: self.rename_selection(),
                                  enabled=self._writable_sel(sel)),
                             Item(f"Compress {what}", self.compress_selection, enabled=self._writable_here()),
                             Item("Duplicate", self.duplicate_selection, enabled=self._writable_here())])
            sections.append([Item("Create Shortcut", self.shortcut_selection, enabled=self._writable_here()),
                             Item("Create Shortcut on Desktop", lambda: self.shortcut_selection(desktop=True))])
            sections.append([Item(f"Copy {what}", self.copy_selection),
                             Item("Copy Path" if n == 1 else "Copy Paths", self.copy_path_selection)])
            paths = [file_of(i).get_path() for i in sel]
            if all(paths) and self._writable_sel(sel):                    # Finder's tag colours
                states = {t: tags.state(paths, t) for t in tags.NAMES}
                sections.append([Item("Tags", widget=lambda pop, p=paths, st=states: tags.menu_row(
                    pop, st, lambda t, on: self.tag_files(p, t, on)))])
            pics = [(file_of(i), actions.picture_kind(i)) for i in sel]
            if all(k for _f, k in pics) and self._writable_sel(sel):          # Finder's Quick Actions
                sections.append([Item("Quick Actions", submenu=[[
                    Item("Rotate Left", lambda p=pics: self._rotate(p, False)),
                    Item("Rotate Right", lambda p=pics: self._rotate(p, True))], [
                    Item("Convert to PNG", lambda p=pics: self._convert(p, "png")),
                    Item("Convert to JPEG", lambda p=pics: self._convert(p, "jpeg"))]])])
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
                                                for vid, _i, label in VIEWS],
                                               [Item("Hide Path Bar" if self.pathbar.get_visible() else "Show Path Bar",
                                                     self.toggle_path_bar),
                                                Item("Hide Hidden Files" if self.tab.folder.show_hidden
                                                     else "Show Hidden Files",
                                                     self.toggle_hidden)]])]]
            if self.view is not self.views["columns"]:
                cur = self.view.sort_state()[0]
                sections[-1].append(Item("Sort By", submenu=[[Item(t, lambda _on=None, t=t: self.sort_by(t),
                                                                   checked=t == cur) for t, _d in SORT_BY]]))
            if self.view is self.views["list"]:                    # Finder's View Options for the list
                from .views import LIST_COLUMNS
                shown = self.views["list"].shown_columns()
                sections[-1].append(Item("Show Columns", submenu=[[Item(t, lambda on, t=t: self.show_column(t, on),
                                                                        checked=t in shown)
                                                                   for t, _w, _d in LIST_COLUMNS]]))
                sections[-1].append(Item("Calculate All Sizes", lambda on: self.set_count_sizes(on),
                                         checked=self.views["list"].count_sizes))
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
        """New Terminal at Folder: Sonata's own Terminal first (Vini: the
        old list skipped it, so on a Sonata-only machine nothing opened and
        nothing said why), then $TERMINAL and the common ones."""
        path = gfile.get_path() if gfile else None
        if not path:
            return
        for argv in terminal_commands(path):
            try:
                GLib.spawn_async(argv, working_directory=path, flags=GLib.SpawnFlags.SEARCH_PATH)
                return
            except GLib.Error:
                continue
        ui.dialog.alert("No terminal app was found.",
                        "Install a terminal app, like kitty or Konsole, to open folders in it.",
                        [("ok", "OK", "default")], parent=self)

    # new folder / rename
    def new_folder(self):
        if not self._writable_here():
            return
        where = Gio.File.new_for_uri(self.location())
        ops.new_folder(where, lambda f: (undo.new_folder(f), self._select_when_listed(f.get_basename(), rename=True)),
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
        elif len(sel) > 1:                                 # Finder: Rename N Items…
            from . import batchrename
            batchrename.dialog(self, [file_of(i) for i in sel])

    def _renamable_folder(self):
        """The window's folder, when its name can be changed here (not a
        place like Recents, the Trash, your home or the disk's top)."""
        if self.pos < 0 or self._in_results or self.view is self.views["columns"]:
            return None
        uri = self.history[self.pos]
        if uri in VIRTUAL or ops.is_trash(uri):
            return None
        f = Gio.File.new_for_uri(uri)
        path = f.get_path()
        if not path or path in ("/", GLib.get_home_dir()) or f.get_parent() is None:
            return None
        try:
            info = f.query_info("access::can-rename", Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return None
        if info.has_attribute("access::can-rename") and not info.get_attribute_boolean("access::can-rename"):
            return None
        return f

    def rename_folder(self):
        """The toolbar's folder name becomes a field (Return renames the
        folder you're in, Escape keeps it)."""
        f = self._renamable_folder()
        if f is None or getattr(self, "_title_entry", None) is not None:
            return
        old = f.get_basename()
        entry = Gtk.Entry(text=old, css_classes=["fs-rename", "fs-title-rename"], valign=Gtk.Align.CENTER,
                          width_chars=max(8, min(len(old) + 2, 32)))
        self._title_entry = entry
        self.title.set_visible(False)
        self.title.get_parent().insert_child_after(entry, self.title)
        done = {"v": False}

        def finish(commit):
            if done["v"]:
                return
            done["v"] = True
            new = entry.get_text().strip()
            self._title_entry = None
            if entry.get_parent() is not None:
                entry.get_parent().remove(entry)
            self.title.set_visible(True)
            if not commit or not new or new == old:
                return
            if "/" in new:
                self._error(f"The name “{new}” can’t be used.", None,
                            "Try using a name with fewer characters, or with no punctuation marks.")
                return

            def renamed(nf):
                from . import sidebar
                undo.renamed(nf, old)
                tags.moved([(f, nf)])
                folderprefs.moved(f.get_uri(), nf.get_uri())
                sidebar.moved(f.get_uri(), nf.get_uri())
                self._folder_moved(f.get_uri(), nf.get_uri())
            ops.rename(f, new, renamed, lambda e: self._error(f"The name “{new}” can’t be used.", e))
        entry.connect("activate", lambda *_: finish(True))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (finish(False), True)[1] if k == Gdk.KEY_Escape else False)
        entry.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.idle_add(lambda: (finish(True), False)[1]))
        entry.add_controller(focus)
        entry.grab_focus()
        entry.select_region(0, -1)

    def _folder_moved(self, old_uri, new_uri):
        """A folder you're in (or inside) got a new name: every tab's history
        follows, and the window shows it under its new name."""
        def moved(u):
            if u == old_uri:
                return new_uri
            if u.startswith(old_uri.rstrip("/") + "/"):
                return new_uri.rstrip("/") + u[len(old_uri.rstrip("/")):]
            return u
        for t in self.tabs:
            t.history = [moved(u) for u in t.history]
        if self.pos >= 0:
            self.go(self.history[self.pos], record=False)

    def _commit_rename(self, info, new_name):
        if "/" in new_name:
            self._error(f"The name “{new_name}” can’t be used.", None,
                        "Try using a name with fewer characters, or with no punctuation marks.")
            return
        old_name = file_of(info).get_basename()

        def renamed(f):
            undo.renamed(f, old_name)
            tags.moved([(f.get_parent().get_child(old_name), f)])
            self._select_when_listed(f.get_basename())
        ops.rename(file_of(info), new_name, renamed, lambda e: self._error(f"The name “{new_name}” can’t be used.", e))

    # drag and drop (Finder: same disk moves, another disk copies, Ctrl copies)
    def files_for_drag(self, view, info):
        sel = view.selected()
        return [file_of(i) for i in (sel if info in sel else [info])]

    def drag_started(self, files):
        """The views report a drag leaving them (None when it ends)."""
        self._dragged = list(files or [])

    def is_dragged(self, f) -> bool:
        return any(f.equal(d) for d in self._dragged)

    def spring_open(self, info):
        """A drag hovering on a folder opens it (spring-loaded folders)."""
        if self.is_dragged(file_of(info)):
            return                                        # never into the folder being dragged
        target = info.get_attribute_string("standard::target-uri") or file_of(info).get_uri()
        self.go(target)

    def drop(self, files, dest, copy=False) -> bool:
        """Files dropped on a folder (view background, folder, sidebar, tab).
        Onto their own folder, onto themselves or a folder into its own
        subfolder: silently nothing (ops.drop_plan)."""
        if dest.get_uri() in VIRTUAL or not files:
            return False
        if ops.is_trash(dest.get_uri()):                  # dropped on Trash: move to the Trash
            files = [f for f in files if not ops.is_trash(f.get_uri())]
            if files:                                     # USB/NFS without a Trash: say so
                ops.trash(files, lambda f, e: self._error(f"“{f.get_basename()}” can’t be moved to the Trash.", e))
            return bool(files)
        files = ops.drop_plan(files, dest)
        if not files:
            return False
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
            ops.trash(files, lambda f, e: self._error(f"“{f.get_basename()}” can’t be moved to the Trash.", e),
                      undo.trashed)
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

    def compress_selection(self):
        files = self._selected_files()
        if files and self._writable_here():
            actions.compress(files, lambda z: self._select_when_listed(z.get_basename()),
                             lambda f, e: self._error("The items can’t be compressed.", e))

    def copy_path_selection(self):
        actions.copy_paths(self, self._selected_files())

    def tag_files(self, paths, tag, on):
        """Add / remove a colour tag (tags.py); a folder showing that tag lists them again."""
        failed = tags.toggle(paths, tag, on)
        if failed:
            p, e = failed[0]
            self._error(f"“{os.path.basename(p)}” can’t be tagged.", None,
                        e.strerror or "The disk it’s on can’t keep tags.")
        for t in self.tabs:
            if tags.tag_of(t.folder.uri):
                t.folder.reload()

    def _rotate(self, pics, clockwise):
        actions.rotate(pics, clockwise, lambda: [t.folder.reload() for t in self.tabs],
                       lambda f, e: self._error(f"“{f.get_basename()}” can’t be rotated.", e))

    def _convert(self, pics, kind):
        actions.convert([f for f, _k in pics], kind, None,
                        lambda f, e: self._error(f"“{f.get_basename()}” can’t be converted.", e))

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
                ops.paste_image(self, dest,                     # a copied picture becomes a file
                                on_error=lambda e: self._error("The picture can’t be pasted.", e))
        ops.read_clipboard(self, got)

    # Edit > Undo / Redo (undo.py: one history for every Files window)
    def _editing(self) -> bool:
        focus = self.get_focus()
        return isinstance(focus, Gtk.Editable) or bool(focus and focus.get_ancestor(Gtk.Entry))

    def undo(self):
        if self._editing():
            return False                        # the text field's own undo
        self._run_history(undo.history.take_undo(), "undo", "Undo")
        return True

    def redo(self):
        if self._editing():
            return False
        self._run_history(undo.history.take_redo(), "redo", "Redo")
        return True

    def _run_history(self, action, which, verb):
        if action is None:
            self.get_display().beep()
            return
        fn = getattr(action, which)

        def work(report):
            try:
                fn()
            except GLib.Error as e:
                report(None, e)
        ops._in_thread(work, lambda: [t.folder.reload() for t in self.tabs],
                       lambda _f, e: self._error(f"{verb} {action.label} can’t be completed.", e))

    def _error(self, heading, err, body=None):
        ui.dialog.alert(heading, body or (err.message if err else ""), [("ok", "OK", "default")], parent=self)

    # -- window ------------------------------------------------------------------------
    def _zoom(self):
        self.unmaximize() if self.is_maximized() else self.maximize()

    def show_column(self, title, on):
        shown = [t for t in self.views["list"].shown_columns() if t != title] + ([title] if on else [])
        for t in self.tabs:
            t.views["list"].show_columns(shown)
        config.update("files", list_shown=shown)

    def set_count_sizes(self, on):
        for t in self.tabs:
            t.views["list"].count_sizes = bool(on)
            if on:
                t.folder.reload()                       # rows bound again: their sizes counted
        config.update("files", count_sizes=bool(on))

    def toggle_path_bar(self):
        on = not self.pathbar.get_visible()
        self.pathbar.set_visible(on)
        config.update("files", path_bar=on)

    def _typed_location(self):
        """(where you are, its path to start typing from)."""
        here = self.location() if self.pos >= 0 else None
        return here, (Gio.File.new_for_uri(here).get_path() or "") if here and here not in VIRTUAL else ""

    def _go_typed(self, text, here):
        from .pathbar import resolve
        found = resolve(text, here)
        if found is None:
            self._error("The folder can’t be found.", None, f"“{text}” doesn’t exist.")
            return
        uri, name = found
        self.go(uri)
        if name:
            self._select_when_listed(name)

    def go_to_folder(self):
        """Finder's Go to Folder (Ctrl+Shift+G): a path typed, gone to."""
        here, start = self._typed_location()
        ui.dialog.ask_text("Go to Folder", start, "Go", lambda t: self._go_typed(t, here), parent=self)

    def edit_address(self):
        """Ctrl+L, or a click on the path bar's empty part: type the address
        in the path bar (Windows' address bar, Vini); the Go to Folder
        dialog when the path bar is hidden."""
        if not self.pathbar.get_visible():
            self.go_to_folder()
            return
        here, start = self._typed_location()
        self.pathbar.edit(start, lambda t: self._go_typed(t, here))

    def connect_to_server(self):
        """Finder's Go > Connect to Server (Ctrl+K): server.py; opened here once connected."""
        from . import server
        server.dialog(self, self.go)

    def toggle_hidden(self):
        """The toolbar's eye, View > Show / Hide Hidden Files, Ctrl+Shift+.:
        this folder's hidden files, remembered for it (Vini)."""
        tab = self.tab
        on = not tab.folder.show_hidden
        tab.folder.show_hidden = on
        if tab.uri and tab.uri not in VIRTUAL:
            folderprefs.remember(tab.uri, hidden=on)
        self._sync_hidden_button()
        tab.folder.reload()
        for col in tab.views["columns"].columns[1:]:          # the folders open to the right too
            if col.owner is not None:
                col.owner.show_hidden = on
                col.owner.reload()

    def _sync_hidden_button(self):
        on = bool(self.tab and self.tab.folder.show_hidden)
        self.hidden_btn.set_tooltip_text("Hide Hidden Files" if on else "Show Hidden Files")
        self.hidden_btn.set_icon_name("view-conceal-symbolic" if on else "view-reveal-symbolic")
        if self.hidden_btn.get_active() != on:
            self._syncing = True
            try:
                self.hidden_btn.set_active(on)
            finally:
                self._syncing = False

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
            ("<Control>w", self.close_tab),                  # (the tab keys take it first)
            ("<Control>n", lambda: FilesWindow(self.get_application(), self.history[self.pos]).present()),
            ("<Control>a", lambda: self.view.select_all()),
            ("<Control>1", lambda: self.set_view("icons")),
            ("<Control>2", lambda: self.set_view("list")),
            ("<Control>3", lambda: self.set_view("columns")),
            ("Escape", lambda: self._close_search() if self.search_rev.get_reveal_child()
             else self.view.unselect_all()),
            ("<Control><Shift>n", self.new_folder),
            ("<Control>c", self.copy_selection),
            ("<Control><Alt>c", self.copy_path_selection),               # Finder: Copy as Pathname (⌥⌘C)
            ("<Control>x", lambda: self.copy_selection(cut=True)),
            ("<Control>v", self.paste),
            ("<Control><Alt>v", lambda: self.paste(move=True)),        # Finder: Move Item Here
            ("<Control>d", self.duplicate_selection),
            ("<Control>i", self.get_info),
            ("<Control>y", self.toggle_quicklook),
            ("<Control><Shift>g", self.go_to_folder),
            ("<Control>l", self.edit_address),                          # type the address (path bar)
            ("<Control><Alt>p", self.toggle_path_bar),                  # Finder: ⌥⌘P
            ("<Control>k", self.connect_to_server),                     # Finder: ⌘K
            ("<Control>z", self.undo),
            ("<Control><Shift>z", self.redo),
        ]
        ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for trig, cb in keys:
            ctl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                          action=Gtk.CallbackAction.new(lambda *_a, f=cb: f() is not False)))
        self.add_controller(ctl)
        # Return renames (Finder), F2 too; Delete / Ctrl+Backspace move to the
        # Trash. Captured before the views (Return would open), never while
        # typing in a field.
        keys2 = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys2.connect("key-pressed", self._file_keys)
        self.add_controller(keys2)
        tabs = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)   # ahead of focus keys
        tabs.connect("key-pressed", self._tab_keys)
        self.add_controller(tabs)

    def _file_keys(self, _c, keyval, _code, state) -> bool:
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable) or (focus and focus.get_ancestor(Gtk.Entry)):
            return False
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_F2 and not ctrl:
            if self.view.selected():
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


for _n in _TAB_STATE:            # self.history, self.view... are the front tab's
    setattr(FilesWindow, _n, _tab_state(_n))
del _n
