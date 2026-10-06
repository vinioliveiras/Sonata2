"""Files sidebar (Finder source list): Favorites (Recents, Applications,
Desktop, Documents, Downloads, home, GTK bookmarks) and Locations (Computer,
mounted drives with an eject button and a capacity meter -- used / free,
Vini's choice). Live: rebuilt when drives come and go; free space is read
again when the window gets focus (no polling).

Pin a folder (Finder): drag it between two Favorites -- a line shows where
it goes -- or onto the Favorites heading. Right-click a pinned folder:
Remove from Sidebar. Pins are GTK's bookmarks (shared with file dialogs)."""
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
.fs-sidebar list row.pin-before { box-shadow: inset 0 2px %(accent)s; }
.fs-sidebar list row.pin-after { box-shadow: inset 0 -2px %(accent)s; }
.fs-sidebar list row.fs-head { min-height: 22px; margin-top: 8px; }
.fs-sidebar .fs-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.fs-sidebar row image.fs-place { color: %(accent)s; text-shadow: %(accent_halo)s; -gtk-icon-shadow: %(accent_halo)s; }
.fs-sidebar row label.fs-place { font-size: %(text_body)s; }
.fs-sidebar button.fs-eject { min-width: 18px; min-height: 18px; padding: 0; background: none;
  box-shadow: none; border: none; color: %(label_secondary)s; }
.fs-sidebar-top { min-height: 52px; }
.fs-sidebar row.fs-disk { min-height: 40px; }
.fs-sidebar .fs-free { font-size: 10px; color: %(label_secondary)s; }
""", key="files-sidebar")


BOOKMARKS = os.path.join(GLib.get_user_config_dir(), "gtk-3.0", "bookmarks")
EDGE = 7                   # px at a row's top/bottom where a drop pins instead of moving


def read_bookmarks() -> list:
    """[(uri, label)] of GTK's bookmarks file."""
    out = []
    try:
        with open(BOOKMARKS, encoding="utf-8") as f:
            for line in f:
                uri, _, label = line.strip().partition(" ")
                if uri:
                    out.append((uri, label))
    except OSError:
        pass
    return out


def write_bookmarks(marks) -> None:
    os.makedirs(os.path.dirname(BOOKMARKS), exist_ok=True)
    with open(BOOKMARKS + ".new", "w", encoding="utf-8") as f:
        f.writelines(f"{u} {l}".rstrip() + "\n" for u, l in marks)
    os.replace(BOOKMARKS + ".new", BOOKMARKS)


_sidebars = []            # live sidebars of this process: rebuilt when pins change


def _changed() -> None:
    for sb in list(_sidebars):
        sb.rebuild()


def pin(uris, before: str = None) -> None:
    """Add folders to Favorites, before the pinned `before` (else at the end)."""
    marks = read_bookmarks()
    have = {u for u, _l in marks}
    new = [(u, "") for u in uris if u not in have]
    at = next((i for i, (u, _l) in enumerate(marks) if u == before), len(marks))
    write_bookmarks(marks[:at] + new + marks[at:])
    _changed()


def moved(old: str, new: str) -> None:
    """A pinned folder renamed: the pin follows it (and keeps its place)."""
    marks = read_bookmarks()
    if any(u == old for u, _l in marks):
        write_bookmarks([(new if u == old else u, l) for u, l in marks])
        _changed()


def unpin(uri) -> None:
    write_bookmarks([(u, l) for u, l in read_bookmarks() if u != uri])
    _changed()


def _favorites():
    out = [("Recents", "document-open-recent-symbolic", folder.RECENTS),
           ("Applications", "view-app-grid-symbolic", folder.APPS)]
    for kind, title, icon in ((GLib.UserDirectory.DIRECTORY_DESKTOP, "Desktop", "user-desktop-symbolic"),
                              (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "Documents", "folder-documents-symbolic"),
                              (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads", "folder-download-symbolic")):
        uri = folder.xdg_uri(kind)
        if uri:
            out.append((title, icon, uri))
    home = Gio.File.new_for_path(GLib.get_home_dir()).get_uri()
    out.append((GLib.get_user_name(), "user-home-symbolic", home))
    known = {u for _t, _i, u in out}
    for uri, label in read_bookmarks():
        if uri not in known:
            f_ = Gio.File.new_for_uri(uri)
            out.append((label or GLib.uri_unescape_string(f_.get_basename() or uri, None) or uri,
                        "folder-symbolic", uri))
            known.add(uri)
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
        # (no sliding selection, no fade: a sidebar click switches at once -- Vini)
        self.append(Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        self._volumes = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed", "volume-added", "volume-removed"):
            self._volumes.connect(sig, lambda *_: self.rebuild())
        self._current = None
        os.makedirs(os.path.dirname(BOOKMARKS), exist_ok=True)      # (a monitor needs the folder)
        self._marks_mon = Gio.File.new_for_path(BOOKMARKS).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._marks_mon.connect("changed", lambda *_: self._rebuild_soon())      # other apps' pins
        _sidebars.append(self)
        self.connect("destroy", lambda *_: self in _sidebars and _sidebars.remove(self))
        self.rebuild()

    def _rebuild_soon(self) -> None:
        if not getattr(self, "_rb_src", 0):
            def run():
                self._rb_src = 0
                self.rebuild()
                return False
            self._rb_src = GLib.timeout_add(150, run)

    def rebuild(self) -> None:
        self.list.remove_all()
        self._rows = {}
        self._disks = []
        head = self._head("Favorites")
        self._pin_target(head, None)                  # on the heading: pinned at the end
        pinned = {u for u, _l in read_bookmarks()}
        for title, icon, uri in _favorites():
            self._place(title, icon, uri, favorite=True, pinned=uri in pinned)
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
        # disks not mounted yet (still mounting, or one that couldn't be): shown too; a click mounts it
        for vol in self._volumes.get_volumes():
            if vol.get_mount() is not None or not vol.can_mount():
                continue
            uri = "volume:" + (vol.get_uuid() or vol.get_identifier("unix-device") or vol.get_name())
            if uri in self._rows:
                continue
            self._place(vol.get_name(), vol.get_symbolic_icon() or "drive-harddisk-symbolic", uri, disk=True)
            self._rows[uri].volume = vol
        from . import server
        if server.can_browse_network():                                # Finder's Network
            self._place("Network", "network-workgroup-symbolic", server.NETWORK)
        self._place("Trash", "user-trash-symbolic", "trash:///")      # (Vini: Trash in the sidebar)
        from . import tags                                              # Finder's Tags
        self._head("Tags")
        for name in tags.NAMES:
            self._place(name, None, tags.uri(name), tag=name)
        if self._current:
            self.select(self._current)

    def _head(self, text) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["fs-head"])
        row.set_child(Gtk.Label(label=text, xalign=0, margin_start=2))
        self.list.append(row)
        return row

    @staticmethod
    def _folders(files) -> list:
        return [f.get_uri() for f in files
                if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY]

    def _pin_target(self, row, before) -> None:
        """A drop target that only pins (the Favorites heading)."""
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        tgt.connect("enter", lambda *_a: (row.add_css_class("pin-after"), Gdk.DragAction.COPY)[1])
        tgt.connect("leave", lambda *_a: row.remove_css_class("pin-after"))
        tgt.connect("drop", lambda _t, v, *_a: (row.remove_css_class("pin-after"),
                                                 pin(self._folders(v.get_files()), before), True)[2])
        row.add_controller(tgt)

    def _zone(self, row, y) -> str:
        """Where a drag is over a Favorites row: "before"/"after" (pin) or "into"."""
        h = row.get_height()
        return "before" if y < EDGE else "after" if y > h - EDGE else "into"

    def _next_pinned(self, row, zone):
        """The pinned uri a new pin goes before, for a drop at `zone` of `row`."""
        rows = [r for r in self._rows.values() if getattr(r, "favorite", False)]
        i = rows.index(row) + (1 if zone == "after" else 0)
        return next((r.uri for r in rows[i:] if r.pinned), None)

    def _place(self, title, icon, uri, mount=None, disk=False, favorite=False, pinned=False, tag=None) -> None:
        row = Gtk.ListBoxRow()
        row.uri = uri
        row.favorite, row.pinned = favorite, pinned
        box = Gtk.Box(spacing=7)
        if tag:
            from . import tags
            img = tags.dot(tag, 10)
            img.set_margin_start(3)                   # 16 px wide: lined up with the icons above
            img.set_margin_end(3)
        else:
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
        if tag:
            self._tag_target(row, tag)
        elif uri not in folder.VIRTUAL or favorite:
            self._drop_target(row, uri, favorite)
        if pinned:
            menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            menu.connect("pressed", lambda _g, _n, x, y, r=row: ui.menu.popup(
                r, [[ui.menu.Item("Remove from Sidebar", lambda: unpin(r.uri))]], at=(x, y), passthrough=True))
            row.add_controller(menu)
        self.list.append(row)
        self._rows[uri] = row

    def _drop_target(self, row, uri, favorite) -> None:
        """Files dropped on a place move/copy there; on a Favorite's top or
        bottom edge, folders get pinned there instead (a line shows it)."""
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        into_ok = uri not in folder.VIRTUAL

        def show(zone):
            for c in ("drop-target", "pin-before", "pin-after"):
                row.remove_css_class(c)
            if zone == "into" and into_ok:
                row.add_css_class("drop-target")
            elif zone in ("before", "after"):
                row.add_css_class("pin-" + zone)

        def motion(_t, _x, y):
            zone = self._zone(row, y) if favorite else "into"
            show(zone)
            if zone == "into":
                return Gdk.DragAction.MOVE if into_ok else 0
            return Gdk.DragAction.COPY          # (the source offers copy/move; nothing is copied)

        def drop(t, value, _x, y):
            zone = self._zone(row, y) if favorite else "into"
            show(None)
            files = list(value.get_files())
            if zone != "into":
                pin(self._folders(files), self._next_pinned(row, zone))
                return True
            if not into_ok or not self.on_drop:
                return False
            return self.on_drop(files, Gio.File.new_for_uri(uri),
                                bool(t.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK))
        tgt.connect("enter", motion)
        tgt.connect("motion", motion)
        tgt.connect("leave", lambda *_a: show(None))
        tgt.connect("drop", drop)
        row.add_controller(tgt)

    def _tag_target(self, row, tag) -> None:
        """Files dropped on a tag get it (Finder)."""
        tgt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)

        def drop(_t, value, *_a):
            row.remove_css_class("drop-target")
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            win = self.get_root()
            if paths and hasattr(win, "tag_files"):
                win.tag_files(paths, tag, True)
                return True
            return False
        # (COPY: the files stay where they are -- a copy drop makes the source do nothing)
        tgt.connect("enter", lambda *_a: (row.add_css_class("drop-target"), Gdk.DragAction.COPY)[1])
        tgt.connect("motion", lambda *_a: Gdk.DragAction.COPY)
        tgt.connect("leave", lambda *_a: row.remove_css_class("drop-target"))
        tgt.connect("drop", drop)
        row.add_controller(tgt)

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
        if self._quiet or not getattr(row, "uri", None):
            return
        vol = getattr(row, "volume", None)
        if vol is not None:                          # not mounted yet: mount, then open it
            def done(v, res):
                try:
                    v.mount_finish(res)
                except GLib.Error as e:
                    from . import rodisk
                    dev = v.get_identifier("unix-device")
                    if dev and rodisk.try_readonly(dev, e.message):        # a Windows disk left dirty: read-only
                        self._open_readonly(v, dev, e.message)
                        return
                    ui.dialog.alert(f"“{v.get_name()}” couldn't be opened.", e.message,
                                    [("ok", "OK", "default")], parent=self.get_root())
                    return
                m = v.get_mount()
                if m is not None:
                    self._on_open(m.get_root().get_uri())
            vol.mount(Gio.MountMountFlags.NONE, ui.mountop.MountOperation(self.get_root()), None, done)
            return
        self._on_open(row.uri)

    def _open_readonly(self, vol, device, why) -> None:
        """Opened read-only (rodisk), and said why -- once, when it opens."""
        from . import rodisk

        def mounted(path, err):
            if path is None:
                ui.dialog.alert(f"“{vol.get_name()}” couldn't be opened.", err or why,
                                [("ok", "OK", "default")], parent=self.get_root())
                return
            m = vol.get_mount()
            uri = m.get_root().get_uri() if m is not None else (Gio.File.new_for_path(path).get_uri() if path else None)
            if uri:
                self._on_open(uri)
            ui.dialog.alert(f"“{vol.get_name()}” is read-only.", rodisk.NOTE, [("ok", "OK", "default")],
                            parent=self.get_root())
        rodisk.mount_readonly(device, mounted)

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
        op = ui.mountop.MountOperation(self.get_root())
        flags = Gio.MountUnmountFlags.NONE
        if mount.can_eject():
            mount.eject_with_operation(flags, op, None, self._ejected)
        else:
            mount.unmount_with_operation(flags, op, None, lambda m, r: self._ejected(m, r, False))

    def _ejected(self, mount, res, eject=True) -> None:
        # finish what was started: can_eject() may have changed once the drive is gone
        try:
            (mount.eject_with_operation_finish if eject else mount.unmount_with_operation_finish)(res)
        except GLib.Error as e:
            ui.dialog.alert(f"The disk “{mount.get_name()}” wasn't ejected.", e.message,
                            [("ok", "OK", "default")], parent=self.get_root())
