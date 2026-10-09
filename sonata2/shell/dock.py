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
import json
import math
import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import applock, apps, badges, config, icons, logs, steamgames, windowapps  # noqa: E402
from .. import ui  # noqa: E402
from . import dock_drop, dock_folder, dock_menu, dock_stack, layer  # noqa: E402

DEFAULTS = {"pinned": None, "icon_size": 48, "edge_gap": 4, "window_gap": 6, "glass": True,
            "indicators": True, "bounce": True, "minimize_effect": "genie", "click_minimizes": True,
            "magnification": False, "magnified_size": 80, "position": "bottom",
            "autohide": False, "autohide_delay_ms": 300, "show_recents": True,
            "recent": [], "stacks": None, "all_displays": False, "folders": {},
            "quit_on_close": True}             # Desktop & Windows: closing the last window quits the app (Vini: on)
EDGES = ("left", "bottom", "right")
MIN_SIZE, MAX_SIZE = 16, 128
PIN_MIN_SIZE = 36           # pins must still fit at this size: more can't be kept in the Dock
SCREEN_MARGIN = 16          # px kept free at both ends of the screen edge
MAX_RECENTS = 3
LAUNCH_BOUNCES = 3          # attention / urgent bounces
MAX_DOTS = 3                        # running dots: one per window, up to this many (a + looked off -- Vini)
# Always in the Dock (like Finder on macOS): Files and Launchpad can't be
# removed -- the shell relies on them (open folders, reach every app).
def merge_order(pinned: list, tile_keys: list) -> list:
    """The pinned keys in the tiles' order; keys without a tile stay where
    they were (between the same neighbours)."""
    shown = [k for k in tile_keys if k in pinned]
    out, it = [], iter(shown)
    for k in pinned:
        out.append(next(it, None) if k in tile_keys else k)
    out = [k for k in out if k is not None]
    out += [k for k in it if k not in out]
    seen = set()
    return [k for k in out if not (k in seen or seen.add(k))]


PERMANENT = ("io.github.vinioliveiras.sonata2.files", "sonata2-launchpad")
# shell tools that open instantly and have no app window (layer surfaces: the
# screenshot toolbar): no launch bounce, and never "stuck" -- the Dock bounced
# the Screenshot icon on and on, then stopped it as stuck (Vini)
NO_BOUNCE = {"sonata2-launchpad", "sonata2-screenshot"}
BOUNCE_MS = 620             # one bounce
STUCK_S = 4                 # clicked again this long after a launch that never showed a window: stuck
STUCK_MAX_S = 90            # ... but not later than this: a slow app long since started isn't stopped
RELAUNCH_MS = 900           # its processes stopped, then the app opens again
# a launch bounces until the app's first window shows up, 10 bounces at most
# (Vini: it went on ~30 s when no window came, e.g. an app already running)
LAUNCH_MAX_BOUNCES = 10
LAUNCH_MAX_MS = LAUNCH_MAX_BOUNCES * BOUNCE_MS
CLOSE_UP_MS = 260           # a removed icon's place closes up
OPEN_UP_MS = 260            # a new icon's place opens, then it fades in
UNGROUP_MS = 340            # a folder undone: its apps pop out in its place
UNGROUP_STAGGER_MS = 45
SETTLE_MS = 200             # a dropped icon glides into its slot
FOLDER_HOLD_MS = 350        # held this long over another app's middle: drop makes a folder
FOLDER_ZONE = 0.3           # the middle: within this part of a cell from the icon's centre
MAG_RADIUS = 3.0            # magnification reaches this many icons away
MAG_IN_MS, MAG_OUT_MS = 120, 250
HIDE_MS = 250               # auto-hide slide
TRIGGER = layer.EDGE_TRIGGER  # px strip at the screen edge that reveals a hidden Dock

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


def reserved(cfg: dict) -> int:
    """How far the resting Dock reaches in from its screen edge (0 when it
    hides itself): full-screen shell surfaces keep their content clear of it."""
    if cfg.get("autohide"):
        return 0
    return cfg["edge_gap"] + 2 * PAD_TOP + cfg["icon_size"] + dot_row(cfg)


def max_icon(cfg: dict) -> int:
    return max(cfg["icon_size"], cfg["magnified_size"]) if cfg["magnification"] else cfg["icon_size"]


def _rounded(rect, radius) -> Gsk.RoundedRect:
    # Keep a reference: `Gsk.RoundedRect().init_from_rect(...)` returns a
    # view of a temporary that PyGObject frees at once (garbage bounds).
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect, radius)
    return rr


def _rgba(spec) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(spec)
    return c


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
  /* Adwaita's buttons animate "all": on the first Light/Dark switch its new
     style sheet made every tile's padding glide -- the Dock stretched sideways */
  transition: none;
}
.edge-left .dock-tile, .edge-right .dock-tile { padding: %(tile_pad)dpx 0; }
.dock-icon { transition: filter %(t_press)s ease-out, transform 160ms cubic-bezier(0.2, 0.8, 0.2, 1); }
/* an app held over another: they'll make a folder (Launchpad) */
.dock-tile.folder-target .dock-icon { transform: scale(1.18); filter: brightness(0.85); }
.dock-tile:active .dock-icon, .dock-tile.drop-hover .dock-icon { filter: brightness(0.62); }
.dock-tile.dragging { opacity: 0; }   /* keeps its gap while being dragged */
.dock-dot { min-width: %(dot)dpx; min-height: %(dot)dpx; border-radius: 99px;
            background-color: %(indicator)s; opacity: 0; transition: opacity %(t_fast)s; }
.edge-bottom .dock-dots { margin: %(dot_in)dpx 0 %(dot_out)dpx 0; }
.edge-left .dock-dots { margin: 0 %(dot_in)dpx 0 %(dot_out)dpx; }
.edge-right .dock-dots { margin: 0 %(dot_out)dpx 0 %(dot_in)dpx; }
.dock-dot.on { opacity: %(dot_on)s; }
.dock-divider > box, .dock-recent-sep > box { background-color: %(separator)s; }
.edge-bottom .dock-divider, .edge-bottom .dock-recent-sep { padding: 0 5px; margin-bottom: %(row)dpx; }
.edge-bottom .dock-divider > box, .edge-bottom .dock-recent-sep > box { min-width: 1px; }
.edge-left .dock-divider, .edge-left .dock-recent-sep { padding: 5px 0; margin-left: %(row)dpx; }
.edge-right .dock-divider, .edge-right .dock-recent-sep { padding: 5px 0; margin-right: %(row)dpx; }
.edge-left .dock-divider > box, .edge-right .dock-divider > box,
.edge-left .dock-recent-sep > box, .edge-right .dock-recent-sep > box { min-height: 1px; }

/* a ball under gravity (macOS): a parabola, quick off the Dock, slowing
   to the top, falling faster back down (GTK ignores per-keyframe curves:
   the arc is sampled, played linearly) */
@keyframes dock-bounce-up {
  0%% { transform: none; }
  10%% { transform: translateY(-6.48px); }
  20%% { transform: translateY(-11.52px); }
  30%% { transform: translateY(-15.12px); }
  40%% { transform: translateY(-17.28px); }
  50%% { transform: translateY(-18.0px); }
  60%% { transform: translateY(-17.28px); }
  70%% { transform: translateY(-15.12px); }
  80%% { transform: translateY(-11.52px); }
  90%% { transform: translateY(-6.48px); }
  100%% { transform: none; } }
@keyframes dock-bounce-right {
  0%% { transform: none; }
  10%% { transform: translateX(6.48px); }
  20%% { transform: translateX(11.52px); }
  30%% { transform: translateX(15.12px); }
  40%% { transform: translateX(17.28px); }
  50%% { transform: translateX(18.0px); }
  60%% { transform: translateX(17.28px); }
  70%% { transform: translateX(15.12px); }
  80%% { transform: translateX(11.52px); }
  90%% { transform: translateX(6.48px); }
  100%% { transform: none; } }
@keyframes dock-bounce-left {
  0%% { transform: none; }
  10%% { transform: translateX(-6.48px); }
  20%% { transform: translateX(-11.52px); }
  30%% { transform: translateX(-15.12px); }
  40%% { transform: translateX(-17.28px); }
  50%% { transform: translateX(-18.0px); }
  60%% { transform: translateX(-17.28px); }
  70%% { transform: translateX(-15.12px); }
  80%% { transform: translateX(-11.52px); }
  90%% { transform: translateX(-6.48px); }
  100%% { transform: none; } }
.edge-bottom .dock-tile.launching .dock-icon { animation: dock-bounce-up %(bounce_ms)dms linear infinite; }
.edge-left .dock-tile.launching .dock-icon { animation: dock-bounce-right %(bounce_ms)dms linear infinite; }
.edge-right .dock-tile.launching .dock-icon { animation: dock-bounce-left %(bounce_ms)dms linear infinite; }
"""


def _open_trash() -> None:
    """The Trash opens in Sonata's Files (like Finder)."""
    from ..files import open_folder
    open_folder("trash:///")


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
        key = (size, ui.is_dark())         # the Trash and Launchpad follow Light/Dark
        if key not in self._paint:
            if len(self._paint) > 2:
                self._paint.clear()
            self._paint[key] = icons.paintable(self, self._gicon, size)
        return self._paint[key]

    badge = ""        # macOS notification badge ("3", "99+"); set_badge()
    locked = False    # a locked app (applock.py): a padlock at the bottom right
    pop = 1.0         # drawn at this scale (Dock.pop_in)
    sandboxed = False  # a copy runs in a sandbox (sandbox.py): an orange "S"

    def set_sandboxed(self, on: bool) -> None:
        if on != self.sandboxed:
            self.sandboxed = on
            self.queue_draw()

    def set_locked(self, on: bool) -> None:
        if on != self.locked:
            self.locked = on
            self.queue_draw()

    badge_k = 1.0     # the badge's scale while it pops in (BADGE_POP_MS)

    def set_badge(self, text: str) -> None:
        if text != self.badge:
            grew = bool(text) and (not self.badge or _badge_n(text) > _badge_n(self.badge))
            self.badge = text
            if grew and self.get_mapped():          # a new message: the badge pops (macOS)
                def step(v):
                    self.badge_k = v
                    self.queue_draw()
                anim = Adw.TimedAnimation.new(self, 0.3, 1.0, BADGE_POP_MS, Adw.CallbackAnimationTarget.new(step))
                anim.set_easing(Adw.Easing.EASE_OUT_BACK)
                self._badge_anim = anim
                self.badge_k = 0.3
                anim.play()
            self.queue_draw()

    def do_snapshot(self, snap) -> None:
        dock = self.get_ancestor(Dock)
        base = dock.cfg["icon_size"] if dock else self._size
        size = self._size if self._size == base else max(self._size, max_icon(dock.cfg))
        if self.pop != 1.0:                    # popping out of an undone folder (Dock.pop_in)
            c = self._size / 2
            snap.translate(Graphene.Point().init(c, c))
            snap.scale(self.pop, self.pop)
            snap.translate(Graphene.Point().init(-c, -c))
        self._paintable(size).snapshot(snap, self._size, self._size)
        if self.locked:
            dock_folder.draw_lock_badge(snap, self._size)
        if self.badge:
            self._draw_badge(snap)
        elif self.sandboxed:                  # Open in Sandbox: an orange "S" where a badge goes
            self._draw_badge(snap, "S", "#ff9f0a", "#000000")

    def _draw_badge(self, snap, text=None, fill="#ff3b30", ink="#ffffff") -> None:
        """Red pill at the icon's top right, white number (scales with the
        icon, so it grows with magnification like macOS)."""
        s = self._size
        layout = self.create_pango_layout(text or self.badge)
        fd = Pango.FontDescription.from_string(f"Sans Bold {max(6, s * 0.2):.1f}px")
        fd.set_absolute_size(max(7, s * 0.24) * Pango.SCALE)
        layout.set_font_description(fd)
        tw, th = layout.get_pixel_size()
        h = max(s * 0.38, th + 2)
        w = max(h, tw + h * 0.55)
        x, y = s - w * 0.78, -h * 0.12
        k = self.badge_k if text is None else 1.0
        if k != 1.0:
            snap.save()
            snap.translate(Graphene.Point().init(x + w / 2, y + h / 2))
            snap.scale(k, k)
            snap.translate(Graphene.Point().init(-(x + w / 2), -(y + h / 2)))
        rr = _rounded(_rect(x, y, w, h), h / 2)
        snap.append_outset_shadow(rr, _rgba("rgba(0,0,0,0.25)"), 0, 1, 0, 2)
        snap.push_rounded_clip(rr)
        snap.append_color(_rgba(fill), _rect(x, y, w, h))
        snap.pop()
        snap.save()
        snap.translate(Graphene.Point().init(x + (w - tw) / 2, y + (h - th) / 2))
        snap.append_layout(layout, _rgba(ink))
        snap.restore()
        if k != 1.0:
            snap.restore()


class DockTile(Gtk.Button):
    """One Dock icon: icon, running dot (towards the edge), hover label."""

    def __init__(self, dock, name: str, gicon, on_click, info=None, on_menu=None, icon=None):
        edge = dock.edge
        super().__init__(css_classes=["dock-tile"], focus_on_click=False, can_focus=False,
                         valign=Gtk.Align.END if edge == "bottom" else Gtk.Align.FILL,
                         halign={"left": Gtk.Align.START, "right": Gtk.Align.END}.get(edge, Gtk.Align.FILL))
        self.info = info
        self.name = name
        self.gicon = gicon
        self.key = None
        self._bounce_src = 0
        self.icon = icon or DockIcon(gicon, dock.cfg["icon_size"])
        # one dot per open window (up to MAX_DOTS; Vini's call, macOS shows one)
        dot = Gtk.Box(css_classes=["dock-dots"], spacing=3, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                      orientation=Gtk.Orientation.HORIZONTAL if edge == "bottom" else Gtk.Orientation.VERTICAL)
        self.dots = [Gtk.Box(css_classes=["dock-dot"]) for _ in range(MAX_DOTS)]
        for d in self.dots:
            dot.append(d)
            d.set_visible(d is self.dots[0])
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

    def set_running(self, windows) -> None:
        """`windows`: how many are open (True = 1); that many dots show."""
        n = min(MAX_DOTS, int(windows))
        running = n > 0
        for i, d in enumerate(self.dots):
            d.set_visible(i < max(1, n))           # the first keeps its place when off
            (d.add_css_class if i < n else d.remove_css_class)("on")
        (self.add_css_class if running else self.remove_css_class)("running")
        if running and self._bounce_src:
            GLib.source_remove(self._bounce_src)
            self._stop_bounce()

    def bounce(self, ms: int = LAUNCH_MAX_MS) -> None:
        """Bounce until the app's first window appears (set_running) or `ms`."""
        self.add_css_class("launching")
        if self._bounce_src:
            GLib.source_remove(self._bounce_src)
        self._bounce_src = GLib.timeout_add(ms, self._stop_bounce)
        if getattr(self, "_bounce_stats", None) is None:
            self._bounce_stats = ui.transition.FrameStats(self, "dock bounce")

    def _stop_bounce(self) -> bool:
        stats, self._bounce_stats = getattr(self, "_bounce_stats", None), None
        if stats:
            stats.stop()
        self.remove_css_class("launching")
        self._bounce_src = 0
        return False


def close_launchpad() -> None:
    """Close Launchpad (its "close" action over D-Bus; nothing if it isn't running)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        app_id = "io.github.vinioliveiras.sonata2.launchpad"
        bus.call(app_id, "/" + app_id.replace(".", "/"), "org.freedesktop.Application", "ActivateAction",
                 GLib.Variant("(sava{sv})", ("close", [], {})), None, Gio.DBusCallFlags.NONE, 800, None, None)
    except GLib.Error:
        pass


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
        drag.connect("drag-begin", lambda g, *_: setattr(self, "_start", (dock.cfg["icon_size"], self._reach(g))))
        drag.connect("drag-update", self._dragged)
        drag.connect("drag-end", lambda *_: dock.save_cfg())
        self.add_controller(drag)
        right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right.connect("pressed", lambda *_: dock_menu.divider_menu(dock, self))
        self.add_controller(right)


    def _reach(self, gesture):
        """How far the pointer is from the screen edge the Dock sits on. The
        gesture's own offsets are relative to this bar, which moves as the Dock
        grows -- the size fed back on itself and jumped up and down."""
        ev = gesture.get_current_event()
        root = self.get_root()
        if ev is None or root is None:
            return None
        ok, x, y = ev.get_position()                     # surface coordinates
        if not ok:
            return None
        edge = self.dock.edge
        if edge == "bottom":
            return root.get_height() - y
        return x if edge == "left" else root.get_width() - x

    def _dragged(self, gesture, _dx, _dy):
        start = getattr(self, "_start", None)
        now = self._reach(gesture)
        if not start or start[1] is None or now is None:
            return
        size = start[0] + (now - start[1])
        if abs(size - self.dock.cfg["icon_size"]) >= 1:
            self.dock.set_icon_size(size, save=False)


BRING_MS = 120          # a window moved to this display: Wayfire takes it and the new aim, then it comes back

# -- Show Desktop (Super+D) -------------------------------------------------------------------
# Sonata's own, not Wayfire's wm-actions one: there, bringing back any window
# brought back all of them (Vini: Super+D, then a click on one app restored
# every app). Here every window goes; a click brings back only that app's;
# Super+D again brings back the ones it hid, if nothing is showing.
_DESK = {"hidden": []}


def toggle_show_desktop(manager) -> str:
    """"hid" / "restored" / "" (nothing to do)."""
    tops = list(manager.toplevels)
    shown = [t for t in tops if not t.minimized]
    still = [t for t in _DESK["hidden"] if t in tops and t.minimized]
    if shown:
        _DESK["hidden"] = still + shown
        for d in list(_DOCKS):
            d._update_rectangles()                # each window flies to the icon on its display
        for t in shown:
            manager.minimize(t)
        return "hid"
    _DESK["hidden"] = []
    if not still:
        return ""
    for t in reversed(still):                     # the one that was in front comes back last (in front)
        manager.activate(t)
    return "restored"


# Docks of this process (one per display with all_displays) and the Wayfire
# events that move windows between displays: their genie targets follow.
_DOCKS = []
GONE_CONFIRM_MS = 20000      # an app's entry still missing this long later: uninstalled, not updating
PACKAGE_LOCKS = ("/var/lib/pacman/db.lck",)


def package_manager_busy() -> bool:
    """pacman is installing or updating (its database is locked)."""
    return any(os.path.exists(p) for p in PACKAGE_LOCKS)
BADGE_POP_MS = 320


def _badge_n(text: str) -> int:
    return 100 if text == "99+" else (int(text) if text.isdigit() else 0)
_SEEN = {"server": None, "ids": {}}   # notification ids seen per app, all Docks (badges.py)
_WATCH = {"on": False}
_ASK = object()       # "fetch Wayfire's window list now" (_update_rectangles / _windows_here)


def scope_has_window(unit) -> bool:
    """A window of that launch's processes is open (Wayfire's list: each
    window's pid, then its cgroup) -- the app is running, maybe grouped under
    another icon (its app id names another entry): never "stuck"."""
    if not unit:
        return False
    try:
        from ..wl.wfipc import WayfireIPC
        views = WayfireIPC().call("window-rules/list-views")
    except Exception:
        return False
    for v in views if isinstance(views, list) else []:
        pid = v.get("pid") if isinstance(v, dict) else None
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
                if unit in f.read():
                    return True
        except OSError:
            continue
    return False


def _watch_outputs() -> None:
    """Once per process: when a window appears or moves to another display,
    every Dock re-aims its windows' minimize targets (the window's display's
    Dock must set it; Wayfire can't translate a target from another display)."""
    if _WATCH["on"]:
        return
    _WATCH["on"] = True
    from ..wl.wfipc import WayfireIPC
    ipc = WayfireIPC()
    _WATCH["ipc"] = ipc

    def event(_msg):
        for d in list(_DOCKS):
            d._rects_soon()
    if not ipc.watch(["view-mapped", "view-set-output"], event):
        _WATCH["on"] = False


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
        self.user_size = cfg["icon_size"]      # the size chosen; cfg["icon_size"] is the size shown
        self.manager = manager if manager and manager.available else None
        self.tiles = {}       # desktop id (or bare app_id) -> DockTile (apps only)
        self.windows = {}     # same keys -> [Toplevel]
        self._starting = {}   # key -> when it was opened from here, until a window of it shows
        self.backdrop = None  # preview only: blurred wallpaper texture under the plate
        self.on_geometry = []  # callbacks when size/magnification changes
        self.on_rebuild = None  # host callback: position changed -> rebuild the Dock
        # an app uninstalled: its icon goes (no empty slot); a burst of changes -> one check
        mon = Gio.AppInfoMonitor.get()
        self._apps_src = 0

        def apps_changed(*_a):
            apps.refresh()                   # a new app's windows find their entry (app_id index)
            if not self._apps_src:
                self._apps_src = GLib.timeout_add(800, lambda: (setattr(self, "_apps_src", 0),
                                                                self._missing_soon(), False)[2])
        hid = mon.connect("changed", apps_changed)
        self._apps_mon = (mon, hid)          # undone by detach() (a rebuilt Dock isn't destroyed)
        self.connect("destroy", lambda *_: self._drop_apps_mon())
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
                              lambda _t: _open_trash(),
                              on_menu=dock_menu.trash_menu)
        self.append(self.trash)
        self.append(self._spacer())
        self._drag = None     # (key, original index) while an icon is dragged
        self.badges = {}      # desktop id -> badge text (Unity LauncherEntry)
        self.title_counts = {}  # key -> a web app's unread count from its title (badges.py)
        self._notes = badges.load()
        self._notes_mon = badges.watch(self._notes_changed)
        self._badge_cfg = config.load("notifications", {})
        self._badge_cfg_mon = config.watch("notifications", self._badge_cfg_changed)
        self._launcher_entries()
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        # only the Dock's own icon drags: a Launchpad app (which also offers
        # a string) must reach the file-list target that pins it
        drop.connect("accept", lambda _t, _d: self._drag is not None)
        drop.connect("motion", self._drag_motion)
        drop.connect("drop", self._drag_drop)
        drop.connect("enter", self._drag_motion)
        drop.connect("leave", self._drag_leave)
        self.add_controller(drop)
        dock_drop.attach_plate(self)
        dock_drop.attach_trash(self, self.trash)
        self._update_thickness()
        apps.scan()          # Flatpak / AppImage entries GIO hasn't noticed yet get their icons too
        for did in cfg["pinned"]:
            self._add_known_tile(did)
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
        self._theme_handle = ui.on_change(self._appearance_changed)
        if self.manager:
            self.manager.listeners.append(self._schedule_sync)
            self._schedule_sync()
            _DOCKS.append(self)
            _watch_outputs()

    def detach(self) -> None:
        """Stop listening to shared objects (before the Dock is replaced)."""
        if self.manager and self._schedule_sync in self.manager.listeners:
            self.manager.listeners.remove(self._schedule_sync)
        if self in _DOCKS:
            _DOCKS.remove(self)
        # everything shared that still points at this Dock: the old widget tree
        # stayed alive (and kept working) after every rebuild
        from ..ui import theme
        theme.off_change(getattr(self, "_theme_handle", None))
        self._drop_apps_mon()
        if getattr(self, "_gone_src", 0):
            GLib.source_remove(self._gone_src)
            self._gone_src = 0
        if self._apps_src:
            GLib.source_remove(self._apps_src)
            self._apps_src = 0
        for attr in ("_notes_mon", "_badge_cfg_mon"):
            if getattr(self, attr, None) is not None:
                getattr(self, attr).cancel()
                setattr(self, attr, None)
        bus, sub = getattr(self, "_launcher_sub", (None, 0))
        if sub:
            bus.signal_unsubscribe(sub)
            self._launcher_sub = (None, 0)
        if getattr(self, "_trash_mon", None) is not None:
            self._trash_mon.cancel()
            self._trash_mon = None
        self.stacks.detach()

    def _drop_apps_mon(self) -> None:
        mon, hid = getattr(self, "_apps_mon", (None, 0))
        if hid:
            mon.disconnect(hid)
            self._apps_mon = (None, 0)

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
        snap.append_color(ui.rgba("dock_material"), rect)          # ui/glass.py: on/off, how see-through
        snap.pop()
        snap.append_inset_shadow(rr, ui.rgba("highlight"), 0, 0, 0.5, 0)
        snap.append_border(_rounded(_rect(x - 0.5, y - 0.5, pw + 1, ph + 1), radius + 0.5),
                           [0.5] * 4, [ui.rgba("hairline")] * 4)
        ui.transition.snapshot_children(self, snap)      # icons glide when re-ordered

    def do_size_allocate(self, width, height, baseline) -> None:
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        self.queue_draw()     # the plate is painted from the new size
        if self._drag:
            self._drag.pop("bounds", None)     # icons moved: _folder_candidate measures again
        if getattr(self, "_mag_strength", 0) <= 0:
            # not while magnifying (every frame): done once the wave settles
            self.refit_soon()     # the screen edge may have changed (display, rotation)
            self._rects_soon()    # icons moved (fit, apps opened/closed, reorder): minimize targets too
        for cb in self.on_geometry:
            cb()

    def save_cfg(self) -> None:
        """dock.json with the chosen size (not the shrunk-to-fit one)."""
        config.save("dock", {**self.cfg, "icon_size": self.user_size})
        # folders linked with Launchpad's (folder_link.py): theirs follow
        sig = json.dumps(self.cfg.get("folders") or {}, sort_keys=True)
        if sig != getattr(self, "_folders_pushed", None):
            self._folders_pushed = sig
            from .. import folder_link
            try:
                folder_link.to_launchpad(self.cfg.get("folders"))
            except OSError as e:
                print(f"sonata2-dock: folders not shared with Launchpad: {e}", flush=True)

    # -- fit (macOS): a full Dock shrinks its icons, and grows back when apps close
    def _span(self) -> float:
        parent = self.get_parent()
        if not getattr(parent, "layer", False):       # only a Dock along a real screen edge
            return 0
        n = parent.get_height() if self.vertical else parent.get_width()
        return n - 2 * SHADOW - 2 * SCREEN_MARGIN

    def _cells_room(self, size: float) -> float:
        """How many icons of `size` fit along the edge."""
        lines = DIVIDER_W * (2 if self.recent_sep.get_visible() else 1)
        return (self._span() - 2 * PAD_SIDE - lines) / (size + 2 * TILE_PAD)

    def _fit_size(self) -> int:
        n = len(self.all_tiles())
        if self._span() <= 0 or not n:
            return self.user_size
        lines = DIVIDER_W * (2 if self.recent_sep.get_visible() else 1)
        fit = (self._span() - 2 * PAD_SIDE - lines) / n - 2 * TILE_PAD
        return int(max(MIN_SIZE, min(self.user_size, fit)))

    def refit(self) -> bool:
        self._refit_src = 0
        if self._span() <= 0:
            # the surface isn't laid out along the edge yet (login): try again
            # shortly instead of waiting for the next click to re-layout
            self._refit_tries = getattr(self, "_refit_tries", 0) + 1
            if self._refit_tries < 50:
                self._refit_src = GLib.timeout_add(100, self.refit)
            return False
        self._refit_tries = 0
        size = self._fit_size()
        if size != self.cfg["icon_size"]:
            self.set_icon_size(size, save=False, chosen=False)
            self.queue_resize()
            parent = self.get_parent()
            if parent is not None:
                parent.queue_resize()
        return False

    def refit_soon(self) -> None:
        if not getattr(self, "_refit_src", 0):
            self._refit_src = GLib.idle_add(self.refit)

    def can_pin(self, key) -> bool:
        """Room for one more pinned app (pins must fit at PIN_MIN_SIZE)."""
        if key in self.cfg["pinned"] or self._span() <= 0:
            return True
        fixed = len(self.stacks.tiles()) + 1                  # stacks + Trash
        return len(self.cfg["pinned"]) + 1 + fixed <= int(self._cells_room(PIN_MIN_SIZE))

    def set_icon_size(self, size: float, save: bool = True, chosen: bool = True) -> None:
        """Resize the Dock (divider drag, settings). chosen=False: the fit
        (the size shown changes, the size chosen stays)."""
        size = int(max(MIN_SIZE, min(MAX_SIZE, round(size))))
        if chosen:
            self.user_size = size
            size = min(size, self._fit_size())
        if size == self.cfg["icon_size"]:
            if chosen and save:
                self.save_cfg()
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
        for cb in self.on_geometry:
            cb()
        if save:
            self.save_cfg()

    def set_magnification(self, on: bool, size: int = None) -> None:
        self.cfg["magnification"] = on
        if size:
            self.cfg["magnified_size"] = int(max(self.cfg["icon_size"], min(MAX_SIZE, size)))
        self.save_cfg()
        self._mag_strength = 0.0
        self._apply_magnification()
        for cb in self.on_geometry:
            cb()

    def set_option(self, key: str, value) -> None:
        """Change a Dock setting; position/recents changes rebuild the Dock."""
        self.cfg[key] = value
        self.save_cfg()
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
        # a mouse reports up to 1000 moves a second: the wave is worked out
        # once per frame, with the latest position
        if not getattr(self, "_mag_tick", 0):
            def tick(*_a):
                self._mag_tick = 0
                self._apply_magnification()
                return GLib.SOURCE_REMOVE
            self._mag_tick = self.add_tick_callback(tick)

    def _mag_animate(self, to: float, ms: int) -> None:
        if not self.cfg["magnification"]:
            return
        if self._mag_anim:
            self._mag_anim.pause()           # a paused animation never emits "done":
        if getattr(self, "_mag_stats", None):
            self._mag_stats.stop()           # its frame counter is stopped here

        def step(v):
            self._mag_strength = v
            self._apply_magnification()
        self._mag_anim = Adw.TimedAnimation.new(self, self._mag_strength, to, ms,
                                                Adw.CallbackAnimationTarget.new(step))
        self._mag_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        stats = self._mag_stats = ui.transition.FrameStats(self, "dock magnify " + ("in" if to else "out"))
        self._mag_anim.connect("done", lambda *_: stats.stop())
        self._mag_anim.play()

    def _launcher_entries(self) -> None:
        """Badges and attention from apps (com.canonical.Unity.LauncherEntry:
        Telegram, Thunderbird, Discord, Signal...): the count as a red badge,
        "urgent" as the attention bounce -- macOS Dock behaviour."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        sub = bus.signal_subscribe(None, "com.canonical.Unity.LauncherEntry", "Update", None, None,
                                   Gio.DBusSignalFlags.NONE, self._launcher_update)
        self._launcher_sub = (bus, sub)      # unsubscribed by detach()

    def _launcher_update(self, _c, _sender, _path, _iface, _sig, params, *_d) -> None:
        uri, props = params.unpack()
        did = uri.replace("application://", "")
        key = did[:-8] if did.endswith(".desktop") else did
        count, visible = props.get("count"), props.get("count-visible")
        if count is not None or visible is not None:
            n = int(count if count is not None else 0)
            show = visible if visible is not None else bool(self.badges.get(key))
            self.badges[key] = badges.label(n) if show else ""
        self.refresh_badges()
        tile = self.tiles.get(key) or next((t for k, t in self.tiles.items()
                                            if k.lower() == key.lower()), None)
        if tile is not None and props.get("urgent"):
            tile.bounce(3 * BOUNCE_MS)

    def _notes_changed(self) -> None:
        self._notes = badges.load()
        self.refresh_badges()

    def _badge_cfg_changed(self, *_a) -> None:
        self._badge_cfg = config.load("notifications", {})
        self.refresh_badges()

    def badge_text(self, key: str, note_counts: dict) -> str:
        """The app's own count, else its web page's, else its unseen notifications
        -- none when badges are off (Settings > Notifications)."""
        tile = self.tiles.get(key)
        if not badges.allowed(getattr(self, "_badge_cfg", {}), key, tile.name if tile else ""):
            return ""
        own = self.badges.get(key)
        if own is None:
            own = next((v for k, v in self.badges.items() if k.lower() == key.lower()), None)
        if own is not None:                          # the app sends counts: its word goes (0 too)
            return own
        return badges.label(self.title_counts.get(key)) or badges.label(note_counts.get(key))

    def refresh_badges(self) -> None:
        server, notes = self._notes
        if server != _SEEN["server"]:                 # another menu bar: its ids start again
            _SEEN["server"], _SEEN["ids"] = server, {}
        names = {k: t.name for k, t in self.tiles.items()}
        for key in self._front_keys():                # in front now: what it showed is seen
            _SEEN["ids"][key] = max(_SEEN["ids"].get(key, 0), badges.newest(notes, names, key))
        counts = badges.note_counts(notes, names, _SEEN["ids"])
        for key, tile in self.tiles.items():
            if hasattr(tile.icon, "set_badge"):
                tile.icon.set_badge(self.badge_text(key, counts))

    def _front_keys(self) -> list:
        return [k for k, wins in getattr(self, "windows", {}).items() if any(t.activated for t in wins)]

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
    def _add_tile(self, key, name, gicon, info=None, icon=None) -> DockTile:
        folder = dock_folder.is_folder(key)
        tile = DockTile(self, name, gicon, lambda t: self._clicked(key, t), info,
                        on_menu=(lambda t: dock_folder.folder_menu(self, t)) if folder
                        else (lambda t: dock_menu.app_menu(self, key, t)), icon=icon)
        tile.key = key
        self.tiles[key] = tile
        if hasattr(tile.icon, "set_badge"):       # a badge that arrived before the tile
            tile.icon.set_badge(self.badge_text(key, badges.note_counts(
                self._notes[1], {key: name}, _SEEN["ids"])))
        self.insert_child_after(tile, self.sep.get_prev_sibling())   # before the divider
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(self._drag_text(key)))
        src.connect("drag-begin", self._drag_begin, tile)
        src.connect("drag-cancel", self._drag_cancel, tile)
        src.connect("drag-end", self._drag_end, tile)
        tile.add_controller(src)
        # middle-click: another window of the app (New Window), or open it
        middle = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        middle.connect("released", lambda g, *_: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                   self._middle_click(key, tile)))
        tile.add_controller(middle)
        if folder:
            return tile
        if hasattr(tile.icon, "set_locked"):
            from .. import sandbox
            tile.icon.set_locked(applock.locked(key))
            tile.icon.set_sandboxed(sandbox.running(key))
        dock_drop.attach_app(self, tile)
        from . import dock_preview
        dock_preview.attach(tile, self)           # minimized windows: previews on hover
        return tile

    def refresh_sandboxes(self) -> None:
        from .. import sandbox
        for key, tile in self.tiles.items():
            if hasattr(tile.icon, "set_sandboxed"):
                tile.icon.set_sandboxed(sandbox.running(key))

    def refresh_locks(self) -> None:
        """Locked apps' padlocks (applock.json changed)."""
        for key, tile in self.tiles.items():
            if hasattr(tile.icon, "set_locked"):
                tile.icon.set_locked(applock.locked(key))

    def _middle_click(self, key, tile) -> None:
        if not tile.info:
            return
        if self.windows.get(key):
            dock_menu.new_window(self, tile)
            tile.bounce(BOUNCE_MS)                  # feedback: something is on its way
        else:
            self._clicked(key, tile)

    def _add_known_tile(self, key) -> bool:
        """A tile for an app with a desktop entry, or an installed Steam game
        (steam_app_N: no entry; name and icon from Steam). False: unknown."""
        if key in self.tiles:
            return True
        if dock_folder.is_folder(key):
            f = self.folder(key)
            if not f:
                return False
            self._add_tile(key, f["name"], None,
                           icon=dock_folder.FolderIcon(f["apps"], self.cfg["icon_size"], f.get("locked", False)))
            return True
        info = apps.lookup(key)
        if info:
            self._add_tile(key, info.get_display_name(), icons.app_icon(info), info)
            return True
        aid = steamgames.appid(key)
        if aid and steamgames.name(aid):
            self._add_tile(key, *steamgames.shown(key))
            return True
        return False

    def _known(self, key) -> bool:
        if dock_folder.is_folder(key):
            return self.folder(key) is not None
        aid = steamgames.appid(key)
        return bool(apps.lookup(key) or (aid and steamgames.name(aid)))

    def missing(self) -> list:
        """Pinned or shown apps whose entry is gone."""
        apps.scan()
        return [k for k in dict.fromkeys(list(self.cfg["pinned"]) + list(self.tiles))
                if k not in PERMANENT and not self._known(k)
                and (k in self.cfg["pinned"] or k not in self.windows)]

    def _missing_soon(self) -> None:
        """An app's entry vanished: gone for good only if it's still gone a
        while later. Vini: an app being updated left the Dock -- the package
        manager removes the old entry and writes the new one a moment later."""
        if not self.missing() or getattr(self, "_gone_src", 0):
            return

        def confirm():
            if package_manager_busy():       # still updating: look again later
                return True
            self._gone_src = 0
            self.forget_missing()
            return False
        self._gone_src = GLib.timeout_add(GONE_CONFIRM_MS, confirm)

    def forget_missing(self) -> None:
        """Apps uninstalled (from the Trash, Launchpad, a package manager):
        their icons leave the Dock instead of leaving an empty slot."""
        apps.scan()
        gone = [k for k in list(self.cfg["pinned"]) if k not in PERMANENT and not self._known(k)]
        for key in gone:
            self.set_pinned(key, False)
        for key in [k for k in list(self.tiles) if k not in self.cfg["pinned"] and k not in self.windows
                    and k not in PERMANENT and not self._known(k)]:
            self._remove_tile(key)

    def _remove_tile(self, key) -> None:
        """The icon goes and its place closes up smoothly (macOS): an empty
        slot of the icon's size takes its place and shrinks to nothing, so
        the plate narrows and the neighbours slide together frame by frame."""
        tile = self.tiles.pop(key)
        tile.label.unparent()
        if getattr(tile, "folder_pop", None) is not None:      # a folder's kept panel
            tile.folder_pop.unparent()
            tile.folder_pop = None
        self.close_up(tile, relayout=True)

    def close_up(self, tile, relayout: bool = False) -> None:
        """`tile` leaves the Dock and its place closes up (apps, stacks)."""
        prev = tile.get_prev_sibling()
        cell = (tile.get_height() if self.vertical else tile.get_width()) if tile.get_mapped() else 0
        self.remove(tile)
        if relayout:
            self._relayout()
        if cell <= 0 or prev is None or prev.get_parent() is not self or not self.get_mapped():
            return
        slot = Gtk.Box(can_target=False, css_classes=["dock-closing-slot"])
        self.insert_child_after(slot, prev)

        def size(v, slot=slot):
            n = max(0, int(round(cell * v)))
            slot.set_size_request(-1 if self.vertical else n, n if self.vertical else -1)
        size(1.0)
        anim = Adw.TimedAnimation.new(slot, 1.0, 0.0, CLOSE_UP_MS, Adw.CallbackAnimationTarget.new(size))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        def done(_a, slot=slot):
            if slot.get_parent() is self:
                self.remove(slot)
        anim.connect("done", done)
        slot._anim = anim              # kept alive while it runs
        anim.play()

    def open_up(self, tile) -> None:
        """A new icon (a folder dropped on the Dock): its place opens, the
        neighbours sliding aside, then it fades in (Vini: it just popped in)."""
        if not self.get_mapped():
            return
        tiles = [t for t in self.app_tiles() + self.stacks.tiles() if t is not tile and t.get_mapped()]
        cell = ((tiles[0].get_height() if self.vertical else tiles[0].get_width()) if tiles
                else self.cfg["icon_size"] + 2 * TILE_PAD)
        slot = Gtk.Box(can_target=False, css_classes=["dock-closing-slot"])
        self.insert_child_after(slot, tile.get_prev_sibling())
        tile.set_visible(False)

        def size(v, slot=slot):
            n = max(0, int(round(cell * v)))
            slot.set_size_request(-1 if self.vertical else n, n if self.vertical else -1)
        size(0.0)
        grow = Adw.TimedAnimation.new(slot, 0.0, 1.0, OPEN_UP_MS, Adw.CallbackAnimationTarget.new(size))
        grow.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        def opened(_a, slot=slot):
            if slot.get_parent() is self:
                self.remove(slot)
            tile.set_opacity(0.0)
            tile.set_visible(True)
            fade = Adw.TimedAnimation.new(tile, 0.0, 1.0, OPEN_UP_MS,
                                          Adw.CallbackAnimationTarget.new(tile.set_opacity))
            tile._fade_in = fade                     # kept alive while it runs
            fade.connect("done", lambda *_a: tile.set_opacity(1.0))
            fade.play()
        grow.connect("done", opened)
        slot._anim = grow
        grow.play()

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
        gap = getattr(self, "_gap", None)      # an app being dragged in keeps its slot
        if gap is not None and gap.get_parent() is self and gap.slot >= 0:
            tiles = self.app_tiles()
            anchor = tiles[gap.slot - 1] if 0 < gap.slot <= len(tiles) else self.get_first_child()
            self.reorder_child_after(gap, anchor)
        self.refit_soon()                      # apps opened/closed: shrink or grow back

    def _save_order(self) -> None:
        """Pinned order = the tiles' order. A pinned app without a tile right
        now (not found yet) keeps its place: it used to be dropped, and came
        back later at the end -- the Dock's order "shuffled" after a log-in."""
        self.cfg["pinned"] = merge_order(self.cfg["pinned"], [t.key for t in self.app_tiles()])
        self.save_cfg()
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
        if self._drag:
            self._drag.pop("bounds", None)
        others = [t for t in self.app_tiles() if t is not tile]
        anchor = others[slot - 1] if slot else self.get_first_child()
        if anchor is not tile:
            before = ui.transition.glide_record(others, self)      # the others slide aside
            self.reorder_child_after(tile, anchor)
            ui.transition.glide_play(before, self)

    # -- an app dragged in from elsewhere (Launchpad, Files): the others make room --
    def hide_drop_gap_soon(self) -> None:
        """Leaving one drop area for the next (plate -> icon) isn't leaving the
        Dock: close the gap only if nothing asks for it again right away."""
        if getattr(self, "_gap_hide", 0):
            return
        def run():
            self._gap_hide = 0
            self.hide_drop_gap()
            return False
        self._gap_hide = GLib.timeout_add(120, run)

    def show_drop_gap(self, x: float, y: float) -> None:
        """Open an empty slot where the dragged app would land (macOS)."""
        if getattr(self, "_gap_hide", 0):
            GLib.source_remove(self._gap_hide)
            self._gap_hide = 0
        gap = getattr(self, "_gap", None)
        tiles = self.app_tiles()
        if gap is None:
            cell = tiles[0].get_width() if tiles and not self.vertical else \
                (tiles[0].get_height() if tiles else self.cfg["icon_size"] + 2 * TILE_PAD)
            gap = self._gap = Gtk.Box(can_target=False)
            gap.set_size_request(-1 if self.vertical else cell, cell if self.vertical else -1)
            gap.slot = -1
        slot = self._slot_at(x, y)
        if slot == gap.slot:
            return
        anchor = tiles[slot - 1] if slot else None
        before = ui.transition.glide_record(tiles, self)       # the others slide aside
        anchor = anchor or self.get_first_child()          # (the leading spacer)
        if gap.get_parent() is None:
            self.insert_child_after(gap, anchor)
        else:
            self.reorder_child_after(gap, anchor)
        gap.slot = slot
        ui.transition.glide_play(before, self)

    def hide_drop_gap(self) -> int:
        """Close it again; returns where it was (-1: none)."""
        if getattr(self, "_gap_hide", 0):
            GLib.source_remove(self._gap_hide)
            self._gap_hide = 0
        gap = getattr(self, "_gap", None)
        if gap is None or gap.get_parent() is None:
            return -1
        slot = gap.slot
        before = ui.transition.glide_record(self.app_tiles(), self)
        self.remove(gap)
        gap.slot = -1
        ui.transition.glide_play(before, self)
        return slot

    def pin_at(self, key, before=None, x=None, y=0.0) -> None:
        """Pin app `key` (desktop id) before tile `before`, or at (x, y)."""
        if not self.can_pin(key):
            print(f"sonata2-dock: the Dock is full; {key} not kept", flush=True)
            return
        tile = self.tiles.get(key)
        if key in self.cfg["pinned"] and tile is not None:
            # already kept: it stays where it is (Vini: dragging it in from
            # Launchpad moved it); a bounce shows where it is
            tile.bounce(BOUNCE_MS)
            return
        if tile is None:
            info = apps.lookup(key)
            if not info:
                return
            tile = self._add_tile(key, info.get_display_name(), icons.app_icon(info), info)
            tile.set_running(len(self.windows.get(key, ())))
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
        if not on and key in PERMANENT:
            return
        if on and key not in pins:
            if not self.can_pin(key):
                return
            pins.append(key)
            self._save_order()         # takes its current position
        elif not on and key in pins:
            pins.remove(key)
            if dock_folder.is_folder(key):
                self.cfg["folders"].pop(dock_folder.folder_id(key), None)
            self.save_cfg()
            if key not in self.tiles:                  # pinned but never shown (not installed)
                return
            if key not in self.windows and not self._is_recent(key):
                self._remove_tile(key)
            else:
                self._relayout()

    # -- folders ---------------------------------------------------------------
    def folder(self, key):
        """The folder's {"name", "apps"} (None: not a folder / gone)."""
        if not dock_folder.is_folder(key):
            return None
        return (self.cfg.get("folders") or {}).get(dock_folder.folder_id(key))

    def folder_tile_of(self, key):
        """The Dock icon of the folder `key` lives in (None: in none)."""
        for fid, f in (self.cfg.get("folders") or {}).items():
            if key in f.get("apps", ()):
                return self.tiles.get(dock_folder.PREFIX + fid)
        return None

    def window_count(self, key) -> int:
        """Open windows of the app -- of all its apps for a folder."""
        f = self.folder(key)
        if f is not None:
            return sum(len(self.windows.get(k, ())) for k in f["apps"])
        return len(self.windows.get(key, ()))

    def in_folder(self, key) -> bool:
        return any(key in f.get("apps", ()) for f in (self.cfg.get("folders") or {}).values())

    def make_folder(self, keys: list, name: str = None, at_key=None) -> str:
        """A folder of these pinned apps, where the first of them (or
        `at_key`) was."""
        keys = [k for k in keys if not dock_folder.is_folder(k) and k not in PERMANENT]
        if not keys:
            return None
        folders = self.cfg.setdefault("folders", {})
        fid = dock_folder.new_id(folders)
        fkey = dock_folder.PREFIX + fid
        folders[fid] = {"name": name or dock_folder.default_name(keys), "apps": list(keys)}
        pins = self.cfg["pinned"]
        at = (pins.index(at_key) if at_key in pins
              else min((pins.index(k) for k in keys if k in pins), default=len(pins)))
        pins.insert(at, fkey)
        for k in keys:
            self._unpin_into_folder(k)
        self._add_known_tile(fkey)
        if fkey in self.tiles:
            self.tiles[fkey].set_running(self.window_count(fkey))
        self.save_cfg()
        self._relayout()
        return fkey

    def _unpin_into_folder(self, key) -> None:
        """The app's own icon leaves the pinned row (it now lives in a folder);
        a running one too: its windows show on the folder (Vini)."""
        if key in self.cfg["pinned"]:
            self.cfg["pinned"].remove(key)
        if key in self.cfg["recent"]:
            self.cfg["recent"].remove(key)
        if key in self.tiles:
            self._remove_tile(key)

    def add_to_folder(self, fkey, key) -> None:
        f = self.folder(fkey)
        if f is None or dock_folder.is_folder(key) or key in PERMANENT:
            return
        if key not in f["apps"]:
            f["apps"].append(key)
        self._unpin_into_folder(key)
        self._folder_changed(fkey)

    def remove_from_folder(self, fkey, key, ungroup_last: bool = True) -> None:
        """The app comes back to the Dock right after the folder (a folder
        left with one app is ungrouped, unless ungroup_last is False)."""
        f = self.folder(fkey)
        if f is None or key not in f["apps"]:
            return
        f["apps"].remove(key)
        pins = self.cfg["pinned"]
        if key not in pins:
            pins.insert(pins.index(fkey) + 1 if fkey in pins else len(pins), key)
            self._add_known_tile(key)
            if key in self.tiles:
                self.tiles[key].set_running(self.window_count(key))
        if len(f["apps"]) <= 1 and ungroup_last:
            self.ungroup(fkey)
            return
        self._folder_changed(fkey)

    # -- an app dragged out of a folder's panel (Vini) --------------------------
    def folder_app_drag_begin(self, fkey, key, drag) -> bool:
        """Out of the folder: the app's own icon comes back to the Dock and is
        dragged like any other -- dropped on the Dock it lands there (or in
        another folder), anywhere else it stays right after the folder. A
        folder left with one app is ungrouped when the drag ends (its panel
        holds the drag's source until then)."""
        if self._drag is not None or key not in (self.folder(fkey) or {}).get("apps", ()):
            return False
        self.remove_from_folder(fkey, key, ungroup_last=False)
        tile = self.tiles.get(key)
        if tile is None:
            return False
        self._drag_begin(None, drag, tile)
        self._drag["left"] = True
        self._drag["from_folder"] = fkey
        tile.set_visible(False)                    # its slot opens where the pointer reaches the Dock
        return True

    def folder_app_drag_end(self, fkey, key) -> None:
        tile = self.tiles.get(key)
        d = self._drag
        if d is None or d.get("from_folder") != fkey:
            return                                 # (its begin was refused)
        if tile is not None:
            self._drag_end(None, None, False, tile)   # never removed from the Dock: it left a folder
        else:
            self._drag = None
        if d is not None and not d["dropped"]:
            self._save_order()

        def last():
            f = self.folder(fkey)
            if f is not None and len(f["apps"]) <= 1:
                self.ungroup(fkey)
            return False
        GLib.idle_add(last)

    def ungroup(self, fkey) -> None:
        """The folder's apps go back to the Dock in its place, popping out of
        it one after the other (Vini: the last app dragged out undid it
        with no animation)."""
        f = self.folder(fkey)
        pins = self.cfg["pinned"]
        if f is None or fkey not in pins:
            return
        at = pins.index(fkey)
        pins.remove(fkey)
        came = []
        for k in [k for k in f["apps"] if k not in pins]:
            pins.insert(at, k)
            at += 1
            fresh = k not in self.tiles
            self._add_known_tile(k)
            if fresh and k in self.tiles:
                came.append(self.tiles[k])
        self.cfg["folders"].pop(dock_folder.folder_id(fkey), None)
        if fkey in self.tiles:
            self._remove_tile(fkey)
        self.save_cfg()
        self._relayout()
        for i, t in enumerate(came):
            self.pop_in(t, i * UNGROUP_STAGGER_MS)

    def pop_in(self, tile, delay: int = 0) -> None:
        """The icon grows out of a point and fades in, overshooting a little."""
        icon = tile.icon
        if not hasattr(icon, "pop") or not self.get_mapped():
            return
        icon.pop = 0.4
        tile.set_opacity(0.0)

        def step(v):
            icon.pop = 0.4 + 0.6 * v
            tile.set_opacity(max(0.0, min(1.0, v * 1.6)))
            icon.queue_draw()

        def start():
            anim = Adw.TimedAnimation.new(tile, 0.0, 1.0, UNGROUP_MS, Adw.CallbackAnimationTarget.new(step))
            anim.set_easing(Adw.Easing.EASE_OUT_BACK)
            anim.connect("done", lambda _a: (setattr(icon, "pop", 1.0), tile.set_opacity(1.0), icon.queue_draw()))
            tile._pop_in = anim                     # kept alive while it runs
            anim.play()
            return False
        if delay:
            GLib.timeout_add(delay, start)
        else:
            start()

    def _folder_changed(self, fkey) -> None:
        f, tile = self.folder(fkey), self.tiles.get(fkey)
        if tile is not None and f is not None:
            tile.icon.set_apps(f["apps"])
            tile.icon.set_locked(f.get("locked", False))
            tile.name = f["name"]
            tile.label.set_text(f["name"])
            tile.set_running(self.window_count(fkey))
        self.save_cfg()
        self._relayout()

    def rename_folder(self, fkey, name: str) -> None:
        f = self.folder(fkey)
        name = (name or "").strip()
        if f is None or not name:
            return
        f["name"] = name
        self._folder_changed(fkey)

    def set_folder_locked(self, fkey, on: bool) -> None:
        """Lock: what's inside shows only after the password (asked by the
        menu before unlocking -- see dock_folder.folder_menu)."""
        f = self.folder(fkey)
        if f is None:
            return
        if on:
            f["locked"] = True
        else:
            f.pop("locked", None)
        self._folder_changed(fkey)

    def _drag_text(self, key) -> str:
        """What a dragged icon carries: the desktop id; a folder carries its
        name and apps (Launchpad takes it) -- not a locked one's."""
        f = self.folder(key)
        if f is not None and not f.get("locked"):
            from ..launchpad_model import encode_folder
            if not f.get("link"):                  # Launchpad's copy stays the same folder (folder_link)
                from ..folder_link import new_link
                f["link"] = new_link()
                self.save_cfg()
            return encode_folder(f["name"], f["apps"], f["link"])
        return key

    def add_folder(self, name: str, app_ids: list, before=None, x=None, y=0.0, link: str = ""):
        """A folder dragged in (from Launchpad), placed where dropped. Its
        apps' own icons leave the pinned row. Returns its key (None: no
        installed apps)."""
        keys = []
        for a in app_ids:
            if a not in keys and a not in PERMANENT and apps.lookup(a):
                keys.append(a)
        if not keys:
            return None
        fkey = self.make_folder(keys, name=name)
        if link and fkey and not any(f.get("link") == link for f in self.cfg["folders"].values()):
            self.folder(fkey)["link"] = link       # Launchpad's: the same folder from now on
            self.save_cfg()
        tile = self.tiles.get(fkey)
        if tile is not None:
            others = [t for t in self.app_tiles() if t is not tile]
            slot = others.index(before) if before in others else (
                self._slot_at(x, y, exclude=tile) if x is not None else len(others))
            self._move_to_slot(tile, slot)
            self._save_order()
        return fkey

    def open_app(self, key, near=None) -> None:
        """Open (or bring forward) an app from a folder."""
        tile = self.tiles.get(key)
        if tile is not None:
            self._clicked(key, tile)
            return
        if self.windows.get(key) and near is not None:      # running (from its folder): forward
            self._clicked(key, near)
            return
        info = apps.lookup(key)
        if not info:
            return
        if near is not None and self.cfg.get("bounce", True):
            near.bounce(BOUNCE_MS)
        try:
            info.launch([], (near or self).get_display().get_app_launch_context())
        except GLib.Error as e:
            print(f"sonata2-dock: cannot launch {info.get_id()}: {e.message}")

    # -- recents ---------------------------------------------------------------
    def _is_recent(self, key) -> bool:
        return self.cfg["show_recents"] and key in self.cfg["recent"]

    def _note_recent(self, key) -> None:
        """An unpinned app was used: most recent first, MAX_RECENTS kept."""
        if key in self.cfg["pinned"] or not apps.lookup(key) or self.in_folder(key):
            return
        rec = [k for k in self.cfg["recent"] if k != key]
        rec.insert(0, key)
        dropped, self.cfg["recent"] = rec[MAX_RECENTS:], rec[:MAX_RECENTS]
        self.save_cfg()
        for k in dropped:
            if k in self.tiles and k not in self.windows and k not in self.cfg["pinned"]:
                self._remove_tile(k)

    # -- drag to reorder -------------------------------------------------------
    def _drag_begin(self, src, drag, tile) -> None:
        self._drag = {"key": tile.key, "index": self.app_tiles().index(tile), "left": False,
                      "dropped": False}
        tile.label.popdown()
        size = self.cfg["icon_size"]
        pic = tile.icon.texture(size) if dock_folder.is_folder(tile.key) else icons.paintable(self, tile.gicon, size)
        if pic is not None:
            self._drag["icon"] = ui.drag.hang(drag, pic, size)
        else:                                       # never GTK's text drag icon (the drag's content)
            Gtk.DragIcon.get_for_drag(drag).set_child(Gtk.Box())
        tile.add_css_class("dragging")
        self._mag_animate(0.0, MAG_OUT_MS)

    def _drag_motion(self, _target, x, y):
        if not self._drag:
            return 0
        self._drag["left"] = False
        self._drag["x"], self._drag["y"] = x, y
        if self._drag.get("icon"):
            self._drag["icon"].feed(x, self)
        tile = self.tiles[self._drag["key"]]
        if not tile.get_visible():                 # back over the Dock: its slot opens again
            self._set_tile_shown(tile, True)
        if self._over_trash(x, y) and self.can_uninstall(tile):
            # over the Trash: let go to uninstall it (macOS); the icon keeps its place meanwhile
            self._hold_over(None)
            self._drag["trash"] = True
            self.trash.add_css_class(dock_drop.HOVER)
            if self.app_tiles().index(tile) != self._drag["index"]:
                self._move_to_slot(tile, self._drag["index"])
            return Gdk.DragAction.MOVE
        self._drag["trash"] = False
        self.trash.remove_css_class(dock_drop.HOVER)
        over = self._folder_candidate(tile, x, y)
        if over is not None:                       # over another app's middle: no reordering
            self._hold_over(over)
            return Gdk.DragAction.MOVE
        self._hold_over(None)
        slot = self._slot_at(x, y, exclude=tile)   # other icons whose centre is before the pointer
        if self.app_tiles().index(tile) != slot:
            self._move_to_slot(tile, slot)
        return Gdk.DragAction.MOVE

    def _over_trash(self, x, y) -> bool:
        ok, b = self.trash.compute_bounds(self)
        return bool(ok) and b.get_x() <= x <= b.get_x() + b.get_width() and \
            b.get_y() <= y <= b.get_y() + b.get_height()

    @staticmethod
    def can_uninstall(tile) -> bool:
        """A Dock icon dropped on the Trash is uninstalled (asked first): an
        installed app -- not a folder, a Steam game or Sonata's own apps."""
        from ..apps import PROTECTED
        key = tile.key or ""
        return (tile.info is not None and not dock_folder.is_folder(key) and key not in PERMANENT
                and not key.startswith(PROTECTED) and not (tile.info.get_id() or "").startswith(PROTECTED))

    def _uninstall_dropped(self, tile) -> None:
        """Dropped on the Trash: asked, then uninstalled; it leaves the Dock
        only once that's done (Vini)."""
        from .uninstall_ui import ask
        key = tile.key
        ask(tile.info, done=lambda ok: ok and self.set_pinned(key, False))

    def _folder_candidate(self, tile, x, y):
        """The pinned tile whose middle is under the pointer, if the dragged
        app may make a folder with it (or go into it)."""
        key = tile.key
        if dock_folder.is_folder(key) or key in PERMANENT or not tile.info:
            return None
        for t, (bx, by, bw, bh) in self._drop_targets(tile):
            pos, start, size = (y, by, bh) if self.vertical else (x, bx, bw)
            across, a0, alen = (x, bx, bw) if self.vertical else (y, by, bh)
            if abs(pos - (start + size / 2)) <= size * FOLDER_ZONE and a0 <= across <= a0 + alen:
                return t
        return None

    def _drop_targets(self, tile) -> list:
        """[(tile, (x, y, w, h))] a dragged app can make a folder with; measured
        once per layout (every pointer motion asked each icon before)."""
        d = self._drag if self._drag is not None else {}
        if d.get("bounds") is None:
            pins, out = self.cfg["pinned"], []
            for t in self.app_tiles():
                if t is tile or t.key not in pins or t.key in PERMANENT:
                    continue
                if not (dock_folder.is_folder(t.key) or t.info):
                    continue
                ok, b = t.compute_bounds(self)
                if ok:
                    out.append((t, (b.get_x(), b.get_y(), b.get_width(), b.get_height())))
            d["bounds"] = out
        return d["bounds"]

    def _hold_over(self, target) -> None:
        """Start (or keep) the hold timer over `target`; None clears it."""
        d = self._drag
        if d is None:
            return
        if d.get("over") is target:
            return
        if d.get("hold_src"):
            GLib.source_remove(d["hold_src"])
        d["hold_src"] = 0
        old = d.get("target")
        if old is not None:
            old.remove_css_class("folder-target")
        d["over"], d["target"] = target, None
        if target is None:
            return

        def ready():
            if self._drag is d and d.get("over") is target:
                d["target"] = target
                target.add_css_class("folder-target")
            d["hold_src"] = 0
            return False
        d["hold_src"] = GLib.timeout_add(FOLDER_HOLD_MS, ready)

    def _drop_into_folder(self, key, target) -> None:
        target.remove_css_class("folder-target")
        if dock_folder.is_folder(target.key):
            self.add_to_folder(target.key, key)
        else:
            self.make_folder([target.key, key], at_key=target.key)

    def _drag_leave(self, _target) -> None:
        self.trash.remove_css_class(dock_drop.HOVER)
        if self._drag:
            self._drag["trash"] = False
            self._hold_over(None)
            self._drag["left"] = True
            key = self._drag["key"]
            tile = self.tiles.get(key)
            if tile is not None and key not in PERMANENT:
                self._set_tile_shown(tile, False)  # dragged out: the others close up (macOS)

    def _set_tile_shown(self, tile, shown: bool) -> None:
        before = ui.transition.glide_record([t for t in self.app_tiles() if t is not tile], self)
        tile.set_visible(shown)
        ui.transition.glide_play(before, self)

    def _drag_drop(self, _target, _value, _x, _y) -> bool:
        if not self._drag:
            return False
        self._drag["dropped"] = True
        key = self._drag["key"]
        target = self._drag.get("target")
        self._hold_over(None)
        if self._drag.get("trash"):                # on the Trash: uninstall (asked first)
            self.trash.remove_css_class(dock_drop.HOVER)
            tile = self.tiles.get(key)
            if tile is not None:
                self._settle(tile, _x, _y)
                self._uninstall_dropped(tile)
            return True
        if target is not None and target.key in self.cfg["pinned"]:
            self._drop_into_folder(key, target)
            return True
        if key not in self.cfg["pinned"] and self.can_pin(key):
            self.cfg["pinned"].append(key)     # dragging a running app into place pins it
        self._save_order()
        self._settle(self.tiles.get(key), _x, _y)
        return True

    def _settle(self, tile, x, y) -> None:
        """The dropped icon glides from under the pointer into its slot
        (it used to blink in place there as the drag icon vanished)."""
        if tile is None or not tile.get_mapped():
            return
        w, h = tile.get_width(), tile.get_height()
        tile.remove_css_class("dragging")          # shown now: no empty frame between the two
        before = {tile: (x - w / 2, y - h / 2, tile.get_parent())}
        ui.transition.glide_play(before, self, SETTLE_MS)

    def _drag_cancel(self, _src, _drag, reason, tile) -> bool:
        d = self._drag
        if d and reason == Gdk.DragCancelReason.NO_TARGET and d["left"] and tile.key not in PERMANENT:
            self.set_pinned(tile.key, False)   # dragged out of the Dock: remove
            self._poof(d)                       # ...in a puff of smoke (macOS)
            return True                         # no snap-back animation
        if d:                                   # Esc / refused: put it back
            tile.set_visible(True)
            self._move_to_slot(tile, d["index"])
        return False

    def poof_at_tile(self, tile) -> None:
        """The puff where an icon was (a folder removed from its menu)."""
        ok, b = tile.compute_bounds(self) if tile.get_parent() is self else (False, None)
        self._poof({"x": b.get_x() + b.get_width() / 2, "y": b.get_y() + b.get_height() / 2} if ok else {},
                   at_pointer=False)

    def _poof_spot(self, d, mon_w: float, mon_h: float, size: float) -> tuple:
        """(x, y) on the display for the puff: the last place along the Dock
        (d["x"]/d["y"] are in the Dock's own coordinates), just off its plate
        on the screen side. The surface spans its whole edge (layer.set_edge),
        so the window's corner is the display's corner along that edge."""
        native = self.get_native()
        nw, nh = (native.get_width(), native.get_height()) if native else (self.get_width(), self.get_height())
        ox, oy = {"bottom": (0, mon_h - nh), "left": (0, 0), "right": (mon_w - nw, 0)}[self.edge]
        along = d.get("y" if self.vertical else "x")
        if along is None:
            along = (self.get_height() if self.vertical else self.get_width()) / 2
        ok, p = self.compute_point(native, Graphene.Point().init(0 if self.vertical else along,
                                                                  along if self.vertical else 0)) \
            if native else (False, None)
        dx, dy = (p.x, p.y) if ok else (0, 0)
        x0, y0, pw, ph = self.plate_rect()          # the plate inside the Dock
        if self.edge == "bottom":
            return ox + dx, oy + dy + y0 - size / 2
        if self.edge == "left":
            return ox + dx + x0 + pw + size / 2, oy + dy
        return ox + dx + x0 - size / 2, oy + dy

    def _poof(self, d, at_pointer: bool = True) -> None:
        try:
            from . import poof
            root = self.get_root()
            app = root.get_application() if root is not None else None
            surface = self.get_native().get_surface() if self.get_native() else None
            mon = self._monitor_of(surface)
            fallback = None
            if mon is not None:                          # where the Dock last saw it, beside the Dock
                g = mon.get_geometry()
                fallback = (mon, *self._poof_spot(d, g.width, g.height, poof.SIZE))
            if app is None:
                print("sonata2-dock: poof: no application", flush=True)
                return
            if at_pointer:
                w = poof.at_pointer(app, fallback)
            elif fallback is not None:
                w = poof.Poof(app, *fallback)
                w.present()
            else:
                w = None
            print(f"sonata2-dock: poof {'shown' if w is not None else 'not shown (no display found)'}", flush=True)
        except Exception as e:                           # an effect: never in the way
            print(f"sonata2-dock: poof: {e}", flush=True)

    def _drag_end(self, _src, _drag, delete, tile) -> None:
        tile.remove_css_class("dragging")
        self._hold_over(None)
        d, self._drag = self._drag, None
        # moved somewhere else that took it (Launchpad), or let go away from
        # the Dock and taken as a copy (the desktop took a folder's text:
        # Vini couldn't drag a folder off the Dock): out of the Dock (macOS)
        if d and not d["dropped"] and (delete or (d.get("left") and not d.get("from_folder"))) \
                and tile.key not in PERMANENT:
            self.set_pinned(tile.key, False)
            if d.get("left"):                      # dropped onto the desktop or another app: the puff too
                self._poof(d)
        # still in the Dock (dropped back, cancelled, or a running app that keeps its icon)
        if self.tiles.get(tile.key) is tile and not tile.get_visible():
            self._set_tile_shown(tile, True)

    # -- running apps ----------------------------------------------------------
    def _schedule_sync(self) -> None:
        # Coalesce bursts of toplevel events into one update.
        if not self._sync_src:
            self._sync_src = GLib.idle_add(self._sync)

    def _sync(self) -> bool:
        self._sync_src = 0
        # Every `done` runs this, pure title changes too (a browser's tab, a
        # terminal's prompt: several a second). What depends on titles alone
        # is the dynamic names and the title badges; regrouping, the dots, the
        # relayout and Wayfire's window list (a new IPC socket in a thread +
        # set_rectangle per window) only when windows or their states changed.
        tops = list(self.manager.toplevels)
        sig = frozenset((id(t), t.app_id, getattr(t, "states", None)) for t in tops)
        if sig == getattr(self, "_sync_sig", None):
            self._sync_titles(self.windows)
            if getattr(self, "_rects_unsure", None):
                # a window Wayfire's list didn't know by its title (which
                # display it's on): its new title may tell now
                GLib.idle_add(self._update_rectangles_bg)
            return False
        self._sync_sig, self._sync_tops = sig, tops       # (kept: no id reused while compared)
        groups = {}
        for t in self.manager.toplevels:
            key = apps.match_app_id(t.app_id) or t.app_id or "?"
            groups.setdefault(key, []).append(t)
        started = [k for k in groups if k not in self.windows]
        if set(groups) != set(self.windows):
            from .. import open_apps
            open_apps.note(list(groups))            # for "Reopen Apps" after a crash
        self.windows = groups
        for key in started:
            self._note_recent(key)
        pinned = set(self.cfg["pinned"])
        for key in [k for k in self.tiles if k not in pinned and
                    ((k not in groups and not self._is_recent(k)) or self.folder_tile_of(k))]:
            self._remove_tile(key)                 # unpinned app quit (or it lives in a folder)
        for key in groups:
            if key not in self.tiles and not self.folder_tile_of(key):   # (Vini: it left its folder)
                if self._add_known_tile(key):        # a desktop entry, or a Steam game
                    pass
                else:
                    # no desktop entry: a Steam game, a Windows program (Proton, Wine),
                    # any other app -- named by windowapps (logged: a window that should
                    # have matched an app is easy to spot)
                    print(f"sonata2-dock: no app for window app_id {key!r}", flush=True)
                    name, gicon, dynamic = windowapps.describe(key, windowapps.window_title(groups[key]))
                    self._add_tile(key, name, gicon).dynamic = dynamic
        self._sync_titles(groups, badges_too=True)   # (who is in front: its badge is seen)
        for key, tile in self.tiles.items():
            tile.set_running(self.window_count(key))
            if groups.get(key):
                self._starting.pop(key, None)      # its window showed: the launch went well
            if getattr(tile, "folder_pop", None) is not None:
                dock_folder.show_running(self, tile.folder_pop)
        self._relayout()
        GLib.idle_add(self._update_rectangles_bg)
        return False

    def _sync_titles(self, groups, badges_too=False) -> None:
        """What follows the windows' titles: dynamic names, title badges."""
        for key, tile in self.tiles.items():               # names that follow their window's title
            if getattr(tile, "dynamic", False) and key in groups:
                name = windowapps.describe(key, windowapps.window_title(groups[key]))[0]
                if name != tile.name:
                    tile.name = name
                    tile.label.set_text(name)
        counts = {k: badges.title_count([t.title for t in wins]) for k, wins in groups.items()
                  if any(badges.counted_title(t.app_id, k) for t in wins)}
        if badges_too or counts != self.title_counts:                   # (a title change is usually not a count's)
            self.title_counts = counts
            self.refresh_badges()

    def _rects_soon(self) -> None:
        """Minimize targets again once the icons settle (not while magnified:
        a zoomed icon is bigger and elsewhere than where it comes back to)."""
        if not self.manager:
            return
        if getattr(self, "_rects_src", 0):
            GLib.source_remove(self._rects_src)

        def run():
            self._rects_src = 0
            if getattr(self, "_mag_strength", 0) > 0:
                self._rects_src = GLib.timeout_add(200, run)
                return False
            return self._update_rectangles_bg()
        self._rects_src = GLib.timeout_add(150, run)

    def _update_rectangles_bg(self) -> bool:
        """As _update_rectangles, with Wayfire's window list fetched in a
        thread: icons moving (every reallocation) never wait on its IPC on the
        main loop. One fetch at a time; changes meanwhile ask once more."""
        if getattr(self, "_rects_busy", False):
            self._rects_again = True
            return False
        self._rects_busy, self._rects_again = True, False

        def work():
            from ..wl.wfipc import WayfireIPC
            views = WayfireIPC().call("window-rules/list-views")
            GLib.idle_add(done, views)

        def done(views):
            self._rects_busy = False
            if self.get_native() is not None:          # not a replaced Dock
                self._update_rectangles(views)
            if self._rects_again:
                self._update_rectangles_bg()
            return False
        import threading
        threading.Thread(target=work, daemon=True).start()
        return False

    def _update_rectangles(self, views=_ASK) -> bool:
        """Tell the compositor where each window minimizes to (its Dock icon).
        views: Wayfire's window list already fetched (default: asked here)."""
        if getattr(self, "_mag_strength", 0) > 0:        # zoomed icons: wait until they settle
            self._rects_soon()
            return False
        native = self.get_native()
        surface = native.get_surface() if native else None
        if not surface:
            return False
        mine, placed = self._windows_here(surface) if views is _ASK else self._windows_here(surface, views)
        several = self.cfg.get("all_displays", False)
        if placed is None and several:
            # Wayfire's IPC didn't answer (busy at login): each Dock would aim
            # every window at itself and the last one won -- the laptop's
            # windows flew to the other screen's icons (Vini). Try again soon.
            self._rects_tries = getattr(self, "_rects_tries", 0) + 1
            if self._rects_tries <= 10:
                GLib.timeout_add(1000, lambda: (self._update_rectangles_bg(), False)[1])
            return False
        self._rects_tries = 0
        # The rectangle is relative to the Dock's surface, and Wayfire adds
        # only the Dock's place *on its display* (foreign-toplevel.cpp:
        # get_surface_root_node()->to_global, one level up) -- the display's
        # own coordinates, the ones the genie runs in. Nothing to take out:
        # taking the display's layout origin out aimed every genie that far
        # off (Vini: the laptop's panel at x = 1920, windows flew to the left).
        ox, oy = 0, 0
        my_apps = {a for a, _t in mine}
        # apps with a window on another display: a title Wayfire hadn't seen
        # yet can't tell which of their windows is here
        elsewhere = {a for a, _t in (placed or set()) - mine}
        self._rects_unsure = placed is not None and any(
            (t.app_id, t.title) not in placed for wins in self.windows.values() for t in wins)
        for key, wins in self.windows.items():
            tile = self.tiles.get(key) or self.folder_tile_of(key)    # (in a folder: minimize into it)
            ok, b = tile.compute_bounds(native) if tile else (False, None)
            if not ok:
                continue
            for t in wins:
                here = placed is None or (t.app_id, t.title) in mine or \
                    ((t.app_id, t.title) not in placed and t.app_id in my_apps and t.app_id not in elsewhere)
                if here:
                    self.manager.set_rectangle(t, surface, b.get_x() - ox, b.get_y() - oy,
                                               b.get_width(), b.get_height())
                elif not several:
                    # a single Dock and the window on another display: Wayfire
                    # can't animate across displays -- no target, plain animation
                    self.manager.set_rectangle(t, surface, 0, 0, 0, 0)
                # (several Docks: the one on the window's display aims it; never
                #  touch it from here, or the last Dock to write would win)
        return False

    def _monitor_of(self, surface):
        """The display this Dock is on: the one its window was put on (layer
        shell). GTK's monitor "at" the surface could name the other display
        (Vini: windows on the external screen flew into the laptop's Dock --
        every minimize, the title bar's button and Super+D too)."""
        root = self.get_root()
        mon = getattr(root, "shown_on", None)
        if mon is not None and mon.is_valid():
            return mon
        return self.get_display().get_monitor_at_surface(surface) if surface else None

    def _aim_at(self, tile, wins) -> None:
        """These windows minimize to / come back from `tile` of this Dock."""
        native = self.get_native()
        surface = native.get_surface() if native else None
        ok, b = tile.compute_bounds(native) if surface and tile else (False, None)
        if not ok:
            return
        ox, oy = 0, 0                                     # (as _update_rectangles: display coordinates)
        for t in wins:
            self.manager.set_rectangle(t, surface, b.get_x() - ox, b.get_y() - oy, b.get_width(), b.get_height())

    def _here(self, wins) -> list:
        """The windows of `wins` on this Dock's display (all of them when
        Wayfire can't tell, or with a single Dock)."""
        native = self.get_native()
        surface = native.get_surface() if native else None
        if not surface or len(_DOCKS) <= 1:
            return list(wins)
        mine, placed = self._windows_here(surface)
        if placed is None:
            return list(wins)
        return [t for t in wins if (t.app_id, t.title) in mine]

    def _bring_here(self, wins) -> bool:
        """Minimized windows of an app that are on another display move to this
        Dock's (Wayfire keeps their place on the screen). True when any moved."""
        from ..wl.wfipc import WayfireIPC
        from . import monitors
        native = self.get_native()
        surface = native.get_surface() if native else None
        mon = self._monitor_of(surface)
        mine = monitors.connector(mon) if mon else ""
        if not mine:
            return False
        ipc = WayfireIPC()
        outputs = ipc.call("window-rules/list-outputs")
        views = ipc.call("window-rules/list-views")
        if not isinstance(outputs, list) or not isinstance(views, list):
            return False
        out_id = next((o.get("id") for o in outputs if o.get("name") == mine), None)
        if out_id is None:
            return False
        wanted = {(t.app_id, t.title) for t in wins}
        moved = False
        for v in views:
            if v.get("type") not in (None, "toplevel") or (v.get("app-id", ""), v.get("title", "")) not in wanted:
                continue
            if v.get("output-name") and v.get("output-name") != mine and v.get("minimized", True):
                ipc.call("window-rules/configure-view", {"id": v["id"], "output_id": out_id})
                moved = True
        return moved

    def _windows_here(self, surface, views=_ASK):
        """(app_id, title) of the windows on this Dock's display, and of all
        windows Wayfire knows; (set(), None) when Wayfire IPC can't tell.
        views: Wayfire's list already fetched (default: asked now)."""
        from ..wl.wfipc import WayfireIPC
        from . import monitors
        mon = self._monitor_of(surface)
        mine = monitors.connector(mon) if mon else ""
        if not mine:
            views = None
        elif views is _ASK:
            views = WayfireIPC().call("window-rules/list-views")
        if not isinstance(views, list):
            return set(), None
        views = [v for v in views if v.get("type") in (None, "toplevel") and v.get("output-name")]
        return ({(v.get("app-id", ""), v.get("title", "")) for v in views if v.get("output-name") == mine},
                {(v.get("app-id", ""), v.get("title", "")) for v in views})

    def _clicked(self, key, tile: DockTile) -> None:
        if dock_folder.is_folder(key):
            dock_folder.open_panel(self, tile)
            return
        wins = self.windows.get(key)
        over_launchpad = getattr(self.get_root(), "_above", False) and key != "sonata2-launchpad"
        if over_launchpad:
            close_launchpad()               # macOS: Launchpad goes, the app comes forward
        if wins:
            shown = [t for t in wins if not t.minimized]
            if self.cfg.get("click_minimizes", True) and any(t.activated for t in shown) and not over_launchpad:
                # the app in front: clicking its icon minimizes its windows (Vini)
                for d in list(_DOCKS) or [self]:
                    if d is not self:
                        d._update_rectangles()     # aimed from the display each window is on now
                # this Dock is magnified under the pointer, so its general update
                # would wait until after the minimize and the windows flew to the
                # old target, the other display's icon (Vini): the clicked icon now
                self._aim_at(tile, self._here(wins))
                for t in shown:
                    self.manager.minimize(t)
                return
            # macOS: bring all of the app's windows forward; if every window
            # is minimized, restore them. The newest window ends up focused.
            # Restored from a Dock on another display: they come to this one,
            # out of this icon (Vini: "trazer a janela pro monitor onde cliquei")
            if not shown and len(_DOCKS) > 1 and self._bring_here(wins):
                # aimed at this icon directly: the general update waits while the
                # Dock is magnified (the pointer is on it), and the old aim came
                # out of the other display's icon (Vini)
                self._aim_at(tile, wins)
                GLib.timeout_add(BRING_MS, lambda: ([self.manager.activate(t) for t in wins], False)[1])
                return
            for t in shown or wins:
                self.manager.activate(t)
        elif tile.info:
            self.launch(tile)
        elif steamgames.appid(key):                 # a Steam game kept in the Dock: start it again
            self.launch_feedback(tile)
            if not steamgames.launch(steamgames.appid(key)):
                tile._stop_bounce()

    def launch_feedback(self, tile: DockTile) -> None:
        # Bounces until the first window maps (set_running), LAUNCH_MAX_MS at most.
        if tile.key not in NO_BOUNCE and self.cfg.get("bounce", True):
            tile.bounce(LAUNCH_MAX_MS)

    def launch(self, tile: DockTile) -> None:
        """Open the app. Clicked again while the last launch never showed a
        window (Vini: Spotify started with Chrome stayed running, its window
        created but never shown, and further clicks only woke that stuck
        copy): that launch is stopped and the app opened afresh."""
        from .. import appscope
        info = tile.info
        key = tile.key
        since = self._starting.get(key)
        if (since is not None and key not in NO_BOUNCE and STUCK_S <= time.monotonic() - since <= STUCK_MAX_S
                and not self.windows.get(key) and not scope_has_window(appscope.launched.get(info.get_id() or ""))
                and appscope.stop(info.get_id() or "")):
            print(f"sonata2-dock: {key} never showed a window: stopped, opened again", flush=True)
            self._starting.pop(key, None)
            self.launch_feedback(tile)
            GLib.timeout_add(RELAUNCH_MS, lambda: (self.launch(tile), False)[1])
            return
        self._starting[key] = time.monotonic()
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
        from ..files import ops
        full = bool(ops.trash_items())               # what the Trash shows, not leftovers
        self.trash.set_gicon(Gio.ThemedIcon.new("user-trash-full" if full else "user-trash"))

    def refresh_icons(self) -> None:
        """Every app's icon again (its choice or shape changed in Settings)."""
        icons.forget_prefs()
        for key, tile in list(self.tiles.items()):
            if dock_folder.is_folder(key):
                f = self.folder(key)
                if f is not None:
                    tile.icon.set_apps(f["apps"])          # its minis, and its frame's shape
            elif tile.info is not None:
                tile.set_gicon(icons.app_icon(tile.info))

    def _appearance_changed(self) -> None:
        """Light/Dark: redraw every icon (the Trash and Launchpad have one
        version per appearance) and the Dock's own drawing."""
        stack = [self]
        while stack:
            w = stack.pop()
            if isinstance(w, DockIcon):
                w.queue_draw()
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.queue_draw()

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
                dot=DOT, dot_in=inner, dot_out=outer, dot_on="1" if cfg.get("indicators", True) else "0")


def load_config() -> dict:
    cfg = config.load("dock", DEFAULTS)
    if not cfg["pinned"]:
        cfg["pinned"] = apps.default_pins()
        config.save("dock", cfg)
    if cfg["stacks"] is None:
        cfg["stacks"] = dock_stack.default_stacks()
        config.save("dock", cfg)
    missing = [k for k in PERMANENT if k not in cfg["pinned"] and apps.lookup(k)]
    for i, k in enumerate(PERMANENT):                     # Files first, Launchpad next
        if k in missing:
            cfg["pinned"].insert(min(i, len(cfg["pinned"])), k)
    if missing:
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

    def __init__(self, app, cfg: dict, manager=None, monitor=None):
        """monitor=None: the main display, following it; else that display
        (the extra Docks of "Show the Dock on every display")."""
        self._monitor = monitor
        # resizable: lets layer-shell stretch the surface along the edge
        # (a fixed-size window would keep its natural width).
        super().__init__(application=app, title="Dock", css_classes=["sonata-dock"],
                         decorated=False, resizable=True)
        # Light/Dark: no picture cross-fade over the Dock (ui.theme wrapped it in
        # an overlay the first time: it re-laid out and the old picture stretched
        # sideways); its plate fades its own colours (ui.on_change)
        self.sonata_no_fade = True
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
        # module-wide lists and file monitors that point back at this window:
        # all undone on "destroy" (_forget), or every display hotplug (an
        # extra Dock destroyed, a new one made) leaked a whole Dock
        self._menu_closed = lambda: self._pointer(self._inside)
        ui.menu.on_closed.append(self._menu_closed)
        self._cfg_mon = config.watch("dock", self._config_changed)
        # Settings > App Icons: new icons / shapes, live
        self._icons_mon = config.watch("icons", lambda: self.dock is not None and self.dock.refresh_icons())
        self._lock_mon = config.watch("applock", lambda: self.dock is not None and self.dock.refresh_locks())
        from .. import sandbox
        self._sandbox_cb = lambda: self.dock is not None and self.dock.refresh_sandboxes()
        sandbox.listeners.append(self._sandbox_cb)
        self.connect("destroy", lambda *_: self._forget())     # (a destroy from C)
        self.rebuild()
        from . import intro
        if intro.entering() and not self.cfg["autohide"]:
            # login: out of sight until the welcome screen fades, then it
            # slides in from its edge like a hidden Dock showing; a restart
            # of Sonata: the same, as soon as it's ready
            self.dock.hide_amount = 1.0
            self.dock.queue_draw()
            intro.wait(lambda: self._slide(False), name="dock")
        intro.leave_on_signal(lambda: self._slide(True))      # restart: the old Dock slides away

    def destroy(self) -> None:
        # The lists above hold the window: GTK's "destroy" (on dispose) would
        # never come while they do -- undone here first
        self._forget()
        super().destroy()

    def _forget(self) -> None:
        """Undo __init__'s registrations (the window is going away)."""
        from .. import sandbox
        if self._menu_closed in ui.menu.on_closed:
            ui.menu.on_closed.remove(self._menu_closed)
        if self._sandbox_cb in sandbox.listeners:
            sandbox.listeners.remove(self._sandbox_cb)
        for attr in ("_cfg_mon", "_icons_mon", "_lock_mon"):
            mon = getattr(self, attr, None)
            if mon is not None:
                mon.cancel()
                setattr(self, attr, None)
        if self.dock is not None:
            self.dock.detach()

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
            if self.layer:                          # on the main display; follows it (monitors.py)
                from . import monitors
                m = self._monitor or monitors.main()
                if m is not None:
                    layer.layer_shell().set_monitor(self, m)
                    self.shown_on = m
                if self._monitor is None:
                    monitors.on_main_changed(self._move_to)
        else:
            layer.set_edge(self, edge, self._exclusive())
        self._hidden = False
        self.dock.hide_amount = 0.0
        self._geometry()
        if self.cfg["autohide"]:
            GLib.timeout_add(600, lambda: (self._pointer(self._inside), False)[1])

    REBUILD_KEYS = {"position", "show_recents"}
    LIVE_KEYS = ("icon_size", "magnification", "magnified_size", "position", "autohide",
                 "autohide_delay_ms", "show_recents", "glass", "edge_gap", "window_gap", "indicators",
                 "bounce", "click_minimizes")

    def _config_changed(self) -> None:
        """dock.json changed (Settings app): apply appearance/behaviour keys.
        The Dock's own writes (pins, recents) don't touch these, so no loop."""
        new = config.load("dock", DEFAULTS)
        d = self.dock
        # folders first: another display's Dock made / changed one (all_displays).
        # Without this the pins below saw "folder:N" as unknown, dropped it and
        # took its apps out -- Vini: making a folder made the apps vanish
        if d and isinstance(new.get("folders"), dict) and new["folders"] != self.cfg.get("folders"):
            self.cfg["folders"] = new["folders"]
            for key, tile in list(d.tiles.items()):
                f = d.folder(key)
                if f is not None:
                    tile.icon.set_apps(f["apps"])
                    tile.icon.set_locked(f.get("locked", False))
                    tile.name = f["name"]
                    tile.label.set_text(f["name"])
        # "Keep in Dock" from Launchpad writes pinned: add/remove those tiles
        if d and new["pinned"] is not None and new["pinned"] != self.cfg["pinned"]:
            # the file's pins, in its order (written by Launchpad, Settings or
            # another display's Dock); nothing written back from here -- two
            # Docks answering each other's saves lost a new folder
            gone = [k for k in self.cfg["pinned"] if k not in new["pinned"]]
            for key in new["pinned"]:
                if key not in d.tiles:
                    d._add_known_tile(key)
            self.cfg["pinned"] = list(new["pinned"])
            for key in gone:
                if key in d.tiles and key not in PERMANENT and key not in d.windows and not d._is_recent(key):
                    d._remove_tile(key)
            d._relayout()
        cur = dict(self.cfg, icon_size=d.user_size if d else self.cfg["icon_size"])
        changed = [k for k in self.LIVE_KEYS if new[k] != cur[k]]
        if not changed:
            return
        for k in changed:
            if k != "icon_size":
                self.cfg[k] = new[k]
        if "icon_size" in changed and d is not None:
            d.set_icon_size(new["icon_size"], save=False)   # the chosen size; the fit still applies
        elif "icon_size" in changed:
            self.cfg["icon_size"] = new["icon_size"]
        load_css(self.cfg)
        if self.dock is None or set(changed) & self.REBUILD_KEYS:
            self.rebuild()
            return
        # everything else applies to the live Dock (it shares self.cfg): no
        # rebuild, so dragging a Settings slider stays smooth
        d = self.dock
        d._mag_strength = 0.0
        d._apply_magnification()
        d._update_thickness()
        d.queue_resize()
        d.queue_draw()
        self._geometry()

    def _exclusive(self) -> int:
        # maximized windows stop above the Dock by the same space the Dock
        # keeps from the screen edge (one gap, both sides of the plate)
        return 0 if self.cfg["autohide"] else plate_height(self.cfg) + 2 * self.cfg["edge_gap"]

    def _thickness(self) -> int:
        return SHADOW + PAD_TOP + max_icon(self.cfg) + dot_row(self.cfg) + self.cfg["edge_gap"]

    def _move_to(self, monitor) -> None:
        """The main display changed (Settings, or it was unplugged)."""
        if monitor is None:
            return
        visible = self.get_visible()
        self.set_visible(False)
        layer.layer_shell().set_monitor(self, monitor)
        self.shown_on = monitor
        self.set_visible(visible)

    def set_above(self, on: bool) -> None:
        """Over Launchpad (OVERLAY) while it is open, TOP otherwise (fullscreen
        apps cover the Dock)."""
        self._above = on                         # auto-hide waits while Launchpad is open
        if logs.verbose():
            print(f"sonata2-dock: above Launchpad: {on}", flush=True)      # dock.log: Launchpad/Dock debugging
        LS = layer.layer_shell()
        if LS and self.layer:
            if on and LS.get_layer(self) == LS.Layer.OVERLAY:
                # already there, but Launchpad was raised over it (Wayfire raises
                # the surface you press on): changing layer again puts the Dock on top
                LS.set_layer(self, LS.Layer.TOP)
                GLib.timeout_add(16, lambda: (LS.set_layer(self, LS.Layer.OVERLAY), False)[1])
            else:
                LS.set_layer(self, LS.Layer.OVERLAY if on else LS.Layer.TOP)
        if on and self._hidden:
            self._slide(False)                   # auto-hide: show it while Launchpad is open
        elif not on and self.cfg["autohide"]:
            self._pointer(self._inside)

    def do_size_allocate(self, w, h, baseline) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        # the screen edge's length (login, display change): the Dock's own
        # allocation may not change when only this surface grows, so fit here
        if self.dock is not None and (w, h) != getattr(self, "_last_wh", None):
            self._last_wh = (w, h)
            self.dock.refit_soon()
        # the input region follows the Dock's real size -- also right after a
        # live settings change (magnification, size...), without a restart
        if not getattr(self, "_input_src", 0):
            self._input_src = GLib.idle_add(self._input_after_layout)

    def _input_after_layout(self) -> bool:
        self._input_src = 0
        self._update_input()
        return False

    def _geometry(self) -> None:
        # Fixed surface thickness = biggest possible Dock; changes only with
        # the Dock size/magnification setting, never while magnifying.
        t = self._thickness()
        if self.cfg["position"] in ("left", "right"):
            self.set_size_request(t, -1)
        else:
            self.set_size_request(-1, t)
        if self.layer:
            ex = self._exclusive()
            if ex != getattr(self, "_last_exclusive", None):   # not every magnified frame:
                self._last_exclusive = ex                      # Wayfire re-lays out windows on it
                layer.set_exclusive(self, ex)
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
        W, H = self.get_width(), self.get_height()
        if self._hidden:          # a thin strip at the screen edge, along the Dock
            x, y, w, h = {"bottom": (x, H - TRIGGER, w, TRIGGER), "left": (0, y, TRIGGER, h),
                          "right": (W - TRIGGER, y, TRIGGER, h)}[self.dock.edge]
        else:                     # down to the screen edge: the gap under the Dock
            edge = self.dock.edge # is still "the Dock" (auto-hide would flicker there)
            if edge == "bottom":
                h = H - y
            elif edge == "left":
                w, x = x + w, 0
            else:
                w = W - x
        layer.set_input_region(self, [(x, y, w, h)])

    # -- auto-hide -------------------------------------------------------------
    def _pointer(self, inside: bool) -> None:
        self._inside = inside
        if not self.cfg["autohide"]:
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        # a menu, a drag from the Dock, or Launchpad open (dragging an app out of
        # it moves the pointer "out" of the Dock): it stays
        busy = ui.menu.OPEN or (self.dock and self.dock._drag) or getattr(self, "_above", False)
        if inside and self._hidden:
            self._timer = GLib.timeout_add(self.cfg["autohide_delay_ms"], self._reveal)
        elif not inside and not self._hidden and not busy:
            self._timer = GLib.timeout_add(400, self._conceal)

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
        dock = self.dock

        def step(v):
            dock.hide_amount = v
            dock.queue_draw()
        self._hide_anim = ui.transition.tween(self, "hide", dock.hide_amount, 1.0 if hide else 0.0, HIDE_MS,
                                              step, "dock " + ("hide" if hide else "show"))
        self._update_input()
