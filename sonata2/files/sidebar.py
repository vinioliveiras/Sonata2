"""Files sidebar (Finder source list): Favorites (Recents, Desktop,
Documents, Downloads, home, GTK bookmarks) and Locations (Computer,
mounted drives with an eject button and a capacity meter -- used / free,
Vini's choice). Live: rebuilt when drives come and go; free space is read
again when the window gets focus (no polling)."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import folder  # noqa: E402

ui.register("""
.fs-sidebar { }
.fs-sidebar list { background: none; padding: 0 10px 10px 10px; }
.fs-sidebar list row { min-height: 28px; padding: 0 6px; border-radius: %(r_menu)s;
  background: none; color: %(label)s; transition: background-color %(t_fast)s; }
.fs-sidebar list row:active { background: %(tool_hover)s; }
.fs-sidebar list row:hover { background: none; }
.fs-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.fs-sidebar list row.drop-target { background: alpha(%(accent)s, 0.25); }
.fs-sidebar list row.fs-head { min-height: 22px; margin-top: 8px; }
.fs-sidebar .fs-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.fs-sidebar row image.fs-place { color: %(accent)s; }
.fs-sidebar row label.fs-place { font-size: %(text_body)s; }
.fs-sidebar button.fs-eject { min-width: 18px; min-height: 18px; padding: 0; background: none;
  box-shadow: none; border: none; color: %(label_secondary)s; }
.fs-sidebar-top { min-height: 52px; }
.fs-sidebar row.fs-disk { min-height: 40px; }
.fs-sidebar .fs-free { font-size: 10px; color: %(label_secondary)s; }
""", key="files-sidebar")


def _favorites():
    out = [("Recents", "document-open-recent-symbolic", folder.RECENTS)]
    for kind, title, icon in ((GLib.UserDirectory.DIRECTORY_DESKTOP, "Desktop", "user-desktop-symbolic"),
                              (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "Documents", "folder-documents-symbolic"),
                              (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads", "folder-download-symbolic")):
        uri = folder.xdg_uri(kind)
        if uri:
            out.append((title, icon, uri))
    home = Gio.File.new_for_path(GLib.get_home_dir()).get_uri()
    out.append((GLib.get_user_name(), "user-home-symbolic", home))
    known = {u for _t, _i, u in out}
    path = os.path.join(GLib.get_user_config_dir(), "gtk-3.0", "bookmarks")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                uri, _, label = line.strip().partition(" ")
                if uri and uri not in known:
                    f_ = Gio.File.new_for_uri(uri)
                    out.append((label or GLib.uri_unescape_string(f_.get_basename() or uri, None) or uri,
                                "folder-symbolic", uri))
                    known.add(uri)
    except OSError:
        pass
    return out


class Sidebar(Gtk.Box):
    """on_open(uri) when a place is clicked."""

    def __init__(self, on_open, top: Gtk.Widget):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["fs-sidebar", "sonata-sidebar"])
        self._on_open = on_open
        self.on_drop = None          # (files, folder Gio.File, copy) -> bool, set by the window
        self._rows = {}
        self._quiet = False
        handle = Gtk.WindowHandle(child=top, css_classes=["fs-sidebar-top"])
        self.append(handle)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", self._activated)
        self.append(Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        self._volumes = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed"):
            self._volumes.connect(sig, lambda *_: self.rebuild())
        self._current = None
        self.rebuild()

    def rebuild(self) -> None:
        self.list.remove_all()
        self._rows = {}
        self._disks = []
        self._head("Favorites")
        for title, icon, uri in _favorites():
            self._place(title, icon, uri)
        self._head("Locations")
        self._place("Computer", "drive-harddisk-symbolic", "file:///", disk=True)
        for mount in self._volumes.get_mounts():
            if mount.is_shadowed():
                continue
            root = mount.get_root()
            if root.get_uri() in self._rows:
                continue
            self._place(mount.get_name(), mount.get_symbolic_icon() or "drive-removable-media-symbolic",
                        root.get_uri(), mount if (mount.can_eject() or mount.can_unmount()) else None, disk=True)
        self._place("Trash", "user-trash-symbolic", "trash:///")      # (Vini: Trash in the sidebar)
        if self._current:
            self.select(self._current)

    def _head(self, text) -> None:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["fs-head"])
        row.set_child(Gtk.Label(label=text, xalign=0, margin_start=2))
        self.list.append(row)

    def _place(self, title, icon, uri, mount=None, disk=False) -> None:
        row = Gtk.ListBoxRow()
        row.uri = uri
        box = Gtk.Box(spacing=7)
        img = Gtk.Image(pixel_size=16, css_classes=["fs-place"])
        if isinstance(icon, str):
            img.set_from_icon_name(icon)
        else:
            img.set_from_gicon(icon)
        box.append(img)
        name = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3, css_classes=["fs-place"])
        if disk:
            row.add_css_class("fs-disk")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
            col.append(name)
            line = Gtk.Box(spacing=6)
            row.meter = ui.progress.meter(0)
            row.meter.set_hexpand(True)
            row.free = Gtk.Label(css_classes=["fs-free"])
            line.append(row.meter)
            line.append(row.free)
            col.append(line)
            box.append(col)
            self._disks.append(row)
            self._read_space(row)
        else:
            box.append(name)
        if mount is not None:
            eject = Gtk.Button(icon_name="media-eject-symbolic", css_classes=["fs-eject"],
                               tooltip_text="Eject", valign=Gtk.Align.CENTER)
            eject.connect("clicked", lambda _b, m=mount: self._eject(m))
            box.append(eject)
        row.set_child(box)
        if uri != folder.RECENTS:                 # drop files on a place = move/copy them there
            tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            tgt.connect("enter", lambda *_a, r=row: (r.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
            tgt.connect("leave", lambda *_a, r=row: r.remove_css_class("drop-target"))
            tgt.connect("drop", lambda t, v, x, y, u=uri, r=row: (r.remove_css_class("drop-target"),
                                                                  self.on_drop and self.on_drop(
                                                                      list(v.get_files()), Gio.File.new_for_uri(u),
                                                                      bool(t.get_current_event_state() &
                                                                           Gdk.ModifierType.CONTROL_MASK)))[1])
            row.add_controller(tgt)
        self.list.append(row)
        self._rows[uri] = row

    def refresh_space(self) -> None:
        for row in self._disks:
            self._read_space(row)

    def _read_space(self, row) -> None:
        def got(f, res):
            try:
                info = f.query_filesystem_info_finish(res)
            except GLib.Error:
                row.meter.set_visible(False)
                return
            total = info.get_attribute_uint64("filesystem::size")
            free = info.get_attribute_uint64("filesystem::free")
            if not total:
                row.meter.get_parent().set_visible(False)
                return
            ui.progress.set_meter(row.meter, (total - free) / total)
            row.free.set_label(f"{ui.fmt.size(free)} free")
            row.set_tooltip_text(f"{ui.fmt.size(free)} available of {ui.fmt.size(total)}\n"
                                 f"{ui.fmt.size(total - free)} used")
        Gio.File.new_for_uri(row.uri).query_filesystem_info_async(
            "filesystem::size,filesystem::free", GLib.PRIORITY_LOW, None, got)

    def _activated(self, _lb, row) -> None:
        if not self._quiet and getattr(row, "uri", None):
            self._on_open(row.uri)

    def select(self, uri) -> None:
        """Highlight the place showing `uri` (none if it isn't one)."""
        self._current = uri
        row = self._rows.get(uri)
        self._quiet = True
        if row:
            self.list.select_row(row)
        else:
            self.list.unselect_all()
        self._quiet = False

    def _eject(self, mount) -> None:
        op = Gtk.MountOperation(parent=self.get_root())
        flags = Gio.MountUnmountFlags.NONE
        if mount.can_eject():
            mount.eject_with_operation(flags, op, None, self._ejected)
        else:
            mount.unmount_with_operation(flags, op, None, self._ejected)

    def _ejected(self, mount, res) -> None:
        try:
            (mount.eject_with_operation_finish if mount.can_eject() else mount.unmount_with_operation_finish)(res)
        except GLib.Error as e:
            ui.dialog.alert(f"The disk “{mount.get_name()}” wasn't ejected.", e.message,
                            [("ok", "OK", "default")], parent=self.get_root())
