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
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from .. import apps, config, icons, ui  # noqa: E402
from .. import launchpad_model as M  # noqa: E402
from . import layer  # noqa: E402

OPEN_MS, CLOSE_MS = 280, 220
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
.lp-badge { min-width: 20px; min-height: 20px; padding: 0; border-radius: 99px; border: none;
  background: rgba(60, 60, 64, 0.92); color: white; box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  -gtk-icon-size: 10px; }
@keyframes lp-jiggle { 0%% { transform: rotate(-1.6deg); } 50%% { transform: rotate(1.6deg); }
                       100%% { transform: rotate(-1.6deg); } }
.jiggle .lp-item .lp-icon, .jiggle .lp-item .lp-folder { animation: lp-jiggle 260ms ease-in-out infinite; }
.jiggle .lp-item.odd .lp-icon, .jiggle .lp-item.odd .lp-folder { animation-delay: -130ms; }
.lp-empty { color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: %(text_title)s; }
""", key="launchpad", lp_label="12px")


def installed_apps() -> dict:
    """desktop id -> Gio.DesktopAppInfo of the apps a launcher should list."""
    out = {}
    for did, info in apps.scan().items():       # the folders themselves: new apps show up at once
        if info.should_show():
            out[did[:-8]] = info
    return out


PROTECTED = apps.PROTECTED


def _removable(info) -> bool:
    if (info.get_id() or "").startswith(PROTECTED):
        return False
    path = info.get_filename() or ""
    return os.path.dirname(os.path.realpath(path)) == os.path.realpath(USER_APPS)


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
        ui.on_change(self.queue_draw)

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
        self.snapshot_child(self.child, snap)
        snap.restore()
        snap.pop()


class LaunchItem(Gtk.Button):
    """An app or folder in the grid."""

    def __init__(self, pad, item, size: int):
        super().__init__(css_classes=["lp-item"], focus_on_click=False, can_focus=False,
                         hexpand=True, vexpand=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.pad, self.item = pad, item
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
        col.append(Gtk.Label(label=self.name, css_classes=["lp-label"], ellipsize=Pango.EllipsizeMode.END,
                             max_width_chars=14, width_chars=1, justify=Gtk.Justification.CENTER))
        self.set_child(col)
        self.connect("clicked", lambda _b: pad.activate_item(self))
        hold = Gtk.GestureLongPress(delay_factor=JIGGLE_HOLD_MS / 500)
        hold.connect("pressed", lambda *_: pad.set_jiggle(True))
        self.add_controller(hold)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda _g, _n, x, y: pad.item_menu(self, x, y))
        self.add_controller(menu)
        pad.attach_drag(self)

    def _folder_icon(self, folder, size) -> Gtk.Widget:
        box = Gtk.Grid(css_classes=["lp-folder"], row_homogeneous=True, column_homogeneous=True,
                       width_request=size, height_request=size, halign=Gtk.Align.CENTER)
        mini = max(10, int(size * 0.8 / 3) - 4)
        for i, app_id in enumerate(folder["apps"][:9]):
            info = self.pad.installed.get(app_id)
            img = Gtk.Image(pixel_size=mini)
            if info:
                icons.set_image(img, icons.app_icon(info))
            box.attach(img, i % 3, i // 3, 1, 1)
        return box


class PageGrid(Gtk.Grid):
    def __init__(self, pad, index: int, cols: int = M.COLS, rows: int = M.ROWS):
        super().__init__(row_homogeneous=True, column_homogeneous=True, hexpand=True, vexpand=True)
        self.pad, self.index, self.cols, self.rows = pad, index, cols, rows
        # Fixed 7 x 5 cells even when the page isn't full (placeholders).
        for i in range(cols * rows):
            self.attach(Gtk.Box(), i % cols, i // cols, 1, 1)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", lambda _t, x, y: pad.drag_over(self, x, y))
        target.connect("drop", lambda _t, _v, x, y: pad.drag_drop(self, x, y))
        target.connect("leave", lambda _t: pad.drag_leave(self))
        self.add_controller(target)

    def do_snapshot(self, snap) -> None:
        ui.transition.snapshot_children(self, snap)      # icons glide when re-ordered

    def cell_at(self, x, y):
        w, h = self.get_width(), self.get_height()
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
        super().__init__(application=app, title="Launchpad", css_classes=["sonata-launchpad"],
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

        self.search = Gtk.SearchEntry(placeholder_text="Search", css_classes=["lp-search"],
                                      halign=Gtk.Align.CENTER, margin_top=40)
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("activate", lambda *_: self._activate_selected())
        self.carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_scroll_wheel=True,
                                     spacing=0, reveal_duration=300)
        self.carousel.connect("page-changed", lambda *_: self._select(-1))
        self.results = PageGrid(self, -1)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.add_named(self.carousel, "pages")
        self.stack.add_named(self.results, "results")
        dots = self.dots = Adw.CarouselIndicatorDots(carousel=self.carousel, css_classes=["lp-dots"],
                                                     margin_bottom=32, margin_top=12)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.col.append(self.search)
        self.grid_area = Gtk.Box(hexpand=True, vexpand=True)
        self.grid_area.append(self.stack)
        self.col.append(self.grid_area)
        self.col.append(dots)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.col)
        self.bin = ZoomBin(self.overlay)
        self.set_child(self.bin)

        click = Gtk.GestureClick()
        click.connect("released", self._background_click)
        self.bin.add_controller(click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        keys.connect("key-released", self._key_up)
        self.add_controller(keys)

        self.layer = layer.overlay_fullscreen(self, "sonata2-launchpad")
        Gio.AppInfoMonitor.get().connect("changed", lambda *_: self._apps_changed())
        self._cfg_mon = config.watch("launchpad", self._config_changed)
        self.render()

    # -- geometry / rendering ----------------------------------------------------
    def do_size_allocate(self, w, h, baseline) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        # Big Sur proportions: ~12 % side margins; icon ~56 % of a cell.
        side = int(w * 0.12)
        self.grid_area.set_margin_start(side)
        self.grid_area.set_margin_end(side)
        cell_w, cell_h = (w - 2 * side) / M.COLS, max(1, h - 160) / M.ROWS
        size = int(max(48, min(128, cell_w * 0.56, cell_h * 0.62)))
        if abs(size - self.icon_size) >= 4 or not self._sized:
            self._sized = True
            self.icon_size = size
            # folder tile: Big Sur rounded square, radius/padding scale with it
            ui.register(""".lp-folder { background: %(tile_on_scrim)s; border-radius: %(r)dpx;
                           padding: %(p)dpx; }""", key="launchpad-size",
                        r=int(size * 0.22), p=int(size * 0.1))
            GLib.idle_add(lambda: (self.render(), False)[1])

    def _item_widget(self, item) -> LaunchItem:
        key = id(item) if M.is_folder(item) else item
        w = self.widgets.get(key)
        if w is None or w.item is not item or getattr(w, "_size", 0) != self.icon_size or \
                (M.is_folder(item) and getattr(w, "_apps", None) != tuple(item["apps"])):
            w = LaunchItem(self, item, self.icon_size)
            w._size = self.icon_size
            if M.is_folder(item):
                w._apps = tuple(item["apps"])
            self.widgets[key] = w
        return w

    def render(self) -> None:
        """Sync carousel pages with the model (widgets are reused)."""
        pages = self.model.pages
        before = ui.transition.glide_record(self.widgets.values(), self)   # icons slide to their new place
        while self.carousel.get_n_pages() < len(pages):
            self.carousel.append(PageGrid(self, self.carousel.get_n_pages()))
        while self.carousel.get_n_pages() > len(pages):
            self.carousel.remove(self.carousel.get_nth_page(self.carousel.get_n_pages() - 1))
        for i, page in enumerate(pages):
            grid = self.carousel.get_nth_page(i)
            grid.index = i
            widgets = [self._item_widget(it) for it in page]
            for n, w in enumerate(widgets):
                (w.add_css_class if n % 2 else w.remove_css_class)("odd")
            grid.fill(widgets)
        self._select(self.selected)
        ui.transition.glide_play(before, self)

    def save(self) -> None:
        config.save("launchpad", self.model.to_json())

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
        self.close_launchpad() if self.get_visible() and self.bin.progress > 0.5 else self.open_launchpad()

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

    def open_launchpad(self) -> None:
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
        self._anim = Adw.TimedAnimation.new(self.bin, self.bin.progress, to, ms,
                                            Adw.CallbackAnimationTarget.new(step))
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC if to else Adw.Easing.EASE_IN_CUBIC)
        if done:
            self._anim.connect("done", lambda *_: done())
        self._anim.play()

    def _background_click(self, gesture, _n, x, y) -> None:
        picked = self.bin.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = picked
        while w is not None and w is not self.bin:
            if isinstance(w, (LaunchItem, Gtk.SearchEntry, Adw.CarouselIndicatorDots, Gtk.EditableLabel)) or \
                    (w.has_css_class("lp-panel")):
                return
            w = w.get_parent()
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
            self._open_folder(widget.item)
            return
        info = self.installed.get(widget.item)
        if info:
            ctx = self.get_display().get_app_launch_context()
            self.close_launchpad(lambda: info.launch([], ctx))

    def _open_folder(self, folder) -> None:
        self._close_folder()
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["lp-panel"])
        title = Gtk.EditableLabel(text=folder["folder"], css_classes=["lp-panel-title"], halign=Gtk.Align.CENTER)

        def renamed(*_):
            name = title.get_text().strip()
            if name and name != folder["folder"]:
                folder["folder"] = name
                self.save()
                self.render()
        title.connect("notify::editing", lambda *_: None if title.get_editing() else renamed())
        n = len(folder["apps"])
        cols = min(M.COLS, max(3, n))
        rows = min(3, (n + cols - 1) // cols)
        grid = PageGrid(self, -2, cols, rows)
        grid.set_size_request(cols * int(self.icon_size * 1.7), rows * int(self.icon_size * 1.7))
        grid.folder = folder
        grid.fill([LaunchItem(self, a, self.icon_size) for a in folder["apps"][:cols * rows]])
        panel.append(grid)
        # Big Sur: the folder name sits above the panel, editable in place.
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        wrap.append(title)
        wrap.append(panel)
        self.overlay.add_overlay(wrap)
        self.folder_view = (wrap, folder, panel)
        self.col.set_opacity(0.35)

    def _close_folder(self) -> None:
        if self.folder_view:
            self.overlay.remove_overlay(self.folder_view[0])
            self.folder_view = None
            self.col.set_opacity(1.0)
            self.render()

    # -- search / selection ----------------------------------------------------------
    def _search_changed(self) -> None:
        q = self.search.get_text()
        self.dots.set_opacity(0 if q else 1)
        if not q:
            self.stack.set_visible_child_name("pages")
            self._select(-1)
            return
        meta = {k: (v.get_display_name(), " ".join(filter(None, [
            v.get_generic_name(), " ".join(v.get_keywords() or []), v.get_executable()])))
            for k, v in self.installed.items() if k in set(self.model.all_apps())}
        found = M.search(meta, q)
        self.results.fill([LaunchItem(self, a, self.icon_size) for a in found] or
                          [Gtk.Label(label="No Results", css_classes=["lp-empty"])])
        self.results.found = found
        self.stack.set_visible_child_name("results")
        self._select(0 if found else -1)

    def _visible_items(self) -> list:
        grid = self.results if self.stack.get_visible_child_name() == "results" else \
            self.carousel.get_nth_page(int(round(self.carousel.get_position())))
        out = []
        for i in range(grid.cols * grid.rows):
            w = grid.get_child_at(i % grid.cols, i // grid.cols)
            if isinstance(w, LaunchItem):
                out.append(w)
        return out

    def _select(self, index: int) -> None:
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
        if keyval == K.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            elif self.folder_view:
                self._close_folder()
            elif self.jiggling:
                self.set_jiggle(False)
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
            if self.stack.get_visible_child_name() == "pages" and (j < 0 or j >= len(items)) and \
                    keyval in (K.KEY_Left, K.KEY_Right):
                np = page + (1 if j >= len(items) else -1)
                if 0 <= np < pages:
                    self.carousel.scroll_to(self.carousel.get_nth_page(np), True)
                    GLib.timeout_add(320, lambda: (self._select(0 if j >= 0 else len(self._visible_items()) - 1),
                                                   False)[1])
                return True
            self._select(max(0, min(len(items) - 1, j)))
            return True
        if keyval in (K.KEY_Page_Down, K.KEY_Page_Up):
            page = int(round(self.carousel.get_position())) + (1 if keyval == K.KEY_Page_Down else -1)
            if 0 <= page < self.carousel.get_n_pages():
                self.carousel.scroll_to(self.carousel.get_nth_page(page), True)
            return True
        return False

    def _key_up(self, _c, keyval, _code, _state) -> None:
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R) and self.jiggling and not self._jiggle_sticky:
            self.set_jiggle(False)

    # -- jiggle / delete -------------------------------------------------------------
    _jiggle_sticky = False

    def set_jiggle(self, on: bool, sticky: bool = True) -> None:
        self.jiggling = on
        self._jiggle_sticky = on and sticky
        (self.bin.add_css_class if on else self.bin.remove_css_class)("jiggle")
        for w in self.widgets.values():
            if hasattr(w, "badge"):
                w.badge.set_visible(on)

    def item_menu(self, widget: LaunchItem, x, y) -> None:
        """Right-click menu (Sonata addition; Launchpad has none on macOS)."""
        Item = ui.menu.Item
        item = widget.item
        if M.is_folder(item):
            sections = [[Item("Open", lambda: self._open_folder(item))]]
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
            if info and info.get_filename():
                from .dock_menu import show_in_files
                sections.append([Item("Show in Files", lambda: self.close_launchpad(
                    lambda: show_in_files(info.get_filename())))])
            if info and _removable(info):
                sections.append([Item("Move to Trash", lambda: self.ask_delete(item))])
        ui.menu.popup(widget, sections, at=(x, y))

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
        info = self.installed.get(app_id)
        if not info or not _removable(info):             # Sonata's own apps stay
            return
        name = info.get_display_name()

        def answer(rid):
            if rid == "delete":
                try:
                    Gio.File.new_for_path(info.get_filename()).trash(None)
                except GLib.Error as e:
                    print(f"sonata2-launchpad: cannot delete {name}: {e.message}")
        ui.dialog.alert(f"Delete “{name}”?", "Its shortcut is moved to the Trash.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    # -- drag and drop ---------------------------------------------------------------
    def _follow_drags(self) -> None:
        """The hanging drag icon follows the pointer over the whole Launchpad."""
        if not getattr(self, "_drag_follow", None):
            self._drag_follow = ui.drag.follow(self, lambda: (self._drag or {}).get("icon"))

    def attach_drag(self, widget: LaunchItem) -> None:
        self._follow_drags()
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)

        def prepare(_s, _x, _y):
            providers = [Gdk.ContentProvider.new_for_value("sonata2-launchpad-item")]
            info = None if M.is_folder(widget.item) else self.installed.get(widget.item)
            if info and info.get_filename():        # lets the Dock pin it
                # plain text/uri-list: a GdkFileList value would also offer the portal's
                # file-transfer format, which the Dock picks first and can't convert
                uri = Gio.File.new_for_path(info.get_filename()).get_uri()
                providers.append(Gdk.ContentProvider.new_for_bytes(
                    "text/uri-list", GLib.Bytes.new((uri + "\r\n").encode())))
            return Gdk.ContentProvider.new_union(providers)

        def begin(s, drag):
            self._dock_above(True)              # pressing raised Launchpad over the Dock: bring it back
            folder = self.folder_view[1] if self.folder_view and widget.item in self.folder_view[1]["apps"] \
                and not M.is_folder(widget.item) else None
            self._drag = {"item": widget.item, "widget": widget, "folder": folder, "target": None}
            self._drag["icon"] = ui.drag.hang(
                drag, Gtk.WidgetPaintable.new(widget.get_first_child().get_first_child()), self.icon_size)
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

    def drag_over(self, grid: PageGrid, x, y):
        d = self._drag
        if not d or grid.index < 0:
            return Gdk.DragAction.MOVE if d else 0
        if d["folder"] is not None and self.folder_view:     # dragged out of the open folder
            self._close_folder()
        index, centre = grid.cell_at(x, y)
        page = self.model.pages[grid.index] if grid.index < len(self.model.pages) else []
        target = page[index] if index < len(page) else None
        # page flip at the sides
        w = grid.get_width()
        if x < w * 0.04 or x > w * 0.96:
            step = -1 if x < w * 0.04 else 1
            self._timer("flip", FLIP_HOLD_MS, lambda: self._flip(grid.index + step))
        else:
            self._cancel("flip")
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
        self._cancel("flip")

    def drag_drop(self, grid, x, y) -> bool:
        d = self._drag
        if not d:
            return False
        t = d.get("target")
        if t is not None and t.has_css_class("folder-target"):
            target = t.item
            if M.is_folder(target):
                self.model.make_folder(target, d["item"], target["folder"])
            else:
                cats_a = (self.installed[target].get_categories() or "").split(";") if target in self.installed else []
                cats_b = (self.installed[d["item"]].get_categories() or "").split(";") \
                    if d["item"] in self.installed else []
                self.model.make_folder(target, d["item"], M.folder_name(cats_a, cats_b))
        self.save()
        self.render()
        return True


def launchpad_desktop_file(command: str) -> str:
    """sonata2-launchpad.desktop (hidden from app lists) so the Dock can
    show Launchpad as a normal app tile."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-launchpad.desktop",
                              "[Desktop Entry]\nType=Application\nName=Launchpad\nComment=Find and open your apps\n"
                              "Icon=sonata-launchpad\nNoDisplay=true\nCategories=System;\n"
                              f"Exec={command} launchpad\n")
