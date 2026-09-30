"""Dock right-click menus (macOS Big Sur layout).

App:    <open windows>  |  Options > Keep in Dock / Open at Login /
        Open File Location  |  Minimize (Maximize), Quit (running) or Open (not running)
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
    """Open the folder containing `path` in Sonata's Files, the file
    selected (never another file manager: Vini's call)."""
    if not path:
        return
    from ..files import open_folder
    open_folder(Gio.File.new_for_path(path).get_uri())


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
    from .. import sounds
    sounds.play("empty-trash")
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
        sections.append([Item(t.title or tile.name, lambda t=t: dock.manager.activate(t),
                              on_close=lambda t=t: dock.manager.close(t))       # the x closes that window
                         for t in wins])
    if info and wins:
        sections.append([Item("New Window", lambda: new_window(dock, tile))])
    pinned = key in dock.cfg["pinned"]
    from .dock import PERMANENT
    opts = []
    if info and key in PERMANENT:                  # can't leave the Dock (like Finder)
        opts = [[Item("Open File Location", lambda: show_in_files(app_file(info)))]]
    elif info:
        opts = [[Item("Keep in Dock", lambda on: dock.set_pinned(key, on), checked=pinned),
                 Item("Open at Login", lambda on: set_open_at_login(info, on),
                      checked=opens_at_login(info.get_id()[:-8]))],
                [Item("Open File Location", lambda: show_in_files(app_file(info)))]]
        from .. import gpu
        g = gpu.menu_item(info, Item)
        if g:
            opts.append([g])
    elif pinned:
        opts = [[Item("Keep in Dock", lambda on: dock.set_pinned(key, on), checked=True)]]
    if opts:
        sections.append([Item("Options", submenu=opts)])
    if wins:
        # Minimize; when every window already is: Maximize (brought back, full size)
        if all(t.minimized for t in wins):
            def restore():
                for t in wins:
                    dock.manager.activate(t)
                    dock.manager.set_maximized(t, True)
            first = Item("Maximize", restore)
        else:
            first = Item("Minimize", lambda: [dock.manager.minimize(t) for t in wins])
        sections.append([first,
                         Item("Force Quit", lambda: force_quit(key)),
                         Item("Quit", lambda: [dock.manager.close(t) for t in wins])])
    elif info:
        sections.append([Item("Open", lambda: dock.launch(tile))])
    tile.label.popdown()
    return ui.menu.popup(tile, sections, position=dock.away)


def force_quit(key) -> bool:
    """macOS Force Quit: the app's processes are killed at once (SIGKILL),
    for an app that doesn't answer. Its windows' processes come from
    Wayfire (IPC list-views: pid of each view whose app id is this app)."""
    import os
    import signal
    from .. import apps
    from ..wl.wfipc import WayfireIPC
    views = WayfireIPC().call("window-rules/list-views") or []
    pids = {v.get("pid") for v in views if isinstance(v, dict) and v.get("pid", 0) > 1
            and (apps.match_app_id(v.get("app-id") or "") or v.get("app-id")) == key}
    pids.discard(os.getpid())
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as e:
            print(f"sonata2-dock: force quit {key} ({pid}): {e}")
    return bool(pids)


NEW_WINDOW = ("new-window", "newwindow", "window-new", "new-empty-window")


def new_window(dock, tile) -> None:
    """Another window of the app (macOS Dock "New Window", or a middle-click
    on the icon), whichever the app offers first:
    1. its desktop entry's New Window action (browsers, VS Code, Files...);
    2. its running GApplication's "new-window" action over D-Bus (GTK apps
       whose "open again" only raises the window they have);
    3. launching it again (apps that start one window per launch)."""
    info = tile.info
    ctx = tile.get_display().get_app_launch_context()
    acts = info.list_actions() if hasattr(info, "list_actions") else []
    for a in acts:
        if a.lower().replace("_", "-") in NEW_WINDOW:
            info.launch_action(a, ctx)
            return
    if _gapplication_new_window(info):
        return
    try:
        info.launch([], ctx)
    except GLib.Error as e:
        print(f"sonata2-dock: cannot open a new window of {info.get_id()}: {e.message}")


def _gapplication_new_window(info) -> bool:
    app_id = (info.get_id() or "")[:-len(".desktop")] if (info.get_id() or "").endswith(".desktop") else ""
    if not app_id or not Gio.dbus_is_name(app_id):
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (app_id,)), None, Gio.DBusCallFlags.NONE, 300, None)
        if not owner.unpack()[0]:
            return False
        path = "/" + app_id.replace(".", "/").replace("-", "_")
        described = bus.call_sync(app_id, path, "org.gtk.Actions", "List", None, None,
                                  Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        name = next((a for a in described if a.lower().replace("_", "-") in NEW_WINDOW), None)
        if not name:
            return False
        bus.call_sync(app_id, path, "org.gtk.Actions", "Activate", GLib.Variant("(sava{sv})", (name, [], {})),
                      None, Gio.DBusCallFlags.NONE, 500, None)
        return True
    except GLib.Error:
        return False


def trash_menu(tile):
    Item = ui.menu.Item
    tile.label.popdown()
    away = getattr(tile.get_parent(), "away", Gtk.PositionType.TOP)
    return ui.menu.popup(tile, [
        [Item("Open", lambda: _open_folder("trash:///"))],
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


def _open_folder(uri: str) -> None:
    from ..files import open_folder
    open_folder(uri)                      # always Sonata's Files
