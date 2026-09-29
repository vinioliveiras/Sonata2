"""Dropping files onto the Dock (macOS behaviour):

- files on an app icon: open them with that app. The icon darkens while
  hovered only if the app can open all of them (MIME types).
- files on the Trash: move them to the Trash.
- an application (.desktop file) anywhere on the Dock: pin it at that spot.
- a folder anywhere on the Dock: add it as a stack.
"""
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import apps  # noqa: E402

USER_APPS = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                         "applications")
HOVER = "drop-hover"


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


def _target(on_motion, on_drop, on_leave) -> Gtk.DropTarget:
    t = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
    t.set_preload(True)     # value available during motion, to decide acceptance
    t.connect("enter", on_motion)
    t.connect("motion", on_motion)
    t.connect("leave", on_leave)
    t.connect("drop", on_drop)
    return t


def attach_app(dock, tile) -> None:
    def motion(target, x, _y):
        files = _files(target.get_value())
        if files and all(_is_app(f) for f in files):
            tile.remove_css_class(HOVER)
            return Gdk.DragAction.COPY           # pin, handled on drop
        ok = can_open(tile.info, files)
        (tile.add_css_class if ok else tile.remove_css_class)(HOVER)
        return Gdk.DragAction.COPY if ok else 0

    def drop(target, value, x, _y):
        tile.remove_css_class(HOVER)
        files = _files(value)
        if files and all(_is_app(f) for f in files):
            return pin_files(dock, files, before=tile)
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

    tile.add_controller(_target(motion, drop, lambda *_: tile.remove_css_class(HOVER)))


def attach_trash(dock, tile) -> None:
    def motion(target, _x, _y):
        ok = bool(_files(target.get_value()))
        (tile.add_css_class if ok else tile.remove_css_class)(HOVER)
        return Gdk.DragAction.MOVE if ok else 0

    def drop(_target, value, _x, _y):
        tile.remove_css_class(HOVER)
        moved = False
        from .. import sounds
        sounds.play("trash")
        for f in _files(value):
            try:
                moved = f.trash(None) or moved
            except GLib.Error as e:
                print(f"sonata2-dock: cannot move {f.get_uri()} to the Trash: {e.message}")
        return moved

    tile.add_controller(_target(motion, drop, lambda *_: tile.remove_css_class(HOVER)))


def _is_dir(f: Gio.File) -> bool:
    return f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY


def attach_plate(dock) -> None:
    """Apps dropped between icons get pinned; folders become stacks."""
    def enter(target, x, y):
        drop = target.get_current_drop()
        fmts = drop.get_formats().to_string() if drop else "?"
        print(f"sonata2-dock: drag entered the Dock ({fmts})", flush=True)      # dock.log: DnD debugging
        return motion(target, x, y)

    def motion(target, _x, _y):
        files = _files(target.get_value())
        ok = files and (all(_is_app(f) for f in files) or all(_is_dir(f) for f in files))
        return Gdk.DragAction.COPY if ok else 0

    def drop(_target, value, x, y):
        files = _files(value)
        if files and all(_is_dir(f) for f in files):
            for f in files:
                dock.stacks.add(f.get_path())
            return True
        if not files or not all(_is_app(f) for f in files):
            return False
        return pin_files(dock, files, x=x, y=y)

    t = _target(motion, drop, lambda *_: None)
    t.connect("enter", enter)
    dock.add_controller(t)


def pin_files(dock, files, before=None, x=None, y=0.0) -> bool:
    """Pin dropped .desktop files at the drop position."""
    ok = False
    for f in files:
        did = app_id_for(f)
        if did:
            dock.pin_at(did, before=before, x=x, y=y)
            ok = True
    return ok
