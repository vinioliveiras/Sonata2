"""The Dock (macOS Big Sur): pinned apps, running apps, a separator and the Trash.

Metrics follow Big Sur at the default 48 px icon size: rounded plate floating
a few px above the screen edge, 4 px running dot under the icon, name label
above the hovered icon. The plate is translucent "glass": the compositor
blurs what is behind it (Wayfire blur plugin, see config/wayfire.ini); with
glass off it is nearly opaque.

Running apps come from wlr-foreign-toplevel (wl/toplevels.py): a dot under
running apps, unpinned running apps after the pinned ones, click brings the
app's windows to the front (macOS behaviour) or launches it.

Right-click menus live in dock_menu.py. Icons are reordered by dragging;
dragging an app out of the Dock removes it (unless it is running)."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import apps, config, icons  # noqa: E402
from .. import ui  # noqa: E402
from . import dock_drop, dock_menu, layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4, "glass": True}
LAUNCH_TIMEOUT_MS = 10000   # stop bouncing if no window shows up
BOUNCE_MS = 620             # one bounce

# Plate padding and running dot, in px. Unlike macOS (where it sits low),
# the dot is centred between the icon's visible artwork and the plate edge.
# Icons have a transparent margin inside their box (Sonata-MacTahoe: 1/12
# of the size), so the gap above the dot is shortened by that margin.
PAD_TOP, DOT, DOT_GAP, SHADOW = 5, 4, 4, 12
ART_INSET = 1 / 12   # measured: 4 px at 48 px


def dot_gaps(icon_size: int):
    """(gap above, gap below) the dot, in px, for visual centring."""
    return max(0, DOT_GAP - round(icon_size * ART_INSET)), DOT_GAP

CSS = """
window.sonata-dock, window.sonata-dock > contents { background: none; box-shadow: none; }
.dock-plate {
  padding: %(pad_top)dpx 4px 0 4px;
  border-radius: %(r_plate)s;
  background-color: %(solid_tint)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_plate)s;
}
/* Glass: frosted, the compositor blurs and saturates the backdrop. */
.glass .dock-plate { background-color: %(glass_tint)s; }
.dock-tile, .dock-tile:hover, .dock-tile:active, .dock-tile:focus {
  padding: 0 2px; margin: 0; min-width: 0; min-height: 0;
  border: none; border-radius: 0; background: none; box-shadow: none; outline: none;
}
.dock-tile image { transition: filter %(t_press)s ease-out; }
.dock-tile:active image, .dock-tile.drop-hover image { filter: brightness(0.62); }
.dock-tile.dragging { opacity: 0; }   /* keeps its gap while being dragged */
.dock-dot { min-width: %(dot)dpx; min-height: %(dot)dpx; margin: %(dot_top)dpx 0 %(dot_bottom)dpx 0;
            border-radius: 99px; background-color: %(indicator)s; opacity: 0; }
.dock-tile.running .dock-dot { opacity: 1; }
.dock-sep { min-width: 1px; margin: 4px 5px %(sep_bottom)dpx 5px; background-color: %(separator)s; }

@keyframes dock-bounce {
  0%%   { transform: translateY(0); }
  50%%  { transform: translateY(-18px); }
  100%% { transform: translateY(0); }
}
.dock-tile.launching image { animation: dock-bounce %(bounce_ms)dms ease-in-out infinite; }
"""


class DockTile(Gtk.Button):
    """One Dock icon: image, running dot, hover label."""

    def __init__(self, name: str, gicon, size: int, on_click, info=None, on_menu=None):
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False)
        self.info = info
        self.name = name
        self._bounce_src = 0
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.image = Gtk.Image(pixel_size=size)
        self.gicon = gicon
        icons.set_image(self.image, gicon)
        box.append(self.image)
        box.append(Gtk.Box(css_classes=["dock-dot"], halign=Gtk.Align.CENTER))
        self.set_child(box)

        self.label = ui.label.HoverLabel(self, name)
        self.connect("clicked", lambda _b: on_click(self))
        if on_menu:
            right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            right.connect("pressed", lambda *_: on_menu(self))
            self.add_controller(right)

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
        self.sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, css_classes=["dock-sep"])
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
        for did in cfg["pinned"]:
            info = apps.lookup(did)
            if info:
                self._add_tile(did, info.get_display_name(), info.get_icon(), info)
        self._watch_trash()
        self._sync_src = 0
        if self.manager:
            self.manager.listeners.append(self._schedule_sync)
            self._schedule_sync()

    def _add_tile(self, key, name, gicon, info=None) -> DockTile:
        tile = DockTile(name, gicon, self.cfg["icon_size"], lambda t: self._clicked(key, t), info,
                        on_menu=lambda t: dock_menu.app_menu(self, key, t))
        tile.key = key
        self.tiles[key] = tile
        self.insert_child_after(tile, self.sep.get_prev_sibling())   # before the separator
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
        out, w = [], self.get_first_child()
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
        self.reorder_child_after(tile, others[slot - 1] if slot else None)
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
        paintable = icons.paintable(self, tile.gicon, self.cfg["icon_size"])
        half = self.cfg["icon_size"] // 2
        src.set_icon(paintable, half, half)
        tile.add_css_class("dragging")

    def _drag_motion(self, _target, x, _y):
        if not self._drag:
            return 0
        self._drag["left"] = False
        tiles = self.app_tiles()
        tile = self.tiles[self._drag["key"]]
        others = [t for t in tiles if t is not tile]
        slot = self._slot_at(x, exclude=tile)   # other icons whose centre is left of x
        if tiles.index(tile) != slot:
            self.reorder_child_after(tile, others[slot - 1] if slot else None)
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
            self.reorder_child_after(tile, tiles[i - 1] if i else None)
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
        icons.set_image(self.trash.image, Gio.ThemedIcon.new("user-trash-full" if full else "user-trash"))

    def _watch_trash(self) -> None:
        self._update_trash()
        path = self._trash_dir()
        os.makedirs(path, exist_ok=True)
        self._trash_mon = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._trash_mon.connect("changed", self._update_trash)


def dot_row(cfg: dict) -> int:
    top, bottom = dot_gaps(cfg["icon_size"])
    return top + DOT + bottom


def plate_height(cfg: dict) -> int:
    return PAD_TOP + cfg["icon_size"] + dot_row(cfg)


def load_css(cfg: dict) -> None:
    ui.setup()
    top, bottom = dot_gaps(cfg["icon_size"])
    ui.register(CSS, key="dock", pad_top=PAD_TOP, sep_bottom=dot_row(cfg), bounce_ms=BOUNCE_MS,
                dot=DOT, dot_top=top, dot_bottom=bottom)


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    return cfg


def apply_glass(widget: Gtk.Widget, cfg: dict) -> None:
    """`glass` class on the Dock's window when the plate should be frosted."""
    (widget.add_css_class if cfg["glass"] else widget.remove_css_class)("glass")


class DockWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cfg: dict, manager=None):
        super().__init__(application=app, title="Dock", css_classes=["sonata-dock"],
                         decorated=False, resizable=False)
        self.dock = Dock(cfg, manager)
        self.dock.set_margin_start(SHADOW)
        self.dock.set_margin_end(SHADOW)
        self.dock.set_margin_top(SHADOW)
        self.set_child(self.dock)
        # The plate sits edge_gap px above the screen edge; windows stop above it.
        self.dock.set_margin_bottom(cfg["edge_gap"])
        layer.anchor_bottom(self, "sonata2-dock", 0, plate_height(cfg) + cfg["edge_gap"])
        apply_glass(self, cfg)
