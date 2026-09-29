"""Dropping files onto the Dock (macOS behaviour):

- files on an app icon: open them with that app (running or not). The icon
  darkens while hovered only if the app can open all of them (MIME types).
  Held there a moment, an open app's windows come forward -- minimized ones
  too -- to drop the item into one (spring-loading).
- files on the Trash: move them to the Trash; an app: uninstall it (asked first).
- an application (.desktop file) anywhere on the Dock: pin it at that spot.
- a folder anywhere on the Dock: add it as a stack.
"""
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import apps  # noqa: E402

USER_APPS = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                         "applications")
HOVER = "drop-hover"
SPRING_MS = 700            # hold a dragged item on an open app's icon: its windows come forward


def _files(value) -> list:
    return list(value.get_files()) if value else []


def _is_app(f: Gio.File) -> bool:
    return (f.get_path() or "").endswith(".desktop")


def can_open(info, files) -> bool:
    """True if `info` declares support for every file's content type
    (folders: file managers only)."""
    if not info or not files:
        return False
    supported = info.get_supported_types() or []
    for f in files:
        try:
            ctype = f.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE,
                                 None).get_content_type()
        except GLib.Error:
            return False
        if not any(Gio.content_type_is_a(ctype, t) for t in supported):
            return False
    return True


def app_id_for(f: Gio.File):
    """Desktop id for a dropped .desktop file; files outside the application
    folders are copied to ~/.local/share/applications first."""
    path = f.get_path()
    did = os.path.basename(path)[:-8]
    if apps.lookup(did):
        return did
    os.makedirs(USER_APPS, exist_ok=True)
    shutil.copyfile(path, os.path.join(USER_APPS, did + ".desktop"))
    apps.refresh()
    return did if apps.lookup(did) else None


class _Files:
    """Stands in for a Gdk.FileList value (what the handlers read)."""

    def __init__(self, files):
        self._files = files

    def get_files(self):
        return self._files


class _Target:
    """What the handlers see as "target": the files read so far, and the drop."""

    def __init__(self):
        self.files, self.drop = None, None

    def get_value(self):
        return _Files(self.files) if self.files is not None else None

    def get_current_drop(self):
        return self.drop


URI_LIST = "text/uri-list"


def _read_uris(drop, done) -> None:
    """The dragged files as a plain uri list. (A Gdk.FileList target lets GTK
    pick the portal's file-transfer format first, which fails to convert
    without the document portal: drags from Files and the Downloads stack
    were refused.)"""
    def got_stream(d, res):
        try:
            stream, _mime = d.read_finish(res)
        except GLib.Error:
            done(None)
            return

        def got_bytes(st, r):
            try:
                data = st.read_bytes_finish(r).get_data() or b""
            except GLib.Error:
                done(None)
                return
            uris = [ln.strip() for ln in data.decode("utf-8", "replace").splitlines()
                    if ln.strip() and not ln.startswith("#")]
            done([Gio.File.new_for_uri(u) for u in uris])
        stream.read_bytes_async(1 << 20, GLib.PRIORITY_DEFAULT, None, got_bytes)
    drop.read_async([URI_LIST], GLib.PRIORITY_DEFAULT, None, got_stream)


def _target(on_motion, on_drop, on_leave) -> Gtk.DropTargetAsync:
    """A file drop target that reads text/uri-list itself (see _read_uris);
    the handlers get (target, x, y) / (target, value, x, y) as before."""
    t = Gtk.DropTargetAsync.new(Gdk.ContentFormats.new([URI_LIST]),
                                Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
    st = _Target()

    def accept(_t, drop):
        return drop.get_formats().contain_mime_type(URI_LIST)

    def enter(_t, drop, x, y):
        st.drop, st.files = drop, None

        def ready(files):
            if st.drop is drop:
                st.files = files or []
        _read_uris(drop, ready)
        return Gdk.DragAction.COPY                  # decided on the next motion, once read

    def motion(_t, _drop, x, y):
        if st.files is None:
            return Gdk.DragAction.COPY
        return on_motion(st, x, y) or 0

    def leave(_t, _drop):
        on_leave(st)
        st.drop, st.files = None, None

    def dropped(_t, drop, x, y):
        def finish(files):
            ok = bool(files) and bool(on_drop(st, _Files(files), x, y))
            drop.finish(Gdk.DragAction.COPY if ok else 0)
            st.drop, st.files = None, None
        if st.files is not None:
            finish(st.files)
        else:
            _read_uris(drop, finish)
        return True
    t.connect("accept", accept)
    t.connect("drag-enter", enter)
    t.connect("drag-motion", motion)
    t.connect("drag-leave", leave)
    t.connect("drop", dropped)
    return t


def attach_app(dock, tile) -> None:
    def motion(target, x, y):
        files = _files(target.get_value())
        if files and all(_is_app(f) for f in files):
            tile.remove_css_class(HOVER)
            ok, p = tile.compute_point(dock, Graphene.Point().init(x, y))
            if ok:
                dock.show_drop_gap(p.x, p.y)     # the icons part where it will land
            return Gdk.DragAction.COPY           # pin, handled on drop
        ok = can_open(tile.info, files)
        (tile.add_css_class if ok else tile.remove_css_class)(HOVER)
        if files and tile.key in dock.windows and not spring["src"]:
            spring["src"] = GLib.timeout_add(SPRING_MS, spring_open)
        return Gdk.DragAction.COPY if ok else 0

    # spring-loading (macOS): held over the icon of an open app, its windows come
    # forward -- minimized ones too -- so the item can be dropped into one
    spring = {"src": 0}

    def spring_open():
        spring["src"] = 0
        for t in dock.windows.get(tile.key, ()):
            dock.manager.activate(t)
        return False

    def spring_cancel():
        if spring["src"]:
            GLib.source_remove(spring["src"])
            spring["src"] = 0

    def drop(target, value, x, _y):
        spring_cancel()
        tile.remove_css_class(HOVER)
        slot = dock.hide_drop_gap()
        files = _files(value)
        if files and all(_is_app(f) for f in files):
            tiles = dock.app_tiles()
            return pin_files(dock, files, before=tiles[slot] if 0 <= slot < len(tiles) else tile)
        if not can_open(tile.info, files):
            return False
        try:
            tile.info.launch(files, tile.get_display().get_app_launch_context())
        except GLib.Error as e:
            print(f"sonata2-dock: cannot open files with {tile.info.get_id()}: {e.message}")
            return False
        if tile.key not in dock.windows:
            dock.launch_feedback(tile)
        return True

    tile.add_controller(_target(motion, drop, lambda *_: (spring_cancel(), tile.remove_css_class(HOVER),
                                                           dock.hide_drop_gap_soon())))


def attach_trash(dock, tile) -> None:
    def motion(target, _x, _y):
        ok = bool(_files(target.get_value()))
        (tile.add_css_class if ok else tile.remove_css_class)(HOVER)
        dock.hide_drop_gap()                     # over the Trash: no gap left open between icons
        return Gdk.DragAction.MOVE if ok else 0

    def drop(_target, value, _x, _y):
        tile.remove_css_class(HOVER)
        dock.hide_drop_gap()
        dropped = _files(value)
        if dropped and all(_is_app(f) for f in dropped):     # an app (from Launchpad, Files): uninstall it
            if dock._drag:
                # a Dock icon: it stays until the uninstall is confirmed and done
                dock._drag["dropped"] = True
            _uninstall_apps(dock, dropped)
            return True
        moved = False
        # Sonata's own apps (Files, Settings, Launchpad) never go to the Trash
        from ..apps import PROTECTED
        files = [f for f in _files(value) if not (_is_app(f) and (f.get_basename() or "").startswith(PROTECTED))]
        if not files:
            return False
        for f in files:
            try:
                moved = f.trash(None) or moved
            except GLib.Error as e:
                print(f"sonata2-dock: cannot move {f.get_uri()} to the Trash: {e.message}")
        if moved:                                # no sound when nothing could be moved
            from .. import sounds
            sounds.play("trash")
        return moved

    tile.add_controller(_target(motion, drop, lambda *_: tile.remove_css_class(HOVER)))


def _uninstall_apps(dock, files) -> None:
    """Apps dropped on the Trash: asked, then uninstalled (macOS: dragging an
    app to the Trash deletes it). Launchpad closes first so the question
    isn't hidden under it."""
    from .uninstall_ui import ask
    from .dock import close_launchpad
    close_launchpad()
    for f in files:
        did = os.path.basename(f.get_path() or "")[:-8]
        info = apps.lookup(did) or apps.DesktopAppInfo.new_from_filename(f.get_path())
        if info:
            ask(info, done=lambda ok, k=did: ok and dock.set_pinned(k, False))


def _is_dir(f: Gio.File) -> bool:
    return f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY


def attach_plate(dock) -> None:
    """Apps dropped between icons get pinned; folders become stacks."""
    def motion(target, x, y):
        files = _files(target.get_value())
        apps_only = bool(files) and all(_is_app(f) for f in files)
        if apps_only:
            dock.show_drop_gap(x, y)             # the icons part where it will land
        ok = files and (apps_only or all(_is_dir(f) for f in files))
        return Gdk.DragAction.COPY if ok else 0

    def drop(_target, value, x, y):
        slot = dock.hide_drop_gap()
        files = _files(value)
        if files and all(_is_dir(f) for f in files):
            for f in files:
                dock.stacks.add(f.get_path())
            return True
        if not files or not all(_is_app(f) for f in files):
            return False
        tiles = dock.app_tiles()
        if 0 <= slot < len(tiles):
            return pin_files(dock, files, before=tiles[slot])
        return pin_files(dock, files, x=x, y=y)

    dock.add_controller(_target(motion, drop, lambda *_: dock.hide_drop_gap_soon()))


def pin_files(dock, files, before=None, x=None, y=0.0) -> bool:
    """Pin dropped .desktop files at the drop position."""
    ok = False
    for f in files:
        did = app_id_for(f)
        if did:
            dock.pin_at(did, before=before, x=x, y=y)
            ok = True
    return ok
