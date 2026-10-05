"""Finder's path bar (View > Show Path Bar) -- Windows' address bar too: the
folder's place, one button per folder from your home (or the disk's top),
at the bottom of the window. A click opens that folder; files dropped on a
folder go into it. The last one is the folder you're in.

And Go to Folder (Finder: ⇧⌘G; Ctrl+L too, as in other file managers):
type a path ("~/Downloads", "/etc", a URI) and go there; a file's path opens
its folder with the file selected."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import folder  # noqa: E402

ui.register("""
.fs-pathbar { min-height: 24px; padding: 0 8px; background: %(content_bg)s; box-shadow: inset 0 1px %(separator)s; }
.fs-pathbar button { min-height: 18px; padding: 0 5px; border-radius: 5px; border: none; box-shadow: none;
  background: none; color: %(label_secondary)s; font-size: %(text_small)s;
  transition: background-color %(t_fast)s, color %(t_fast)s; }
.fs-pathbar button:hover { background: %(tool_hover)s; color: %(label)s; }
.fs-pathbar button.current { color: %(label)s; font-weight: 600; }
.fs-pathbar button.drop { background: alpha(%(accent)s, 0.22); }
.fs-pathbar button image { -gtk-icon-size: 13px; }
.fs-pathbar .fs-path-sep { color: %(label_tertiary)s; font-size: %(text_small)s; margin: 0 1px; }
""", key="files-pathbar")


def crumbs(uri: str, home: str = None) -> list:
    """[(uri, name, icon)] from the top of the path to `uri`: from your home
    when it's inside it (Finder starts at the disk; your home is where Linux
    keeps your things), else from "/". Places without a path: just themselves."""
    home = home or GLib.get_home_dir()
    f = Gio.File.new_for_uri(uri)
    path = f.get_path()
    if not path:
        return [(uri, folder.display_name(uri), "folder-symbolic")]
    path = os.path.normpath(path)
    if path == home or path.startswith(home.rstrip("/") + "/"):
        top, top_name, top_icon = home, os.path.basename(home) or home, "user-home-symbolic"
    else:
        top, top_name, top_icon = "/", "Computer", "drive-harddisk-symbolic"
    out = [(Gio.File.new_for_path(top).get_uri(), top_name, top_icon)]
    rel = os.path.relpath(path, top)
    if rel != ".":
        cur = top
        for part in rel.split(os.sep):
            cur = os.path.join(cur, part)
            out.append((Gio.File.new_for_path(cur).get_uri(), part, "folder-symbolic"))
    return out


class PathBar(Gtk.ScrolledWindow):
    def __init__(self, go, drop=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER,
                         css_classes=["fs-pathbar"])
        self.go, self.drop = go, drop
        self.box = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
        self.set_child(self.box)
        self.uri = None

    def set_uri(self, uri: str) -> None:
        if not uri or uri == self.uri:
            return
        self.uri = uri
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        items = crumbs(uri)
        for i, (u, name, icon) in enumerate(items):
            if i:
                self.box.append(Gtk.Label(label="›", css_classes=["fs-path-sep"]))
            b = Gtk.Button(focus_on_click=False, can_focus=False)
            row = Gtk.Box(spacing=4)
            row.append(Gtk.Image(icon_name=icon))
            row.append(Gtk.Label(label=name, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=28))
            b.set_child(row)
            if i == len(items) - 1:
                b.add_css_class("current")
            b.connect("clicked", lambda _b, u=u: self.go(u))
            if self.drop is not None:
                self._drop_target(b, u)
            self.box.append(b)
        GLib.idle_add(self._scroll_end)

    def _scroll_end(self):
        adj = self.get_hadjustment()
        adj.set_value(adj.get_upper())
        return False

    def _drop_target(self, button, uri):
        t = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        t.connect("enter", lambda *_a: (button.add_css_class("drop"), Gdk.DragAction.MOVE)[1])
        t.connect("leave", lambda *_a: button.remove_css_class("drop"))
        t.connect("drop", lambda tgt, val, _x, _y: (button.remove_css_class("drop"), self.drop(
            list(val.get_files()), Gio.File.new_for_uri(uri),
            bool(tgt.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)))[1])
        button.add_controller(t)


def resolve(text: str, here: str = None):
    """What Go to Folder opens for `text`: (folder uri, name to select or None),
    or None when there's nothing there."""
    text = (text or "").strip()
    if not text:
        return None
    if "://" in text or text.startswith(("trash:", "sonata:")):
        f = Gio.File.new_for_uri(text)
    else:
        p = os.path.expanduser(text)
        if not os.path.isabs(p):
            base = Gio.File.new_for_uri(here).get_path() if here else None
            p = os.path.join(base or GLib.get_home_dir(), p)
        f = Gio.File.new_for_path(os.path.normpath(p))
    try:
        info = f.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return None
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        return f.get_uri(), None
    parent = f.get_parent()
    return (parent.get_uri(), f.get_basename()) if parent else None
