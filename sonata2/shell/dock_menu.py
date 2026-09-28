"""Dock right-click menus (macOS Big Sur layout).

App:    <open windows>  |  Options > Keep in Dock / Open at Login /
        Open File Location  |  Hide, Quit (running) or Open (not running)
Trash:  Open  |  Empty Trash...
"""
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402

AUTOSTART_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                             "autostart")


# -- helpers ---------------------------------------------------------------------
def app_file(info) -> str:
    """The file behind an app: its real executable (AppImage, script, binary),
    or its .desktop file for Flatpak/Snap and unresolvable commands."""
    exe = info.get_executable() or ""
    if os.path.basename(exe) not in ("flatpak", "snap", "env", "sh", "bash"):
        path = exe if os.path.isabs(exe) else shutil.which(exe) if exe else None
        if path and os.path.exists(path) and not path.startswith("/snap/bin/"):
            return os.path.realpath(path)
    return info.get_filename() or ""


def show_in_files(path: str) -> None:
    """Open the folder containing `path` with the file selected (FileManager1
    D-Bus API: Nautilus, Dolphin, Nemo, Thunar...), else just open the folder."""
    if not path:
        return
    uri = Gio.File.new_for_path(path).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        folder = Gio.File.new_for_path(os.path.dirname(path)).get_uri()
        Gio.AppInfo.launch_default_for_uri(folder, None)


def autostart_path(did: str) -> str:
    return os.path.join(AUTOSTART_DIR, did + ".desktop")


def opens_at_login(did: str) -> bool:
    return os.path.exists(autostart_path(did))


def set_open_at_login(info, on: bool) -> None:
    """XDG autostart entry (a copy of the app's .desktop)."""
    dst = autostart_path(info.get_id()[:-8])
    if on:
        os.makedirs(AUTOSTART_DIR, exist_ok=True)
        shutil.copyfile(info.get_filename(), dst)
    elif os.path.exists(dst):
        os.remove(dst)


def _trash_count() -> int:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    try:
        return sum(1 for _ in os.scandir(os.path.join(data, "Trash", "files")))
    except OSError:
        return 0


def empty_trash() -> None:
    """Permanently delete the Trash's contents (via gvfs trash:///, which also
    covers trash folders on other drives; plain files as a fallback)."""
    trash = Gio.File.new_for_uri("trash:///")
    try:
        for child in trash.enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, None):
            trash.get_child(child.get_name()).delete(None)
        return
    except GLib.Error:
        pass
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    for sub in ("files", "info"):
        d = os.path.join(data, "Trash", sub)
        for e in os.scandir(d) if os.path.isdir(d) else ():
            (shutil.rmtree if e.is_dir(follow_symlinks=False) else os.remove)(e.path)


def confirm_empty_trash() -> None:
    n = _trash_count()
    items = "the item" if n == 1 else f"the {n} items" if n else "the items"
    ui.dialog.alert(f"Are you sure you want to permanently erase {items} in the Trash?",
                    "You can't undo this action.",
                    [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")],
                    on_response=lambda r: empty_trash() if r == "empty" else None)


# -- menus -----------------------------------------------------------------------
def app_menu(dock, key: str, tile):
    info = tile.info
    wins = dock.windows.get(key, [])
    Item = ui.menu.Item
    sections = []
    if wins:
        sections.append([Item(t.title or tile.name, lambda t=t: dock.manager.activate(t))
                         for t in wins])
    pinned = key in dock.cfg["pinned"]
    opts = []
    if info:
        opts = [[Item("Keep in Dock", lambda on: dock.set_pinned(key, on), checked=pinned),
                 Item("Open at Login", lambda on: set_open_at_login(info, on),
                      checked=opens_at_login(info.get_id()[:-8]))],
                [Item("Open File Location", lambda: show_in_files(app_file(info)))]]
    elif pinned:
        opts = [[Item("Keep in Dock", lambda on: dock.set_pinned(key, on), checked=True)]]
    if opts:
        sections.append([Item("Options", submenu=opts)])
    if wins:
        sections.append([Item("Hide", lambda: [dock.manager.minimize(t) for t in wins]),
                         Item("Quit", lambda: [dock.manager.close(t) for t in wins])])
    elif info:
        sections.append([Item("Open", lambda: dock.launch(tile))])
    tile.label.popdown()
    return ui.menu.popup(tile, sections, position=dock.away)


def trash_menu(tile):
    Item = ui.menu.Item
    tile.label.popdown()
    away = getattr(tile.get_parent(), "away", Gtk.PositionType.TOP)
    return ui.menu.popup(tile, [
        [Item("Open", lambda: Gio.AppInfo.launch_default_for_uri("trash:///", None))],
        [Item("Empty Trash…", confirm_empty_trash, enabled=_trash_count() > 0)],
    ], position=away)


def divider_menu(dock, divider):
    """macOS Dock divider menu."""
    Item = ui.menu.Item
    cfg = dock.cfg
    return ui.menu.popup(divider, [
        [Item("Turn Hiding Off" if cfg["autohide"] else "Turn Hiding On",
              lambda: dock.set_option("autohide", not cfg["autohide"])),
         Item("Turn Magnification Off" if cfg["magnification"] else "Turn Magnification On",
              lambda: dock.set_magnification(not cfg["magnification"]))],
        [Item("Position on Screen", submenu=[[
            Item(label, lambda on, e=e: on and dock.set_option("position", e), checked=cfg["position"] == e)
            for e, label in (("left", "Left"), ("bottom", "Bottom"), ("right", "Right"))]]),
         Item("Show Recent Applications", lambda on: dock.set_option("show_recents", on),
              checked=cfg["show_recents"])],
    ], position=dock.away)
