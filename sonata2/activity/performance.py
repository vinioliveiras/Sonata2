"""Task Manager's Performance page (Windows 11 layout, macOS look): a list
of resources with sparklines -- CPU, Memory, each disk, each network
interface, every GPU (a sleeping one too) -- and, for the selected one, a
big 60-second graph with its details."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from .pages import Page, fmt_bits  # noqa: E402
from .procfs import fmt_bytes, fmt_count  # noqa: E402
from .widgets import CompositionBar, Graph, Series  # noqa: E402

COLORS = {"cpu": "sys_blue", "memory": "sys_purple", "disk": "sys_green", "net": "sys_orange", "gpu": "sys_teal"}
GPU_GONE_SAMPLES = 5         # a GPU row goes after this many samples without its load


def fmt_ghz(mhz: float) -> str:
    return f"{mhz / 1000:.2f} GHz" if mhz else "–"


def fmt_uptime(seconds: float) -> str:
    """Windows' d:hh:mm:ss."""
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{d}:{h:02d}:{m:02d}:{s:02d}"


def fmt_gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def iface_title(name: str) -> str:
    if name.startswith(("wl", "wlan")):
        return "Wi-Fi"
    if name == "lo":
        return "Loopback"
    if name.startswith(("tun", "wg", "tap")):
        return "VPN"
    return "Ethernet"


class Resource:
    """One entry of the list: its history (one or two lines) and texts."""

    def __init__(self, key, kind, title, detail=""):
        self.key, self.kind, self.title, self.detail = key, kind, title, detail
        self.a, self.b = Series(), Series()        # a: main line (%, read, receive); b: write / send
        self.subtitle = ""
        self.stats = {}
        self.maximum = 100.0
        self.two = kind in ("net",)

    def lines(self):
        color = COLORS[self.kind]
        if self.two:
            return [(color, self.a), ("sys_red", self.b)]
        return [(color, self.a)]


class PerformancePage(Page):
    id, title, icon = "performance", "Performance", "am-system-monitor-symbolic"

    def __init__(self, win):
        super().__init__(win)
        self.resources = {}                         # key -> Resource (list order = insertion)
        self.rows = {}                              # key -> list row widgets
        self._gpu_missing = {}                      # card -> samples in a row without its load
        self.current = None
        self.list = Gtk.ListBox(css_classes=["tm-perf-list"], selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.connect("row-selected", lambda _l, r: r is not None and self.select(r.key))
        left = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER)
        left.set_size_request(ui.window.SIDEBAR_W, -1)
        self.pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True,
                            css_classes=["tm-perf-pane"])
        box = Gtk.Box()
        box.append(left)
        box.append(Gtk.ScrolledWindow(child=self.pane, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.widget = box
        self._add(Resource("cpu", "cpu", "CPU"))
        self._add(Resource("memory", "memory", "Memory"))

    # -- list -----------------------------------------------------------------------------------
    def _add(self, res: Resource) -> None:
        self.resources[res.key] = res
        row = Gtk.ListBoxRow()
        row.key = res.key
        h = Gtk.Box(spacing=10)
        spark = Graph(res.lines(), res.maximum, grid=False, width=64, height=40, radius=3)
        spark.set_hexpand(False)
        h.append(spark)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=1)
        title = Gtk.Label(label=res.title, xalign=0, css_classes=["tm-res-title"])
        sub = Gtk.Label(xalign=0, css_classes=["tm-res-sub"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        v.append(title)
        v.append(sub)
        h.append(v)
        row.set_child(h)
        row.spark, row.sub = spark, sub
        self.list.append(row)
        self.rows[res.key] = row
        if self.current is None:
            self.list.select_row(row)

    def _remove(self, key) -> None:
        row = self.rows.pop(key, None)
        self.resources.pop(key, None)
        if row is not None:
            self.list.remove(row)
        if self.current == key:
            self.list.select_row(self.rows["cpu"])

    # -- detail pane ----------------------------------------------------------------------------
    def select(self, key: str) -> None:
        self.current = key
        res = self.resources[key]
        while self.pane.get_first_child():
            self.pane.remove(self.pane.get_first_child())
        head = Gtk.Box()
        head.append(Gtk.Label(label=res.title, xalign=0, hexpand=True, css_classes=["tm-perf-title"]))
        self.model_lbl = Gtk.Label(label=res.detail, xalign=1, css_classes=["tm-perf-model"],
                                   ellipsize=Pango.EllipsizeMode.END)
        head.append(self.model_lbl)
        self.pane.append(head)
        cap = Gtk.Box()
        self.cap_left = Gtk.Label(xalign=0, hexpand=True, css_classes=["tm-caption"])
        self.cap_right = Gtk.Label(xalign=1, css_classes=["tm-caption"])
        cap.append(self.cap_left)
        cap.append(self.cap_right)
        self.pane.append(cap)
        self.graph = Graph(res.lines(), res.maximum if res.kind != "net" else None, height=220)
        self.pane.append(self.graph)
        foot = Gtk.Box()
        foot.append(Gtk.Label(label="60 seconds", xalign=0, hexpand=True, css_classes=["tm-caption"]))
        foot.append(Gtk.Label(label="0", xalign=1, css_classes=["tm-caption"]))
        self.pane.append(foot)
        self.bar = None
        if res.kind == "memory":
            self.pane.append(Gtk.Label(label="Memory composition", xalign=0, css_classes=["tm-caption"],
                                       margin_top=6))
            self.bar = CompositionBar()
            self.pane.append(self.bar)
        stats = Gtk.Box(spacing=36, margin_top=10)
        self.big = Gtk.Grid(row_spacing=10, column_spacing=28)
        self.small = Gtk.Grid(row_spacing=3, column_spacing=16, valign=Gtk.Align.START)
        stats.append(self.big)
        stats.append(self.small)
        self.pane.append(stats)
        self.stat_labels = {}
        self._fill_stats(res)

    def _fill_stats(self, res: Resource) -> None:
        big, small = res.stats.get("big", []), res.stats.get("small", [])
        known = set(self.stat_labels)
        if known and known == {k for k, _v in big} | {k for k, _v in small}:
            for k, v in big + small:
                if self.stat_labels[k].get_label() != v:
                    self.stat_labels[k].set_label(v)
        else:
            for grid in (self.big, self.small):
                while grid.get_first_child():
                    grid.remove(grid.get_first_child())
            self.stat_labels = {}
            for i, (k, v) in enumerate(big):
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                cell.append(Gtk.Label(label=k, xalign=0, css_classes=["tm-stat-key"]))
                lbl = Gtk.Label(label=v, xalign=0, css_classes=["tm-stat-big"])
                cell.append(lbl)
                self.big.attach(cell, i % 3, i // 3, 1, 1)
                self.stat_labels[k] = lbl
            for i, (k, v) in enumerate(small):
                self.small.attach(Gtk.Label(label=k + ":", xalign=0, css_classes=["tm-stat-key"]), 0, i, 1, 1)
                lbl = Gtk.Label(label=v, xalign=0, css_classes=["tm-stat-val"])
                self.small.attach(lbl, 1, i, 1, 1)
                self.stat_labels[k] = lbl
        self.cap_left.set_label(res.stats.get("caption", ""))
        self.cap_right.set_label(res.stats.get("top", ""))
        self.model_lbl.set_label(res.detail)

    # -- data -----------------------------------------------------------------------------------
    def update(self, snap) -> None:
        """Always called (the history keeps filling while another page shows);
        redraws only when this page is the one on screen."""
        info = self.win.sampler.cpu_info()
        first = snap.interval <= 0
        visible = self.win.page_id == self.id
        # CPU
        r = self.resources["cpu"]
        util = 100 - snap.cpu.get("idle", 100)
        r.detail = info["model"]
        r.subtitle = f"{util:.0f}%  {fmt_ghz(snap.cpu_mhz)}"
        r.stats = {"caption": "% Utilization", "top": "100%",
                   "big": [("Utilization", f"{util:.0f}%"), ("Speed", fmt_ghz(snap.cpu_mhz)), ("", ""),
                           ("Processes", fmt_count(len(snap.procs))), ("Threads", fmt_count(snap.threads)),
                           ("Up time", fmt_uptime(snap.uptime))],
                   "small": [("Base speed", fmt_ghz(info["base_mhz"])), ("Sockets", str(info["sockets"])),
                             ("Cores", str(info["cores"])), ("Logical processors", str(info["logical"]))]}
        if not first:
            r.a.push(util)
        # Memory
        m = snap.memory
        r = self.resources["memory"]
        total = m.get("total", 0) or 1
        r.maximum = total
        r.detail = fmt_gb(total)
        r.subtitle = f"{fmt_gb(m.get('used', 0))}/{fmt_gb(total)} ({100 * m.get('used', 0) / total:.0f}%)"
        r.stats = {"caption": "Memory usage", "top": fmt_gb(total),
                   "big": [("In use", fmt_gb(m.get("used", 0))), ("Available", fmt_gb(m.get("available", 0))),
                           ("Committed", f"{fmt_gb(m.get('committed', 0))}/{fmt_gb(m.get('commit_limit', 0))}"),
                           ("Cached", fmt_gb(m.get("cached", 0))),
                           ("Swap used", f"{fmt_gb(m.get('swap', 0))}/{fmt_gb(m.get('swap_total', 0))}")],
                   "small": [("Paged pool", fmt_bytes(m.get("paged_pool", 0))),
                             ("Non-paged pool", fmt_bytes(m.get("non_paged_pool", 0))),
                             ("Kernel", fmt_bytes(m.get("wired", 0))),
                             ("Compressed", fmt_bytes(m.get("compressed", 0)))]}
        r.composition = {"in_use": max(0, m.get("used", 0) - m.get("modified", 0)), "modified": m.get("modified", 0),
                         "standby": m.get("cached", 0), "free": m.get("free", 0)}
        if not first:
            r.a.push(m.get("used", 0))
        # Disks
        for i, (name, d) in enumerate(sorted(snap.disks.items())):
            key = "disk:" + name
            if key not in self.resources:
                self._add(Resource(key, "disk", f"Disk {i} ({name})"))
            r = self.resources[key]
            r.detail = d.get("model") or name
            r.subtitle = f"{d.get('kind', '')} {d['active']:.0f}%".strip()
            r.stats = {"caption": "Active time", "top": "100%",
                       "big": [("Active time", f"{d['active']:.0f}%"),
                               ("Read speed", fmt_bytes(d["read_bytes_ps"]) + "/s"),
                               ("Write speed", fmt_bytes(d["write_bytes_ps"]) + "/s")],
                       "small": [("Capacity", fmt_gb(d.get("capacity", 0))), ("Type", d.get("kind", "")),
                                 ("Reads", fmt_count(d["reads"])), ("Writes", fmt_count(d["writes"]))]}
            if not first:
                r.a.push(d["active"])
        for key in [k for k in self.resources if k.startswith("disk:") and k[5:] not in snap.disks]:
            self._remove(key)
        # Network interfaces (not the loopback)
        for name, n in sorted(snap.interfaces.items()):
            key = "net:" + name
            if name == "lo" or (key not in self.resources and not (n["rx_bytes"] or n["tx_bytes"])):
                continue                                  # the loopback, unused virtual adapters
            if key not in self.resources:
                self._add(Resource(key, "net", f"{iface_title(name)} ({name})", name))
            r = self.resources[key]
            send, recv = 8 * n["tx_bytes_ps"], 8 * n["rx_bytes_ps"]
            r.subtitle = f"S: {fmt_bits(send)}  R: {fmt_bits(recv)}"
            r.stats = {"caption": "Throughput", "top": fmt_bits(max(max(r.a.values, default=0),
                                                                   max(r.b.values, default=0)) * 1.15),
                       "big": [("Send", fmt_bits(send)), ("Receive", fmt_bits(recv))],
                       "small": [("Adapter name", name), ("Sent", fmt_bytes(n["tx_bytes"])),
                                 ("Received", fmt_bytes(n["rx_bytes"])),
                                 ("Packets", f"{fmt_count(n['tx_packets'])} / {fmt_count(n['rx_packets'])}")]}
            if not first:
                r.a.push(recv)
                r.b.push(send)
        for key in [k for k in self.resources if k.startswith("net:") and k[4:] not in snap.interfaces]:
            self._remove(key)
        # Every GPU (Vini: "only one GPU shows" with the Radeon 680M + NVIDIA):
        # a card without a reading still gets its row -- "Sleeping" when runtime
        # PM has it suspended (never woken to ask), else "N/A".
        infos = getattr(snap, "gpu_info", {}) or {}
        for i, (card, busy) in enumerate(sorted(snap.gpus.items(), key=lambda kv: (len(kv[0]), kv[0]))):
            key = "gpu:" + card
            gi = infos.get(card, {})
            if key not in self.resources:
                self._add(Resource(key, "gpu", f"GPU {i}", card))
            r = self.resources[key]
            r.detail = f"{gi['maker']} ({card})" if gi.get("maker") else card
            state = f"{busy:.0f}%" if busy is not None else ("Sleeping" if gi.get("asleep") else "N/A")
            vram = gi.get("vram")
            small = [("Card", card)] + ([("Maker", gi["maker"])] if gi.get("maker") else [])
            big = [("Utilization", state)]
            if vram:
                big.append(("Dedicated memory", f"{fmt_gb(vram[0])}/{fmt_gb(vram[1])}"))
            r.subtitle = (f"{gi['maker']}  {state}" if gi.get("maker") else state)
            r.stats = {"caption": "Utilization", "top": "100%", "big": big, "small": small}
            if not first:
                r.a.push(busy or 0.0)                         # a sleeping card does no work: 0
        for key in [k for k in self.resources if k.startswith("gpu:")]:
            if key[4:] in snap.gpus:
                self._gpu_missing.pop(key, None)
                continue
            # an unplugged card goes; one missed read (a card waking up) doesn't flicker the list
            self._gpu_missing[key] = self._gpu_missing.get(key, 0) + 1
            if self._gpu_missing[key] >= GPU_GONE_SAMPLES:
                self._gpu_missing.pop(key)
                self._remove(key)
        if not visible:
            return
        for key, row in self.rows.items():
            res = self.resources[key]
            if row.sub.get_label() != res.subtitle:
                row.sub.set_label(res.subtitle)
            row.spark.set_lines(res.lines(), res.maximum if res.kind != "net" else None)
        if self.current in self.resources:
            res = self.resources[self.current]
            self.graph.set_lines(res.lines(), res.maximum if res.kind != "net" else None)
            self._fill_stats(res)
            if self.bar is not None:
                self.bar.set_values(getattr(res, "composition", {}))

    def shown(self) -> None:
        if self.win.last is not None:
            self.update_view()

    def update_view(self) -> None:
        for key, row in self.rows.items():
            res = self.resources[key]
            row.sub.set_label(res.subtitle)
            row.spark.queue_draw()
        if self.current in self.resources:
            res = self.resources[self.current]
            self._fill_stats(res)
            self.graph.queue_draw()
            if self.bar is not None:
                self.bar.set_values(getattr(res, "composition", {}))

