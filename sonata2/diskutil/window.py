"""Disk Utility (macOS Ventura Disk Utility): disks and their volumes.

Sidebar (translucent source list): Internal / External / Disk Images, each
disk with its volumes indented under it, an eject button on external
disks. The main pane shows the selection: big icon, name, kind, size, a
capacity bar split into coloured segments (the disk's partitions; Used /
Free of a mounted volume) with its legend, and an info table (Mount Point,
Capacity, Available, Used, Device, Type, Connection, UUID, Label,
Partition Map, S.M.A.R.T. status).

The glass toolbar acts on the selection: First Aid (check, then repair),
Partition (not available), Erase (Name, Format, Scheme for disks; always
confirmed; never the running system's volumes: /, /boot, /boot/efi, /home,
swap), Rename, Mount / Unmount / Unlock (encrypted volumes ask for the
password), Eject (unmounts the disk's volumes first) and Info. The same
actions are on the sidebar's right-click menu.

Everything goes through UDisks2 on the system bus (udisks.py, plain Gio,
async), so UDisks' polkit rules ask for a password when needed. Plugging
or unplugging a drive, mounting elsewhere, renaming: the window follows
live (UDisks' signals, no polling). Without UDisks2 the window says so.

Keys (⌘ = Ctrl or Super): I Info, E Eject, R Rename, Shift+E Erase,
Shift+F First Aid, W close."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import model  # noqa: E402
from .model import Disk, Volume  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.diskutil"
SWATCHES = model.SEGMENT_COLORS + ("sys_gray",)

ui.register("""
window.sonata-diskutil .du-main { background: %(window_bg)s; }
.du-paned > separator { min-width: 1px; background: %(window_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.du-sidebar list { background: none; padding: 0 10px 10px 10px; }
.du-sidebar list row { min-height: 26px; padding: 0 6px; border-radius: %(r_menu)s; background: none;
  color: %(label)s; transition: background-color %(t_fast)s; }
.du-sidebar list row:active { background: %(tool_hover)s; }
.du-sidebar list row:hover { background: none; }
.du-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.du-sidebar list row.du-head { min-height: 22px; margin-top: 10px; }
.du-sidebar .du-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.du-sidebar row label.du-row { font-size: %(text_body)s; }
.du-sidebar row image.du-lock { color: %(label_tertiary)s; }
.du-sidebar button.du-eject { min-width: 18px; min-height: 18px; padding: 0; background: none; box-shadow: none;
  border: none; color: %(label_secondary)s; transition: color %(t_fast)s; }
.du-sidebar button.du-eject:hover { color: %(label)s; }
.du-sidebar button.du-eject:active { color: %(label)s; opacity: 0.6; transition: opacity %(t_press)s; }
.du-header { padding: 20px 24px 12px 24px; }
.du-name { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700; color: %(label)s; }
.du-sub { font-size: %(text_small)s; color: %(label_secondary)s; }
.du-size { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 500; color: %(label)s; }
.du-size-sub { font-size: %(text_small)s; font-weight: 600; color: %(label_secondary)s; }
.du-legend { padding: 0 24px; }
.du-legend-name { font-size: %(text_small)s; color: %(label)s; }
.du-legend-size { font-size: %(text_small)s; color: %(label_secondary)s; }
.du-swatch { min-width: 9px; min-height: 9px; border-radius: 3px; }
.du-swatch.free { background: %(control_off)s; box-shadow: inset 0 0 0 1px %(separator)s; }
.du-info { margin: 18px 24px 20px 24px; background: %(content_bg)s; border-radius: %(r_menu)s;
  box-shadow: 0 0 0 1px %(separator)s; }
.du-info-row + .du-info-row { box-shadow: inset 0 1px %(separator)s; }
.du-cell { padding: 7px 12px; min-height: 16px; }
.du-cell + .du-cell { box-shadow: inset 1px 0 %(separator)s; }
.du-key { font-size: %(text_small)s; color: %(label)s; }
.du-value { font-size: %(text_small)s; color: %(label_secondary)s; }
.du-value.du-bad { color: %(destructive)s; font-weight: 600; }
.du-status { padding: 8px 24px; font-size: %(text_small)s; color: %(label_secondary)s; }
.du-missing { color: %(label)s; }
.du-missing .du-missing-title { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700; }
.du-missing .du-missing-body { font-size: %(text_body)s; color: %(label_secondary)s; }
.du-empty { font-size: %(text_title)s; color: %(label_tertiary)s; }
.du-form { margin-top: 6px; }
.du-form label { font-size: %(text_small)s; color: %(label)s; }
.du-infopanel { padding: 12px 14px; }
.du-infopanel label.du-key { font-size: %(text_small)s; font-weight: 400; color: %(label_secondary)s; }
.du-infopanel label.du-value { font-weight: 400; color: %(label)s; }
""" + "\n".join(".du-swatch.%s { background: %%(%s)s; }" % (s, s) for s in SWATCHES), key="diskutil")


class CapacityBar(Gtk.DrawingArea):
    """The segmented capacity bar (Disk Utility's coloured strip)."""
    HEIGHT = 22

    def __init__(self):
        super().__init__(hexpand=True, css_classes=["du-bar"])
        self.set_content_height(self.HEIGHT)
        self.segments = []
        self.set_draw_func(self._draw)
        ui.on_change(self.queue_draw)

    def set_segments(self, segments) -> None:
        self.segments = [s for s in segments if s[1] > 0]
        self.queue_draw()

    @staticmethod
    def widths(sizes, width: float, min_w: float = 4) -> list:
        """Segment widths filling `width`, each at least min_w (tiny partitions stay visible)."""
        total = sum(sizes) or 1
        raw = [max(min_w, width * s / total) for s in sizes]
        k = width / (sum(raw) or 1)
        return [w * k for w in raw]

    def _draw(self, _a, cr, w, h) -> None:
        r = ui.px("r_label")
        free = ui.rgba("control_off")

        def rounded(x, y, ww, hh):
            import math
            cr.new_sub_path()
            cr.arc(x + ww - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + ww - r, y + hh - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + hh - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()
        rounded(0.5, 0.5, w - 1, h - 1)
        cr.save()
        cr.clip_preserve()
        Gdk.cairo_set_source_rgba(cr, ui.rgba("content_bg"))
        cr.fill()
        x = 0.0
        gap = ui.rgba("content_bg")
        for (_n, _size, colour), sw in zip(self.segments, self.widths([s[1] for s in self.segments], w)):
            Gdk.cairo_set_source_rgba(cr, free if colour == "free" else ui.rgba(colour))
            cr.rectangle(x, 0, sw, h)
            cr.fill()
            if x > 0:                                   # a hairline gap between segments
                Gdk.cairo_set_source_rgba(cr, gap)
                cr.rectangle(x - 0.5, 0, 1, h)
                cr.fill()
            x += sw
        cr.restore()
        rounded(0.5, 0.5, w - 1, h - 1)
        Gdk.cairo_set_source_rgba(cr, ui.rgba("separator"))
        cr.set_line_width(1)
        cr.stroke()


class DiskUtilityWindow(Gtk.ApplicationWindow):
    def __init__(self, app, client=None):
        if not GLib.get_application_name():
            GLib.set_application_name("Disk Manager")
        super().__init__(application=app, title="Disk Manager", css_classes=["sonata-diskutil"])
        ui.window.standard(self)
        ui.window.remember_size(self, "diskutil", 900, 600)
        self.disks = []
        self.selected = None               # a Disk or a Volume
        self._sel_key = None               # its block path: kept across reloads
        self.usage = {}                    # mount point -> (used, free) from statvfs
        self.busy = ""
        self.available = False
        self._can_format = {}              # fs type -> mkfs available (Manager.CanFormat)
        self.toolbar = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Show or Hide the Sidebar", self.toggle_sidebar),), end=(
            ("workspacelistentryicon-bandaid-symbolic", "First Aid", self.first_aid),
            ("am-pie-symbolic", "Partition — Not available", lambda: None),
            ("draw-eraser-symbolic", "Erase", self.erase),
            ("layer-rename-symbolic", "Rename", self.rename),
            ("media-mount-symbolic", "Mount", self.mount_toggle),
            ("media-eject-symbolic", "Eject", self.eject),
            ("info-outline-symbolic", "Info", self.info)))
        tools = self.toolbar.get_child().get_end_widget()
        self.btn = {}
        child = tools.get_first_child()
        for key in ("first_aid", "partition", "erase", "rename", "mount", "eject", "info"):
            self.btn[key] = child
            child = child.get_next_sibling()
        self.btn["partition"].set_sensitive(False)

        # sidebar | main pane
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["du-list"])
        self.list.connect("row-selected", self._row_selected)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._context_menu)
        self.list.add_controller(menu)
        self.sidebar = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          css_classes=["sonata-sidebar", "du-sidebar"])
        self.sidebar.set_size_request(ui.window.SIDEBAR_W, -1)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True,
                               css_classes=["du-main"])
        self.stack.add_named(self._build_detail(), "detail")
        self.stack.add_named(Gtk.Label(label="No Selection", css_classes=["du-empty"]), "empty")
        self.stack.add_named(self._build_missing(), "missing")
        self.stack.add_named(ui.progress.spinner(size=32), "loading")
        self.stack.set_visible_child_name("loading")
        self.paned = Gtk.Paned(start_child=self.sidebar, end_child=self.stack, shrink_start_child=False,
                               resize_start_child=False, position=ui.window.SIDEBAR_W, vexpand=True, css_classes=["du-paned"])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        col.append(self.paned)
        self.set_child(col)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._update_tools()
        if client is None:
            from .udisks import Client
            client = Client()
        self.client = client
        self.connect("destroy", lambda *_: getattr(self.client, "stop", lambda: None)())
        client.start(self.load, self.unavailable)

    # -- building ------------------------------------------------------------------------------
    def _build_detail(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=14, css_classes=["du-header"])
        self.icon = Gtk.Image(pixel_size=64)
        head.append(self.icon)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        self.name_label = Gtk.Label(xalign=0, css_classes=["du-name"], ellipsize=Pango.EllipsizeMode.END)
        self.sub_label = Gtk.Label(xalign=0, css_classes=["du-sub"], ellipsize=Pango.EllipsizeMode.END)
        self.sub2_label = Gtk.Label(xalign=0, css_classes=["du-sub"], ellipsize=Pango.EllipsizeMode.END)
        for w in (self.name_label, self.sub_label, self.sub2_label):
            names.append(w)
        head.append(names)
        sizes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.size_label = Gtk.Label(xalign=1, css_classes=["du-size"])
        self.size_sub = Gtk.Label(xalign=1, css_classes=["du-size-sub"])
        sizes.append(self.size_label)
        sizes.append(self.size_sub)
        head.append(sizes)
        box.append(head)
        self.bar = CapacityBar()
        self.bar.set_margin_start(24)
        self.bar.set_margin_end(24)
        box.append(self.bar)
        self.legend = Gtk.Box(spacing=26, css_classes=["du-legend"], margin_top=10)
        box.append(self.legend)
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["du-info"])
        box.append(self.info_box)
        self.status = Gtk.Box(spacing=8, css_classes=["du-status"], visible=False, valign=Gtk.Align.END,
                              vexpand=True)
        self.status_spin = ui.progress.spinner(False)
        self.status_text = Gtk.Label(xalign=0)
        self.status.append(self.status_spin)
        self.status.append(self.status_text)
        box.append(self.status)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def _build_missing(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, css_classes=["du-missing"])
        box.append(Gtk.Image(icon_name="sonata-diskmanager", pixel_size=96))
        self.missing_title = Gtk.Label(label="Disk Manager needs UDisks2", css_classes=["du-missing-title"])
        box.append(self.missing_title)
        self.missing_body = Gtk.Label(label="Install the udisks2 package and start its service "
                                            "(udisks2.service), then open Disk Manager again.",
                                      css_classes=["du-missing-body"], wrap=True, max_width_chars=48,
                                      justify=Gtk.Justification.CENTER)
        box.append(self.missing_body)
        return box

    # -- data ----------------------------------------------------------------------------------
    def unavailable(self, message: str = "") -> None:
        """UDisks2 can't be reached: say so instead of the disks."""
        self.available = False
        self.disks = []
        self._fill_sidebar()
        self.sidebar.set_visible(False)
        self.stack.set_visible_child_name("missing")
        self._update_tools()

    def load(self, objects: dict) -> None:
        """A fresh UDisks tree (at start and after every change)."""
        self.available = True
        self.sidebar.set_visible(True)
        self.disks = model.parse(objects)
        self._fill_sidebar()
        self._query_formats()

    def _item_key(self, item):
        return item.block_path if isinstance(item, Disk) else item.path

    def _fill_sidebar(self) -> None:
        key = self._sel_key
        self._filling = True
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        target = None
        for title, disks in model.grouped(self.disks):
            head = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["du-head"])
            head.set_child(Gtk.Label(label=title, xalign=0))
            head.item = None
            self.list.append(head)
            for d in disks:
                row = self._row(d)
                self.list.append(row)
                target = row if self._item_key(d) == key or target is None else target
                for v in d.volumes:
                    if v.listed:
                        vr = self._row(v)
                        self.list.append(vr)
                        if v.path == key:
                            target = vr
        self._filling = False
        if target is not None:
            self.list.select_row(target)
            self._row_selected(self.list, target)
        else:
            self._select(None)

    def _row(self, item) -> Gtk.ListBoxRow:
        is_disk = isinstance(item, Disk)
        disk = item if is_disk else item.disk
        box = Gtk.Box(spacing=6, margin_start=0 if is_disk else 20)
        box.append(Gtk.Image(icon_name=disk.icon, pixel_size=18 if is_disk else 16))
        box.append(Gtk.Label(label=item.name, xalign=0, hexpand=True, css_classes=["du-row"],
                             ellipsize=Pango.EllipsizeMode.END))
        if not is_disk and item.encrypted:
            box.append(Gtk.Image(icon_name="system-lock-screen-symbolic" if item.locked else "padlock-open-symbolic",
                                 pixel_size=12, css_classes=["du-lock"]))
        if is_disk and (disk.external or disk.is_image):
            ej = Gtk.Button(icon_name="media-eject-symbolic", css_classes=["du-eject"], tooltip_text="Eject",
                            valign=Gtk.Align.CENTER, can_focus=False)
            ej.connect("clicked", lambda _b, d=disk: self.eject(d))
            box.append(ej)
        row = Gtk.ListBoxRow(child=box)
        row.item = item
        return row

    def _row_selected(self, _list, row) -> None:
        if getattr(self, "_filling", False):
            return
        self._select(row.item if row is not None else None)

    def _select(self, item) -> None:
        self.selected = item
        self._sel_key = self._item_key(item) if item is not None else self._sel_key
        if not self.available:
            return
        if item is None:
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("detail")
            self._show(item)
            if isinstance(item, Volume) and item.mounted:
                self._read_usage(item)
        self._update_tools()

    def _read_usage(self, v: Volume) -> None:
        from ..backend.system import run_async
        mp = v.mount_point

        def stat(path):
            s = os.statvfs(path)
            return (s.f_blocks - s.f_bfree) * s.f_frsize, s.f_bavail * s.f_frsize

        def got(res):
            if res is not None:
                self.usage[mp] = res
                if self.selected is v:
                    self._show(v)
        run_async(stat, got, mp)

    # -- main pane -----------------------------------------------------------------------------
    def _show(self, item) -> None:
        is_disk = isinstance(item, Disk)
        disk = item if is_disk else item.disk
        self.icon.set_from_icon_name(disk.icon)
        self.name_label.set_label(item.name)
        where = model.BUS_NAMES.get(disk.bus, disk.bus.upper())
        table = model.TABLE_NAMES.get(disk.table, disk.table.upper())
        if is_disk:
            self.sub_label.set_label(" • ".join(x for x in (disk.kind, table) if x))
            n = sum(1 for v in disk.volumes if v.listed)
            self.size_sub.set_label(f"{n} VOLUME{'S' if n != 1 else ''}" if n else "")
            self.sub2_label.set_label(disk.device)
        else:
            place = "Disk Image" if disk.is_image else ("External" if disk.external else "Internal")
            self.sub_label.set_label(" • ".join(x for x in (
                " ".join(x for x in (where, place, "Physical Volume") if x), item.kind) if x))
            self.sub2_label.set_label(item.device + (" — Locked" if item.locked else ""))
            self.size_sub.set_label("")
        self.size_label.set_label(ui.fmt.size(item.size))
        usage = self.usage.get(item.mount_point) if not is_disk and item.mounted else None
        segs = model.segments(disk, None if is_disk else item, usage)
        self.bar.set_segments(segs)
        self._fill_legend(segs)
        self._fill_info(self._info_rows(item, usage))

    def _fill_legend(self, segs) -> None:
        while (c := self.legend.get_first_child()) is not None:
            self.legend.remove(c)
        for name, size, colour in segs[:8]:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            top = Gtk.Box(spacing=6)
            top.append(Gtk.Box(css_classes=["du-swatch", colour], valign=Gtk.Align.CENTER))
            top.append(Gtk.Label(label=name, xalign=0, css_classes=["du-legend-name"],
                                 ellipsize=Pango.EllipsizeMode.END, max_width_chars=18))
            col.append(top)
            col.append(Gtk.Label(label=ui.fmt.size(size), xalign=0, margin_start=15, css_classes=["du-legend-size"]))
            self.legend.append(col)

    def _info_rows(self, item, usage) -> list:
        dash = "—"
        if isinstance(item, Disk):
            d = item
            n = sum(1 for v in d.volumes if v.listed)
            return [("Location", "Disk Image" if d.is_image else ("External" if d.external else "Internal")),
                    ("Capacity", ui.fmt.size(d.size)),
                    ("Connection", model.BUS_NAMES.get(d.bus, d.bus.upper()) or ("Loop" if d.is_image else "Internal")),
                    ("Child count", str(n)),
                    ("Partition Map", model.TABLE_NAMES.get(d.table, d.table.upper()) or "None"),
                    ("Type", d.media_kind),
                    ("S.M.A.R.T. status", d.smart or "Not Supported"),
                    ("Device", d.device)]
        v, d = item, item.disk
        used, free = usage if usage else (None, None)
        return [("Mount Point", v.mount_point or ("Locked" if v.locked else "Not Mounted")),
                ("Type", v.kind),
                ("Capacity", ui.fmt.size(v.size)),
                ("Label", v.label or dash),
                ("Available", ui.fmt.size(free) if free is not None else dash),
                ("Connection", model.BUS_NAMES.get(d.bus, d.bus.upper()) or ("Loop" if d.is_image else "Internal")),
                ("Used", ui.fmt.size(used) if used is not None else dash),
                ("Device", v.device),
                ("UUID", v.uuid or dash),
                ("Partition Map", model.TABLE_NAMES.get(d.table, d.table.upper()) or "None")]

    def _fill_info(self, rows) -> None:
        while (c := self.info_box.get_first_child()) is not None:
            self.info_box.remove(c)
        for i in range(0, len(rows), 2):
            line = Gtk.Box(homogeneous=True, css_classes=["du-info-row"])
            for key, value in rows[i:i + 2]:
                cell = Gtk.Box(spacing=12, css_classes=["du-cell"])
                cell.append(Gtk.Label(label=key + ":", xalign=0, css_classes=["du-key"]))
                val = Gtk.Label(label=value, xalign=1, hexpand=True, selectable=False, css_classes=["du-value"],
                                ellipsize=Pango.EllipsizeMode.MIDDLE, tooltip_text=value)
                if key == "S.M.A.R.T. status" and value == "Failing":
                    val.add_css_class("du-bad")
                cell.append(val)
                line.append(cell)
            self.info_box.append(line)

    # -- toolbar state -------------------------------------------------------------------------
    def actions(self, item=None) -> dict:
        """Which actions the selection allows ({name: bool}), and the mount button's verb."""
        item = self.selected if item is None else item
        ok = self.available and not self.busy and item is not None
        vol = item if isinstance(item, Volume) else None
        disk = item if isinstance(item, Disk) else (vol.disk if vol else None)
        verb = "Mount"
        if vol is not None:
            verb = "Unlock" if vol.locked else ("Unmount" if vol.mounted else "Mount")
        in_use = bool(vol and vol.mounted and vol.protected)          # the running system
        return {"first_aid": bool(ok and vol and vol.has_fs and not vol.locked and not in_use),
                "erase": bool(ok and not item.protected),
                "rename": bool(ok and vol and vol.has_fs and not vol.locked),
                "mount": bool(ok and vol and (vol.locked or (vol.has_fs and not vol.swap and not in_use))),
                "eject": bool(ok and disk and (disk.external or disk.is_image) and not disk.protected),
                "info": bool(ok),
                "verb": verb}

    def _update_tools(self) -> None:
        a = self.actions()
        for k in ("first_aid", "erase", "rename", "mount", "eject", "info"):
            self.btn[k].set_sensitive(a[k])
        self.btn["mount"].set_icon_name("padlock-open-symbolic" if a["verb"] == "Unlock" else "media-mount-symbolic")
        self.btn["mount"].set_tooltip_text(a["verb"])

    def toggle_sidebar(self) -> None:
        if self.available:
            self.sidebar.set_visible(not self.sidebar.get_visible())

    # -- running UDisks calls ------------------------------------------------------------------
    def _set_busy(self, text: str) -> None:
        self.busy = text
        self.status.set_visible(bool(text))
        self.status_text.set_label(text)
        self.status_spin.set_spinning(bool(text))
        self._update_tools()

    def _failed(self, heading: str, err) -> None:
        from .udisks import dismissed, error_text
        if err is None or dismissed(err):
            return
        ui.dialog.alert(heading, error_text(err), [("ok", "OK", "default")], parent=self)

    def _chain(self, steps, done, heading: str) -> None:
        """Run step(cb) one after the other (cb(out, err)); done(out) after the last,
        an alert if one fails."""
        steps = list(steps)

        def nxt(out=None, err=None):
            if err is not None:
                self._set_busy("")
                self._failed(heading, err)
                return
            if not steps:
                done(out)
                return
            steps.pop(0)(nxt)
        nxt()

    def mount_toggle(self, vol=None) -> None:
        v = vol or self.selected
        if not isinstance(v, Volume):
            return
        if v.locked:
            self.unlock(v)
        elif v.mounted:
            self.client.unmount(v.fs_path, lambda _o, e: self._failed(f"“{v.name}” couldn't be unmounted.", e))
        else:
            self.client.mount(v.fs_path, lambda _o, e: self._failed(f"“{v.name}” couldn't be mounted.", e))

    def unlock(self, v: Volume) -> None:
        entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True, css_classes=["du-form"])
        dlg = ui.dialog.alert(f"Unlock “{v.name}”", "Enter the password to unlock this volume.",
                              [("cancel", "Cancel", ""), ("unlock", "Unlock", "default")],
                              lambda rid: rid == "unlock" and self._do_unlock(v, entry.get_text()), parent=self)
        dlg.set_extra_child(entry)
        GLib.idle_add(lambda: (entry.grab_focus(), False)[1])

    def _do_unlock(self, v: Volume, password: str) -> None:
        def unlocked(out, err):
            if err is not None:
                self._failed(f"“{v.name}” couldn't be unlocked.", err)
                return
            self.client.mount(out[0], lambda _o, _e: None)        # like macOS: unlocked volumes mount
        self.client.unlock(v.path, password, unlocked)

    def eject(self, disk=None) -> None:
        item = disk if isinstance(disk, Disk) else self.selected
        d = item if isinstance(item, Disk) else (item.disk if isinstance(item, Volume) else None)
        if d is None or d.protected:
            return
        c = self.client
        steps = []
        for v in d.volumes:
            if v.mounted:
                steps.append(lambda cb, v=v: c.unmount(v.fs_path, cb))
            if v.encrypted and not v.locked:
                steps.append(lambda cb, v=v: c.lock(v.path, cb))
        if d.is_image:
            from .udisks import options
            steps.append(lambda cb: c.call(d.block_path, "Loop", "Delete", GLib.Variant.new_tuple(options()), cb))
        else:
            if d.ejectable:
                steps.append(lambda cb: c.eject(d.path, cb))
            if d.can_power_off:
                steps.append(lambda cb: c.power_off(d.path, cb))
        self._chain(steps, lambda _o: None, f"“{d.name}” couldn't be ejected.")

    def rename(self) -> None:
        v = self.selected
        if not isinstance(v, Volume) or not v.has_fs:
            return
        return ui.dialog.ask_text(f"Rename “{v.name}”", v.label, "Rename",
                                  lambda text: self.client.set_label(
                                      v.fs_path, text, lambda _o, e: self._failed(f"“{v.name}” couldn't be renamed.", e)),
                                  body="Enter a new name for this volume.", parent=self)

    # erase ----------------------------------------------------------------------------------------
    def _query_formats(self) -> None:
        if self._can_format or not hasattr(self.client, "can"):
            return
        for t, _label, _m in model.FORMATS:
            self.client.can("Format", t, lambda ok, _u, t=t: self._can_format.__setitem__(t, ok))

    def format_choices(self) -> list:
        can = (lambda t: self._can_format[t]) if "ntfs" in self._can_format else None
        return model.formats(can)

    def erase(self) -> None:
        item = self.selected
        if item is None or item.protected:
            if item is not None:
                self._refuse_erase(item)
            return
        is_disk = isinstance(item, Disk)
        choices = self.format_choices()
        grid = Gtk.Grid(column_spacing=10, row_spacing=8, css_classes=["du-form"])
        name = Gtk.Entry(text=(item.volumes[0].label if is_disk and item.volumes else getattr(item, "label", ""))
                         or "Untitled", hexpand=True, activates_default=True)
        external = (item if is_disk else item.disk).external       # removable media: ExFAT, like macOS
        fmt = ui.controls.popup_button([label for _t, label in choices],
                                       next((i for i, (t, _l) in enumerate(choices) if t == "exfat"), 0)
                                       if external else 0)
        rows = [("Name:", name), ("Format:", fmt)]
        scheme = None
        if is_disk:
            scheme = ui.controls.popup_button([label for _t, label in model.SCHEMES], 0)
            rows.append(("Scheme:", scheme))
        for i, (label, w) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)

        def answer(rid):
            if rid != "erase":
                return
            label = name.get_text().strip()
            fs_type = choices[fmt.get_selected()][0]
            table = model.SCHEMES[scheme.get_selected()][0] if scheme is not None else None
            self._confirm_erase(item, label, fs_type, table)
        dlg = ui.dialog.alert(f"Erase “{item.name}”?",
                              f"Erasing “{item.name}” will permanently erase all data stored on it. "
                              "Enter a name, choose a format and click Erase.",
                              [("cancel", "Cancel", ""), ("erase", "Erase", "default")], answer, parent=self)
        dlg.set_extra_child(grid)

    def _confirm_erase(self, item, label: str, fs_type: str, table) -> None:
        """The last word before erasing: names the device and its size."""
        what = f"{item.device} ({ui.fmt.size(item.size)})"
        ui.dialog.alert(f"Are you sure you want to erase “{item.name}”?",
                        f"All the data on {what} will be permanently erased. You can't undo this action.",
                        [("cancel", "Cancel", ""), ("erase", "Erase", "destructive")],
                        lambda rid: rid == "erase" and self._do_erase(item, label, fs_type, table), parent=self)

    def _refuse_erase(self, item) -> None:
        ui.dialog.alert(f"“{item.name}” can't be erased.",
                        "It holds the running system (/, /boot, /home, swap or one of their devices).",
                        [("ok", "OK", "default")], parent=self)

    def _find(self, disks, item):
        """`item` (a Disk or Volume) in another parse of the tree, or None."""
        key = self._item_key(item)
        for d in disks:
            for it in [d] + d.volumes:
                if type(it) is type(item) and self._item_key(it) == key:
                    return it
        return None

    def _do_erase(self, item, label: str, fs_type: str, table) -> None:
        """Checked again on a fresh tree (not the object the sheet was
        opened with: it may have been mounted as a system volume since)."""
        def check(objects):
            cur = self._find(model.parse(objects), item) if objects else None
            if cur is None or cur.protected:
                self._refuse_erase(cur or item)
                return
            self._erase_now(cur, label, fs_type, table)
        if callable(getattr(type(self.client), "fetch", None)):
            self.client.fetch(check)
        else:
            check(getattr(self.client, "objects", None))

    def _erase_now(self, item, label: str, fs_type: str, table) -> None:
        c = self.client
        heading = f"“{item.name}” couldn't be erased."
        self._set_busy(f"Erasing “{item.name}”…")
        done = lambda _o: self._set_busy("")            # noqa: E731
        if isinstance(item, Volume):
            self._chain([lambda cb: c.format(item.path, fs_type, label, cb, update_partition_type=True)],
                        done, heading)
            return
        tries = {"n": 0}

        def partition(cb):
            def retry(out, err):             # the new table shows up on D-Bus a moment after Format
                if err is not None and tries["n"] < 5 and "PartitionTable" in str(err):
                    tries["n"] += 1
                    GLib.timeout_add(800, lambda: (c.create_partition(item.block_path, fs_type, label, retry),
                                                   False)[1])
                    return
                cb(out, err)
            c.create_partition(item.block_path, fs_type, label, retry)
        self._chain([lambda cb: c.format(item.block_path, table, "", cb), partition], done, heading)

    # first aid ------------------------------------------------------------------------------------
    def first_aid(self) -> None:
        v = self.selected
        if not isinstance(v, Volume) or not v.has_fs:
            return
        if v.mounted and v.protected:
            ui.dialog.alert(f"First Aid can't check “{v.name}” while it's in use.",
                            "It holds the running system. Check it from another system.",
                            [("ok", "OK", "default")], parent=self)
            return
        body = "First Aid will check the volume for errors." + \
            (" It will be unmounted while it's checked." if v.mounted else "")
        ui.dialog.alert(f"Would you like to run First Aid on “{v.name}”?", body,
                        [("cancel", "Cancel", ""), ("run", "Run", "default")],
                        lambda rid: rid == "run" and self._check(v), parent=self)

    def _check(self, v: Volume) -> None:
        def can(ok, util):
            if not ok:
                ui.dialog.alert("First Aid isn't available for this volume.",
                                f"Install {util} to check {v.kind} volumes." if util else
                                f"{v.kind} volumes can't be checked.", [("ok", "OK", "default")], parent=self)
                return
            self._set_busy(f"Checking “{v.name}”…")
            c, was = self.client, v.mounted
            steps = [lambda cb: c.unmount(v.fs_path, cb)] if was else []
            steps.append(lambda cb: c.check(v.fs_path, cb))
            self._chain(steps, lambda out: self._checked(v, bool(out and out[0]), was), "First Aid failed.")
        self.client.can("Check", v.fs_type, can)

    def _remount(self, v: Volume, was: bool) -> None:
        if was:
            self.client.mount(v.fs_path, lambda _o, _e: None)

    def _checked(self, v: Volume, consistent: bool, was: bool) -> None:
        self._set_busy("")
        if consistent:
            self._remount(v, was)
            ui.dialog.alert("First Aid process has completed.", f"The volume “{v.name}” appears to be OK.",
                            [("done", "Done", "default")], parent=self)
            return

        def can(ok, _util):
            if not ok:
                self._remount(v, was)
                ui.dialog.alert(f"First Aid found problems on “{v.name}”.", "They can't be repaired here.",
                                [("done", "Done", "default")], parent=self)
                return
            ui.dialog.alert(f"First Aid found problems on “{v.name}”.", "Do you want to repair them now?",
                            [("cancel", "Not Now", ""), ("repair", "Repair", "default")],
                            lambda rid: self._repair(v, was) if rid == "repair" else self._remount(v, was),
                            parent=self)
        self.client.can("Repair", v.fs_type, can)

    def _repair(self, v: Volume, was: bool) -> None:
        self._set_busy(f"Repairing “{v.name}”…")

        def done(out):
            self._set_busy("")
            self._remount(v, was)
            ok = bool(out and out[0])
            ui.dialog.alert("First Aid process has completed." if ok else f"“{v.name}” couldn't be repaired.",
                            f"The volume “{v.name}” was repaired." if ok else
                            "Copy its data to another disk, then erase it.",
                            [("done", "Done", "default")], parent=self)
        self._chain([lambda cb: self.client.repair(v.fs_path, cb)], done, "First Aid failed.")

    # info -----------------------------------------------------------------------------------------
    def info_fields(self, item=None) -> list:
        item = self.selected if item is None else item
        if item is None:
            return []
        if isinstance(item, Disk):
            d = item
            return [("Name", d.name), ("Type", d.kind), ("Device", d.device), ("Model", d.model or "—"),
                    ("Vendor", d.vendor or "—"), ("Capacity", f"{ui.fmt.size(d.size)} ({d.size:,} bytes)"),
                    ("Partition Map", model.TABLE_NAMES.get(d.table, d.table) or "None"),
                    ("Media", d.media_kind), ("Removable", "Yes" if d.removable else "No"),
                    ("Ejectable", "Yes" if d.ejectable else "No"),
                    ("S.M.A.R.T. Status", d.smart or "Not Supported")] + \
                ([("Image File", d.image)] if d.is_image else [])
        v = item
        return [("Name", v.name), ("Type", v.kind), ("Device", v.device), ("Mount Point", v.mount_point or "—"),
                ("Capacity", f"{ui.fmt.size(v.size)} ({v.size:,} bytes)"), ("Label", v.label or "—"),
                ("UUID", v.uuid or "—"), ("Partition Number", str(v.number) if v.number else "—"),
                ("Partition Offset", f"{v.offset:,} bytes"), ("Encrypted", "Yes" if v.encrypted else "No"),
                ("System Volume", "Yes" if v.protected else "No"), ("Disk", v.disk.name)]

    def info(self) -> None:
        fields = self.info_fields()
        if not fields:
            return
        grid = Gtk.Grid(column_spacing=14, row_spacing=4, css_classes=["du-infopanel"])
        for i, (k, val) in enumerate(fields):
            grid.attach(Gtk.Label(label=k, xalign=1, css_classes=["du-key"]), 0, i, 1, 1)
            grid.attach(Gtk.Label(label=val, xalign=0, selectable=True, css_classes=["du-value"],
                                  ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=40), 1, i, 1, 1)
        ui.panel.popup(self.btn["info"], grid)

    # -- input ---------------------------------------------------------------------------------
    def _context_menu(self, gesture, _n, x, y) -> None:
        row = self.list.get_row_at_y(int(y))
        if row is None or getattr(row, "item", None) is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.list.select_row(row)
        a = self.actions(row.item)
        Item = ui.menu.Item
        ui.menu.popup(self.list, [
            [Item(a["verb"], self.mount_toggle, enabled=a["mount"]), Item("Eject", self.eject, enabled=a["eject"])],
            [Item("Rename…", self.rename, enabled=a["rename"]), Item("Erase…", self.erase, enabled=a["erase"]),
             Item("Run First Aid…", self.first_aid, enabled=a["first_aid"])],
            [Item("Get Info", self.info, enabled=a["info"])],
        ], at=(x, y), glass=True, passthrough=True)

    def _key(self, _c, keyval, _code, state) -> bool:
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        shift = state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        a = self.actions()
        act = {Gdk.KEY_i: ("info", self.info), Gdk.KEY_e: ("erase", self.erase) if shift else ("eject", self.eject),
               Gdk.KEY_r: ("rename", self.rename), Gdk.KEY_f: ("first_aid", self.first_aid) if shift else None,
               Gdk.KEY_w: ("", self.close)}.get(k)
        if act is None:
            return False
        if not act[0] or a[act[0]]:
            act[1]()
        return True


def open_windows(app, paths=()) -> None:
    """One Disk Utility window; opening it again brings it forward."""
    win = next((w for w in app.get_windows() if isinstance(w, DiskUtilityWindow)), None)
    (win or DiskUtilityWindow(app)).present()


def diskutil_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Disk Manager\n"
                              "Comment=Manage disks and volumes\nIcon=sonata-diskmanager\n"
                              "Categories=System;Utility;\nKeywords=disk;drive;partition;format;erase;mount;usb;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} diskutil %F\n")
