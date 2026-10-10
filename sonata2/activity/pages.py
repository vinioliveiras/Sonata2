"""Task Manager's list pages: Processes (Apps / Background / System
processes, apps expand to their processes, heat-map cells), App history,
Startup apps, Users, Details (every process) and Services.

Each page exposes .widget, .buttons (toolbar buttons shown while it is
the current page), update(snapshot), search(text), shown(), and for the
process pages selected_rows() / context menus. Rows are GObjects updated
in place (the ProcessRows are shared by Processes and Details)."""
import os
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from ..backend import system  # noqa: E402
from . import manage  # noqa: E402
from .procfs import fmt_bytes, fmt_count, fmt_cpu_time  # noqa: E402
from .widgets import name_column, row_at, table, text_column, tool_button  # noqa: E402

NCPU = os.cpu_count() or 1
GROUPS = ("Apps", "Background processes", "System processes")


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


# -- rows ---------------------------------------------------------------------------------------
class RowBase(GObject.Object):
    """What every process-like row shows (Processes page columns). sv holds
    the same values as plain Python for fast sorting."""
    __gtype_name__ = "SonataTaskRowBase"
    name = GObject.Property(type=str, default="")
    status = GObject.Property(type=str, default="")
    cpu = GObject.Property(type=float, default=0.0)          # % of one core
    mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    disk = GObject.Property(type=float, default=0.0)         # bytes/s
    net = GObject.Property(type=float, default=-1.0)        # bytes/s; -1: not measured per process
    gpu = GObject.Property(type=float, default=0.0)         # % of its busiest GPU engine
    gpu_on = GObject.Property(type=str, default="")         # which graphics card(s): "NVIDIA", "AMD"...
    group = GObject.Property(type=int, default=0)
    icon = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self.sv = {k: v for k, v in kw.items()}
        self.sv.setdefault("name", "")
        self.sv.setdefault("gpu_on", "")

    def set_values(self, vals: dict) -> None:
        sv = self.sv
        for k, v in vals.items():
            if sv.get(k) != v:                       # no notify (and no redraw) for the same value
                sv[k] = v
                self.set_property(k, v)


class ProcessRow(RowBase):
    __gtype_name__ = "SonataTaskProcessRow"
    pid = GObject.Property(type=int, default=0)
    user = GObject.Property(type=str, default="")
    cpu_time = GObject.Property(type=float, default=0.0)
    threads = GObject.Property(type=int, default=0)
    read_bytes = GObject.Property(type=GObject.TYPE_INT64, default=-1)
    write_bytes = GObject.Property(type=GObject.TYPE_INT64, default=-1)
    command = GObject.Property(type=str, default="")

    def __init__(self, p, icon, name, app_key=None):
        cmd = p.cmdline.replace("\n", " ")
        super().__init__(name=name, pid=p.pid, user=p.user, command=cmd)
        self.key = (p.pid, p.start_ticks)
        self.uid, self.comm, self.exe, self.cmdline, self.started = p.uid, p.comm, p.exe, p.cmdline, p.started
        self.ppid = p.ppid
        self.icon, self.app_key = icon, app_key
        self.sv.update(pid=p.pid, user=p.user, command=cmd)
        self.update(p)

    def update(self, p) -> None:
        self.ppid = p.ppid
        self.set_values({"cpu": round(p.cpu, 1), "cpu_time": round(p.cpu_time, 2), "threads": p.threads,
                         "mem": p.rss, "read_bytes": p.read_bytes, "write_bytes": p.write_bytes,
                         "disk": round(p.read_ps + p.write_ps), "gpu": round(p.gpu, 1), "status": p.status,
                         "gpu_on": getattr(p, "gpu_on", "")})


class AppNode(RowBase):
    """An app on the Processes page: its processes' totals; expands to them."""
    __gtype_name__ = "SonataTaskAppNode"

    def __init__(self, key, name, icon):
        super().__init__(name=name, group=0)
        self.key, self.icon, self.app_name = key, icon, name
        self.children = Gio.ListStore(item_type=ProcessRow)
        self.members = []

    @property
    def pid(self):
        return self.members[0].pid if self.members else 0


class UserRow(GObject.Object):
    __gtype_name__ = "SonataTaskUserRow"
    name = GObject.Property(type=str, default="")
    procs = GObject.Property(type=int, default=0)
    cpu = GObject.Property(type=float, default=0.0)
    mem = GObject.Property(type=GObject.TYPE_INT64, default=0)
    disk = GObject.Property(type=float, default=0.0)

    def __init__(self, name):
        super().__init__(name=name)
        self.icon = Gio.ThemedIcon.new("avatar-default-symbolic")
        self.sv = {"name": name}


class HistoryRow(GObject.Object):
    __gtype_name__ = "SonataTaskHistoryRow"
    name = GObject.Property(type=str, default="")
    cpu_time = GObject.Property(type=float, default=0.0)
    disk = GObject.Property(type=float, default=0.0)

    def __init__(self, key, name, icon):
        super().__init__(name=name)
        self.key, self.icon = key, icon
        self.sv = {"name": name}


class StartupRow(GObject.Object):
    __gtype_name__ = "SonataTaskStartupRow"
    name = GObject.Property(type=str, default="")
    comment = GObject.Property(type=str, default="")
    command = GObject.Property(type=str, default="")
    status = GObject.Property(type=str, default="")
    enabled = GObject.Property(type=bool, default=True)

    def __init__(self, entry):
        super().__init__(name=entry.name, comment=entry.comment, command=entry.command,
                         status="Enabled" if entry.enabled else "Disabled", enabled=entry.enabled)
        self.entry = entry
        self.icon = _icon(entry.icon)
        self.sv = {"name": entry.name, "comment": entry.comment, "command": entry.command, "status": self.status}


class ServiceRow(GObject.Object):
    __gtype_name__ = "SonataTaskServiceRow"
    name = GObject.Property(type=str, default="")
    description = GObject.Property(type=str, default="")
    status = GObject.Property(type=str, default="")
    scope = GObject.Property(type=str, default="")

    def __init__(self, s):
        super().__init__(name=s.unit, description=s.description, status=s.status,
                         scope="User" if s.scope == "user" else "System")
        self.service = s
        self.icon = None
        self.sv = {"name": s.unit, "description": s.description, "status": s.status, "scope": self.scope}


def _icon(name: str):
    if not name:
        return Gio.ThemedIcon.new("application-x-executable")
    if os.path.isabs(name):
        return Gio.FileIcon.new(Gio.File.new_for_path(name))
    return Gio.ThemedIcon.new(name)


# -- formats / heat -----------------------------------------------------------------------------
def fmt_cpu(v: float) -> str:
    return f"{v / NCPU:.1f}%"


def fmt_mem(v: int) -> str:
    return f"{v / 1024 ** 2:.1f} MB"


def fmt_disk(v: float) -> str:
    return f"{v / 1024 ** 2:.1f} MB/s"


def fmt_net(v: float) -> str:
    return "–" if v < 0 else fmt_bits(v * 8)


def fmt_bits(bits: float) -> str:
    """Windows' network speeds: "0 Kbps", "12.4 Kbps", "3.1 Mbps"."""
    if bits < 1000 ** 2:
        return f"{bits / 1000:.1f} Kbps" if bits >= 100 else "0 Kbps"
    if bits < 1000 ** 3:
        return f"{bits / 1000 ** 2:.1f} Mbps"
    return f"{bits / 1000 ** 3:.2f} Gbps"


def _level(frac: float, steps) -> int:
    return sum(1 for s in steps if frac >= s)


def heat_cpu(_row, v):
    return _level(v / (100 * NCPU), (0.005, 0.05, 0.15, 0.3, 0.6))


class _MemHeat:
    total = 1

    def __call__(self, _row, v):
        return _level(v / max(1, self.total), (0.005, 0.02, 0.05, 0.1, 0.25))


heat_mem = _MemHeat()


def heat_disk(_row, v):
    return _level(v, (1e4, 1e5, 1e6, 1e7, 5e7))


def fmt_gpu(v: float) -> str:
    return f"{v:.1f}%"


def heat_gpu(_row, v):
    return _level(v / 100, (0.005, 0.05, 0.15, 0.3, 0.6))


def heat_net(_row, _v):
    return 0


def sync_store(store: Gio.ListStore, wanted: list, present: set = None) -> None:
    """Make `store` hold exactly `wanted` (objects), moving nothing that
    stays: gone ones are removed, new ones appended."""
    want = set(map(id, wanted))
    have = set()
    for i in range(store.get_n_items() - 1, -1, -1):
        it = store.get_item(i)
        if id(it) not in want:
            store.remove(i)
        else:
            have.add(id(it))
    new = [w for w in wanted if id(w) not in have]
    if new:
        store.splice(store.get_n_items(), 0, new)


def keep_top(view, scroller, resort) -> None:
    """Sort again; at the top, stay at the top (the list follows the row
    that was at the top otherwise)."""
    adj = scroller.get_vadjustment()
    at_top = adj.get_value() < 1
    resort()
    if at_top and view.get_model() is not None and view.get_model().get_n_items():
        view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        # ...and above the first section's header, once laid out
        GLib.idle_add(lambda: (adj.set_value(0), False)[1], priority=GLib.PRIORITY_LOW)


# -- pages --------------------------------------------------------------------------------------
class Page:
    id = ""
    title = ""
    icon = ""

    def __init__(self, win):
        self.win = win
        self.buttons = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.text = ""

    def button(self, icon, label, cb, tooltip=None) -> Gtk.Button:
        b = tool_button(icon, label, cb, tooltip)
        self.buttons.append(b)
        return b

    def update(self, snap) -> None:
        pass

    def search(self, text: str) -> None:
        self.text = text.strip().casefold()

    def shown(self) -> None:
        pass

    def selected_rows(self) -> list:
        return []

    def attach(self, on: bool) -> None:
        """A hidden page's table lets go of its model: an unallocated list
        view would otherwise build rows for up to 200 items on every change."""
        view = getattr(self, "view", None)
        if view is not None:
            if on and view.get_model() is None:
                view.set_model(self.selection)
            elif not on:
                view.set_model(None)

    def _scrolled(self, view) -> Gtk.ScrolledWindow:
        return Gtk.ScrolledWindow(child=view, vexpand=True, hexpand=True)


class ProcessesPage(Page):
    """Windows' Processes: Apps (expandable), Background processes, System
    processes; Name, Status, CPU, Memory, Disk, Network with heat tint."""
    id, title, icon = "processes", "Processes", "view-app-grid-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.end_btn = self.button("process-stop-symbolic", "End task", lambda: win.end_task(self.selected_rows()))
        self.props_btn = self.button("info-outline-symbolic", "", lambda: win.show_properties(self.selected_rows()),
                                     "Properties")
        self.root = Gio.ListStore(item_type=RowBase)
        self.nodes = {}                          # app key -> AppNode
        self.counts = [0, 0, 0]
        self.headers = {}                        # header label -> group
        self.tree = Gtk.TreeListModel.new(self.root, False, False, self._children)
        self.filtered = Gtk.FilterListModel(model=self.tree, filter=Gtk.CustomFilter.new(self._accepts))
        self.view = table()
        self.inner = Gtk.CustomSorter.new(self._compare)
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=Gtk.TreeListRowSorter.new(self.inner),
                                        section_sorter=Gtk.CustomSorter.new(
                                            lambda a, b, _d: _cmp(a.get_item().sv["group"], b.get_item().sv["group"])))
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", lambda *_: self._buttons())
        self.view.set_model(self.selection)
        hf = Gtk.SignalListItemFactory()
        hf.connect("setup", lambda _f, h: h.set_child(Gtk.Label(xalign=0)))
        hf.connect("bind", self._bind_header)
        hf.connect("unbind", lambda _f, h: self.headers.pop(h.get_child(), None))
        self.view.set_header_factory(hf)
        dummy = lambda: Gtk.CustomSorter.new(lambda *_a: 0)      # noqa: E731  (headers clickable; _compare sorts)
        self.cols = {"name": name_column(self.view, "Name", 240, tree=True, sorter=dummy())}
        self.cols["status"] = text_column(self.view, "Status", "status", None, 90, numeric=False, sorter=dummy(),
                                          dim=True)
        self.cols["cpu"] = text_column(self.view, "CPU", "cpu", fmt_cpu, 80, heat=heat_cpu, sorter=dummy())
        self.cols["mem"] = text_column(self.view, "Memory", "mem", fmt_mem, 100, heat=heat_mem, sorter=dummy())
        self.cols["disk"] = text_column(self.view, "Disk", "disk", fmt_disk, 90, heat=heat_disk, sorter=dummy())
        self.cols["net"] = text_column(self.view, "Network", "net", fmt_net, 90, heat=heat_net, sorter=dummy())
        self.cols["gpu"] = text_column(self.view, "GPU", "gpu", fmt_gpu, 70, heat=heat_gpu, sorter=dummy())
        # which card draws it (Vini): two-GPU laptops, the integrated card for everyday apps
        self.cols["gpu_on"] = text_column(self.view, "GPU Engine", "gpu_on", None, 96, numeric=False,
                                          sorter=dummy(), dim=True)
        for cid, c in self.cols.items():
            c.cid = cid
        self._order = ("cpu", True)
        self.view.get_sorter().connect("changed", self._sort_changed)
        self.view.sort_by_column(self.cols["cpu"], Gtk.SortType.DESCENDING)
        self.view.connect("activate", lambda *_: win.show_properties(self.selected_rows()))
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._menu)
        self.view.add_controller(menu)
        self.scroller = self._scrolled(self.view)
        self.widget = self.scroller
        self._buttons()

    def _sort_changed(self, cs, *_a) -> None:
        col = cs.get_primary_sort_column()
        self._order = (col.cid if col is not None and hasattr(col, "cid") else "cpu",
                       cs.get_primary_sort_order() == Gtk.SortType.DESCENDING)
        self.inner.changed(Gtk.SorterChange.DIFFERENT)
        self._reveal()

    def _children(self, item):
        return item.children if isinstance(item, AppNode) else None

    def _compare(self, a, b, _d=None) -> int:
        """Group first (fixed order), then the sorted column, either way."""
        g = _cmp(a.sv.get("group", 0), b.sv.get("group", 0))
        if g:
            return g
        cid, desc = self._order
        if cid == "name":
            r = _cmp(a.sv["name"].casefold(), b.sv["name"].casefold())
        else:
            r = _cmp(a.sv.get(cid, 0), b.sv.get(cid, 0))
        if desc:
            r = -r
        return r or _cmp(getattr(a, "pid", 0), getattr(b, "pid", 0))

    def _accepts(self, tree_row) -> bool:
        if not self.text:
            return True
        row = tree_row.get_item()
        if self.text in row.sv["name"].casefold() or self.text in str(getattr(row, "pid", "")):
            return True
        if isinstance(row, AppNode):                  # an app shows when one of its processes matches
            return any(self.text in m.sv["name"].casefold() for m in row.members)
        parent = tree_row.get_parent()
        return parent is not None and self.text in parent.get_item().sv["name"].casefold()

    def _bind_header(self, _f, header) -> None:
        tree_row = header.get_item()
        g = tree_row.get_item().sv.get("group", 0) if tree_row is not None else 0
        self.headers[header.get_child()] = g
        header.get_child().set_label(f"{GROUPS[g]} ({self.counts[g]})")

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)
        self._reveal()

    def _reveal(self) -> None:
        pos = self.selection.get_selected()
        if self.view.get_model() is not None and self.sorted.get_n_items():
            self.view.scroll_to(0 if pos == Gtk.INVALID_LIST_POSITION else pos, None, Gtk.ListScrollFlags.NONE,
                                None)

    def selected_rows(self) -> list:
        tr = self.selection.get_selected_item()
        if tr is None:
            return []
        row = tr.get_item()
        return list(row.members) if isinstance(row, AppNode) else [row]

    def _buttons(self) -> None:
        on = bool(self.selected_rows())
        self.end_btn.set_sensitive(on)
        self.props_btn.set_sensitive(on)

    def update(self, snap) -> None:
        win = self.win
        heat_mem.total = snap.memory.get("total", 1) or 1
        rows = win.pid_rows
        wanted = []
        for key, pids in win.groups[0].items():
            node = self.nodes.get(key)
            if node is None:
                icon, name = win.app_info[key]
                node = self.nodes[key] = AppNode(key, name, icon)
            node.members = [rows[p] for p in pids if p in rows]
            for m in node.members:
                m.set_values({"group": 0})
            sync_store(node.children, node.members)
            node.set_values({"name": f"{node.app_name} ({len(node.members)})",
                             "cpu": round(sum(m.sv["cpu"] for m in node.members), 1),
                             "mem": sum(m.sv["mem"] for m in node.members),
                             "disk": sum(m.sv["disk"] for m in node.members),
                             "gpu": round(min(100.0, sum(m.sv.get("gpu", 0) for m in node.members)), 1),
                             "gpu_on": ", ".join(sorted({c for m in node.members
                                                         for c in m.sv.get("gpu_on", "").split(", ") if c})),
                             "status": next((m.sv["status"] for m in node.members if m.sv["status"]), "")})
            wanted.append(node)
        for key in [k for k in self.nodes if k not in win.groups[0]]:
            del self.nodes[key]
        for g in (1, 2):
            for pid in win.groups[g]:
                r = rows.get(pid)
                if r is not None:
                    r.set_values({"group": g})
                    wanted.append(r)
        self.counts = [len(win.groups[0]), len(win.groups[1]), len(win.groups[2])]
        win._restoring = True
        try:
            sync_store(self.root, wanted)
            keep_top(self.view, self.scroller, lambda: self.inner.changed(Gtk.SorterChange.DIFFERENT))
        finally:
            win._restoring = False
        for lbl, g in list(self.headers.items()):
            lbl.set_label(f"{GROUPS[g]} ({self.counts[g]})")
        cpu = 100 - snap.cpu.get("idle", 100)
        self.cols["cpu"].set_title(f"{cpu:.0f}%\nCPU")
        m = snap.memory
        self.cols["mem"].set_title(f"{100 * m.get('used', 0) / max(1, m.get('total', 1)):.0f}%\nMemory")
        active = max((d["active"] for d in snap.disks.values()), default=0)
        self.cols["disk"].set_title(f"{active:.0f}%\nDisk")
        self.cols["net"].set_title(f"{fmt_bits(8 * (snap.net.get('rx_bytes_ps', 0) + snap.net.get('tx_bytes_ps', 0)))}"
                                   "\nNetwork")
        gpu = max((v for v in snap.gpus.values() if v is not None), default=None)   # a sleeping dGPU: None
        self.cols["gpu"].set_title(f"{gpu:.0f}%\nGPU" if gpu is not None else "GPU")
        self._buttons()

    def _menu(self, gesture, _n, x, y) -> None:
        row = row_at(self.view, x, y)
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        for i in range(self.sorted.get_n_items()):
            if self.sorted.get_item(i).get_item() is row:
                self.selection.set_selected(i)
                break
        rows = self.selected_rows()
        first = rows[0] if rows else None
        Item = ui.menu.Item
        ui.menu.popup(self.view, [
            [Item("End task", lambda: self.win.end_task(rows))],
            [Item("Open file location", lambda: self.win.open_location(first), enabled=bool(first and first.exe)),
             Item("Search online", lambda: self.win.search_online(row.name))],
            [Item("Properties", lambda: self.win.show_properties(rows))],
        ], at=(x, y), glass=True, passthrough=True)


class DetailsPage(Page):
    """Every process in one table (Windows' Details)."""
    id, title, icon = "details", "Details", "view-list-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.end_btn = self.button("process-stop-symbolic", "End task", lambda: win.end_task(self.selected_rows()))
        self.props_btn = self.button("info-outline-symbolic", "", lambda: win.show_properties(self.selected_rows()),
                                     "Properties")
        self.filtered = Gtk.FilterListModel(model=win.all_store, filter=Gtk.CustomFilter.new(self._accepts))
        self.view = table()
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", self._selection_changed)
        self.view.set_model(self.selection)

        def srt(k, fold=False):
            if fold:
                return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv[k].casefold(), b.sv[k].casefold()))
            return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv[k], b.sv[k]) or _cmp(a.sv["pid"], b.sv["pid"]))
        self.cols = {"name": name_column(self.view, "Name", 220, sorter=srt("name", True))}
        for cid, title, fmt, width, numeric, heat in (
                ("pid", "PID", str, 70, True, None), ("status", "Status", None, 110, False, None),
                ("user", "User name", None, 100, False, None), ("cpu", "CPU", fmt_cpu, 70, True, heat_cpu),
                ("cpu_time", "CPU time", fmt_cpu_time, 90, True, None), ("threads", "Threads", fmt_count, 70, True, None),
                ("mem", "Memory", fmt_mem, 100, True, heat_mem), ("read_bytes", "Read", fmt_bytes, 90, True, None),
                ("write_bytes", "Written", fmt_bytes, 90, True, None),
                ("command", "Command line", None, 320, False, None)):
            self.cols[cid] = text_column(self.view, title, cid, fmt, width, numeric=numeric, heat=heat,
                                         sorter=srt(cid, not numeric), dim=cid in ("user", "command", "status"))
        for cid, c in self.cols.items():
            c.cid = cid
        self._ticking = False
        self.view.get_sorter().connect("changed", lambda *_: self._ticking or self._reveal())
        self.view.sort_by_column(self.cols["cpu"], Gtk.SortType.DESCENDING)
        self.view.connect("activate", lambda *_: win.show_properties(self.selected_rows()))
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._menu)
        self.view.add_controller(menu)
        self.scroller = self._scrolled(self.view)
        self.widget = self.scroller
        self._sel_key = None
        self._buttons()

    def _accepts(self, row) -> bool:
        t = self.text
        return not t or t in row.sv["name"].casefold() or t in str(row.sv["pid"]) or t in row.sv["user"].casefold()

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)
        self.restore_selection()
        self._reveal()

    def _reveal(self) -> None:
        pos = self.selection.get_selected()
        if self.view.get_model() is not None and self.sorted.get_n_items():
            self.view.scroll_to(0 if pos == Gtk.INVALID_LIST_POSITION else pos, None, Gtk.ListScrollFlags.NONE,
                                None)

    def _selection_changed(self, *_a) -> None:
        row = self.selection.get_selected_item()
        if row is not None:
            self._sel_key = row.key
        elif not self.win._restoring:
            self._sel_key = None
        self._buttons()

    def restore_selection(self) -> None:
        """Keep the selected process selected when rows move or filter."""
        row = self.selection.get_selected_item()
        if self._sel_key is None or (row is not None and row.key == self._sel_key):
            return
        target = self.win.rows.get(self._sel_key)
        self.win._restoring = True
        try:
            if target is None:
                self._sel_key = None
            else:
                for i in range(self.sorted.get_n_items()):
                    if self.sorted.get_item(i) is target:
                        self.selection.set_selected(i)
                        return
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        finally:
            self.win._restoring = False
            self._buttons()

    def selected_rows(self) -> list:
        row = self.selection.get_selected_item()
        return [row] if row is not None else []

    def _buttons(self) -> None:
        on = bool(self.selected_rows())
        self.end_btn.set_sensitive(on)
        self.props_btn.set_sensitive(on)

    def update(self, snap) -> None:
        heat_mem.total = snap.memory.get("total", 1) or 1
        self.win._restoring = self._ticking = True
        try:
            keep_top(self.view, self.scroller, lambda: self.view.get_sorter().changed(Gtk.SorterChange.DIFFERENT))
        finally:
            self.win._restoring = self._ticking = False
        self.restore_selection()

    def _menu(self, gesture, _n, x, y) -> None:
        row = row_at(self.view, x, y)
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        for i in range(self.sorted.get_n_items()):
            if self.sorted.get_item(i) is row:
                self.selection.set_selected(i)
                break
        tree = [r for r in self.win.rows.values() if self.win.descends(r, row)]
        Item = ui.menu.Item
        ui.menu.popup(self.view, [
            [Item("End task", lambda: self.win.end_task([row])),
             Item("End process tree", lambda: self.win.end_task([row] + tree)),
             Item("Force quit", lambda: self.win.end_task([row], force=True))],
            [Item("Open file location", lambda: self.win.open_location(row), enabled=bool(row.exe)),
             Item("Search online", lambda: self.win.search_online(row.name)),
             Item("Copy PID", lambda: self.win.get_clipboard().set(str(row.sv["pid"])))],
            [Item("Properties", lambda: self.win.show_properties([row]))],
        ], at=(x, y), glass=True, passthrough=True)


class UsersPage(Page):
    """Per-user totals (Windows' Users)."""
    id, title, icon = "users", "Users", "system-users-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.store = Gio.ListStore(item_type=UserRow)
        self.rows = {}
        self.filtered = Gtk.FilterListModel(model=self.store, filter=Gtk.CustomFilter.new(
            lambda r: not self.text or self.text in r.sv["name"].casefold()))
        self.view = table()
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.view.set_model(self.selection)

        def srt(k):
            return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv.get(k, 0), b.sv.get(k, 0)))
        name_column(self.view, "User", 240, sorter=Gtk.CustomSorter.new(
            lambda a, b, _d: _cmp(a.sv["name"].casefold(), b.sv["name"].casefold())))
        text_column(self.view, "Processes", "procs", fmt_count, 90, sorter=srt("procs"))
        self.cpu_col = text_column(self.view, "CPU", "cpu", fmt_cpu, 90, heat=heat_cpu, sorter=srt("cpu"))
        text_column(self.view, "Memory", "mem", fmt_mem, 110, heat=heat_mem, sorter=srt("mem"))
        text_column(self.view, "Disk", "disk", fmt_disk, 100, heat=heat_disk, sorter=srt("disk"))
        self.view.sort_by_column(self.cpu_col, Gtk.SortType.DESCENDING)
        self.scroller = self._scrolled(self.view)
        self.widget = self.scroller

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)

    def update(self, snap) -> None:
        users = manage.per_user(snap.procs)
        for name, u in users.items():
            r = self.rows.get(name)
            if r is None:
                r = self.rows[name] = UserRow(name)
                self.store.append(r)
            for k, v in (("procs", u["procs"]), ("cpu", round(u["cpu"], 1)), ("mem", u["mem"]),
                         ("disk", round(u["disk"]))):
                if r.sv.get(k) != v:
                    r.sv[k] = v
                    r.set_property(k, v)
        for name in [n for n in self.rows if n not in users]:
            ok, pos = self.store.find(self.rows.pop(name))
            if ok:
                self.store.remove(pos)
        keep_top(self.view, self.scroller, lambda: self.view.get_sorter().changed(Gtk.SorterChange.DIFFERENT))


class HistoryPage(Page):
    """CPU time and disk use per app since a date (Windows' App history)."""
    id, title, icon = "history", "App history", "document-open-recent-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.button("edit-delete-symbolic", "Delete usage history", self.clear)
        self.store = Gio.ListStore(item_type=HistoryRow)
        self.rows = {}
        self.filtered = Gtk.FilterListModel(model=self.store, filter=Gtk.CustomFilter.new(
            lambda r: not self.text or self.text in r.sv["name"].casefold()))
        self.view = table()
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.view.set_model(self.selection)

        def srt(k):
            return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv.get(k, 0), b.sv.get(k, 0)))
        name_column(self.view, "Name", 300, sorter=Gtk.CustomSorter.new(
            lambda a, b, _d: _cmp(a.sv["name"].casefold(), b.sv["name"].casefold())))
        self.cpu_col = text_column(self.view, "CPU time", "cpu_time", fmt_cpu_time, 120, sorter=srt("cpu_time"))
        text_column(self.view, "Disk", "disk", lambda v: fmt_bytes(int(v)), 120, sorter=srt("disk"))
        self.view.sort_by_column(self.cpu_col, Gtk.SortType.DESCENDING)
        self.note = Gtk.Label(xalign=0, css_classes=["tm-note"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.note)
        self.scroller = self._scrolled(self.view)
        box.append(self.scroller)
        self.widget = box

    def clear(self) -> None:
        self.win.history.clear()
        self.win.save_history()
        self.store.remove_all()
        self.rows = {}
        self.update(None)

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)

    def update(self, snap) -> None:
        h = self.win.history
        self.note.set_label("Resource usage since " + time.strftime("%d/%m/%Y", time.localtime(h.since)) +
                            " for the current user account.")
        for key, a in h.apps.items():
            r = self.rows.get(key)
            if r is None:
                icon = self.win.app_info.get(key, (None, a["name"]))[0]
                r = self.rows[key] = HistoryRow(key, a["name"], icon)
                self.store.append(r)
            for k in ("cpu_time", "disk"):
                v = round(a[k], 1)
                if r.sv.get(k) != v:
                    r.sv[k] = v
                    r.set_property(k, v)
        self.view.get_sorter().changed(Gtk.SorterChange.DIFFERENT)


class StartupPage(Page):
    """Login items (~/.config/autostart over /etc/xdg/autostart) with an
    Enabled switch (Windows' Startup apps)."""
    id, title, icon = "startup", "Startup apps", "system-run-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.enable_btn = self.button("media-playback-start-symbolic", "Enable", lambda: self._toggle_selected(True))
        self.disable_btn = self.button("media-playback-stop-symbolic", "Disable",
                                       lambda: self._toggle_selected(False))
        self.store = Gio.ListStore(item_type=StartupRow)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=Gtk.CustomFilter.new(
            lambda r: not self.text or self.text in r.sv["name"].casefold() or self.text in r.sv["command"].casefold()))
        self.view = table()
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", lambda *_: self._buttons())
        self.view.set_model(self.selection)

        def srt(k):
            return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv[k].casefold(), b.sv[k].casefold()))
        name = name_column(self.view, "Name", 240, sorter=srt("name"))
        text_column(self.view, "Description", "comment", None, 260, numeric=False, sorter=srt("comment"), dim=True)
        text_column(self.view, "Command", "command", None, 240, numeric=False, sorter=srt("command"), dim=True)
        text_column(self.view, "Status", "status", None, 90, numeric=False, sorter=srt("status"))
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, it: it.set_child(Gtk.Box(halign=Gtk.Align.CENTER)))
        f.connect("bind", self._bind_switch)
        sw_col = Gtk.ColumnViewColumn(title="Enabled", factory=f)
        sw_col.set_fixed_width(80)
        self.view.append_column(sw_col)
        self.view.sort_by_column(name, Gtk.SortType.ASCENDING)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._menu)
        self.view.add_controller(menu)
        self.empty = Gtk.Label(label="No startup apps", css_classes=["tm-note"], visible=False, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._scrolled(self.view))
        box.append(self.empty)
        self.widget = box
        self._loaded = False
        self._buttons()

    def _bind_switch(self, _f, item) -> None:
        box, row = item.get_child(), item.get_item()
        while box.get_first_child():
            box.remove(box.get_first_child())
        box.append(ui.controls.switch(row.enabled, lambda on, r=row: self.set_enabled(r, on)))

    def shown(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        self._loaded = True
        system.run_async(manage.startup_entries, self._loaded_entries)

    def _loaded_entries(self, entries) -> None:
        self.store.remove_all()
        self.store.splice(0, 0, [StartupRow(e) for e in entries or []])
        self.empty.set_visible(not entries)

    def set_enabled(self, row, on: bool) -> None:
        if row.enabled == on:
            return

        def done(path):
            if path is None:
                ui.dialog.alert(f"“{row.name}” couldn't be changed.", "Its startup file couldn't be written.",
                                [("ok", "OK", "default")], parent=self.win)
                self.reload()
                return
            row.enabled = on
            row.status = row.sv["status"] = "Enabled" if on else "Disabled"
            self._rebind(row)
            self._buttons()
        system.run_async(manage.set_startup_enabled, done, row.entry, on)

    def _toggle_selected(self, on: bool) -> None:
        row = self.selection.get_selected_item()
        if row is not None:
            self.set_enabled(row, on)

    def _rebind(self, row) -> None:
        ok, pos = self.store.find(row)
        if ok:                                         # the switch follows
            self.store.splice(pos, 1, [row])

    def _buttons(self) -> None:
        row = self.selection.get_selected_item()
        self.enable_btn.set_sensitive(row is not None and not row.enabled)
        self.disable_btn.set_sensitive(row is not None and row.enabled)

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)

    def _menu(self, gesture, _n, x, y) -> None:
        row = row_at(self.view, x, y)
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        Item = ui.menu.Item
        ui.menu.popup(self.view, [
            [Item("Enable", lambda: self.set_enabled(row, True), enabled=not row.enabled),
             Item("Disable", lambda: self.set_enabled(row, False), enabled=row.enabled)],
            [Item("Open file location", lambda: self.win.reveal_path(row.entry.path)),
             Item("Search online", lambda: self.win.search_online(row.name))],
        ], at=(x, y), glass=True, passthrough=True)


class ServicesPage(Page):
    """systemd services of the user and the system, Start / Stop / Restart
    (system ones ask through polkit)."""
    id, title, icon = "services", "Services", "emblem-system-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.start_btn = self.button("media-playback-start-symbolic", "Start", lambda: self.act("start"))
        self.stop_btn = self.button("media-playback-stop-symbolic", "Stop", lambda: self.act("stop"))
        self.restart_btn = self.button("view-refresh-symbolic", "Restart", lambda: self.act("restart"))
        self.store = Gio.ListStore(item_type=ServiceRow)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=Gtk.CustomFilter.new(
            lambda r: not self.text or self.text in r.sv["name"].casefold()
            or self.text in r.sv["description"].casefold()))
        self.view = table()
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.connect("selection-changed", lambda *_: self._buttons())
        self.view.set_model(self.selection)

        def srt(k):
            return Gtk.CustomSorter.new(lambda a, b, _d: _cmp(a.sv[k].casefold(), b.sv[k].casefold()))
        name = text_column(self.view, "Name", "name", None, 260, numeric=False, sorter=srt("name"))
        text_column(self.view, "Description", "description", None, 320, numeric=False, sorter=srt("description"),
                    expand=True, dim=True)
        self._status_column(srt("status"))
        text_column(self.view, "Type", "scope", None, 80, numeric=False, sorter=srt("scope"), dim=True)
        self.view.sort_by_column(name, Gtk.SortType.ASCENDING)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._menu)
        self.view.add_controller(menu)
        self.message = Gtk.Label(css_classes=["tm-note"], visible=False, wrap=True, xalign=0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.message)
        box.append(self._scrolled(self.view))
        self.widget = box
        self._loaded = False
        self._busy = False
        self._buttons()

    def _status_column(self, sorter) -> None:
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, it: it.set_child(Gtk.Label(xalign=0, css_classes=["tm-cell"])))

        def bind(_f, it):
            lbl, row = it.get_child(), it.get_item()
            lbl.set_label(row.status)
            lbl.tm_row = row
            for c, on in (("tm-running", row.status == "Running"), ("tm-failed", row.status == "Failed")):
                (lbl.add_css_class if on else lbl.remove_css_class)(c)
        f.connect("bind", bind)
        c = Gtk.ColumnViewColumn(title="Status", factory=f, sorter=sorter, resizable=True)
        c.set_fixed_width(100)
        self.view.append_column(c)

    def shown(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        self._loaded = True

        def both():
            return manage.list_services("user"), manage.list_services("system")
        system.run_async(both, self._listed)

    def _listed(self, res) -> None:
        user, sys_ = res if res else (None, None)
        sel = self.selection.get_selected_item()
        sel_name = sel.sv["name"] if sel is not None else None
        items = [ServiceRow(s) for s in (user or []) + (sys_ or [])]
        self.store.remove_all()
        self.store.splice(0, 0, items)
        if user is None and sys_ is None:
            self.message.set_label("Services aren't available: systemd isn't running on this computer.")
        elif sys_ is None or user is None:
            self.message.set_label(f"Only {'user' if sys_ is None else 'system'} services are shown.")
        self.message.set_visible(user is None or sys_ is None)
        if sel_name:
            for i in range(self.sorted.get_n_items()):
                if self.sorted.get_item(i).sv["name"] == sel_name:
                    self.selection.set_selected(i)
                    break
        self._buttons()

    def act(self, action: str, row=None) -> None:
        row = row or self.selection.get_selected_item()
        if row is None or self._busy:
            return
        self._busy = True
        s = row.service

        def done(res):
            self._busy = False
            ok, msg = res if res else (False, "")
            if not ok:
                ui.dialog.alert(f"“{s.unit}” couldn't {action}.", msg or "The service manager refused.",
                                [("ok", "OK", "default")], parent=self.win)
            self.reload()
        system.run_async(manage.service_action, done, s.unit, action, s.scope)

    def _buttons(self) -> None:
        row = self.selection.get_selected_item()
        running = row is not None and row.service.active == "active"
        self.start_btn.set_sensitive(row is not None and not running)
        self.stop_btn.set_sensitive(running)
        self.restart_btn.set_sensitive(row is not None)

    def search(self, text: str) -> None:
        super().search(text)
        self.filtered.get_filter().changed(Gtk.FilterChange.DIFFERENT)

    def _menu(self, gesture, _n, x, y) -> None:
        row = row_at(self.view, x, y)
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        running = row.service.active == "active"
        Item = ui.menu.Item
        ui.menu.popup(self.view, [
            [Item("Start", lambda: self.act("start", row), enabled=not running),
             Item("Stop", lambda: self.act("stop", row), enabled=running),
             Item("Restart", lambda: self.act("restart", row))],
            [Item("Search online", lambda: self.win.search_online(row.sv["name"]))],
        ], at=(x, y), glass=True, passthrough=True)
