"""The Dock (macOS Big Sur): pinned apps, running/recent apps, a divider,
stacks and the Trash -- at the bottom, left or right screen edge.

Metrics follow Big Sur at the default 48 px icon size: rounded plate floating
a few px from the screen edge, running dot between the icon and the edge,
name label beside the hovered icon. The plate is translucent "glass": the
compositor blurs what is behind it (Wayfire blur plugin, config/wayfire.ini);
with glass off it is nearly opaque. It is painted by Dock.do_snapshot at a
fixed thickness against the edge, so magnified icons grow away from it.

Layout along the Dock:
  [pinned apps] | [recent + running unpinned apps] || [stacks] [Trash]
(the single bar only when that middle section is non-empty.)

Running apps come from wlr-foreign-toplevel (wl/toplevels.py). Menus:
dock_menu.py. File drops: dock_drop.py. Stacks: dock_stack.py. Auto-hide
lives in DockWindow."""
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
from . import dock_drop, dock_menu, dock_stack, layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4, "glass": True,
            "magnification": False, "magnified_size": 80, "position": "bottom",
            "autohide": False, "autohide_delay_ms": 300, "show_recents": True,
            "recent": [], "stacks": None}
EDGES = ("left", "bottom", "right")
MIN_SIZE, MAX_SIZE = 16, 128
MAX_RECENTS = 3
LAUNCH_BOUNCES = 3          # macOS bounces a few times, then stops even if no window shows up
NO_BOUNCE = {"sonata2-launchpad"}   # shell toggles open instantly: no launch bounce
BOUNCE_MS = 620             # one bounce
MAG_RADIUS = 3.0            # magnification reaches this many icons away
MAG_IN_MS, MAG_OUT_MS = 120, 250
HIDE_MS = 250               # auto-hide slide
TRIGGER = 2                 # px strip at the screen edge that reveals a hidden Dock

# Plate padding and running dot, in px. Unlike macOS (where it sits low),
# the dot is centred between the icon's visible artwork and the plate edge.
# Icons have a transparent margin inside their box (Sonata-MacTahoe: 1/12
# of the size), so the gap between icon and dot is shortened by that margin.
PAD_TOP, PAD_SIDE, TILE_PAD, DOT, DOT_GAP, SHADOW = 5, 4, 2, 4, 4, 12
DIVIDER_W = 11              # 1 px line + 5 px each side (also the drag handle)
ART_INSET = 1 / 12          # measured: 4 px at 48 px


def dot_gaps(icon_size: int):
    """(gap icon->dot, gap dot->edge), in px, for visual centring."""
    return max(0, DOT_GAP - round(icon_size * ART_INSET)), DOT_GAP


def dot_row(cfg: dict) -> int:
    inner, outer = dot_gaps(cfg["icon_size"])
    return inner + DOT + outer


def plate_height(cfg: dict) -> int:
    """Plate thickness (perpendicular to the edge)."""
    return PAD_TOP + cfg["icon_size"] + dot_row(cfg)


def max_icon(cfg: dict) -> int:
    return max(cfg["icon_size"], cfg["magnified_size"]) if cfg["magnification"] else cfg["icon_size"]


def _rounded(rect, radius) -> Gsk.RoundedRect:
    # Keep a reference: `Gsk.RoundedRect().init_from_rect(...)` returns a
    # view of a temporary that PyGObject frees at once (garbage bounds).
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect, radius)
    return rr


def _rect(x, y, w, h) -> Graphene.Rect:
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


CSS = """
window.sonata-dock, window.sonata-dock > contents { background: none; box-shadow: none; }
/* no GTK drop-target outline (the dashed frame while dragging) */
window.sonata-dock *:drop(active) { box-shadow: none; outline: none; border-color: transparent; }
.dock-tile, .dock-tile:hover, .dock-tile:active, .dock-tile:focus {
  padding: 0 %(tile_pad)dpx; margin: 0; min-width: 0; min-height: 0;
  border: none; border-radius: 0; background: none; box-shadow: none; outline: none;
}
.edge-left .dock-tile, .edge-right .dock-tile { padding: %(tile_pad)dpx 0; }
.dock-icon { transition: filter %(t_press)s ease-out; }
.dock-tile:active .dock-icon, .dock-tile.drop-hover .dock-icon { filter: brightness(0.62); }
.dock-tile.dragging { opacity: 0; }   /* keeps its gap while being dragged */
.dock-dot { min-width: %(dot)dpx; min-height: %(dot)dpx; border-radius: 99px;
            background-color: %(indicator)s; opacity: 0; }
.edge-bottom .dock-dot { margin: %(dot_in)dpx 0 %(dot_out)dpx 0; }
.edge-left .dock-dot { margin: 0 %(dot_in)dpx 0 %(dot_out)dpx; }
.edge-right .dock-dot { margin: 0 %(dot_out)dpx 0 %(dot_in)dpx; }
.dock-tile.running .dock-dot { opacity: 1; }
.dock-divider > box, .dock-recent-sep > box { background-color: %(separator)s; }
.edge-bottom .dock-divider, .edge-bottom .dock-recent-sep { padding: 0 5px; margin-bottom: %(row)dpx; }
.edge-bottom .dock-divider > box, .edge-bottom .dock-recent-sep > box { min-width: 1px; }
.edge-left .dock-divider, .edge-left .dock-recent-sep { padding: 5px 0; margin-left: %(row)dpx; }
.edge-right .dock-divider, .edge-right .dock-recent-sep { padding: 5px 0; margin-right: %(row)dpx; }
.edge-left .dock-divider > box, .edge-right .dock-divider > box,
.edge-left .dock-recent-sep > box, .edge-right .dock-recent-sep > box { min-height: 1px; }

@keyframes dock-bounce-up { 0%% { transform: none; } 50%% { transform: translateY(-18px); } 100%% { transform: none; } }
@keyframes dock-bounce-right { 0%% { transform: none; } 50%% { transform: translateX(18px); } 100%% { transform: none; } }
@keyframes dock-bounce-left { 0%% { transform: none; } 50%% { transform: translateX(-18px); } 100%% { transform: none; } }
.edge-bottom .dock-tile.launching .dock-icon { animation: dock-bounce-up %(bounce_ms)dms ease-in-out infinite; }
.edge-left .dock-tile.launching .dock-icon { animation: dock-bounce-right %(bounce_ms)dms ease-in-out infinite; }
.edge-right .dock-tile.launching .dock-icon { animation: dock-bounce-left %(bounce_ms)dms ease-in-out infinite; }
"""


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
    """One Dock icon: icon, running dot (towards the edge), hover label."""

    def __init__(self, dock, name: str, gicon, on_click, info=None, on_menu=None):
        edge = dock.edge
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False,
                         valign=Gtk.Align.END if edge == "bottom" else Gtk.Align.FILL,
                         halign={"left": Gtk.Align.START, "right": Gtk.Align.END}.get(edge, Gtk.Align.FILL))
        self.info = info
        self.name = name
        self.gicon = gicon
        self.key = None
        self._bounce_src = 0
        self.icon = DockIcon(gicon, dock.cfg["icon_size"])
        dot = Gtk.Box(css_classes=["dock-dot"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if edge == "bottom" else Gtk.Orientation.HORIZONTAL)
        for w in ((dot, self.icon) if edge == "left" else (self.icon, dot)):
            box.append(w)
        self.set_child(box)

        self.label = ui.label.HoverLabel(self, name, position=dock.away)
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

    def bounce(self, ms: int = LAUNCH_BOUNCES * BOUNCE_MS) -> None:
        """Bounce until the app's first window appears (set_running) or `ms`."""
        self.add_css_class("launching")
        if self._bounce_src:
            GLib.source_remove(self._bounce_src)
        self._bounce_src = GLib.timeout_add(ms, self._stop_bounce)

    def _stop_bounce(self) -> bool:
        self.remove_css_class("launching")
        self._bounce_src = 0
        return False


class DockLine(Gtk.Box):
    """A 1 px bar across the Dock (divider or recents separator)."""

    def __init__(self, dock, css: str):
        super().__init__(css_classes=[css],
                         orientation=Gtk.Orientation.HORIZONTAL if dock.vertical else Gtk.Orientation.VERTICAL)
        self.dock = dock
        self.append(Gtk.Box(hexpand=True, vexpand=True))
        if dock.edge == "bottom":
            self.set_valign(Gtk.Align.END)
        else:
            self.set_halign(Gtk.Align.START if dock.edge == "left" else Gtk.Align.END)
        self.update()

    def update(self) -> None:
        span = max(8, self.dock.cfg["icon_size"] - 8)    # the icon area, not the dot row
        if self.dock.vertical:
            self.set_size_request(span, DIVIDER_W)
        else:
            self.set_size_request(DIVIDER_W, span)


class DockDivider(DockLine):
    """The bar before stacks and the Trash. Drag it (towards/away from the
    screen centre) to resize the Dock; right-click for the Dock options."""

    def __init__(self, dock):
        super().__init__(dock, "dock-divider")
        self.set_cursor_from_name("ew-resize" if dock.vertical else "ns-resize")
        drag = Gtk.GestureDrag()
        sign = {"bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}[dock.edge]
        drag.connect("drag-begin", lambda *_: setattr(self, "_start", dock.cfg["icon_size"]))
        drag.connect("drag-update", lambda _g, dx, dy: dock.set_icon_size(
            self._start + sign[0] * dx + sign[1] * dy, save=False))
        drag.connect("drag-end", lambda *_: config.save("dock", dock.cfg))
        self.add_controller(drag)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda *_: dock_menu.divider_menu(dock, self))
        self.add_controller(right)


class Dock(Gtk.Box):
    """The plate with all tiles. Hosted by DockWindow or by the preview.
    `manager` is a wl.toplevels.ToplevelManager (None = no window tracking)."""

    def __init__(self, cfg: dict, manager=None):
        self.edge = cfg["position"] if cfg["position"] in EDGES else "bottom"
        self.vertical = self.edge != "bottom"
        P = Gtk.PositionType
        self.away = {"bottom": P.TOP, "left": P.RIGHT, "right": P.LEFT}[self.edge]  # labels/menus side
        super().__init__(css_classes=["dock-plate", "edge-" + self.edge],
                         orientation=Gtk.Orientation.VERTICAL if self.vertical else Gtk.Orientation.HORIZONTAL,
                         halign={"left": Gtk.Align.START, "right": Gtk.Align.END}.get(self.edge, Gtk.Align.CENTER),
                         valign=Gtk.Align.END if self.edge == "bottom" else Gtk.Align.CENTER)
        self.cfg = cfg
        self.manager = manager if manager and manager.available else None
        self.tiles = {}       # desktop id (or bare app_id) -> DockTile (apps only)
        self.windows = {}     # same keys -> [Toplevel]
        self.backdrop = None  # preview only: blurred wallpaper texture under the plate
        self.on_geometry = []  # callbacks when size/magnification changes
        self.on_rebuild = None  # host callback: position changed -> rebuild the Dock
        self.hide_amount = 0.0  # 0 shown .. 1 slid out (auto-hide), set by the host
        # No CSS padding: the plate is painted over the allocation's edge
        # side, so padding would offset it. Spacers + a minimum thickness
        # (tiles hug the edge) give the same insets.
        self.append(self._spacer())
        self.recent_sep = DockLine(self, "dock-recent-sep")
        self.append(self.recent_sep)
        self.sep = DockDivider(self)
        self.append(self.sep)
        self.stacks = dock_stack.StackRow(self)      # stack tiles live between divider and Trash
        self.trash = DockTile(self, "Trash", Gio.ThemedIcon.new("user-trash"),
                              lambda _t: Gio.AppInfo.launch_default_for_uri("trash:///", None),
                              on_menu=dock_menu.trash_menu)
        self.append(self.trash)
        self.append(self._spacer())
        self._drag = None     # (key, original index) while an icon is dragged
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drag_motion)
        drop.connect("drop", self._drag_drop)
        drop.connect("enter", self._drag_motion)
        drop.connect("leave", self._drag_leave)
        self.add_controller(drop)
        dock_drop.attach_plate(self)
        dock_drop.attach_trash(self, self.trash)
        self._update_thickness()
        for did in cfg["pinned"]:
            info = apps.lookup(did)
            if info:
                self._add_tile(did, info.get_display_name(), icons.app_icon(info), info)
        if cfg["show_recents"]:
            for did in cfg["recent"]:
                info = apps.lookup(did)
                if info and did not in self.tiles:
                    self._add_tile(did, info.get_display_name(), icons.app_icon(info), info)
        self.stacks.load()
        self._relayout()
        self._watch_trash()
        self._sync_src = 0
        self._setup_magnification()
        ui.on_change(self.queue_draw)
        if self.manager:
            self.manager.listeners.append(self._schedule_sync)
            self._schedule_sync()

    def detach(self) -> None:
        """Stop listening to shared objects (before the Dock is replaced)."""
        if self.manager and self._schedule_sync in self.manager.listeners:
            self.manager.listeners.remove(self._schedule_sync)

    def _spacer(self) -> Gtk.Box:
        return Gtk.Box(height_request=PAD_SIDE) if self.vertical else Gtk.Box(width_request=PAD_SIDE)

    def _update_thickness(self) -> None:
        t = plate_height(self.cfg)
        self.set_size_request(t, -1) if self.vertical else self.set_size_request(-1, t)

    # -- plate -----------------------------------------------------------------
    def plate_rect(self):
        w, h = self.get_width(), self.get_height()
        t = plate_height(self.cfg)
        return {"bottom": (0, h - t, w, t), "left": (0, 0, t, h), "right": (w - t, 0, t, h)}[self.edge]

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        t = plate_height(self.cfg)
        if w <= 0 or h <= 0 or (h if self.edge == "bottom" else w) < t:
            return      # not allocated yet (a blurred shadow of 0 px aborts GSK)
        if self.hide_amount > 0:     # auto-hide: slide everything towards the edge
            d = self.hide_amount * (t + self.cfg["edge_gap"] + SHADOW)
            snap.translate(Graphene.Point().init(*{"bottom": (0, d), "left": (-d, 0),
                                                   "right": (d, 0)}[self.edge]))
        x, y, pw, ph = self.plate_rect()
        rect = _rect(x, y, pw, ph)
        radius = ui.px("r_plate")
        rr = _rounded(rect, radius)
        dx, dy, blur, col = ui.shadow("shadow_plate")
        snap.append_outset_shadow(rr, col, dx, dy, 0, blur)
        snap.push_rounded_clip(rr)
        if self.backdrop:          # preview: stands in for the compositor's blur
            ok, p = self.compute_point(self.get_root(), Graphene.Point().init(0, 0))
            if ok:
                snap.append_texture(self.backdrop, _rect(-p.x, -p.y, self.backdrop.get_width(),
                                                         self.backdrop.get_height()))
        snap.append_color(ui.rgba("glass_tint" if self.cfg["glass"] else "solid_tint"), rect)
        snap.pop()
        snap.append_inset_shadow(rr, ui.rgba("highlight"), 0, 0, 0.5, 0)
        snap.append_border(_rounded(_rect(x - 0.5, y - 0.5, pw + 1, ph + 1), radius + 0.5),
                           [0.5] * 4, [ui.rgba("hairline")] * 4)
        Gtk.Box.do_snapshot(self, snap)

    def do_size_allocate(self, width, height, baseline) -> None:
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        self.queue_draw()     # the plate is painted from the new size
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
        self.recent_sep.update()
        self._update_thickness()
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

    def set_option(self, key: str, value) -> None:
        """Change a Dock setting; position/recents changes rebuild the Dock."""
        self.cfg[key] = value
        config.save("dock", self.cfg)
        if key in ("position", "show_recents") and self.on_rebuild:
            GLib.idle_add(lambda: (self.on_rebuild(), False)[1])
        for cb in self.on_geometry:
            cb()

    # -- magnification ---------------------------------------------------------
    def _setup_magnification(self) -> None:
        """macOS wave: icons near the pointer grow (cosine falloff over
        MAG_RADIUS icons), neighbours make room; it eases in on enter and out
        on leave. Distances use the unmagnified layout so the wave doesn't
        feed back on itself."""
        self._mag_pos = None
        self._mag_strength = 0.0
        self._mag_anim = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, y: self._mag_enter(x, y))
        motion.connect("motion", lambda _c, x, y: self._mag_move(x, y))
        motion.connect("leave", lambda _c: self._mag_animate(0.0, MAG_OUT_MS))
        self.add_controller(motion)

    def _mag_enter(self, x, y) -> None:
        self._mag_move(x, y)
        self._mag_animate(1.0, MAG_IN_MS)

    def _mag_move(self, x, y) -> None:
        if not self.cfg["magnification"] or self._drag:
            return
        parent = self.get_parent()
        ok, p = self.compute_point(parent, Graphene.Point().init(x, y)) if parent else (False, None)
        self._mag_pos = (p.y if self.vertical else p.x) if ok else None
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
        return self.app_tiles() + self.stacks.tiles() + [self.trash]

    def _apply_magnification(self) -> None:
        base = self.cfg["icon_size"]
        tiles = self.all_tiles()
        s = self._mag_strength
        if not self.cfg["magnification"] or s <= 0 or self._mag_pos is None:
            for t in tiles:
                t.icon.set_size(base)
            return
        extra = max(0, self.cfg["magnified_size"] - base) * s
        parent = self.get_parent()
        cell = base + 2 * TILE_PAD
        lines = DIVIDER_W * (2 if self.recent_sep.get_visible() else 1)
        length = 2 * PAD_SIDE + len(tiles) * cell + lines
        span = (parent.get_height() if self.vertical else parent.get_width()) if parent else length
        pos = (span - length) / 2 + PAD_SIDE
        radius = MAG_RADIUS * cell
        first_extra = self._first_extra()
        after_divider = (self.stacks.tiles() or [self.trash])[0]
        for t in tiles:
            if t is first_extra and self.recent_sep.get_visible():
                pos += DIVIDER_W
            if t is after_divider:
                pos += DIVIDER_W
            d = abs(pos + cell / 2 - self._mag_pos)
            f = math.cos(math.pi / 2 * d / radius) ** 2 if d < radius else 0.0
            t.icon.set_size(base + extra * f)
            pos += cell

    # -- tiles and sections ------------------------------------------------------
    def _add_tile(self, key, name, gicon, info=None) -> DockTile:
        tile = DockTile(self, name, gicon, lambda t: self._clicked(key, t), info,
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
        self._relayout()

    def app_tiles(self) -> list:
        """App tiles in Dock order (pinned, then recent/running)."""
        out, w = [], self.get_first_child()
        while w is not None and w is not self.sep:
            if isinstance(w, DockTile):
                out.append(w)
            w = w.get_next_sibling()
        return out

    def _extras(self) -> list:
        pinned = set(self.cfg["pinned"])
        return [t for t in self.app_tiles() if t.key not in pinned]

    def _first_extra(self):
        ex = self._extras()
        return ex[0] if ex else None

    def _relayout(self) -> None:
        """Children order: spacer, pinned (config order), recents bar,
        recent/running (current order), divider..."""
        prev = self.get_first_child()
        for key in self.cfg["pinned"]:
            tile = self.tiles.get(key)
            if tile:
                self.reorder_child_after(tile, prev)
                prev = tile
        extras = self._extras()
        self.reorder_child_after(self.recent_sep, prev)
        prev = self.recent_sep
        for tile in extras:
            self.reorder_child_after(tile, prev)
            prev = tile
        self.recent_sep.set_visible(bool(extras) and self.cfg["show_recents"])

    def _save_order(self) -> None:
        pinned = set(self.cfg["pinned"])
        self.cfg["pinned"] = [t.key for t in self.app_tiles() if t.key in pinned]
        config.save("dock", self.cfg)
        self._relayout()

    def _slot_at(self, x: float, y: float = 0.0, exclude=None) -> int:
        """Index among app tiles where something dropped at (x, y) goes."""
        slot = 0
        for t in self.app_tiles():
            if t is exclude:
                continue
            ok, b = t.compute_bounds(self)
            if not ok:
                continue
            centre = b.get_y() + b.get_height() / 2 if self.vertical else b.get_x() + b.get_width() / 2
            if centre < (y if self.vertical else x):
                slot += 1
        return slot

    def _move_to_slot(self, tile, slot: int) -> None:
        others = [t for t in self.app_tiles() if t is not tile]
        anchor = others[slot - 1] if slot else self.get_first_child()
        if anchor is not tile:
            self.reorder_child_after(tile, anchor)

    def pin_at(self, key, before=None, x=None, y=0.0) -> None:
        """Pin app `key` (desktop id) before tile `before`, or at (x, y)."""
        tile = self.tiles.get(key)
        if tile is None:
            info = apps.lookup(key)
            if not info:
                return
            tile = self._add_tile(key, info.get_display_name(), icons.app_icon(info), info)
            tile.set_running(key in self.windows)
        others = [t for t in self.app_tiles() if t is not tile]
        slot = others.index(before) if before in others else (
            self._slot_at(x, y, exclude=tile) if x is not None else len(self.cfg["pinned"]))
        self._move_to_slot(tile, slot)
        if key not in self.cfg["pinned"]:
            self.cfg["pinned"].append(key)
        self._save_order()

    def set_pinned(self, key, on: bool) -> None:
        """Keep in Dock on/off. Unpinning a running app keeps its icon until it
        quits (in the recent/running section, like macOS)."""
        pins = self.cfg["pinned"]
        if on and key not in pins:
            pins.append(key)
            self._save_order()         # takes its current position
        elif not on and key in pins:
            pins.remove(key)
            config.save("dock", self.cfg)
            if key not in self.windows and not self._is_recent(key):
                self._remove_tile(key)
            else:
                self._relayout()

    # -- recents ---------------------------------------------------------------
    def _is_recent(self, key) -> bool:
        return self.cfg["show_recents"] and key in self.cfg["recent"]

    def _note_recent(self, key) -> None:
        """An unpinned app was used: most recent first, MAX_RECENTS kept."""
        if key in self.cfg["pinned"] or not apps.lookup(key):
            return
        rec = [k for k in self.cfg["recent"] if k != key]
        rec.insert(0, key)
        dropped, self.cfg["recent"] = rec[MAX_RECENTS:], rec[:MAX_RECENTS]
        config.save("dock", self.cfg)
        for k in dropped:
            if k in self.tiles and k not in self.windows and k not in self.cfg["pinned"]:
                self._remove_tile(k)

    # -- drag to reorder -------------------------------------------------------
    def _drag_begin(self, src, _drag, tile) -> None:
        self._drag = {"key": tile.key, "index": self.app_tiles().index(tile), "left": False,
                      "dropped": False}
        tile.label.popdown()
        size = self.cfg["icon_size"]
        src.set_icon(icons.paintable(self, tile.gicon, size), size // 2, size // 2)
        tile.add_css_class("dragging")
        self._mag_animate(0.0, MAG_OUT_MS)

    def _drag_motion(self, _target, x, y):
        if not self._drag:
            return 0
        self._drag["left"] = False
        tile = self.tiles[self._drag["key"]]
        slot = self._slot_at(x, y, exclude=tile)   # other icons whose centre is before the pointer
        if self.app_tiles().index(tile) != slot:
            self._move_to_slot(tile, slot)
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
            self._move_to_slot(tile, d["index"])
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
        started = [k for k in groups if k not in self.windows]
        self.windows = groups
        for key in started:
            self._note_recent(key)
        pinned = set(self.cfg["pinned"])
        for key in [k for k in self.tiles
                    if k not in pinned and k not in groups and not self._is_recent(k)]:
            self._remove_tile(key)                 # unpinned app quit
        for key in groups:
            if key not in self.tiles:
                info = apps.lookup(key)
                if info:
                    self._add_tile(key, info.get_display_name(), icons.app_icon(info), info)
                else:   # no .desktop: generic icon, app_id as name
                    self._add_tile(key, key, Gio.ThemedIcon.new("application-x-executable"))
        for key, tile in self.tiles.items():
            tile.set_running(key in groups)
        self._relayout()
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
        # Stops when the first window maps, or after a few bounces anyway
        # (apps whose window we can't match must not bounce forever).
        if tile.key not in NO_BOUNCE:
            tile.bounce(LAUNCH_BOUNCES * BOUNCE_MS)

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
    inner, outer = dot_gaps(cfg["icon_size"])
    ui.register(CSS, key="dock", tile_pad=TILE_PAD, row=dot_row(cfg), bounce_ms=BOUNCE_MS,
                dot=DOT, dot_in=inner, dot_out=outer)


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    if cfg["stacks"] is None:
        cfg["stacks"] = dock_stack.default_stacks()
        config.save("dock", cfg)
    return cfg


def apply_margins(dock: Dock) -> None:
    """Shadow room along the edge; the plate floats edge_gap px off the edge."""
    gap = dock.cfg["edge_gap"]
    if dock.edge == "bottom":
        dock.set_margin_start(SHADOW)
        dock.set_margin_end(SHADOW)
        dock.set_margin_bottom(gap)
    else:
        dock.set_margin_top(SHADOW)
        dock.set_margin_bottom(SHADOW)
        (dock.set_margin_start if dock.edge == "left" else dock.set_margin_end)(gap)


class DockWindow(Gtk.ApplicationWindow):
    """Layer surface spanning the Dock's screen edge, thick enough for
    magnified icons; only the Dock itself receives clicks (input region).
    Auto-hide: the Dock slides out; a TRIGGER px strip at the edge (along
    the Dock) brings it back after autohide_delay_ms."""

    def __init__(self, app, cfg: dict, manager=None):
        # resizable: lets layer-shell stretch the surface along the edge
        # (a fixed-size window would keep its natural width).
        super().__init__(application=app, title="Dock", css_classes=["sonata-dock"],
                         decorated=False, resizable=True)
        self.cfg = cfg
        self.manager = manager
        self.dock = None
        self.layer = False
        self._hidden = False
        self._hide_anim = None
        self._timer = 0
        self._inside = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._pointer(True))
        motion.connect("leave", lambda *_: self._pointer(False))
        self.add_controller(motion)
        ui.menu.on_closed.append(lambda: self._pointer(self._inside))
        self._cfg_mon = config.watch("dock", self._config_changed)
        self.rebuild()

    def rebuild(self) -> None:
        if self.dock:
            self.dock.detach()
        edge = self.cfg["position"] if self.cfg["position"] in EDGES else "bottom"
        self.dock = Dock(self.cfg, self.manager)
        self.dock.on_rebuild = self.rebuild
        self.dock.on_geometry.append(self._geometry)
        apply_margins(self.dock)
        self.set_child(self.dock)
        if not self.layer:
            self.layer = layer.anchor_edge(self, "sonata2-dock", edge, self._exclusive())
        else:
            layer.set_edge(self, edge, self._exclusive())
        self._hidden = False
        self.dock.hide_amount = 0.0
        self._geometry()
        if self.cfg["autohide"]:
            GLib.timeout_add(600, lambda: (self._pointer(self._inside), False)[1])

    LIVE_KEYS = ("icon_size", "magnification", "magnified_size", "position", "autohide",
                 "autohide_delay_ms", "show_recents", "glass", "edge_gap")

    def _config_changed(self) -> None:
        """dock.json changed (Settings app): apply appearance/behaviour keys.
        The Dock's own writes (pins, recents) don't touch these, so no loop."""
        new = config.load("dock", DEFAULTS)
        if all(new[k] == self.cfg[k] for k in self.LIVE_KEYS):
            return
        for k in self.LIVE_KEYS:
            self.cfg[k] = new[k]
        load_css(self.cfg)
        self.rebuild()

    def _exclusive(self) -> int:
        return 0 if self.cfg["autohide"] else plate_height(self.cfg) + self.cfg["edge_gap"]

    def _thickness(self) -> int:
        return SHADOW + PAD_TOP + max_icon(self.cfg) + dot_row(self.cfg) + self.cfg["edge_gap"]

    def _geometry(self) -> None:
        # Fixed surface thickness = biggest possible Dock; changes only with
        # the Dock size/magnification setting, never while magnifying.
        t = self._thickness()
        if self.cfg["position"] in ("left", "right"):
            self.set_size_request(t, -1)
        else:
            self.set_size_request(-1, t)
        if self.layer:
            layer.set_exclusive(self, self._exclusive())
            self._update_input()
        if not self.cfg["autohide"] and self._hidden:
            self._slide(False)
        elif self.cfg["autohide"] and not self._hidden and not self._inside:
            self._pointer(False)

    def _update_input(self) -> None:
        if not self.layer or not self.dock:
            return
        ok, b = self.dock.compute_bounds(self)
        if not ok:
            return
        x, y, w, h = b.get_x(), b.get_y(), b.get_width(), b.get_height()
        if self._hidden:          # a thin strip at the screen edge, along the Dock
            W, H = self.get_width(), self.get_height()
            x, y, w, h = {"bottom": (x, H - TRIGGER, w, TRIGGER), "left": (0, y, TRIGGER, h),
                          "right": (W - TRIGGER, y, TRIGGER, h)}[self.dock.edge]
        layer.set_input_region(self, [(x, y, w, h)])

    # -- auto-hide -------------------------------------------------------------
    def _pointer(self, inside: bool) -> None:
        self._inside = inside
        if not self.cfg["autohide"]:
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        busy = ui.menu.OPEN or (self.dock and self.dock._drag)
        if inside and self._hidden:
            self._timer = GLib.timeout_add(self.cfg["autohide_delay_ms"], self._reveal)
        elif not inside and not self._hidden and not busy:
            self._timer = GLib.timeout_add(200, self._conceal)

    def _reveal(self) -> bool:
        self._timer = 0
        self._slide(False)
        return False

    def _conceal(self) -> bool:
        self._timer = 0
        self._slide(True)
        return False

    def _slide(self, hide: bool) -> None:
        self._hidden = hide
        if self._hide_anim:
            self._hide_anim.pause()
        dock = self.dock

        def step(v):
            dock.hide_amount = v
            dock.queue_draw()
        self._hide_anim = Adw.TimedAnimation.new(self, dock.hide_amount, 1.0 if hide else 0.0,
                                                 HIDE_MS, Adw.CallbackAnimationTarget.new(step))
        self._hide_anim.set_easing(Adw.Easing.EASE_IN_OUT_CUBIC)
        self._hide_anim.play()
        self._update_input()
