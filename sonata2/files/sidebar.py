"""Files sidebar (Finder source list): Favorites (Recents, Desktop,
Documents, Downloads, home, GTK bookmarks) and Locations (Computer,
mounted drives with an eject button). Live: rebuilt when drives come and go."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import folder  # noqa: E402

ui.register("""
.fs-sidebar { background: %(sidebar_bg)s; }
.fs-sidebar list { background: none; padding: 0 10px 10px 10px; }
.fs-sidebar list row { min-height: 28px; padding: 0 6px; border-radius: %(r_menu)s;
  background: none; color: %(label)s; }
.fs-sidebar list row:hover { background: none; }
.fs-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.fs-sidebar list row.fs-head { min-height: 22px; margin-top: 8px; }
.fs-sidebar .fs-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.fs-sidebar row image.fs-place { color: %(accent)s; }
.fs-sidebar row label.fs-place { font-size: %(text_body)s; }
.fs-sidebar button.fs-eject { min-width: 18px; min-height: 18px; padding: 0; background: none;
  box-shadow: none; border: none; color: %(label_secondary)s; }
.fs-sidebar-top { min-height: 52px; }
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
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["fs-sidebar"])
        self._on_open = on_open
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
        self._head("Favorites")
        for title, icon, uri in _favorites():
            self._place(title, icon, uri)
        self._head("Locations")
        self._place("Computer", "drive-harddisk-symbolic", "file:///")
        for mount in self._volumes.get_mounts():
            if mount.is_shadowed():
                continue
            root = mount.get_root()
            if root.get_uri() in self._rows:
                continue
            self._place(mount.get_name(), mount.get_symbolic_icon() or "drive-removable-media-symbolic",
                        root.get_uri(), mount if (mount.can_eject() or mount.can_unmount()) else None)
        if self._current:
            self.select(self._current)

    def _head(self, text) -> None:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["fs-head"])
        row.set_child(Gtk.Label(label=text, xalign=0, margin_start=2))
        self.list.append(row)

    def _place(self, title, icon, uri, mount=None) -> None:
        row = Gtk.ListBoxRow()
        row.uri = uri
        box = Gtk.Box(spacing=7)
        img = Gtk.Image(pixel_size=16, css_classes=["fs-place"])
        if isinstance(icon, str):
            img.set_from_icon_name(icon)
        else:
            img.set_from_gicon(icon)
        box.append(img)
        box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3, css_classes=["fs-place"]))
        if mount is not None:
            eject = Gtk.Button(icon_name="media-eject-symbolic", css_classes=["fs-eject"],
                               tooltip_text="Eject", valign=Gtk.Align.CENTER)
            eject.connect("clicked", lambda _b, m=mount: self._eject(m))
            box.append(eject)
        row.set_child(box)
        self.list.append(row)
        self._rows[uri] = row

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
