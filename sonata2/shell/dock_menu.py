"""Dock right-click menus (macOS Big Sur layout).

App:    <open windows>  |  Options > Keep in Dock / Open at Login /
        Open File Location  |  Hide, Quit (running) or Open (not running)
Trash:  Open  |  Empty Trash...
"""
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

AUTOSTART_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                             "autostart")

# All menus of the shell process (including GTK's nested submenus, which
# don't inherit our classes) use `popover.menu`; light/dark is swapped by
# reloading the provider, since submenus aren't under the window's .dark.
MENU_CSS = """
popover.menu { background: none; box-shadow: none; padding: 0; }
popover.menu > contents {
  padding: 5px; border-radius: 7px; min-width: 190px;
  font-family: "SF Pro Text", "Inter", "Cantarell", sans-serif; font-size: 13px;
  color: %(fg)s; background-color: %(bg)s; box-shadow: %(shadow)s;
}
popover.menu modelbutton {
  min-height: 22px; padding: 0 10px; border-radius: 4px; color: inherit; background: none;
}
popover.menu modelbutton:hover, popover.menu modelbutton:selected,
popover.menu modelbutton:focus-visible {
  background-color: %(accent)s; color: #ffffff;
}
popover.menu modelbutton:disabled { color: %(disabled)s; }
popover.menu modelbutton check { min-width: 12px; min-height: 12px; margin-right: 4px;
  border: none; background: none; box-shadow: none; color: inherit; -gtk-icon-size: 12px; }
popover.menu modelbutton arrow { -gtk-icon-size: 12px; color: inherit; }
popover.menu separator { margin: 5px 10px; min-height: 1px; background-color: %(sep)s; }
"""
MENU_THEME = {
    False: {"fg": "rgba(0, 0, 0, 0.85)", "bg": "rgba(236, 236, 236, 0.97)",
            "shadow": "0 0 0 0.5px rgba(0, 0, 0, 0.18), 0 6px 18px rgba(0, 0, 0, 0.22)",
            "accent": "#0a64e1", "disabled": "rgba(0, 0, 0, 0.3)", "sep": "rgba(0, 0, 0, 0.11)"},
    True: {"fg": "#f5f5f7", "bg": "rgba(44, 44, 46, 0.97)",
           "shadow": "0 0 0 0.5px rgba(0, 0, 0, 0.6), inset 0 0 0 0.5px rgba(255, 255, 255, 0.14),"
                     " 0 6px 18px rgba(0, 0, 0, 0.4)",
           "accent": "#0a84ff", "disabled": "rgba(255, 255, 255, 0.3)", "sep": "rgba(255, 255, 255, 0.12)"},
}


def install_css() -> None:
    """Menu style for this process, following the system appearance."""
    prov = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                              Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
    sm = Adw.StyleManager.get_default()

    def load(*_a):
        prov.load_from_data((MENU_CSS % MENU_THEME[sm.get_dark()]).encode())
    sm.connect("notify::dark", load)
    load()


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
    dlg = Adw.MessageDialog(heading=f"Are you sure you want to permanently erase {items} in the Trash?",
                            body="You can't undo this action.")
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("empty", "Empty Trash")
    dlg.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.set_default_response("cancel")
    dlg.connect("response", lambda _d, r: empty_trash() if r == "empty" else None)
    dlg.present()


# -- menus -----------------------------------------------------------------------
def _popup(tile, model: Gio.Menu, group: Gio.SimpleActionGroup) -> Gtk.PopoverMenu:
    tile.insert_action_group("dock", group)
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    pop.set_has_arrow(False)
    pop.set_position(Gtk.PositionType.TOP)
    pop.set_offset(0, -6)
    pop.set_parent(tile)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    tile.label.popdown()
    pop.popup()
    return pop


def _action(group, name, cb, state=None, ptype=None):
    if state is None:
        a = Gio.SimpleAction.new(name, ptype)
        a.connect("activate", lambda _a, p: cb(p) if ptype else cb())
    else:
        a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", state))
        a.connect("activate", lambda act, _p: cb(not act.get_state().get_boolean()))
    group.add_action(a)


def app_menu(dock, key: str, tile) -> Gtk.PopoverMenu:
    info = tile.info
    wins = dock.windows.get(key, [])
    group = Gio.SimpleActionGroup()
    model = Gio.Menu()

    if wins:
        sec = Gio.Menu()
        for i, t in enumerate(wins):
            sec.append(t.title or tile.name, f"dock.win({i})")
        model.append_section(None, sec)
        _action(group, "win", lambda p: dock.manager.activate(wins[p.get_int32()]),
                ptype=GLib.VariantType.new("i"))

    opts = Gio.Menu()
    pinned = key in dock.cfg["pinned"]
    if info:
        opts.append("Keep in Dock", "dock.keep")
        _action(group, "keep", lambda on: dock.set_pinned(key, on), state=pinned)
        did = info.get_id()[:-8]
        opts.append("Open at Login", "dock.login")
        _action(group, "login", lambda on: set_open_at_login(info, on), state=opens_at_login(did))
        loc = Gio.Menu()
        loc.append("Open File Location", "dock.location")
        opts.append_section(None, loc)
        _action(group, "location", lambda: show_in_files(app_file(info)))
    elif pinned:
        opts.append("Keep in Dock", "dock.keep")
        _action(group, "keep", lambda on: dock.set_pinned(key, on), state=True)
    sec = Gio.Menu()
    sec.append_submenu("Options", opts)
    model.append_section(None, sec)

    sec = Gio.Menu()
    if wins:
        sec.append("Hide", "dock.hide")
        _action(group, "hide", lambda: [dock.manager.minimize(t) for t in wins])
        sec.append("Quit", "dock.quit")
        _action(group, "quit", lambda: [dock.manager.close(t) for t in wins])
    elif info:
        sec.append("Open", "dock.open")
        _action(group, "open", lambda: dock.launch(tile))
    model.append_section(None, sec)
    return _popup(tile, model, group)


def trash_menu(tile) -> Gtk.PopoverMenu:
    group = Gio.SimpleActionGroup()
    model = Gio.Menu()
    model.append("Open", "dock.open")
    _action(group, "open", lambda: Gio.AppInfo.launch_default_for_uri("trash:///", None))
    sec = Gio.Menu()
    sec.append("Empty Trash…", "dock.empty")
    _action(group, "empty", confirm_empty_trash)
    model.append_section(None, sec)
    group.lookup_action("empty").set_enabled(_trash_count() > 0)
    return _popup(tile, model, group)
