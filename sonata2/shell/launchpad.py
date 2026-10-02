"""Launchpad (macOS Big Sur): full-screen app grid over the blurred desktop.

- 7 x 5 pages (Adw.Carousel: swipe, scroll, arrow keys, page dots).
- Search field at the top; typing filters instantly, Return opens the first.
- Folders: drop an app on another (hold ~0.4 s over it) to make one, named
  from the apps' category; click to open, rename in place, drag apps out.
- Drag to reorder (neighbours make room live); hold at the screen side to
  flip pages; drag an app onto the Dock to pin it (.desktop file list).
- Jiggle mode (press and hold an icon, or hold Alt): user-installed
  shortcuts (~/.local/share/applications) get a delete badge -> Trash.
- Opens/closes with the Big Sur zoom + fade; click empty space, Esc or
  launching an app closes it.

Runs as its own single-instance process: `python3 -m sonata2 launchpad`
toggles it. Layout: launchpad_model.py (~/.config/sonata2/launchpad.json)."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import apps, config, icons, logs, names, ui  # noqa: E402
from .. import launchpad_model as M  # noqa: E402
from . import layer  # noqa: E402

OPEN_MS, CLOSE_MS = 230, 170
FOLDER_HOLD_MS, FLIP_HOLD_MS, JIGGLE_HOLD_MS = 400, 650, 800
REORDER_HOLD_MS = 220       # icons make way only after a short pause (so you can reach an icon to make a folder)
ZOOM_FROM = 1.12            # icons zoom in from 112 % while fading in (Big Sur)
USER_APPS = os.path.join(GLib.get_user_data_dir(), "applications")

ui.register("""
window.sonata-launchpad, window.sonata-launchpad > contents { background: none; }
window.sonata-launchpad *:drop(active) { box-shadow: none; outline: none; border-color: transparent; }
.lp-search { min-height: 28px; min-width: 240px; border-radius: 8px; padding: 0 8px;
  background: %(field_on_scrim)s; color: %(on_scrim)s; border: none; box-shadow: 0 0 0 0.5px rgba(255,255,255,0.18);
  font-family: %(font)s; font-size: %(text_body)s; caret-color: %(on_scrim)s; }
.lp-search:focus-within { outline: none; box-shadow: 0 0 0 0.5px rgba(255,255,255,0.28); }
.lp-search image { color: %(on_scrim_secondary)s; }
.lp-search text placeholder { color: %(on_scrim_secondary)s; }
.lp-item, .lp-item:hover, .lp-item:active { background: none; border: none; box-shadow: none; outline: none;
  padding: 6px 0; border-radius: 18px; }
.lp-item.selected { background: %(tile_on_scrim)s; }
.lp-item:active .lp-icon { filter: brightness(0.7); }
.lp-item.folder-target .lp-icon, .lp-item.folder-target .lp-folder { transform: scale(1.12); }
.lp-item.dragging { opacity: 0; }
.lp-label { color: %(on_scrim)s; font-family: %(font)s; font-size: %(lp_label)s; font-weight: 400;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.55); margin-top: 6px; }
.lp-panel { background: %(folder_panel)s; border-radius: 28px;
  box-shadow: 0 0 0 0.5px rgba(255,255,255,0.14), 0 12px 40px rgba(0,0,0,0.35); padding: 24px 28px; }
.lp-panel-title, .lp-panel-title text { color: %(on_scrim)s; font-family: %(font_display)s;
  font-size: 30px; font-weight: 600; background: none; }
.lp-dots { color: %(on_scrim)s; }
.lp-more { border-radius: 99px; min-width: 30px; min-height: 30px; padding: 0; border: none; box-shadow: none;
  background: %(field_on_scrim)s; color: %(on_scrim)s; }
.lp-more:hover { background: %(tile_on_scrim)s; }
.lp-more .lp-more-dots { font-size: 15px; letter-spacing: 1px; margin-top: -4px; }
.lp-badge { min-width: 20px; min-height: 20px; padding: 0; border-radius: 99px; border: none;
  background: rgba(60, 60, 64, 0.92); color: white; box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  -gtk-icon-size: 10px; }
@keyframes lp-jiggle { 0%% { transform: rotate(-1.6deg); } 50%% { transform: rotate(1.6deg); }
                       100%% { transform: rotate(-1.6deg); } }
.jiggle .lp-item .lp-icon, .jiggle .lp-item .lp-folder { animation: lp-jiggle 260ms ease-in-out infinite; }
.jiggle .lp-item.odd .lp-icon, .jiggle .lp-item.odd .lp-folder { animation-delay: -130ms; }
.lp-lock { color: %(on_scrim)s; }
.lp-lock-text { color: %(on_scrim)s; font-family: %(font)s; font-size: %(text_body)s; }
.lp-lock-hint { color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: %(text_small)s; }
.lp-lock-panel passwordentry { min-width: 220px; min-height: 28px; border-radius: 8px; padding: 0 8px;
  background: %(field_on_scrim)s; color: %(on_scrim)s; box-shadow: 0 0 0 0.5px rgba(255,255,255,0.18); }
@keyframes lp-shake {   /* one selector per step: GTK's CSS has no "0%%, 100%%" lists */
  0%% { transform: none; } 20%% { transform: translateX(-8px); } 40%% { transform: translateX(8px); }
  60%% { transform: translateX(-8px); } 80%% { transform: translateX(8px); } 100%% { transform: none; } }
.lp-lock-panel.shake { animation: lp-shake 360ms ease-in-out; }
/* folders open and close with a zoom + fade (Big Sur); the grid dims behind */
.lp-col { transition: opacity %(fold_in)dms cubic-bezier(0.2, 0.8, 0.2, 1); }
.lp-col.dimmed { opacity: 0.35; }
@keyframes lp-folder-in { from { opacity: 0; transform: scale(0.86); } to { opacity: 1; transform: none; } }
@keyframes lp-folder-out { from { opacity: 1; transform: none; } to { opacity: 0; transform: scale(0.92); } }
.lp-folder-view { animation: lp-folder-in %(fold_in)dms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.lp-folder-view.closing { animation: lp-folder-out %(fold_out)dms ease-in both; }
.lp-empty { color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: %(text_title)s; }
""", key="launchpad", lp_label="12px", fold_in=ui.tokens.ms(240), fold_out=ui.tokens.ms(160))


def installed_apps() -> dict:
    """desktop id -> Gio.DesktopAppInfo of the apps a launcher should list."""
    out = {}
    for did, info in apps.scan().items():       # the folders themselves: new apps show up at once
        if info.should_show():
            out[did[:-8]] = info
    return out


PROTECTED = apps.PROTECTED


def _removable(info) -> bool:
    """Apps the jiggle-mode x uninstalls (asked first): all but Sonata's own."""
    return not (info.get_id() or "").startswith(PROTECTED)


class ZoomBin(Gtk.Widget):
    """One child, drawn with a zoom + opacity (open/close animation) over a
    scrim (+ blurred wallpaper in previews)."""

    def __init__(self, child):
        super().__init__()
        self.set_layout_manager(Gtk.BinLayout())
        child.set_parent(self)
        self.child = child
        self.progress = 0.0         # 0 closed .. 1 open
        self.backdrop = None        # preview only
        self.frozen = None          # the grid as one texture while it zooms (freeze())
        self.cached = None          # that texture, kept while the grid doesn't change
        self._size = (0, 0)
        ui.on_change(lambda: (self.invalidate(), self.queue_draw()))

    def invalidate(self, *_a) -> None:
        """The grid changed (apps, page, search, folder, jiggle, size, look):
        the next animation draws a new picture of it."""
        self.cached = None

    def freeze(self) -> None:
        """Draw the grid once into a texture for the open/close animation:
        each frame then scales one picture instead of re-drawing every icon
        and label (smooth at 144 Hz, little CPU). The picture is kept for the
        next open while the grid stays the same: opening starts at once."""
        if self.cached is not None:
            self.frozen = self.cached
            return
        self.frozen = None
        w, h = self.get_width(), self.get_height()
        native = self.get_native()
        if w <= 0 or h <= 0 or native is None or native.get_renderer() is None:
            return
        sf = self.get_scale_factor() or 1
        snap = Gtk.Snapshot()
        snap.scale(sf, sf)
        self.snapshot_child(self.child, snap)
        node = snap.to_node()
        if node is None:
            return
        try:
            self.frozen = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w * sf, h * sf))
        except GLib.Error:
            self.frozen = None
        self.cached = self.frozen

    def thaw(self) -> None:
        self.frozen = None
        self.queue_draw()

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        p = self.progress
        rect = Graphene.Rect()
        rect.init(0, 0, w, h)
        snap.push_opacity(p)
        if self.backdrop:
            snap.append_texture(self.backdrop, rect)
        snap.append_color(ui.rgba("scrim"), rect)
        snap.pop()
        if p <= 0:
            return
        s = ZOOM_FROM + (1 - ZOOM_FROM) * p
        snap.push_opacity(p)
        snap.save()
        snap.translate(Graphene.Point().init(w / 2, h / 2))
        snap.scale(s, s)
        snap.translate(Graphene.Point().init(-w / 2, -h / 2))
        if self.frozen is not None and p < 1:
            snap.append_scaled_texture(self.frozen, Gsk.ScalingFilter.LINEAR, rect)
        else:
            self.snapshot_child(self.child, snap)
        snap.restore()
        snap.pop()


class LaunchItem(Gtk.Button):
    """An app or folder in the grid."""

    def __init__(self, pad, item, size: int):
        super().__init__(css_classes=["lp-item"], focus_on_click=False, can_focus=False,
                         hexpand=True, vexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.pad, self.item, self.size = pad, item, size
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        over = Gtk.Overlay()
        if M.is_folder(item):
            self.name = item["folder"]
            over.set_child(self._folder_icon(item, size))
        else:
            info = pad.installed.get(item)
            self.name = info.get_display_name() if info else item
            img = Gtk.Image(pixel_size=size, css_classes=["lp-icon"])
            icons.set_image(img, icons.app_icon(info) if info
                            else Gio.ThemedIcon.new("application-x-executable"))
            over.set_child(img)
            if info and _removable(info):
                badge = Gtk.Button(icon_name="window-close-symbolic", css_classes=["lp-badge"],
                                   halign=Gtk.Align.START, valign=Gtk.Align.START, can_focus=False)
                badge.connect("clicked", lambda _b: pad.ask_delete(item))
                badge.set_visible(pad.jiggling)
                self.badge = badge
                over.add_overlay(badge)
        col.append(over)
        self.label = Gtk.Label(label=self.name, css_classes=["lp-label"], ellipsize=Pango.EllipsizeMode.END,
                               max_width_chars=14, width_chars=1, justify=Gtk.Justification.CENTER)
        col.append(self.label)
        self.set_child(col)
        self.connect("clicked", lambda _b: pad.activate_item(self))
        hold = Gtk.GestureLongPress(delay_factor=JIGGLE_HOLD_MS / 500)
        hold.connect("pressed", lambda *_: pad.set_jiggle(True))
        self.add_controller(hold)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda _g, _n, x, y: pad.item_menu(self, x, y))
        self.add_controller(menu)
        pad.attach_drag(self)

    def set_name_text(self, name: str) -> None:
        self.name = name
        self.label.set_label(name)

    def _folder_icon(self, folder, size) -> Gtk.Widget:
        """The same folder icon as the Dock's: exactly an app's frame (Vini),
        its apps in a 3 x 3 grid; Hidden (locked): blank tiles and a lock."""
        from .dock_folder import FolderIcon
        icon = FolderIcon(folder["apps"], size, locked=bool(folder.get("locked")), on_scrim=True,
                          css=("lp-folder",))
        icon.set_halign(Gtk.Align.CENTER)
        return icon


class PageGrid(Gtk.Grid):
    def __init__(self, pad, index: int, cols: int = None, rows: int = None):
        cols, rows = cols or M.COLS, rows or M.ROWS
        super().__init__(row_homogeneous=True, column_homogeneous=True, hexpand=True, vexpand=True)
        self.pad, self.index, self.cols, self.rows = pad, index, cols, rows
        # Fixed 7 x 5 cells even when the page isn't full (placeholders).
        for i in range(cols * rows):
            self.attach(Gtk.Box(), i % cols, i // cols, 1, 1)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", lambda _t, x, y: pad.drag_over(self, x, y))
        target.connect("drop", lambda _t, v, x, y: pad.drag_drop(self, x, y, v))
        target.connect("leave", lambda _t: pad.drag_leave(self))
        self.add_controller(target)

    def do_snapshot(self, snap) -> None:
        ui.transition.snapshot_children(self, snap)      # icons glide when re-ordered

    def cell_at(self, x, y):
        w, h = self.get_width(), self.get_height()
        w, h = max(1, w), max(1, h)                     # (not laid out yet)
        c = min(self.cols - 1, max(0, int(x / (w / self.cols))))
        r = min(self.rows - 1, max(0, int(y / (h / self.rows))))
        cx, cy = (c + 0.5) * w / self.cols, (r + 0.5) * h / self.rows
        # "over the icon" = inner part of the cell (folder creation)
        centre = abs(x - cx) < w / self.cols * 0.22 and abs(y - cy) < h / self.rows * 0.22
        return r * self.cols + c, centre

    def fill(self, widgets) -> None:
        for i in range(self.cols * self.rows):
            old = self.get_child_at(i % self.cols, i // self.cols)
            if old is not None:
                self.remove(old)
            w = widgets[i] if i < len(widgets) else Gtk.Box()
            if w.get_parent() is not None:
                w.unparent()
            self.attach(w, i % self.cols, i // self.cols, 1, 1)


class Launchpad(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=names.APPS, css_classes=["sonata-launchpad"],
                         decorated=False)
        self.installed = installed_apps()
        self._apps_sig = apps.signature()
        self.model = M.Model(config.load("launchpad", {"pages": [], "hidden": []}), {
            k: v.get_display_name() for k, v in self.installed.items()})
        self.save()                # new/removed apps reconciled
        self.jiggling = False
        self.icon_size = 96
        self._sized = False
        self.widgets = {}          # id(item) or app id -> LaunchItem
        self.selected = -1
        self._drag = None
        self._timers = {}
        self._anim = None
        self.folder_view = None
        self.mode = "fullscreen"   # or "menu": the Apps Menu layout (launchpad_window.MenuView)
        self.menu = None

        self.search = Gtk.SearchEntry(placeholder_text="Search", css_classes=["lp-search"],
                                      halign=Gtk.Align.CENTER, margin_top=40)
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("activate", lambda *_: self._activate_selected())
        self.carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_scroll_wheel=True,
                                     spacing=0, reveal_duration=300)
        self.carousel.connect("page-changed", lambda *_: (self.bin.invalidate(), self._select(-1)))
        self.results = PageGrid(self, -1)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.add_named(self.carousel, "pages")
        self.stack.add_named(self.results, "results")
        dots = self.dots = Adw.CarouselIndicatorDots(carousel=self.carousel, css_classes=["lp-dots"],
                                                     margin_bottom=32, margin_top=12)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["lp-col"])
        self.col.append(self.search)
        self.grid_area = Gtk.Box(hexpand=True, vexpand=True)
        self.grid_area.append(self.stack)
        self.col.append(self.grid_area)
        self.col.append(dots)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.col)
        # the "•••" menu, as in the Apps Menu (Vini): the other layout, its settings
        self.more = Gtk.Button(child=Gtk.Label(label="\u2022\u2022\u2022", css_classes=["lp-more-dots"]),
                               css_classes=["lp-more"], can_focus=False, halign=Gtk.Align.END,
                               valign=Gtk.Align.START, margin_top=40, margin_end=48, tooltip_text="Options")
        self.more.connect("clicked", lambda b: __import__("sonata2.shell.launchpad_window",
                                                          fromlist=["options_menu"]).options_menu(self, b))
        self.overlay.add_overlay(self.more)
        self.bin = ZoomBin(self.overlay)
        self.set_child(self.bin)

        click = Gtk.GestureClick()
        click.connect("released", self._background_click)
        self.bin.add_controller(click)
        menu_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)    # on the window: both layouts
        menu_click.connect("pressed", self._background_menu)
        self.add_controller(menu_click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        keys.connect("key-released", self._key_up)
        self.add_controller(keys)

        self.layer = layer.overlay_fullscreen(self, "sonata2-launchpad")
        self._overlay = True
        Gio.AppInfoMonitor.get().connect("changed", lambda *_: self._apps_changed())
        self._cfg_mon = config.watch("launchpad", self._config_changed)
        self._icons_mon = config.watch("icons", lambda: (icons.forget_prefs(), self.widgets.clear(), self.render()))   # App Icons
        # Wayfire raises the layer surface you press on: any press here (swiping
        # pages, holding an icon) would put Launchpad over the Dock -- the Dock
        # is put back on top right away, it always stays above Launchpad
        # (only needed in the rare case Launchpad sits on the overlay layer, see _pick_layer)
        press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_a: self._overlay and GLib.timeout_add(20, lambda: (
            self.get_visible() and self._dock_above(True), False)[1]))
        self.add_controller(press)
        self.render()

    # -- geometry / rendering ----------------------------------------------------
    def do_size_allocate(self, w, h, baseline) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        if getattr(self, "bin", None) and (w, h) != self.bin._size:
            self.bin._size = (w, h)
            self.bin.invalidate()
        # Big Sur proportions: ~12 % side margins; icon ~56 % of a cell.
        side = int(w * 0.12)
        self.grid_area.set_margin_start(side)
        self.grid_area.set_margin_end(side)
        # as many rows as the display has room for (search, page dots and the Dock
        # take their share): 5 on 16:9 screens, 6 on taller ones like 16:10
        from . import dock as D
        dcfg = config.load("dock", D.DEFAULTS)
        room = max(1, h - 160 - (D.reserved(dcfg) if dcfg.get("position", "bottom") == "bottom" else 0))
        width = max(1, w - 2 * side - (D.reserved(dcfg) if dcfg.get("position") in ("left", "right") else 0))
        cols = 7 if width >= 900 else width // 150               # macOS: 7; fewer on narrow screens
        rows = room / max(1.0, width / max(1, cols) * 0.72)     # row pitch ~72 % of a column (macOS)
        if M.set_grid(cols, rows):
            logs.verbose() and print(f"sonata2-launchpad: grid {M.COLS}x{M.ROWS} for {w}x{h} (room {room}, width {width}, "
                  f"dock {D.reserved(dcfg)})", flush=True)                # launchpad.log
            GLib.idle_add(lambda: (self._rows_changed(), False)[1])     # not during allocation
        cell_w, cell_h = width / M.COLS, room / M.ROWS
        size = int(max(48, min(128, cell_w * 0.56, cell_h * 0.62)))
        if abs(size - self.icon_size) >= 4 or not self._sized:
            self._sized = True
            self.icon_size = size
            # (folder icons: dock_folder.FolderIcon, drawn at this size)
            GLib.idle_add(lambda: (self.render(), False)[1])

    def _item_widget(self, item) -> LaunchItem:
        key = id(item) if M.is_folder(item) else item
        w = self.widgets.get(key)
        size = self._tile_size()
        if w is None or w.item is not item or getattr(w, "_size", 0) != size or \
                (M.is_folder(item) and getattr(w, "_apps", None) != tuple(item["apps"])):
            w = LaunchItem(self, item, size)
            w._size = size
            if M.is_folder(item):
                w._apps = tuple(item["apps"])
            self.widgets[key] = w
        elif M.is_folder(item) and w.name != item["folder"]:     # renamed: the kept tile follows
            w.set_name_text(item["folder"])
        # the Apps Menu: every highlight the cell's width, whatever the name (Vini)
        w.set_halign(Gtk.Align.FILL if self.mode == "menu" else Gtk.Align.CENTER)
        return w

    def _rows_changed(self) -> None:
        """The page size changed (another display): pages rebuilt with the new
        grid, filled up in order."""
        self.model.repack()
        while self.carousel.get_n_pages():
            self.carousel.remove(self.carousel.get_nth_page(0))
        self.stack.remove(self.results)
        self.results = PageGrid(self, -1)
        self.stack.add_named(self.results, "results")
        self.render()
        self.save()
        if self.search.get_text():                   # searching: results on the new grid
            self._search_changed()

    def render(self) -> None:
        """Sync carousel pages with the model (widgets are reused); in the
        Apps Menu, its grids."""
        menu = self.menu if getattr(self, "mode", "") == "menu" else None
        if getattr(self, "bin", None):
            self.bin.invalidate()
        pages = self._pages_with_hidden()
        before = ui.transition.glide_record(self.widgets.values(), self)   # icons slide to their new place
        if menu is not None:                       # the Apps Menu: the same pages, stacked (Vini: only the look)
            grids = menu.page_grids(pages)
        else:
            while self.carousel.get_n_pages() < len(pages):
                self.carousel.append(PageGrid(self, self.carousel.get_n_pages()))
            while self.carousel.get_n_pages() > len(pages):
                self.carousel.remove(self.carousel.get_nth_page(self.carousel.get_n_pages() - 1))
            grids = [self.carousel.get_nth_page(i) for i in range(len(pages))]
        for i, page in enumerate(pages):
            grid = grids[i]
            grid.index = i
            widgets = [self._item_widget(it) for it in page]
            for n, w in enumerate(widgets):
                (w.add_css_class if n % 2 else w.remove_css_class)("odd")
            grid.fill(widgets)
        if menu is not None:
            menu.show()
        self._select(self.selected)
        ui.transition.glide_play(before, self)

    def save(self) -> None:
        data = self.model.to_json()
        config.save("launchpad", data)
        from .. import folder_link                   # the Dock's copies of linked folders follow
        try:
            folder_link.to_dock(data)
        except OSError as e:
            print(f"sonata2-launchpad: folders not shared with the Dock: {e}", flush=True)

    # -- Hidden: apps hidden from the grid, in a folder that asks for the password ----------
    HIDDEN = "Hidden"

    def _pages_with_hidden(self):
        """The model's pages plus the Hidden folder after the last item (not
        stored: it holds model.hidden, shown only while there are some)."""
        pages = self.model.pages
        hidden = [a for a in self.model.hidden if a in self.installed]
        if not hidden:
            return pages
        if not hasattr(self, "_hidden_item"):
            self._hidden_item = {"folder": self.HIDDEN, "apps": [], "locked": True}
        self._hidden_item["apps"][:] = hidden
        pages = [list(p) for p in pages]
        if len(pages[-1]) < M.PER_PAGE:
            pages[-1].append(self._hidden_item)
        else:
            pages.append([self._hidden_item])
        return pages

    def hide_app(self, app_id: str) -> None:
        """Into the Hidden folder: out of the grid, search and the Dock."""
        self.model.hide(app_id)
        self.save()
        self.render()
        pins = config.load("dock", {"pinned": None}).get("pinned")
        if pins and app_id in pins:
            self._save_dock_pins([p for p in pins if p != app_id])

    def unhide_app(self, app_id: str) -> None:
        """Back from Hidden to the end of the grid."""
        if app_id in self.model.hidden:
            self.model.hidden.remove(app_id)
            self.model.reconcile()
            self.save()
            self._close_folder()

    def _folder_tile_name(self, folder, name: str) -> None:
        w = self.widgets.get(id(folder))
        if w is not None:
            w.set_name_text(name)

    def _host(self):
        """Where folder panels open, and what dims behind them: the full
        screen's grid, or the Apps Menu's panel."""
        if self.mode == "menu" and self.menu is not None:
            return self.menu.overlay, self.menu.content
        return self.overlay, self.col

    def _tile_size(self) -> int:
        if self.mode == "menu":
            from .launchpad_window import ICON
            return ICON
        return self.icon_size

    def _ask_password(self, folder) -> None:
        """The Hidden folder opens only with the user's password (PAM, like
        the lock screen); it stays open until Launchpad closes."""
        from .. import pam
        self._close_folder()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["lp-panel", "lp-lock-panel"])
        panel.append(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=36, css_classes=["lp-lock"]))
        panel.append(Gtk.Label(label="Enter your password to see hidden apps", css_classes=["lp-lock-text"]))
        entry = Gtk.PasswordEntry(show_peek_icon=True, halign=Gtk.Align.CENTER)
        panel.append(entry)
        hint = Gtk.Label(label="" if pam.available() else "PAM is not available: can't check passwords",
                         css_classes=["lp-lock-hint"])
        panel.append(hint)
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=folder["folder"], css_classes=["lp-panel-title"])
        wrap.append(title)
        wrap.append(panel)
        host, dim = self._host()
        host.add_overlay(wrap)
        self.folder_view = (wrap, None, panel)
        wrap.add_css_class("lp-folder-view")
        dim.add_css_class("dimmed")
        dim.set_can_target(False)                 # a click behind the folder closes it
        entry.grab_focus()

        def done(ok):
            if self.folder_view is None or self.folder_view[0] is not wrap:
                return False
            entry.set_sensitive(True)
            if ok:
                self._unlocked = True
                self._open_folder(folder)
            else:
                hint.set_label("Wrong password")
                entry.set_text("")
                entry.grab_focus()
                panel.remove_css_class("shake")
                GLib.idle_add(lambda: (panel.add_css_class("shake"), False)[1])
            return False

        def check(_e):
            pw = entry.get_text()
            if not pw:
                return
            entry.set_sensitive(False)
            import threading
            user = GLib.get_user_name()
            threading.Thread(target=lambda: GLib.idle_add(done, pam.authenticate(user, pw)), daemon=True).start()
        entry.connect("activate", check)

    def _config_changed(self) -> None:
        """launchpad.json changed elsewhere (Settings: reset, unhide)."""
        data = config.load("launchpad", {"pages": [], "hidden": []})
        if data == self.model.to_json():
            return                              # our own save
        self.model = M.Model(data, self.model.installed)
        self.widgets.clear()
        self.render()

    def _apps_changed(self) -> None:
        self._apps_sig = apps.signature()
        apps.refresh()
        self.installed = installed_apps()
        self.model.installed = {k: v.get_display_name() for k, v in self.installed.items()}
        self.model.reconcile()
        self.save()
        self.render()

    # -- open / close --------------------------------------------------------------
    def toggle(self) -> None:
        shown = self.get_visible() and (self.menu.is_open() if self.mode == "menu" and self.menu
                                        else self.bin.progress > 0.5)
        self.close_launchpad() if shown else self.open_launchpad()

    # -- the two layouts: full screen, Apps Menu (Settings > Apps > Style) ---------------------------
    def _set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        LS = layer.layer_shell()
        if mode == "menu":
            if self.menu is None:
                from .launchpad_window import MenuView
                self.menu = MenuView(self)
            self._close_folder()
            self.menu.take_search()                    # the same field, in the menu's title
            self.set_child(self.menu.root)
            self.add_css_class("lpw-mode")
            if LS and self.layer:
                LS.set_layer(self, LS.Layer.TOP)
                LS.set_exclusive_zone(self, 0)        # inside the menu bar's and the Dock's space
            self._overlay = False
        else:
            self._close_folder()
            if self.menu is not None:
                self.menu.give_search()
            self.set_child(self.bin)
            self.remove_css_class("lpw-mode")
            if LS and self.layer:
                LS.set_exclusive_zone(self, -1)
        self.mode = mode
        self.set_jiggle(False)
        self.render()

    def _open_menu(self) -> None:
        self._set_mode("menu")
        self._check_apps()
        self.menu.open()

    def _dock_above(self, on: bool) -> None:
        """Ask the Dock (its own process) to sit above Launchpad."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        app_id = "io.github.vinioliveiras.sonata2.dock"
        bus.call(app_id, "/" + app_id.replace(".", "/"), "org.freedesktop.Application", "ActivateAction",
                 GLib.Variant("(sava{sv})", ("above", [GLib.Variant("b", on)], {})), None,
                 Gio.DBusCallFlags.NONE, 1000, None, None)

    def _check_apps(self) -> None:
        """Apps installed or removed since the last look (cheap: folder times)."""
        sig = apps.signature()
        if sig != getattr(self, "_apps_sig", None):
            first = getattr(self, "_apps_sig", None) is None
            self._apps_sig = sig
            if not first:
                self._apps_changed()

    def _pick_layer(self) -> None:
        """Launchpad goes on the TOP layer, under the Dock (OVERLAY while
        Launchpad is open): Wayfire raises whatever you click, and a click on
        Launchpad could otherwise cover the Dock (it blinked back). A focused
        full-screen app hides the TOP layer, so then Launchpad uses OVERLAY."""
        LS = layer.layer_shell()
        if not LS or not self.layer:
            return
        full = False
        try:
            from ..wl.wfipc import WayfireIPC
            views = WayfireIPC().call("window-rules/list-views") or []
            full = any(v.get("fullscreen") and v.get("activated") for v in views if isinstance(v, dict))
        except Exception:
            pass
        self._overlay = full
        LS.set_layer(self, LS.Layer.OVERLAY if full else LS.Layer.TOP)

    def open_launchpad(self) -> None:
        from .launchpad_window import style
        if style() == "window":
            self._open_menu()
            return
        self._set_mode("fullscreen")
        self._pick_layer()
        self._check_apps()
        # the Dock moves up to OVERLAY once Launchpad is mapped: the surface
        # that changes layer last is on top, so the Dock stays reachable
        # (drag an app onto it to pin it)
        GLib.timeout_add(80, lambda: (self.get_visible() and self._dock_above(True), False)[1])
        self._clear_dock()
        self.search.set_text("")
        self.set_jiggle(False)
        self._close_folder()
        self.bin.progress = 0.0
        self.present()
        self.search.grab_focus()
        # start once the surface is on screen: mapping a full-screen surface
        # takes a few frames, and an animation started before it would
        # already be half over when it first shows (the "lag")
        def first_frame(_w, _clock):
            self._animate(1.0, OPEN_MS)
            return GLib.SOURCE_REMOVE
        self.bin.add_tick_callback(first_frame)

    def _clear_dock(self) -> None:
        """The Dock stays over Launchpad (macOS): the grid and the page dots
        keep out of its way, at whichever edge it is."""
        from . import dock as D
        cfg = config.load("dock", D.DEFAULTS)
        room = D.reserved(cfg) + 8
        edge = cfg.get("position", "bottom")
        self.col.set_margin_bottom(room if edge == "bottom" else 0)
        self.col.set_margin_start(room if edge == "left" else 0)
        self.col.set_margin_end(room if edge == "right" else 0)

    def close_launchpad(self, then=None) -> None:
        self._unlocked = False                  # Hidden asks for the password again next time
        if self.mode == "menu" and self.menu is not None:
            self._close_folder()
            self.set_jiggle(False)
            self.menu.close(then)
            return
        self._dock_above(False)

        def done():
            self.set_visible(False)
            if then:
                then()
        self._animate(0.0, CLOSE_MS, done)

    def _animate(self, to, ms, done=None) -> None:
        if self._anim:
            self._anim.pause()

        def step(v):
            self.bin.progress = v
            self.bin.queue_draw()
        self.bin.freeze()
        self._anim = Adw.TimedAnimation.new(self.bin, self.bin.progress, to, ms,
                                            Adw.CallbackAnimationTarget.new(step))
        # macOS: a quick start that settles softly, both ways
        self._anim.set_easing(Adw.Easing.EASE_OUT_QUART if to else Adw.Easing.EASE_OUT_CUBIC)

        stats = ui.transition.FrameStats(self.bin, "launchpad " + ("open" if to else "close"))

        def finished(*_a):
            stats.stop()
            self.bin.thaw()
            if done:
                done()
        self._anim.connect("done", finished)
        self._anim.play()

    @staticmethod
    def _on_background(root, x, y) -> bool:
        """Nothing of Launchpad's under (x, y) in root: no icon, search,
        dots, folder panel or button."""
        w = root.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not root:
            if isinstance(w, (LaunchItem, Gtk.SearchEntry, Adw.CarouselIndicatorDots, Gtk.EditableLabel,
                              Gtk.Button)) or w.has_css_class("lp-panel") or w.has_css_class("lp-more"):
                return False
            w = w.get_parent()
        return True

    def _background_menu(self, gesture, _n, x, y) -> None:
        """Right-click on the background (both layouts): New Web App…"""
        root = self.get_child()
        if self.folder_view or self.jiggling or not self._on_background(root, x, y):
            return
        if self.mode == "menu" and not self.menu._inside(self.menu.panel, root, x, y):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        from .. import webapps
        Item = ui.menu.Item
        ui.menu.popup(root, [[Item("New Web App…", lambda: self.close_launchpad(webapps.open_new))]],
                      at=(x, y))

    def _background_click(self, gesture, _n, x, y) -> None:
        if not self._on_background(self.bin, x, y):
            return
        if self.folder_view:
            self._close_folder()
        elif self.jiggling:
            self.set_jiggle(False)
        else:
            self.close_launchpad()

    # -- launching / folders ---------------------------------------------------------
    def activate_item(self, widget: LaunchItem) -> None:
        if self.jiggling and not M.is_folder(widget.item):
            return
        if M.is_folder(widget.item):
            if widget.item.get("locked") and not getattr(self, "_unlocked", False):
                self._ask_password(widget.item)
            else:
                self._open_folder(widget.item)
            return
        info = self.installed.get(widget.item)
        if info:
            ctx = self.get_display().get_app_launch_context()
            self.close_launchpad(lambda: info.launch([], ctx))

    def _open_folder(self, folder) -> None:
        self._close_folder()
        self.bin.invalidate()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["lp-panel"])
        title = Gtk.EditableLabel(text=folder["folder"], css_classes=["lp-panel-title"], halign=Gtk.Align.CENTER,
                                  editable=not folder.get("locked"))

        def renamed(*_):
            name = title.get_text().strip()
            if not name:                                 # empty: the old name back
                title.set_text(folder["folder"])
                self._folder_tile_name(folder, folder["folder"])
            elif name != folder["folder"]:
                folder["folder"] = name
                self.save()
                self.render()
        title.connect("notify::editing", lambda *_: None if title.get_editing() else renamed())
        # live (Vini): the folder's name in the grid follows each key typed
        title.connect("changed", lambda *_: title.get_editing() and
                      self._folder_tile_name(folder, title.get_text().strip() or folder["folder"]))
        n = len(folder["apps"])
        cols = min(M.COLS, max(3, n))
        rows = min(3, (n + cols - 1) // cols)
        grid = PageGrid(self, -2, cols, rows)
        size = self._tile_size()
        grid.set_size_request(cols * int(size * 1.7), rows * int(size * 1.7))
        grid.folder = folder
        grid.fill([LaunchItem(self, a, size) for a in folder["apps"][:cols * rows]])
        panel.append(grid)
        # Big Sur: the folder name sits above the panel, editable in place.
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        wrap.append(title)
        wrap.append(panel)
        host, dim = self._host()
        host.add_overlay(wrap)
        self.folder_view = (wrap, folder, panel)
        wrap.add_css_class("lp-folder-view")
        dim.add_css_class("dimmed")
        dim.set_can_target(False)                 # a click behind the folder closes it (Vini)

    def _close_folder(self) -> None:
        """Zooms/fades out (the grid comes back at once for drags and clicks)."""
        if self.folder_view:
            wrap = self.folder_view[0]
            self.folder_view = None
            host, dim = self._host()
            dim.remove_css_class("dimmed")
            dim.set_can_target(True)
            wrap.set_can_target(False)
            wrap.add_css_class("closing")
            GLib.timeout_add(ui.tokens.ms(160) + 20, lambda: (
                isinstance(wrap.get_parent(), Gtk.Overlay) and wrap.get_parent().remove_overlay(wrap), False)[1])
            self.render()

    # -- search / selection ----------------------------------------------------------
    def _search_changed(self) -> None:
        if getattr(self, "bin", None):
            self.bin.invalidate()
        q = self.search.get_text()
        self.dots.set_opacity(0 if q else 1)
        menu = self.menu if self.mode == "menu" else None
        if not q:
            self.stack.set_visible_child_name("pages")
            if menu is not None:
                menu.show()
            self._select(-1)
            return
        from .spotlight import _keywords
        meta = {k: (v.get_display_name(), " ".join(filter(None, [
            apps._entry_field(v, "get_generic_name", "GenericName"), _keywords(v), v.get_executable()])))
            for k, v in self.installed.items() if k in set(self.model.all_apps())}
        found = M.search(meta, q)
        results = menu.results if menu is not None else self.results
        results.fill([LaunchItem(self, a, self._tile_size()) for a in found] or
                     [Gtk.Label(label="No Results", css_classes=["lp-empty"])])
        results.found = found
        self.stack.set_visible_child_name("results")
        if menu is not None:
            menu.show()
        self._select(0 if found else -1)

    def _visible_items(self) -> list:
        if self.mode == "menu" and self.menu is not None:
            return self.menu.visible_items()
        grid = self.results if self.stack.get_visible_child_name() == "results" else \
            self.carousel.get_nth_page(int(round(self.carousel.get_position())))
        out = []
        for i in range(grid.cols * grid.rows):
            w = grid.get_child_at(i % grid.cols, i // grid.cols)
            if isinstance(w, LaunchItem):
                out.append(w)
        return out

    def _select(self, index: int) -> None:
        if getattr(self, "bin", None):
            self.bin.invalidate()
        for w in self.widgets.values():
            w.remove_css_class("selected")
        items = self._visible_items() if self.carousel.get_n_pages() else []
        for w in items:
            w.remove_css_class("selected")
        self.selected = index if 0 <= index < len(items) else -1
        if self.selected >= 0:
            items[self.selected].add_css_class("selected")

    def _activate_selected(self) -> None:
        items = self._visible_items()
        if 0 <= self.selected < len(items):
            self.activate_item(items[self.selected])

    def _key(self, _c, keyval, _code, state) -> bool:
        K = Gdk
        if self.folder_view and self.folder_view[1] is None and keyval != K.KEY_Escape:
            return False                        # typing the Hidden folder's password
        editing = self._editing_title()
        if editing is not None:                 # renaming a folder: the keys move in its text (Vini)
            if keyval == K.KEY_Escape:
                editing.stop_editing(False)     # the old name back; the folder stays open
                return True
            return False
        if keyval == K.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            elif self.folder_view:
                self._close_folder()
            elif self.jiggling:
                self.set_jiggle(False)
            elif self.mode == "menu" and self.menu is not None and self.menu.tab is not None:
                self.menu.set_tab(None)                # the Apps Menu: its tab first
            else:
                self.close_launchpad()
            return True
        if keyval in (K.KEY_Alt_L, K.KEY_Alt_R) and not self.jiggling:
            self.set_jiggle(True, sticky=False)     # while Alt is held (macOS: Option)
            return False
        cols = M.COLS
        moves = {K.KEY_Left: -1, K.KEY_Right: 1, K.KEY_Up: -cols, K.KEY_Down: cols}
        if keyval in moves:
            items = self._visible_items()
            if not items:
                return True
            i = self.selected if self.selected >= 0 else -moves[keyval] if moves[keyval] > 0 else len(items)
            j = i + moves[keyval]
            pages = self.carousel.get_n_pages()
            page = int(round(self.carousel.get_position()))
            if self.mode != "menu" and self.stack.get_visible_child_name() == "pages" and \
                    (j < 0 or j >= len(items)) and \
                    keyval in (K.KEY_Left, K.KEY_Right):
                np = page + (1 if j >= len(items) else -1)
                if 0 <= np < pages:
                    self.carousel.scroll_to(self.carousel.get_nth_page(np), True)
                    GLib.timeout_add(320, lambda: (self._select(0 if j >= 0 else len(self._visible_items()) - 1),
                                                   False)[1])
                return True
            self._select(max(0, min(len(items) - 1, j)))
            return True
        if keyval in (K.KEY_Page_Down, K.KEY_Page_Up) and self.mode != "menu":
            page = int(round(self.carousel.get_position())) + (1 if keyval == K.KEY_Page_Down else -1)
            if 0 <= page < self.carousel.get_n_pages():
                self.carousel.scroll_to(self.carousel.get_nth_page(page), True)
            return True
        return False

    def _editing_title(self):
        """The folder's name being edited (an EditableLabel), or None."""
        w = self.get_root().get_focus() if self.get_root() else None
        while w is not None:
            if isinstance(w, Gtk.EditableLabel):
                return w if w.get_editing() else None
            w = w.get_parent()
        return None

    def _key_up(self, _c, keyval, _code, _state) -> None:
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R) and self.jiggling and not self._jiggle_sticky:
            self.set_jiggle(False)

    # -- jiggle / delete -------------------------------------------------------------
    _jiggle_sticky = False

    def set_jiggle(self, on: bool, sticky: bool = True) -> None:
        if on != getattr(self, "jiggling", False) and getattr(self, "bin", None):
            self.bin.invalidate()
        self.jiggling = on
        self._jiggle_sticky = on and sticky
        (self.bin.add_css_class if on else self.bin.remove_css_class)("jiggle")
        tiles = list(self.widgets.values())
        if self.menu is not None:
            (self.menu.root.add_css_class if on else self.menu.root.remove_css_class)("jiggle")
        for w in tiles:
            if hasattr(w, "badge"):
                w.badge.set_visible(on)

    def item_menu(self, widget: LaunchItem, x, y) -> None:
        """Right-click menu (Sonata addition; Launchpad has none on macOS)."""
        Item = ui.menu.Item
        item = widget.item
        in_hidden = bool(self.folder_view and self.folder_view[1] and self.folder_view[1].get("locked"))
        if M.is_folder(item):
            sections = [[Item("Open", lambda: self.activate_item(widget))]]
        elif in_hidden:
            sections = [[Item("Open", lambda: self.activate_item(widget))],
                        [Item(f"Show in {names.APPS}", lambda: self.unhide_app(item))]]
        else:
            info = self.installed.get(item)
            sections = [[Item("Open", lambda: self.activate_item(widget))]]
            dock = config.load("dock", {"pinned": None})
            pins = dock.get("pinned")
            from .dock import PERMANENT
            if pins is not None and item not in PERMANENT:       # Files / Launchpad always stay
                kept = item in pins

                def toggle_dock(on=not kept):
                    cfg = config.load("dock", {"pinned": None})
                    if cfg.get("pinned") is None:
                        return
                    if on and item not in cfg["pinned"]:
                        cfg["pinned"].append(item)
                    elif not on and item in cfg["pinned"]:
                        cfg["pinned"].remove(item)
                    self._save_dock_pins(cfg["pinned"])
                sections.append([Item("Remove from Dock" if kept else "Keep in Dock", toggle_dock)])
            from .. import gpu
            g = gpu.menu_item(info, Item) if info else None
            if g:
                sections.append([g])
            if info and info.get_filename():
                from .dock_menu import show_in_files
                sections.append([Item("Show in Files", lambda: self.close_launchpad(
                    lambda: show_in_files(info.get_filename())))])
            hide = [Item("Hide", lambda: self.hide_app(item))]      # into the Hidden folder
            if info and not (info.get_id() or "").startswith(PROTECTED):
                hide.append(Item("Move to Trash", lambda: self.ask_delete(item)))
            from .. import webapps
            if webapps.is_webapp(item):
                sections.append([Item("Change Icon…", lambda: self.close_launchpad(
                    lambda: __import__("sonata2.shell.topbar", fromlist=["open_settings"]).open_settings(
                        "appicons/" + item)))])
                hide.append(Item("Delete Web App…", lambda: self.ask_delete_webapp(item)))
            sections.append(hide)
        ui.menu.popup(widget, sections, at=(x, y))

    def ask_delete_webapp(self, item) -> None:
        """Asked like "Move to Trash" (ask_delete): Launchpad closes and the
        question is a glass alert of its own (Vini: it came inside Launchpad,
        without the glass)."""
        from .. import webapps
        wid = webapps.id_of(item)
        entry = webapps.get(wid) or {}

        def answer(rid):
            if rid == "delete":
                webapps.remove(wid)           # its entry goes: Launchpad drops it (AppInfoMonitor)
        self.close_launchpad(lambda: ui.dialog.alert(
            f"Delete “{entry.get('name', 'this web app')}”?",
            "Its login and everything it saved on this computer are deleted too.",
            [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer))

    def _save_dock_pins(self, pins) -> None:
        """Only the "pinned" key of dock.json (the Dock reloads it live)."""
        import json
        path = os.path.join(config.CONFIG_DIR, "dock.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data["pinned"] = pins
        config.save("dock", data)

    def ask_delete(self, app_id: str) -> None:
        """Uninstall (asked first): the package, the Flatpak or the shortcut
        (backend/uninstall.py). Launchpad closes so the question shows."""
        info = self.installed.get(app_id)
        if not info or (info.get_id() or "").startswith(PROTECTED):   # Sonata's own apps stay
            return
        from .uninstall_ui import ask
        self.close_launchpad(lambda: ask(info))

    # -- drag and drop ---------------------------------------------------------------
    def _follow_drags(self) -> None:
        """The hanging drag icon follows the pointer over the whole Launchpad;
        held near a side of the screen, the pages turn (macOS)."""
        if not getattr(self, "_drag_follow", None):
            self._drag_follow = ui.drag.follow(self, lambda: (self._drag or {}).get("icon"))
            edge = Gtk.DropControllerMotion()
            edge.connect("motion", lambda _c, x, _y: (setattr(self, "_edge_x", x), self._edge_flip(x)))
            edge.connect("leave", lambda _c: (setattr(self, "_edge_x", None), self._cancel("flip")))
            self.add_controller(edge)

    def _edge_flip(self, x) -> None:
        if not self._drag or self.mode == "menu":      # (the Apps Menu has no pages)
            return
        w = max(1, self.get_width())
        side = self.grid_area.get_margin_start() or int(w * 0.12)
        if x < side or x > w - side:             # beside the grid: the next / previous page
            step = -1 if x < side else 1
            page = int(round(self.carousel.get_position()))
            self._timer("flip", FLIP_HOLD_MS, lambda: (self._flip(page + step), self._cancel("flip"),
                                                       self._edge_again(x))[0])
        else:
            self._cancel("flip")

    def _edge_again(self, _x) -> None:
        """Still held there after the page turned: keep turning."""
        GLib.timeout_add(450, lambda: (self._drag and getattr(self, "_edge_x", None) is not None
                                       and self._edge_flip(self._edge_x), False)[1])

    def attach_drag(self, widget: LaunchItem) -> None:
        self._follow_drags()
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)

        def prepare(_s, _x, _y):
            if M.is_folder(widget.item) and widget.item.get("locked"):
                return None                     # Hidden itself stays where it is (its apps can leave: Vini)
            providers = [Gdk.ContentProvider.new_for_value("sonata2-launchpad-item")]
            if M.is_folder(widget.item):            # the Dock keeps a copy of the folder
                if not widget.item.get("link"):     # the Dock's copy stays the same folder (folder_link)
                    from ..folder_link import new_link
                    widget.item["link"] = new_link()
                    self.save()
                uri = M.encode_folder(widget.item["folder"], widget.item["apps"], widget.item["link"])
                providers.append(Gdk.ContentProvider.new_for_bytes(
                    "text/uri-list", GLib.Bytes.new((uri + "\r\n").encode())))
            info = None if M.is_folder(widget.item) else self.installed.get(widget.item)
            if info and info.get_filename():        # lets the Dock pin it
                # plain text/uri-list: a GdkFileList value would also offer the portal's
                # file-transfer format, which the Dock picks first and can't convert
                uri = Gio.File.new_for_path(info.get_filename()).get_uri()
                providers.append(Gdk.ContentProvider.new_for_bytes(
                    "text/uri-list", GLib.Bytes.new((uri + "\r\n").encode())))
            return Gdk.ContentProvider.new_union(providers)

        def begin(s, drag):
            if self._overlay:
                self._dock_above(True)          # pressing raised Launchpad over the Dock: bring it back
            folder = self.folder_view[1] if self.folder_view and widget.item in self.folder_view[1]["apps"] \
                and not M.is_folder(widget.item) else None
            self._drag = {"item": widget.item, "widget": widget, "folder": folder, "target": None}
            self._drag["icon"] = ui.drag.hang(
                drag, Gtk.WidgetPaintable.new(widget.get_first_child().get_first_child()), widget.size)
            widget.add_css_class("dragging")

        def end(*_):
            widget.remove_css_class("dragging")
            self._clear_target()
            self._drag = None
            self.save()
            self.render()
        src.connect("prepare", prepare)
        src.connect("drag-begin", begin)
        src.connect("drag-end", end)
        widget.add_controller(src)

    def _timer(self, name, ms, fn) -> None:
        if name in self._timers:
            return
        def fire():
            self._timers.pop(name, None)
            fn()
            return False
        self._timers[name] = GLib.timeout_add(ms, fire)

    def _cancel(self, name) -> None:
        src = self._timers.pop(name, None)
        if src:
            GLib.source_remove(src)

    def _clear_target(self) -> None:
        self._cancel("folder")
        if self._drag and self._drag.get("target"):
            self._drag["target"].remove_css_class("folder-target")
            self._drag["target"] = None

    def leave_folder(self, d) -> bool:
        """The dragged app came out of its open folder: Hidden shows it
        again (Vini: dragging out of Hidden did nothing), a folder lets it
        go to the end. True when it was out of Hidden."""
        folder = d.get("folder")
        if folder is None or not folder.get("locked"):
            return False
        d["folder"] = None
        self._close_folder()
        if d["item"] in self.model.hidden:
            self.model.hidden.remove(d["item"])
            self.model.reconcile()               # back at the end of the grid
            self.save()
            self.render()
        return True

    def drag_over(self, grid: PageGrid, x, y):
        d = self._drag
        if not d:
            return Gdk.DragAction.MOVE      # an app dragged from the Dock: dropping here unpins it
        if grid.index < 0:
            return Gdk.DragAction.MOVE
        if self.leave_folder(d):
            return Gdk.DragAction.MOVE
        if d["folder"] is not None and self.folder_view:     # dragged out of the open folder
            self._close_folder()
        index, centre = grid.cell_at(x, y)
        shown = self._pages_with_hidden()         # the Hidden folder too: dropping on it hides
        page = shown[grid.index] if grid.index < len(shown) else []
        target = page[index] if index < len(page) else None
        dragged_is_app = not M.is_folder(d["item"])
        if centre and target is not None and target is not d["item"] and dragged_is_app:
            self._cancel("reorder")
            d["pending"] = None
            tw = self._item_widget(target)
            if d["target"] is not tw:
                self._clear_target()
                d["target"] = tw
                self._timer("folder", FOLDER_HOLD_MS, lambda: tw.add_css_class("folder-target"))
            return Gdk.DragAction.MOVE
        self._clear_target()
        # reorder live, after a short pause over the same slot
        if d["folder"] is not None:
            self.model.take_out_of_folder(d["folder"], d["item"], grid.index, index)
            d["folder"] = None
            self.render()
            return Gdk.DragAction.MOVE
        want = (grid.index, index)
        if d.get("pending") != want:
            d["pending"] = want
            self._cancel("reorder")
            self._timer("reorder", REORDER_HOLD_MS, lambda: self._reorder_to(*want))
        return Gdk.DragAction.MOVE

    def _reorder_to(self, page_i: int, index: int) -> None:
        d = self._drag
        if not d or d.get("pending") != (page_i, index):
            return
        d["pending"] = None
        page = self.model.pages[page_i] if page_i < len(self.model.pages) else []
        loc = self._top_location(d["item"])
        if loc != (page_i, min(index, len(page) - (1 if loc and loc[0] == page_i else 0))):
            self.model.move(d["item"], page_i, index)
            self.render()

    def _top_location(self, item):
        for p, page in enumerate(self.model.pages):
            for i, it in enumerate(page):
                if it is item or (not M.is_folder(item) and it == item):
                    return p, i
        return None

    def _flip(self, page: int) -> None:
        if 0 <= page < self.carousel.get_n_pages():
            self.carousel.scroll_to(self.carousel.get_nth_page(page), True)
        elif page == self.carousel.get_n_pages() and self._drag:
            self.model.move(self._drag["item"], page, 0)     # new last page
            self.render()
            self.carousel.scroll_to(self.carousel.get_nth_page(page), True)

    def drag_leave(self, _grid) -> None:
        pass                                    # (turning pages: _edge_flip, over the whole window)

    def drag_drop(self, grid, x, y, value=None) -> bool:
        d = self._drag
        if not d:
            # an app dragged out of the Dock (its desktop id): accepted -- the Dock
            # sees a finished move and takes the app out (macOS)
            folder = M.decode_folder(value)
            if folder is not None:                  # a Dock folder: it moves here, where dropped
                return self.drop_dock_folder(grid, x, y, folder)
            return isinstance(value, str) and value in self.installed

        self._drop_on_target()
        self.save()
        self.render()
        return True

    def _drop_on_target(self) -> None:
        """Dropped on an app (a new folder) or a folder (into it; Hidden: hidden)
        held long enough to light up."""
        d = self._drag
        t = d.get("target") if d else None
        if t is not None and t.has_css_class("folder-target"):
            target = t.item
            if M.is_folder(target) and target.get("locked"):
                if not M.is_folder(d["item"]):
                    self.hide_app(d["item"])    # dropped on Hidden: hidden (and off the Dock)
            elif M.is_folder(target):
                self.model.make_folder(target, d["item"], target["folder"])
            else:
                def cats(k):
                    return apps._entry_field(self.installed[k], "get_categories", "Categories").split(";") \
                        if k in self.installed else []
                cats_a, cats_b = cats(target), cats(d["item"])
                self.model.make_folder(target, d["item"], M.folder_name(cats_a, cats_b))

    def drop_dock_folder(self, grid, x, y, folder) -> bool:
        """A folder dragged out of the Dock lands at that cell (its apps
        come out of where they were); the Dock then lets it go."""
        if grid.index < 0:
            return False
        index, _centre = grid.cell_at(x, y)
        if self.model.add_folder(folder["folder"], folder["apps"], grid.index, index,
                                 link=folder.get("link", "")) is None:
            return False
        self.save()
        self.render()
        return True


def launchpad_desktop_file(command: str) -> str:
    """sonata2-launchpad.desktop (hidden from app lists) so the Dock can
    show Launchpad as a normal app tile."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-launchpad.desktop",
                              f"[Desktop Entry]\nType=Application\nName={names.APPS}\nComment=Find and open your apps\n"
                              "Icon=sonata-launchpad\nNoDisplay=true\nCategories=System;\n"
                              f"Exec={command} launchpad\n")
