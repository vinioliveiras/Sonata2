"""The Dock (macOS Big Sur): pinned apps, a separator and the Trash.

Metrics follow Big Sur at the default 48 px icon size: rounded translucent
plate floating a few px above the screen edge, 4 px running dot under the
icon, name label above the hovered icon. Running-app tracking
(wlr-foreign-toplevel) comes in the next milestone; `set_running()` is the
hook it will call."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps, config  # noqa: E402
from ..style import install_css  # noqa: E402
from . import layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4}

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
  25%%  { transform: translateY(-18px); }
  50%%  { transform: translateY(0); }
  75%%  { transform: translateY(-18px); }
  100%% { transform: translateY(0); }
}
.dock-tile.launching image { animation: dock-bounce 1.2s ease-in-out 1; }

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

    def __init__(self, name: str, gicon, size: int, on_click):
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False)
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

    def set_running(self, running: bool) -> None:
        (self.add_css_class if running else self.remove_css_class)("running")

    def bounce(self) -> None:
        self.remove_css_class("launching")
        self.add_css_class("launching")
        GLib.timeout_add(1300, lambda: (self.remove_css_class("launching"), False)[1])


class Dock(Gtk.Box):
    """The plate with all tiles. Hosted by DockWindow or by the preview."""

    def __init__(self, cfg: dict):
        super().__init__(css_classes=["dock-plate"], halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.END)
        self.cfg = cfg
        self.tiles = {}
        size = cfg["icon_size"]
        for did in cfg["pinned"]:
            info = apps.lookup(did)
            if not info:
                continue
            tile = DockTile(info.get_display_name(), info.get_icon(), size,
                            lambda t, i=info: self._launch(t, i))
            self.tiles[did] = tile
            self.append(tile)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, css_classes=["dock-sep"]))
        self.trash = DockTile("Trash", Gio.ThemedIcon.new("user-trash"), size,
                              lambda _t: Gio.AppInfo.launch_default_for_uri("trash:///", None))
        self.append(self.trash)
        self._watch_trash()

    def _launch(self, tile: DockTile, info) -> None:
        tile.bounce()
        ctx = tile.get_display().get_app_launch_context()
        try:
            info.launch([], ctx)
        except GLib.Error as e:
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
    install_css(CSS % {"pad_top": PAD_TOP, "sep_bottom": DOT_ROW})


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    return cfg


def follow_theme(widget: Gtk.Widget) -> None:
    """Add/remove the `dark` class on `widget` with the system appearance."""
    sm = Adw.StyleManager.get_default()

    def sync(*_a):
        (widget.add_css_class if sm.get_dark() else widget.remove_css_class)("dark")
    sm.connect("notify::dark", sync)
    sync()


class DockWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cfg: dict):
        super().__init__(application=app, title="Dock", css_classes=["sonata-dock"],
                         decorated=False, resizable=False)
        self.dock = Dock(cfg)
        self.dock.set_margin_start(SHADOW)
        self.dock.set_margin_end(SHADOW)
        self.dock.set_margin_top(SHADOW)
        self.set_child(self.dock)
        # The plate sits edge_gap px above the screen edge; windows stop above it.
        self.dock.set_margin_bottom(cfg["edge_gap"])
        layer.anchor_bottom(self, "sonata2-dock", 0, plate_height(cfg) + cfg["edge_gap"])
        follow_theme(self)
