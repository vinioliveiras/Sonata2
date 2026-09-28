"""The Dock (macOS Big Sur): pinned apps, running apps, a separator and the Trash.

Metrics follow Big Sur at the default 48 px icon size: rounded plate floating
a few px above the screen edge, 4 px running dot under the icon, name label
above the hovered icon. The plate is translucent "glass": the compositor
blurs what is behind it (Wayfire blur plugin, see config/wayfire.ini); with
glass off it is nearly opaque.

Running apps come from wlr-foreign-toplevel (wl/toplevels.py): a dot under
running apps, unpinned running apps after the pinned ones, click brings the
app's windows to the front (macOS behaviour) or launches it."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps, config  # noqa: E402
from ..style import install_css  # noqa: E402
from . import layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4, "glass": True}
LAUNCH_TIMEOUT_MS = 10000   # stop bouncing if no window shows up
BOUNCE_MS = 620             # one bounce
GLASS_TINT = {"light": "rgba(246, 246, 250, 0.38)", "dark": "rgba(30, 30, 34, 0.42)"}

# Plate padding / dot row, in px (Big Sur at 48 px icons -> 62 px plate).
PAD_TOP, DOT_ROW, SHADOW = 5, 9, 12

CSS = """
window.sonata-dock, window.sonata-dock > contents { background: none; box-shadow: none; }
.dock-plate {
  padding: %(pad_top)dpx 4px 0 4px;
  border-radius: 18px;
  background-color: rgba(236, 236, 240, 0.78);
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.14),
              inset 0 0.5px 0 rgba(255, 255, 255, 0.65),
              0 6px 18px rgba(0, 0, 0, 0.16);
}
.dark .dock-plate {
  background-color: rgba(38, 38, 42, 0.74);
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.55),
              inset 0 0 0 0.5px rgba(255, 255, 255, 0.14),
              0 6px 18px rgba(0, 0, 0, 0.30);
}
/* Glass: frosted, the compositor blurs and saturates the backdrop. */
.glass .dock-plate {
  background-color: %(tint_light)s;
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.12),
              inset 0 0 0 0.5px rgba(255, 255, 255, 0.35),
              0 6px 18px rgba(0, 0, 0, 0.12);
}
.dark.glass .dock-plate {
  background-color: %(tint_dark)s;
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.5),
              inset 0 0 0 0.5px rgba(255, 255, 255, 0.16),
              0 6px 18px rgba(0, 0, 0, 0.25);
}
.dock-tile, .dock-tile:hover, .dock-tile:active, .dock-tile:focus {
  padding: 0 2px; margin: 0; min-width: 0; min-height: 0;
  border: none; border-radius: 0; background: none; box-shadow: none; outline: none;
}
.dock-tile image { transition: filter 80ms ease-out; }
.dock-tile:active image { filter: brightness(0.62); }
.dock-dot { min-width: 4px; min-height: 4px; margin: 2px 0 3px 0;
            border-radius: 99px; background-color: rgba(0, 0, 0, 0.62); opacity: 0; }
.dark .dock-dot { background-color: rgba(255, 255, 255, 0.72); }
.dock-tile.running .dock-dot { opacity: 1; }
.dock-sep { min-width: 1px; margin: 4px 5px %(sep_bottom)dpx 5px;
            background-color: rgba(0, 0, 0, 0.16); }
.dark .dock-sep { background-color: rgba(255, 255, 255, 0.18); }

@keyframes dock-bounce {
  0%%   { transform: translateY(0); }
  50%%  { transform: translateY(-18px); }
  100%% { transform: translateY(0); }
}
.dock-tile.launching image { animation: dock-bounce %(bounce_ms)dms ease-in-out infinite; }

popover.dock-label { background: none; box-shadow: none; padding: 0; }
popover.dock-label > contents {
  padding: 3px 10px; border-radius: 6px; min-height: 0;
  font-family: "SF Pro Text", "Inter", "Cantarell", sans-serif; font-size: 13px; font-weight: 400;
  color: rgba(0, 0, 0, 0.85);
  background-color: rgba(236, 236, 236, 0.97);
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.16), 0 2px 8px rgba(0, 0, 0, 0.18);
}
.dark popover.dock-label > contents {
  color: #f5f5f7;
  background-color: rgba(48, 48, 50, 0.97);
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.6), inset 0 0 0 0.5px rgba(255, 255, 255, 0.14),
              0 2px 8px rgba(0, 0, 0, 0.35);
}
"""


class DockTile(Gtk.Button):
    """One Dock icon: image, running dot, hover label."""

    def __init__(self, name: str, gicon, size: int, on_click, info=None):
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False)
        self.info = info
        self._bounce_src = 0
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.image = Gtk.Image(gicon=gicon, pixel_size=size)
        box.append(self.image)
        box.append(Gtk.Box(css_classes=["dock-dot"], halign=Gtk.Align.CENTER))
        self.set_child(box)

        self.label = Gtk.Popover(css_classes=["dock-label"], has_arrow=False, autohide=False,
                                 can_target=False, position=Gtk.PositionType.TOP)
        self.label.set_child(Gtk.Label(label=name))
        self.label.set_offset(0, -8)
        self.label.set_parent(self)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.label.popup())
        motion.connect("leave", lambda *_: self.label.popdown())
        self.add_controller(motion)
        self.connect("clicked", lambda _b: on_click(self))
        self.connect("destroy", lambda _b: self.label.unparent())

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
                              lambda _t: Gio.AppInfo.launch_default_for_uri("trash:///", None))
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
        tile = DockTile(name, gicon, self.cfg["icon_size"], lambda t: self._clicked(key, t), info)
        self.tiles[key] = tile
        self.insert_child_after(tile, self.sep.get_prev_sibling())   # before the separator
        return tile

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
            self.remove(self.tiles.pop(key))       # unpinned app quit
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
            self._launch(tile, tile.info)

    def _launch(self, tile: DockTile, info) -> None:
        # With window tracking the bounce stops when the first window maps;
        # without it, bounce twice.
        tile.bounce(LAUNCH_TIMEOUT_MS if self.manager else 2 * BOUNCE_MS)
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
        self.trash.image.set_from_icon_name("user-trash-full" if full else "user-trash")

    def _watch_trash(self) -> None:
        self._update_trash()
        path = self._trash_dir()
        os.makedirs(path, exist_ok=True)
        self._trash_mon = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._trash_mon.connect("changed", self._update_trash)


def plate_height(cfg: dict) -> int:
    return PAD_TOP + cfg["icon_size"] + DOT_ROW


def load_css() -> None:
    install_css(CSS % {"pad_top": PAD_TOP, "sep_bottom": DOT_ROW, "bounce_ms": BOUNCE_MS,
                       "tint_light": GLASS_TINT["light"], "tint_dark": GLASS_TINT["dark"]})


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    return cfg


def follow_theme(widget: Gtk.Widget, cfg: dict) -> None:
    """Keep the `dark` class on `widget` in sync with the system appearance;
    set `glass` from the config."""
    if cfg["glass"]:
        widget.add_css_class("glass")
    sm = Adw.StyleManager.get_default()

    def sync(*_a):
        (widget.add_css_class if sm.get_dark() else widget.remove_css_class)("dark")
    sm.connect("notify::dark", sync)
    sync()


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
        follow_theme(self, cfg)
