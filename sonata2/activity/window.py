"""Task Manager: Windows 11 Task Manager's structure with the macOS look
(Sonata tokens, glass toolbar, translucent sidebar).

The sidebar holds the pages: Processes (Apps -- expandable to their
processes --, Background processes and System processes, with Name,
Status, CPU, Memory, Disk and Network cells tinted like a heat map),
Performance (CPU, Memory, disks, network interfaces and GPUs with
sparklines; the selected one in a big 60 s graph with its details), App
history (CPU time and disk use per app since a date), Startup apps (login
items with an Enabled switch), Users (per-user totals), Details (every
process) and Services (systemd user and system services: Start, Stop,
Restart).

The glass toolbar shows the page's title, its commands (End task,
Properties, Enable/Disable, Start/Stop/Restart...) and a search field that
filters the page. Right-click a process: End task, Open file location,
Search online, Properties. Double-click (or ⌘I) shows Properties; ⌘F
searches; Delete ends the selected task; ⌘1..7 switch pages; ⌘W closes.

Data comes only from /proc and /sys (procfs.py), sampled every 2 s off the
main loop and only while the window is on screen. Rows are GObjects
updated in place: selections and scroll positions survive refreshes."""
import os
import signal
import time
from urllib.parse import quote_plus

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..backend import system  # noqa: E402
from . import manage, pages, procfs  # noqa: E402
from .pages import (DetailsPage, HistoryPage, ProcessesPage, ProcessRow, ServicesPage, StartupPage,  # noqa: E402
                    UsersPage, fmt_cpu, fmt_mem)
from .performance import PerformancePage  # noqa: E402
from .procfs import fmt_count, fmt_cpu_time  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.activity"
INTERVAL = 2                      # seconds between samples
DEFAULTS = {"page": "processes", "sidebar_width": 190}
HISTORY_SAVE_EVERY = 30           # samples (~1 minute)
GENERIC_ICON = "application-x-executable"
SEARCH_URL = "https://duckduckgo.com/?q="
# launchers and interpreters: their name says nothing about the app
_NOT_APPS = {"python", "python3", "sh", "bash", "zsh", "fish", "env", "sudo", "flatpak", "bwrap", "java", "node",
             "perl", "ruby", "systemd", "dbus-daemon", "gjs", "electron", "xdg-open", "sonata2",
             # Windows-game launchers: one of them in a game's entry made every wine
             # process look like that game (Vini: Halloween shown as Red Dead Redemption)
             "wine", "wine64", "wine-preloader", "wine64-preloader", "wineserver", "umu-run", "proton",
             "pressure-vessel", "pressure-vessel-wrap", "reaper", "gamemoderun", "mangohud", "gamescope"}


# -- app icons ----------------------------------------------------------------------------------
def icon_index() -> dict:
    """Installed apps by the names their processes go by: the executable,
    StartupWMClass and desktop id -> (Gio.Icon, app name). Runs off the
    main loop (reads every .desktop file once)."""
    idx = {}
    for info in Gio.AppInfo.get_all():
        icon = info.get_icon()
        if icon is None:
            continue
        name = info.get_display_name() or info.get_name()
        keys = []
        exe = info.get_executable() or ""
        if exe:
            keys.append(os.path.basename(exe))
        wm = info.get_startup_wm_class() if hasattr(info, "get_startup_wm_class") else None
        if wm:
            keys.append(wm)
        did = (info.get_id() or "").removesuffix(".desktop")
        if did:
            keys += [did, did.rsplit(".", 1)[-1]]
        for k in keys:
            k = k.lower()
            if k and k not in _NOT_APPS:
                idx.setdefault(k, (icon, name))
    return idx


def app_for(p, idx: dict):
    """(app key or None, icon, display name) of a process: its app's when one
    matches, else no icon and the process name. Sonata's own apps run as
    `python3 -m sonata2 <component>` and show as themselves."""
    if "sonata2" in p.cmdline:
        parts = p.cmdline.split()
        for i, a in enumerate(parts[:-1]):
            if a == "sonata2" or a.endswith("/sonata2"):
                key = f"io.github.vinioliveiras.sonata2.{parts[i + 1].lower()}"
                if key in idx:
                    return (key,) + idx[key]
    if getattr(p, "steam", ""):                       # a Steam game's process: the game, by its app id
        from .. import steamgames
        key = f"steam_app_{p.steam}"
        got = steamgames.shown(key, p.name)
        if got:
            name, icon = got
            return key, icon, name
    for key in (p.exe.lower(), p.comm.lower()):
        if key and key not in _NOT_APPS and key in idx:
            icon, name = idx[key]
            # the app's own process takes its name; helpers keep theirs
            main = p.comm.lower() == key or p.exe.lower().startswith(p.comm.lower())
            return key, icon, (name if main else p.name)
    return None, None, p.name


class TaskManagerWindow(Gtk.ApplicationWindow):
    def __init__(self, app, sampler: procfs.Sampler = None):
        super().__init__(application=app, title="Task Manager")
        for c in ("sonata-activity", "sonata-glass"):
            self.add_css_class(c)
        ui.window.standard(self)
        self.set_default_size(1040, 680)
        self.set_size_request(720, 460)
        self.settings = config.load("activity", DEFAULTS)
        self.sampler = sampler or procfs.Sampler()
        self.history = manage.History(config.load("activity-history", {"since": 0, "apps": {}}))
        self.icons = None                   # icon_index(), filled off the main loop
        self.rows = {}                      # (pid, start) -> ProcessRow
        self.pid_rows = {}                  # pid -> ProcessRow
        self.all_store = Gio.ListStore(item_type=ProcessRow)
        self.groups = ({}, [], [])          # apps {key: [pids]}, background [pids], system [pids]
        self.app_info = {}                  # app key -> (icon, name)
        self.last = None
        self._timer = 0
        self._busy = False
        self._alive = True
        self._restoring = False
        self._samples = 0
        self._info = None

        self.pages = [ProcessesPage(self), PerformancePage(self), HistoryPage(self), StartupPage(self),
                      UsersPage(self), DetailsPage(self), ServicesPage(self)]
        self.page_by_id = {p.id: p for p in self.pages}
        for p in self.pages:
            p.attach(False)
        self.page_id = None

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self._toolbar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True,
                               css_classes=["tm-content"])
        for p in self.pages:
            self.stack.add_named(p.widget, p.id)
        paned = Gtk.Paned(start_child=self._sidebar(), end_child=self.stack, shrink_start_child=False,
                          resize_start_child=False, shrink_end_child=False, vexpand=True)
        paned.set_position(self.settings.get("sidebar_width", DEFAULTS["sidebar_width"]))
        col.append(paned)
        self.set_child(col)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("map", self._mapped)
        self.connect("unmap", lambda *_: self._schedule())
        self.connect("realize", self._realized)
        self.connect("destroy", self._destroyed)
        self.show_page(self.settings["page"] if self.settings["page"] in self.page_by_id else "processes")
        system.run_async(icon_index, self._icons_ready)

    # -- chrome ---------------------------------------------------------------------------------
    def _toolbar(self) -> Gtk.Widget:
        self.toolbar = ui.window.glass_toolbar(self)
        bar = self.toolbar.get_child()
        bar.add_css_class("tm-toolbar")
        self.title_lbl = Gtk.Label(css_classes=["tm-title"], xalign=0)
        bar.set_start_widget(self.title_lbl)
        end = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.page_buttons = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hhomogeneous=False)
        for p in self.pages:
            self.page_buttons.add_named(p.buttons, p.id)
        end.append(self.page_buttons)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=16, valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda e: self.page_by_id[self.page_id].search(e.get_text()))
        self.search.connect("stop-search", lambda e: e.set_text(""))
        end.append(self.search)
        bar.set_end_widget(end)
        return self.toolbar

    def _sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sonata-sidebar", "tm-sidebar"])
        box.set_size_request(170, -1)
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        for p in self.pages:
            row = Gtk.ListBoxRow()
            row.page_id = p.id
            h = Gtk.Box(spacing=8)
            h.append(Gtk.Image(icon_name=p.icon))
            h.append(Gtk.Label(label=p.title, xalign=0, ellipsize=Pango.EllipsizeMode.END))
            row.set_child(h)
            self.side.append(row)
        self.side.connect("row-selected", lambda _l, r: r is not None and self.show_page(r.page_id))
        box.append(self.side)
        return box

    def show_page(self, page_id: str) -> None:
        if page_id == self.page_id:
            return
        if self.page_id is not None:
            self.page_by_id[self.page_id].attach(False)
        self.page_id = page_id
        page = self.page_by_id[page_id]
        row = self.side.get_row_at_index(self.pages.index(page))
        if self.side.get_selected_row() is not row:
            self.side.select_row(row)
        self.stack.set_visible_child_name(page_id)
        self.page_buttons.set_visible_child_name(page_id)
        self.title_lbl.set_label(page.title)
        self.search.set_visible(page_id != "performance")
        if self.search.get_text():
            self.search.set_text("")
        page.search("")
        page.attach(True)
        page.shown()
        if self.last is not None and page_id not in ("performance", "startup", "services"):
            page.update(self.last)                  # catch up on what changed while it was hidden
        if self.settings.get("page") != page_id:
            self.settings["page"] = page_id
            config.update("activity", page=page_id)

    # -- sampling -------------------------------------------------------------------------------
    def _icons_ready(self, idx) -> None:
        self.icons = idx or {}
        if self._alive:
            self._schedule()

    def _mapped(self, *_a) -> None:
        view = getattr(self.page_by_id[self.page_id], "view", None)
        if view is not None and self.get_focus() in (None, self.search.get_delegate() or self.search):
            view.grab_focus()                       # the list, not the search field, has the keyboard
        self._schedule()

    def _realized(self, *_a) -> None:
        surface = self.get_surface()
        if surface is not None:
            surface.connect("notify::state", lambda *_: self._schedule())

    def _on_screen(self) -> bool:
        if not self.get_mapped():
            return False
        surface = self.get_surface()
        if surface is not None and hasattr(surface, "get_state"):
            hidden = Gdk.ToplevelState.MINIMIZED
            if hasattr(Gdk.ToplevelState, "SUSPENDED"):          # Wayland: minimized/hidden windows
                hidden |= Gdk.ToplevelState.SUSPENDED
            if surface.get_state() & hidden:
                return False
        return True

    def _schedule(self) -> None:
        """Sample only while the window is on screen (pause otherwise)."""
        run = self._alive and self.icons is not None and self._on_screen()
        if run and not self._timer:
            self._timer = GLib.timeout_add_seconds(INTERVAL, self._tick)
            self._tick()
            if self.last is None or self.last.interval <= 0:            # rates need a second sample soon
                GLib.timeout_add(700, lambda: (self._timer and self._tick(), False)[1])
        elif not run and self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self) -> bool:
        if not self._busy and self._alive:
            self._busy = True
            system.run_async(self.sampler.sample, self._apply)
        return True

    def _apply(self, snap) -> None:
        self._busy = False
        if not self._alive or snap is None:
            return
        self.apply_snapshot(snap)

    def apply_snapshot(self, snap: procfs.Snapshot) -> None:
        """Update the shared rows in place, regroup, feed the pages."""
        self.last = snap
        pages.heat_mem.total = snap.memory.get("total", 1) or 1      # before any cell binds
        idx = self.icons or {}
        self._restoring = True                     # rows leaving/moving don't lose selections
        live, new, app_keys = {}, [], {}
        for p in snap.procs.values():
            key = (p.pid, p.start_ticks)
            row = self.rows.get(key)
            if row is None:
                akey, icon, name = app_for(p, idx)
                if akey:
                    self.app_info.setdefault(akey, idx[akey] if akey in idx else (icon, name))
                row = ProcessRow(p, icon, name, akey)
                new.append(row)
            else:
                row.update(p)
            live[key] = row
            app_keys[p.pid] = row.app_key
        gone = [k for k in self.rows if k not in live]
        if gone:
            gone_rows = {id(self.rows[k]) for k in gone}
            for i in range(self.all_store.get_n_items() - 1, -1, -1):
                if id(self.all_store.get_item(i)) in gone_rows:
                    self.all_store.remove(i)
        self.rows = live
        self.pid_rows = {r.sv["pid"]: r for r in live.values()}
        if new:
            self.all_store.splice(self.all_store.get_n_items(), 0, new)
        self._restoring = False
        self.groups = manage.group_processes(snap.procs, lambda p: app_keys.get(p.pid), os.getuid())
        self.history.add(snap, self.groups[0], {k: v[1] for k, v in self.app_info.items()})
        self._samples += 1
        if self._samples % HISTORY_SAVE_EVERY == 0:
            self.save_history()
        self.page_by_id["performance"].update(snap)          # its history always fills
        page = self.page_by_id[self.page_id]
        if page.id != "performance":
            page.update(snap)
        self._update_info()

    def save_history(self) -> None:
        config.save("activity-history", self.history.to_dict())

    def _destroyed(self, *_a) -> None:
        self._alive = False
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._samples:
            self.save_history()

    # -- process actions ------------------------------------------------------------------------
    def descends(self, row, ancestor) -> bool:
        """Whether `row` runs below `ancestor` (End process tree)."""
        seen = set()
        pid = row.ppid
        target = ancestor.sv["pid"]
        while pid and pid not in seen:
            if pid == target:
                return True
            seen.add(pid)
            parent = self.pid_rows.get(pid)
            pid = parent.ppid if parent is not None else 0
        return False

    def end_task(self, rows, force: bool = False) -> None:
        """SIGTERM (force: SIGKILL). A system process or another user's asks
        first, like Windows."""
        rows = [r for r in rows if r is not None]
        if not rows:
            return
        me = os.getuid()
        risky = [r for r in rows if r.uid != me]
        sig = signal.SIGKILL if force else signal.SIGTERM
        if not risky:
            self._signal(rows, sig)
            return
        name = rows[0].sv["name"]

        def answer(rid):
            if rid == "end":
                self._signal(rows, sig)
        ui.dialog.alert(f"Do you want to end the system process “{name}”?",
                        "Ending this process may make the system unstable or lose unsaved data. "
                        "It may need an administrator.",
                        [("cancel", "Cancel", ""), ("end", "End process", "destructive")], answer, parent=self)

    def _signal(self, rows, sig) -> None:
        denied = []
        for r in rows:
            try:
                os.kill(r.sv["pid"], sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                denied.append(r)
        if denied:
            ui.dialog.alert(f"“{denied[0].sv['name']}” couldn't be ended.",
                            "You don't have permission to end a process that belongs to another user.",
                            [("ok", "OK", "default")], parent=self)
        if self._timer and not self._busy:
            GLib.timeout_add(300, lambda: (self._alive and self._tick(), False)[1])

    @staticmethod
    def exe_path(row) -> str:
        try:
            return os.readlink(f"/proc/{row.sv['pid']}/exe")
        except OSError:
            argv0 = (row.cmdline.split() or [""])[0]
            if os.path.isabs(argv0):
                return argv0
            from shutil import which
            return which(argv0) or ""

    def open_location(self, row) -> None:
        path = self.exe_path(row) if row is not None else ""
        if path:
            self.reveal_path(path)

    def reveal_path(self, path: str) -> None:
        """Files shows the item selected in its folder (FileManager1)."""
        def done(bus, res):
            try:
                bus.call_finish(res)
            except GLib.Error:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            bus = None
        if bus is None:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)
            return
        bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1",
                 "ShowItems", GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")), None,
                 Gio.DBusCallFlags.NONE, 3000, None, done)

    def search_online(self, name: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(SEARCH_URL + quote_plus(name), None)
        except GLib.Error:
            pass

    def _props(self, row) -> list:
        parent = self.pid_rows.get(row.ppid)
        return [("pid", "PID", str(row.sv["pid"])),
                ("parent", "Parent process", f"{parent.sv['name']} ({row.ppid})" if parent else str(row.ppid)),
                ("user", "User", f"{row.sv['user']} ({row.uid})"),
                ("status", "Status", row.sv.get("status") or "Running"),
                ("cpu", "CPU", fmt_cpu(row.sv.get("cpu", 0))),
                ("cpu_time", "CPU time", fmt_cpu_time(row.sv.get("cpu_time", 0))),
                ("threads", "Threads", fmt_count(row.sv.get("threads", 0))),
                ("mem", "Memory", fmt_mem(row.sv.get("mem", 0))),
                ("started", "Started", time.strftime("%d %b %Y, %H:%M:%S", time.localtime(row.started))),
                ("path", "Location", self.exe_path(row) or "–")]

    def show_properties(self, rows) -> None:
        rows = [r for r in rows or [] if r is not None]
        if not rows:
            return
        row = rows[0]
        page = self.page_by_id[self.page_id]
        anchor = next((b for b in _children(page.buttons) if b.get_tooltip_text() == "Properties"), self.search)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["tm-info"])
        head = Gtk.Box(spacing=10)
        img = Gtk.Image(pixel_size=32)
        img.set_from_gicon(row.icon) if row.icon is not None else img.set_from_icon_name(GENERIC_ICON)
        head.append(img)
        head.append(Gtk.Label(label=row.sv["name"], xalign=0, css_classes=["tm-info-name"],
                              ellipsize=Pango.EllipsizeMode.END, max_width_chars=28))
        box.append(head)
        grid = Gtk.Grid(row_spacing=3, column_spacing=12)
        labels = {}
        for i, (key, title, value) in enumerate(self._props(row)):
            grid.attach(Gtk.Label(label=title + ":", xalign=1, css_classes=["tm-key"]), 0, i, 1, 1)
            v = Gtk.Label(label=value, xalign=0, selectable=True, css_classes=["tm-val"],
                          ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=40)
            grid.attach(v, 1, i, 1, 1)
            labels[key] = v
        box.append(grid)
        box.append(Gtk.Label(label="Command line:", xalign=0, css_classes=["tm-key"]))
        box.append(Gtk.Label(label=row.cmdline.replace("\n", " ")[:2000], xalign=0, wrap=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, max_width_chars=48, lines=6,
                             ellipsize=Pango.EllipsizeMode.END, css_classes=["tm-cmd"]))
        pop = ui.panel.popup(anchor, box)
        self._info = (row, labels)
        pop.connect("closed", lambda *_: setattr(self, "_info", None))

    def _update_info(self) -> None:
        if self._info is None:
            return
        row, labels = self._info
        for key, _t, value in self._props(row):
            if key != "path" and labels[key].get_label() != value:
                labels[key].set_label(value)

    def selected_rows(self) -> list:
        return self.page_by_id[self.page_id].selected_rows()

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if not cmd:
            if keyval == Gdk.KEY_Delete and not self.search.has_focus():
                self.end_task(self.selected_rows())
                return True
            return False
        if k == Gdk.KEY_f:
            self.search.grab_focus()
        elif k == Gdk.KEY_i:
            self.show_properties(self.selected_rows())
        elif k == Gdk.KEY_w:
            self.close()
        elif Gdk.KEY_1 <= k <= Gdk.KEY_7:
            self.show_page(self.pages[k - Gdk.KEY_1].id)
        else:
            return False
        return True


ActivityWindow = TaskManagerWindow            # the component is still "activity"


def _children(box):
    c = box.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


def open_windows(app, paths=()) -> None:
    """One Task Manager window (opening it again brings it forward)."""
    win = next((w for w in app.get_windows() if isinstance(w, TaskManagerWindow)), None)
    (win or TaskManagerWindow(app)).present()


def activity_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Task Manager\n"
                              "Comment=See and manage running apps and processes\n"
                              "Icon=utilities-system-monitor\nCategories=System;Monitor;\n"
                              "Keywords=process;task;cpu;memory;kill;startup;services;activity;monitor;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} activity\n")
