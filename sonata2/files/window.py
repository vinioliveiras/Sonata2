"""Files window (macOS Big Sur Finder): sidebar | unified toolbar + view.

Icons / List / Columns views (views.py), navigation (back/forward/
enclosing folder), open with the default app, filter-as-you-type search of
the current folder, hidden files toggle, live folder updates. Context
menus, rename, drag and drop come next (ROADMAP M7)."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import folder  # noqa: E402
from .folder import RECENTS, file_of, is_dir  # noqa: E402
from .views import ColumnsView, IconsView, ListView  # noqa: E402
from .sidebar import Sidebar  # noqa: E402

VIEWS = (("icons", "view-grid-symbolic", "as Icons"), ("list", "view-list-symbolic", "as List"),
         ("columns", "view-dual-symbolic", "as Columns"))
DEFAULTS = {"view": "icons"}

ui.register("""
window.sonata-files { background: %(content_bg)s; color: %(label)s; font-family: %(font)s;
  font-size: %(text_body)s; }
.fs-paned > separator { min-width: 1px; background: %(separator)s; }
.fs-toolbar { min-height: 52px; padding: 0 10px 0 8px; background: %(content_bg)s;
  box-shadow: inset 0 -1px %(separator)s; }
.fs-toolbar .fs-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.fs-toolbar button { min-width: 28px; min-height: 26px; padding: 0 4px; border-radius: %(r_button)s;
  background: none; box-shadow: none; border: none; color: %(tool_icon)s; }
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
        super().__init__(application=app, title="Files", css_classes=["sonata-files"],
                         default_width=920, default_height=560)
        self.set_size_request(560, 320)
        self.history, self.pos = [], -1
        self.folder = folder.Folder(self._loaded, self._load_failed)

        self.sidebar = Sidebar(self.go, ui.window.traffic_lights(self.close, self.minimize, self._zoom))
        paned = Gtk.Paned(start_child=self.sidebar, shrink_start_child=False, resize_start_child=False,
                          css_classes=["fs-paned"])
        paned.set_position(200)
        self.sidebar.set_size_request(150, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
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
        ]
        ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for trig, cb in keys:
            ctl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trig),
                                          action=Gtk.CallbackAction.new(lambda *_a, f=cb: (f(), True)[1])))
        self.add_controller(ctl)
