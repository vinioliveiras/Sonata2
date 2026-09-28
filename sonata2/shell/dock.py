"""The Dock (macOS Big Sur): pinned apps, running apps, a divider and the Trash.

Metrics follow Big Sur at the default 48 px icon size: rounded plate floating
a few px above the screen edge, running dot under the icon, name label above
the hovered icon. The plate is translucent "glass": the compositor blurs what
is behind it (Wayfire blur plugin, see config/wayfire.ini); with glass off it
is nearly opaque. The plate is painted by Dock.do_snapshot at a fixed height,
so magnified icons grow above it, like on macOS.

Running apps come from wlr-foreign-toplevel (wl/toplevels.py): a dot under
running apps, unpinned running apps after the pinned ones, click brings the
app's windows to the front (macOS behaviour) or launches it.

Right-click menus: dock_menu.py. File drops: dock_drop.py. Icons are
reordered by dragging; dragging an app out of the Dock removes it (unless
it is running). Dragging the divider up/down resizes the Dock."""
import math
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .. import apps, config, icons  # noqa: E402
from .. import ui  # noqa: E402
from . import dock_drop, dock_menu, layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4, "glass": True,
            "magnification": False, "magnified_size": 80}
MIN_SIZE, MAX_SIZE = 16, 128
LAUNCH_TIMEOUT_MS = 10000   # stop bouncing if no window shows up
BOUNCE_MS = 620             # one bounce
MAG_RADIUS = 3.0            # magnification reaches this many icons away
MAG_IN_MS, MAG_OUT_MS = 120, 250

# Plate padding and running dot, in px. Unlike macOS (where it sits low),
# the dot is centred between the icon's visible artwork and the plate edge.
# Icons have a transparent margin inside their box (Sonata-MacTahoe: 1/12
# of the size), so the gap above the dot is shortened by that margin.
PAD_TOP, PAD_SIDE, TILE_PAD, DOT, DOT_GAP, SHADOW = 5, 4, 2, 4, 4, 12
DIVIDER_W = 11              # 1 px line + 5 px each side (also the drag handle)
ART_INSET = 1 / 12          # measured: 4 px at 48 px


def dot_gaps(icon_size: int):
    """(gap above, gap below) the dot, in px, for visual centring."""
    return max(0, DOT_GAP - round(icon_size * ART_INSET)), DOT_GAP


def dot_row(cfg: dict) -> int:
    top, bottom = dot_gaps(cfg["icon_size"])
    return top + DOT + bottom


def plate_height(cfg: dict) -> int:
    return PAD_TOP + cfg["icon_size"] + dot_row(cfg)


def max_icon(cfg: dict) -> int:
    return max(cfg["icon_size"], cfg["magnified_size"]) if cfg["magnification"] else cfg["icon_size"]


CSS = """
window.sonata-dock, window.sonata-dock > contents { background: none; box-shadow: none; }
.dock-tile, .dock-tile:hover, .dock-tile:active, .dock-tile:focus {
  padding: 0 %(tile_pad)dpx; margin: 0; min-width: 0; min-height: 0;
  border: none; border-radius: 0; background: none; box-shadow: none; outline: none;
}
.dock-icon { transition: filter %(t_press)s ease-out; }
.dock-tile:active .dock-icon, .dock-tile.drop-hover .dock-icon { filter: brightness(0.62); }
.dock-tile.dragging { opacity: 0; }   /* keeps its gap while being dragged */
.dock-dot { min-width: %(dot)dpx; min-height: %(dot)dpx; margin: %(dot_top)dpx 0 %(dot_bottom)dpx 0;
            border-radius: 99px; background-color: %(indicator)s; opacity: 0; }
.dock-tile.running .dock-dot { opacity: 1; }
.dock-divider { padding: 0 5px; margin-bottom: %(sep_bottom)dpx; }
.dock-divider > box { min-width: 1px; background-color: %(separator)s; }

@keyframes dock-bounce {
  0%%   { transform: translateY(0); }
  50%%  { transform: translateY(-18px); }
  100%% { transform: translateY(0); }
}
.dock-tile.launching .dock-icon { animation: dock-bounce %(bounce_ms)dms ease-in-out infinite; }
"""


def _rounded(rect, radius) -> Gsk.RoundedRect:
    # Keep a reference: `Gsk.RoundedRect().init_from_rect(...)` returns a
    # view of a temporary that PyGObject frees at once (garbage bounds).
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect, radius)
    return rr


class DockIcon(Gtk.Widget):
    """An icon drawn at any size from one paintable. Magnification changes
    the size every frame; resampling a cached texture on the GPU is far
    cheaper than re-rendering SVGs at each new size (Gtk.Image would)."""

    def __init__(self, gicon, size: int):
        super().__init__(css_classes=["dock-icon"])
        self._gicon, self._size = gicon, size
        self._paint = {}          # pixel size -> paintable

    def set_gicon(self, gicon) -> None:
        self._gicon = gicon
        self._paint.clear()
        self.queue_draw()

    def set_size(self, size: float) -> None:
        size = int(round(size))
        if size != self._size:
            self._size = size
            self.queue_resize()

    def get_gicon(self):
        return self._gicon

    def do_measure(self, _orientation, _for_size):
        return self._size, self._size, -1, -1

    def _paintable(self, size: int):
        # Exact-size paintable at rest (crisp); while magnified, one big one.
        if size not in self._paint:
            if len(self._paint) > 2:
                self._paint.clear()
            self._paint[size] = icons.paintable(self, self._gicon, size)
        return self._paint[size]

    def do_snapshot(self, snap) -> None:
        dock = self.get_ancestor(Dock)
        base = dock.cfg["icon_size"] if dock else self._size
        size = self._size if self._size == base else max(self._size, max_icon(dock.cfg))
        self._paintable(size).snapshot(snap, self._size, self._size)


class DockTile(Gtk.Button):
    """One Dock icon: icon, running dot, hover label."""

    def __init__(self, name: str, gicon, size: int, on_click, info=None, on_menu=None):
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False,
                         valign=Gtk.Align.END)
        self.info = info
        self.name = name
        self.gicon = gicon
        self._bounce_src = 0
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.icon = DockIcon(gicon, size)
        box.append(self.icon)
        box.append(Gtk.Box(css_classes=["dock-dot"], halign=Gtk.Align.CENTER))
        self.set_child(box)

        self.label = ui.label.HoverLabel(self, name)
        self.connect("clicked", lambda _b: on_click(self))
        if on_menu:
            right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            right.connect("pressed", lambda *_: on_menu(self))
            self.add_controller(right)

    def set_gicon(self, gicon) -> None:
        self.gicon = gicon
        self.icon.set_gicon(gicon)

    def set_running(self, running: bool) -> None:
        (self.add_css_class if running else self.remove_css_class)("running")
        if running and self._bounce_src:
            GLib.source_remove(self._bounce_src)
            self._stop_bounce()

    def bounce(self, ms: int = LAUNCH_TIMEOUT_MS) -> None:
        """Bounce until the app's first window appears (set_running) or `ms`."""
        self.add_css_class("launching")
        if self._bounce_src:
            GLib.source_remove(self._bounce_src)
        self._bounce_src = GLib.timeout_add(ms, self._stop_bounce)

    def _stop_bounce(self) -> bool:
        self.remove_css_class("launching")
        self._bounce_src = 0
        return False


class DockDivider(Gtk.Box):
    """The line between apps and the Trash. Drag it up/down to resize the
    Dock; right-click for the Dock options (macOS)."""

    def __init__(self, dock):
        super().__init__(css_classes=["dock-divider"], valign=Gtk.Align.END)
        self.dock = dock
        self.append(Gtk.Box(hexpand=True))
        self.set_cursor_from_name("ns-resize")
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_: setattr(self, "_start", dock.cfg["icon_size"]))
        drag.connect("drag-update", lambda _g, _x, dy: dock.set_icon_size(self._start - dy, save=False))
        drag.connect("drag-end", lambda *_: config.save("dock", dock.cfg))
        self.add_controller(drag)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda *_: dock_menu.divider_menu(dock, self))
        self.add_controller(right)
        self.update()

    def update(self) -> None:
        # Spans the icon area, not the dot row.
        self.set_size_request(DIVIDER_W, max(8, self.dock.cfg["icon_size"] - 8))


class Dock(Gtk.Box):
    """The plate with all tiles. Hosted by DockWindow or by the preview.
    `manager` is a wl.toplevels.ToplevelManager (None = no window tracking)."""

    def __init__(self, cfg: dict, manager=None):
        super().__init__(css_classes=["dock-plate"], halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.END)
        self.cfg = cfg
        self.manager = manager if manager and manager.available else None
        self.tiles = {}       # desktop id (or bare app_id) -> DockTile, pinned first
        self.windows = {}     # same keys -> [Toplevel]
        self.backdrop = None  # preview only: blurred wallpaper texture under the plate
        self.on_geometry = []  # callbacks when size/magnification changes
        # No CSS padding: the plate is painted over the whole allocation's
        # bottom, so padding would offset it. Side spacers + a minimum height
        # (tiles are bottom-aligned) give the same insets.
        self.append(Gtk.Box(width_request=PAD_SIDE))
        self.sep = DockDivider(self)
        self.append(self.sep)
        self.trash = DockTile("Trash", Gio.ThemedIcon.new("user-trash"), cfg["icon_size"],
                              lambda _t: Gio.AppInfo.launch_default_for_uri("trash:///", None),
                              on_menu=dock_menu.trash_menu)
        self._drag = None     # (key, original index) while an icon is dragged
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drag_motion)
        drop.connect("drop", self._drag_drop)
        drop.connect("enter", self._drag_motion)
        drop.connect("leave", self._drag_leave)
        self.add_controller(drop)
        dock_drop.attach_plate(self)
        dock_drop.attach_trash(self, self.trash)
        self.append(self.trash)
        self.append(Gtk.Box(width_request=PAD_SIDE))
        self.set_size_request(-1, plate_height(cfg))
        for did in cfg["pinned"]:
            info = apps.lookup(did)
            if info:
                self._add_tile(did, info.get_display_name(), info.get_icon(), info)
        self._watch_trash()
        self._sync_src = 0
        self._setup_magnification()
        ui.on_change(self.queue_draw)
        if self.manager:
            self.manager.listeners.append(self._schedule_sync)
            self._schedule_sync()

    # -- plate -----------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        ph = plate_height(self.cfg)
        if w <= 0 or h < ph:      # not allocated yet (a blurred shadow of 0 px crashes GSK)
            Gtk.Box.do_snapshot(self, snap)
            return
        rect = Graphene.Rect().init(0, h - ph, w, ph)
        radius = ui.px("r_plate")
        rr = _rounded(rect, radius)
        dx, dy, blur, col = ui.shadow("shadow_plate")
        snap.append_outset_shadow(rr, col, dx, dy, 0, blur)
        snap.push_rounded_clip(rr)
        if self.backdrop:          # preview: stands in for the compositor's blur
            ok, p = self.compute_point(self.get_root(), Graphene.Point().init(0, 0))
            if ok:
                snap.append_texture(self.backdrop, Graphene.Rect().init(
                    -p.x, -p.y, self.backdrop.get_width(), self.backdrop.get_height()))
        snap.append_color(ui.rgba("glass_tint" if self.cfg["glass"] else "solid_tint"), rect)
        snap.pop()
        snap.append_inset_shadow(rr, ui.rgba("highlight"), 0, 0, 0.5, 0)
        outer = _rounded(Graphene.Rect().init(-0.5, h - ph - 0.5, w + 1, ph + 1), radius + 0.5)
        hair = ui.rgba("hairline")
        snap.append_border(outer, [0.5] * 4, [hair] * 4)
        Gtk.Box.do_snapshot(self, snap)

    def do_size_allocate(self, width, height, baseline) -> None:
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        self.queue_draw()     # the plate is painted from the new height
        for cb in self.on_geometry:
            cb()

    def set_icon_size(self, size: float, save: bool = True) -> None:
        """Resize the Dock (divider drag, settings)."""
        size = int(max(MIN_SIZE, min(MAX_SIZE, round(size))))
        if size == self.cfg["icon_size"]:
            return
        self.cfg["icon_size"] = size
        self.cfg["magnified_size"] = max(self.cfg["magnified_size"], size)
        load_css(self.cfg)                # dot gaps scale with the size
        for tile in self.all_tiles():
            tile.icon.set_size(size)
        self.sep.update()
        self.set_size_request(-1, plate_height(self.cfg))
        self.queue_draw()
        if save:
            config.save("dock", self.cfg)

    def set_magnification(self, on: bool, size: int = None) -> None:
        self.cfg["magnification"] = on
        if size:
            self.cfg["magnified_size"] = int(max(self.cfg["icon_size"], min(MAX_SIZE, size)))
        config.save("dock", self.cfg)
        self._mag_strength = 0.0
        self._apply_magnification()
        for cb in self.on_geometry:
            cb()

    # -- magnification ---------------------------------------------------------
    def _setup_magnification(self) -> None:
        """macOS wave: icons near the pointer grow (cosine falloff over
        MAG_RADIUS icons), neighbours make room; it eases in on enter and out
        on leave. Distances use the unmagnified layout so the wave doesn't
        feed back on itself."""
        self._mag_x = None
        self._mag_strength = 0.0
        self._mag_anim = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, _y: self._mag_enter(x))
        motion.connect("motion", lambda _c, x, _y: self._mag_move(x))
        motion.connect("leave", lambda _c: self._mag_animate(0.0, MAG_OUT_MS))
        self.add_controller(motion)

    def _mag_enter(self, x) -> None:
        self._mag_move(x)
        self._mag_animate(1.0, MAG_IN_MS)

    def _mag_move(self, x) -> None:
        if not self.cfg["magnification"] or self._drag:
            return
        parent = self.get_parent()
        ok, p = self.compute_point(parent, Graphene.Point().init(x, 0)) if parent else (False, None)
        self._mag_x = p.x if ok else None
        self._apply_magnification()

    def _mag_animate(self, to: float, ms: int) -> None:
        if not self.cfg["magnification"]:
            return
        if self._mag_anim:
            self._mag_anim.pause()

        def step(v):
            self._mag_strength = v
            self._apply_magnification()
        self._mag_anim = Adw.TimedAnimation.new(self, self._mag_strength, to, ms,
                                                Adw.CallbackAnimationTarget.new(step))
        self._mag_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._mag_anim.play()

    def all_tiles(self) -> list:
        return self.app_tiles() + [self.trash]

    def _apply_magnification(self) -> None:
        base = self.cfg["icon_size"]
        tiles = self.all_tiles()
        s = self._mag_strength
        if not self.cfg["magnification"] or s <= 0 or self._mag_x is None:
            for t in tiles:
                t.icon.set_size(base)
            return
        extra = max(0, self.cfg["magnified_size"] - base) * s
        parent = self.get_parent()
        cell = base + 2 * TILE_PAD
        base_w = 2 * PAD_SIDE + len(tiles) * cell + DIVIDER_W
        x = (parent.get_width() - base_w) / 2 + PAD_SIDE if parent else PAD_SIDE
        radius = MAG_RADIUS * cell
        for i, t in enumerate(tiles):
            if t is self.trash:
                x += DIVIDER_W
            d = abs(x + cell / 2 - self._mag_x)
            f = math.cos(math.pi / 2 * d / radius) ** 2 if d < radius else 0.0
            t.icon.set_size(base + extra * f)
            x += cell

    # -- tiles -----------------------------------------------------------------
    def _add_tile(self, key, name, gicon, info=None) -> DockTile:
        tile = DockTile(name, gicon, self.cfg["icon_size"], lambda t: self._clicked(key, t), info,
                        on_menu=lambda t: dock_menu.app_menu(self, key, t))
        tile.key = key
        self.tiles[key] = tile
        self.insert_child_after(tile, self.sep.get_prev_sibling())   # before the divider
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(key))
        src.connect("drag-begin", self._drag_begin, tile)
        src.connect("drag-cancel", self._drag_cancel, tile)
        src.connect("drag-end", self._drag_end, tile)
        tile.add_controller(src)
        dock_drop.attach_app(self, tile)
        return tile

    def _remove_tile(self, key) -> None:
        tile = self.tiles.pop(key)
        tile.label.unparent()
        self.remove(tile)

    def app_tiles(self) -> list:
        """App tiles in Dock order (pinned, then unpinned running)."""
        out, w = [], self.get_first_child().get_next_sibling()   # after the side spacer
        while w is not None and w is not self.sep:
            out.append(w)
            w = w.get_next_sibling()
        return out

    def _save_order(self) -> None:
        pinned = set(self.cfg["pinned"])
        self.cfg["pinned"] = [t.key for t in self.app_tiles() if t.key in pinned]
        config.save("dock", self.cfg)

    def _slot_at(self, x: float, exclude=None) -> int:
        """Index among app tiles where something dropped at `x` goes."""
        slot = 0
        for t in self.app_tiles():
            if t is exclude:
                continue
            ok, b = t.compute_bounds(self)
            if ok and b.get_x() + b.get_width() / 2 < x:
                slot += 1
        return slot

    def pin_at(self, key, before=None, x=None) -> None:
        """Pin app `key` (desktop id) before tile `before`, or at plate x."""
        tile = self.tiles.get(key)
        if tile is None:
            info = apps.lookup(key)
            if not info:
                return
            tile = self._add_tile(key, info.get_display_name(), info.get_icon(), info)
            tile.set_running(key in self.windows)
        others = [t for t in self.app_tiles() if t is not tile]
        slot = others.index(before) if before in others else (
            self._slot_at(x, exclude=tile) if x is not None else len(others))
        self.reorder_child_after(tile, others[slot - 1] if slot else self.get_first_child())
        if key not in self.cfg["pinned"]:
            self.cfg["pinned"].append(key)
        self._save_order()

    def set_pinned(self, key, on: bool) -> None:
        """Keep in Dock on/off. Unpinning a running app keeps its icon until it quits."""
        pins = self.cfg["pinned"]
        if on and key not in pins:
            pins.append(key)
            self._save_order()         # takes its current position
        elif not on and key in pins:
            pins.remove(key)
            config.save("dock", self.cfg)
            if key not in self.windows:
                self._remove_tile(key)
            else:                      # macOS: running unpinned apps sit after the pinned ones
                self.reorder_child_after(self.tiles[key], self.sep.get_prev_sibling())

    # -- drag to reorder -------------------------------------------------------
    def _drag_begin(self, src, _drag, tile) -> None:
        self._drag = {"key": tile.key, "index": self.app_tiles().index(tile), "left": False,
                      "dropped": False}
        tile.label.popdown()
        size = self.cfg["icon_size"]
        src.set_icon(icons.paintable(self, tile.gicon, size), size // 2, size // 2)
        tile.add_css_class("dragging")
        self._mag_animate(0.0, MAG_OUT_MS)

    def _drag_motion(self, _target, x, _y):
        if not self._drag:
            return 0
        self._drag["left"] = False
        tiles = self.app_tiles()
        tile = self.tiles[self._drag["key"]]
        others = [t for t in tiles if t is not tile]
        slot = self._slot_at(x, exclude=tile)   # other icons whose centre is left of x
        if tiles.index(tile) != slot:
            self.reorder_child_after(tile, others[slot - 1] if slot else self.get_first_child())
        return Gdk.DragAction.MOVE

    def _drag_leave(self, _target) -> None:
        if self._drag:
            self._drag["left"] = True

    def _drag_drop(self, _target, _value, _x, _y) -> bool:
        if not self._drag:
            return False
        self._drag["dropped"] = True
        key = self._drag["key"]
        if key not in self.cfg["pinned"]:
            self.cfg["pinned"].append(key)     # dragging a running app into place pins it
        self._save_order()
        return True

    def _drag_cancel(self, _src, _drag, reason, tile) -> bool:
        d = self._drag
        if d and reason == Gdk.DragCancelReason.NO_TARGET and d["left"]:
            self.set_pinned(tile.key, False)   # dragged out of the Dock: remove
            return True                         # no snap-back animation
        if d:                                   # Esc / refused: put it back
            tiles = [t for t in self.app_tiles() if t is not tile]
            i = d["index"]
            self.reorder_child_after(tile, tiles[i - 1] if i else self.get_first_child())
        return False

    def _drag_end(self, _src, _drag, _delete, tile) -> None:
        tile.remove_css_class("dragging")
        self._drag = None

    # -- running apps ----------------------------------------------------------
    def _schedule_sync(self) -> None:
        # Coalesce bursts of toplevel events into one update.
        if not self._sync_src:
            self._sync_src = GLib.idle_add(self._sync)

    def _sync(self) -> bool:
        self._sync_src = 0
        groups = {}
        for t in self.manager.toplevels:
            key = apps.match_app_id(t.app_id) or t.app_id or "?"
            groups.setdefault(key, []).append(t)
        self.windows = groups
        pinned = set(self.cfg["pinned"])
        for key in [k for k in self.tiles if k not in pinned and k not in groups]:
            self._remove_tile(key)                 # unpinned app quit
        for key in groups:
            if key not in self.tiles:
                info = apps.lookup(key)
                if info:
                    self._add_tile(key, info.get_display_name(), info.get_icon(), info)
                else:   # no .desktop: generic icon, app_id as name
                    self._add_tile(key, key, Gio.ThemedIcon.new("application-x-executable"))
        for key, tile in self.tiles.items():
            tile.set_running(key in groups)
        GLib.idle_add(self._update_rectangles)
        return False

    def _update_rectangles(self) -> bool:
        """Tell the compositor where each window minimizes to (its Dock icon)."""
        native = self.get_native()
        surface = native.get_surface() if native else None
        if not surface:
            return False
        for key, wins in self.windows.items():
            tile = self.tiles.get(key)
            ok, b = tile.compute_bounds(native) if tile else (False, None)
            if ok:
                for t in wins:
                    self.manager.set_rectangle(t, surface, b.get_x(), b.get_y(),
                                               b.get_width(), b.get_height())
        return False

    def _clicked(self, key, tile: DockTile) -> None:
        wins = self.windows.get(key)
        if wins:
            # macOS: bring all of the app's windows forward; if every window
            # is minimized, restore them. The newest window ends up focused.
            for t in [t for t in wins if not t.minimized] or wins:
                self.manager.activate(t)
        elif tile.info:
            self.launch(tile)

    def launch_feedback(self, tile: DockTile) -> None:
        # With window tracking the bounce stops when the first window maps;
        # without it, bounce twice.
        tile.bounce(LAUNCH_TIMEOUT_MS if self.manager else 2 * BOUNCE_MS)

    def launch(self, tile: DockTile) -> None:
        info = tile.info
        self.launch_feedback(tile)
        ctx = tile.get_display().get_app_launch_context()
        try:
            info.launch([], ctx)
        except GLib.Error as e:
            tile._stop_bounce()
            print(f"sonata2-dock: cannot launch {info.get_id()}: {e.message}")

    # -- Trash -----------------------------------------------------------------
    def _trash_dir(self) -> str:
        data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(data, "Trash", "files")

    def _update_trash(self, *_a) -> None:
        try:
            full = any(os.scandir(self._trash_dir()))
        except OSError:
            full = False
        self.trash.set_gicon(Gio.ThemedIcon.new("user-trash-full" if full else "user-trash"))

    def _watch_trash(self) -> None:
        self._update_trash()
        path = self._trash_dir()
        os.makedirs(path, exist_ok=True)
        self._trash_mon = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._trash_mon.connect("changed", self._update_trash)


def load_css(cfg: dict) -> None:
    ui.setup()
    top, bottom = dot_gaps(cfg["icon_size"])
    ui.register(CSS, key="dock", tile_pad=TILE_PAD,
                sep_bottom=dot_row(cfg), bounce_ms=BOUNCE_MS, dot=DOT, dot_top=top, dot_bottom=bottom)


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    return cfg


class DockWindow(Gtk.ApplicationWindow):
    """Layer surface spanning the bottom edge, tall enough for magnified
    icons; only the Dock itself receives clicks (input region)."""

    def __init__(self, app, cfg: dict, manager=None):
        super().__init__(application=app, title="Dock", css_classes=["sonata-dock"],
                         decorated=False, resizable=False)
        self.cfg = cfg
        self.dock = Dock(cfg, manager)
        self.dock.set_margin_start(SHADOW)
        self.dock.set_margin_end(SHADOW)
        self.dock.set_margin_bottom(cfg["edge_gap"])   # plate floats above the edge
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END)
        self.box.append(self.dock)
        self.set_child(self.box)
        self.layer = layer.anchor_edge(self, "sonata2-dock", "bottom", self._exclusive())
        self.dock.on_geometry.append(self._geometry)
        self._geometry()

    def _exclusive(self) -> int:
        return plate_height(self.cfg) + self.cfg["edge_gap"]

    def _geometry(self) -> None:
        # Fixed surface height = biggest possible Dock; changes only with
        # the Dock size/magnification setting, never while magnifying.
        h = SHADOW + PAD_TOP + max_icon(self.cfg) + dot_row(self.cfg) + self.cfg["edge_gap"]
        if self.box.get_size_request()[1] != h:
            self.box.set_size_request(-1, h)
        if self.layer:
            layer.set_exclusive(self, self._exclusive())
            ok, b = self.dock.compute_bounds(self)
            if ok:
                layer.set_input_region(self, [(b.get_x(), b.get_y(), b.get_width(), b.get_height())])
