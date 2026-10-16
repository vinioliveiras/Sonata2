"""Window previews in the Dock (Vini: like Windows' taskbar, in Sonata's
look): hovering an app shows every window it has open (minimized ones too)
above its icon, live; click one to bring it forward.

Wayland gives no client pictures of other windows. Sonata's Wayfire plugin
draws a window by itself, even covered or on another desktop
(`sonata/window-shot`, sonata-corners.cpp): the popover opens at once with
the windows' names, their pictures arrive from a thread, and are drawn
again every REFRESH_MS while it's open. Without that plugin, the older way:
a picture taken with grim when a window gets the focus (it's on top then).
Needs the Wayfire `ipc` + `ipc-rules` plugins."""
import os
import shutil
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

from .. import apps, ui  # noqa: E402
from ..i18n import _  # noqa: E402
from ..wl.wfipc import WayfireIPC  # noqa: E402

# small (Vini): a window is at most PIC_W x PIC_H -- the size of Mission
# Control's desktop pictures (spaces.THUMB_W x THUMB_H), Vini's pick
PIC_W, PIC_H = 128, 80
SHOT_W = 2 * PIC_W              # (crisp at 2x scale)
ICON_PX = 32                    # the app's icon standing in until a picture comes
MAX_WINDOWS = 6
HOVER_MS = 1000                 # the pointer rests on the icon this long first (Vini: not at once)
REFRESH_MS = 1500               # pictures drawn again while the previews are open: live, cheaply
CAPTURE_DELAY_MS = 700          # let the window finish drawing / animating

ui.register("""
popover.dock-preview > contents { background: %(panel_material)s; border-radius: %(r_dialog)s; padding: 3px;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_menu)s; }
/* a thin frame around each picture (Vini: a small border) */
.dock-preview button.shot { padding: 4px 4px 3px 4px; border-radius: 8px; background: none; border: none; box-shadow: none; }
.dock-preview button.shot:hover { background: alpha(%(label)s, 0.12); }
.dock-preview picture.shot-pic { border-radius: 5px; box-shadow: 0 0 0 0.5px %(hairline)s, 0 1px 3px rgba(0,0,0,0.2); }
.dock-preview label { color: %(label)s; font-family: %(font)s; font-size: %(text_small)s; margin-top: 3px; }
.dock-preview .minimized picture.shot-pic { opacity: 0.6; }
/* close a window from its preview (Vini): on the side of the user's window buttons; under the pointer only */
.dock-preview button.shot-close { min-width: 18px; min-height: 18px; padding: 0; margin: 2px; border-radius: 99px;
  background: %(menu_bg)s; color: %(label)s; border: none; opacity: 0;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_control)s; transition: opacity %(t_fast)s; }
.dock-preview button.shot-close image { -gtk-icon-size: 10px; }
.dock-preview .shot-item:hover button.shot-close { opacity: 1; }
.dock-preview button.shot-close:hover { background: %(sys_red)s; color: white; }
""", key="dock-preview")


class WindowShots:
    """view id -> {"tex", "app", "title", "minimized"}, kept up to date."""

    def __init__(self):
        self.ipc = WayfireIPC()
        self.shots = {}
        self.compositor = None          # sonata/window-shot answers (None: not asked yet)
        self.grim = shutil.which("grim") is not None
        self.enabled = self.ipc.available
        self._pending = 0
        if self.enabled:
            self.enabled = self.ipc.watch(["view-focused", "view-minimized", "view-unmapped",
                                           "view-title-changed"], self._event)

    # -- the compositor draws the window (sonata/window-shot) --------------------------------
    def snapshot(self, vid, done) -> None:
        """The window's picture now, from a thread; done(vid, texture) on the
        main loop. Falls back to the grim pictures when the plugin isn't there."""
        if self.compositor is False:
            return

        def work():
            path = os.path.join(GLib.get_user_runtime_dir(), f"sonata2-shot-{os.getpid()}-{vid}")
            r = WayfireIPC().call("sonata/window-shot", {"id": vid, "width": SHOT_W, "path": path})
            if not r or r.get("result") != "ok":
                if r is not None and "method" in str(r.get("error", "")).lower():
                    self.compositor = False          # no such method: an older plugin
                return
            try:
                with open(path, "rb") as f:
                    data = f.read()
                os.unlink(path)
            except OSError:
                return
            w, h = int(r.get("width", 0)), int(r.get("height", 0))
            if w <= 0 or h <= 0 or len(data) != w * h * 4:
                return
            self.compositor = True
            GLib.idle_add(self._store_raw, vid, data, w, h, done)
        threading.Thread(target=work, daemon=True).start()

    def _store_raw(self, vid, data, w, h, done) -> bool:
        tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED, GLib.Bytes.new(data), w * 4)
        self.shots.setdefault(vid, {})["tex"] = tex
        done(vid, tex)
        return False

    def open_windows(self, key: str) -> list:
        """[{"id", "title", "minimized", "tex"}] of a Dock app's open windows,
        the last used first."""
        views = self.ipc.call("window-rules/list-views")
        out = []
        for v in views if isinstance(views, list) else []:
            if v.get("type") != "toplevel" or not v.get("mapped", True) or v.get("role") not in (None, "toplevel"):
                continue
            aid = v.get("app-id", "")
            if (apps.match_app_id(aid) or aid) != key:
                continue
            vid = v.get("id")
            out.append({"id": vid, "title": v.get("title", ""), "minimized": bool(v.get("minimized")),
                        "tex": (self.shots.get(vid) or {}).get("tex"),
                        "last": v.get("last-focus-timestamp", 0)})
        out.sort(key=lambda w: -(w["last"] or 0))
        return out[:MAX_WINDOWS]

    def _event(self, msg) -> None:
        view = msg.get("view") or {}
        vid = view.get("id")
        if vid is None:
            return
        ev = msg.get("event")
        if ev == "view-unmapped":
            self.shots.pop(vid, None)
            return
        entry = self.shots.setdefault(vid, {"tex": None})
        entry.update(app=view.get("app-id", ""), title=view.get("title", ""),
                     minimized=bool(view.get("minimized")))
        if ev == "view-focused" and view.get("type", "toplevel") == "toplevel" and not entry["minimized"] \
                and self.grim and self.compositor is not True:      # (the plugin draws them itself)
            if self._pending:
                GLib.source_remove(self._pending)
            self._pending = GLib.timeout_add(CAPTURE_DELAY_MS, self._capture, vid)

    def _capture(self, vid) -> bool:
        self._pending = 0
        info = self.ipc.call("window-rules/view-info", {"id": vid})
        view = (info or {}).get("info") or {}
        g = view.get("geometry") or {}
        if not view or view.get("minimized") or not view.get("activated", True) or g.get("width", 0) < 40:
            return False
        # (whole pixels: Wayfire 0.12 reports 500.0 -- grim refused "500.0,241.5 920.0x560.0")
        region = f"{round(g['x'])},{round(g['y'])} {round(g['width'])}x{round(g['height'])}"
        scale = min(1.0, SHOT_W / g["width"])

        def work():
            try:
                r = subprocess.run(["grim", "-g", region, "-s", f"{scale:.3f}", "-t", "png", "-"],
                                   capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return
            if r.returncode == 0 and r.stdout:
                GLib.idle_add(self._store, vid, r.stdout)
        threading.Thread(target=work, daemon=True).start()
        return False

    def _store(self, vid, png: bytes) -> bool:
        try:
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
        except GLib.Error:
            return False
        if vid in self.shots:
            self.shots[vid]["tex"] = tex
        return False

    def windows_for(self, key: str):
        """[(view id, texture, title)] of this Dock app's windows that have a picture."""
        out = []
        for vid, e in self.shots.items():
            if e.get("tex") is not None and \
                    (apps.match_app_id(e.get("app", "")) or e.get("app")) == key:
                out.append((vid, e["tex"], e.get("title", "")))
        return out


class _Pic(Gtk.Picture):
    """A picture exactly `size` big. A Gtk.Picture asks for its image's own
    size (a whole window: the preview came out ~1000 px wide -- Vini: "a very
    big frame"), and a size request is only a minimum."""

    def __init__(self, **kw):
        super().__init__(can_shrink=True, **kw)
        self.size = (ICON_PX, ICON_PX)

    def do_measure(self, orientation, _for_size):
        n = self.size[0] if orientation == Gtk.Orientation.HORIZONTAL else self.size[1]
        return n, n, -1, -1


class PreviewPopover(Gtk.Popover):
    """The windows above a Dock icon -- a small picture and the name; a click
    brings that window forward (restored when minimized), x closes it.
    Pictures arrive later (set_shot); until then the app's icon, small."""

    def __init__(self, tile, position, windows, restore, close=None):
        super().__init__(css_classes=["dock-preview"], has_arrow=False, autohide=False, position=position)
        self.box = Gtk.Box(spacing=0)
        self.pics, self.items = {}, {}
        self.close_view = close
        for w in windows:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["minimized"] if w["minimized"] else [])
            slot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)       # PIC_W x PIC_H, the picture inside
            slot.set_size_request(PIC_W, PIC_H)
            pic = _Pic(content_fit=Gtk.ContentFit.CONTAIN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                       hexpand=True)
            slot.append(pic)
            col.append(slot)
            col.append(Gtk.Label(label=w["title"] or tile.name, ellipsize=Pango.EllipsizeMode.END,
                                 max_width_chars=13))
            b = Gtk.Button(child=col, css_classes=["shot"], can_focus=False, tooltip_text=w["title"] or None)
            b.connect("clicked", lambda _b, v=w["id"]: (self.popdown(), restore(v)))
            item = Gtk.Overlay(css_classes=["shot-item"])
            item.set_child(b)
            if close is not None:
                x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["shot-close"], can_focus=False,
                               halign=ui.window.close_align(), valign=Gtk.Align.START,
                               tooltip_text=_("Close Window"))
                x.connect("clicked", lambda _b, v=w["id"]: self._close(v))
                item.add_overlay(x)
            self.box.append(item)
            self.pics[w["id"]], self.items[w["id"]] = pic, item
            self.set_shot(w["id"], w.get("tex"), icon=_icon(tile))
        self.set_child(self.box)
        self.set_parent(tile)
        self.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))

    def set_shot(self, vid, tex, icon=None) -> None:
        """The window's picture, as big as fits PIC_W x PIC_H in its own shape;
        none yet: the app's icon, small, in the middle (no empty frame)."""
        pic = self.pics.get(vid)
        if pic is None:
            return
        if tex is None:
            if icon is None and pic.get_paintable() is not None:
                return                                         # (keep what's there)
            pic.set_paintable(icon)
            pic.size = (ICON_PX, ICON_PX)
            pic.queue_resize()
            pic.remove_css_class("shot-pic")
            return
        w, h = max(1, tex.get_width()), max(1, tex.get_height())
        k = min(PIC_W / w, PIC_H / h)
        pic.set_paintable(tex)
        pic.size = (max(1, round(w * k)), max(1, round(h * k)))
        pic.queue_resize()
        pic.add_css_class("shot-pic")

    def _close(self, vid) -> None:
        """x: that window closes (the app may ask to save first); its preview goes."""
        if self.close_view is not None:
            self.close_view(vid)
        item = self.items.pop(vid, None)
        self.pics.pop(vid, None)
        if item is not None:
            self.box.remove(item)
        if not self.items:
            self.popdown()


def _icon(tile):
    """The app's icon, standing in for a window's picture until it comes."""
    theme = Gtk.IconTheme.get_for_display(tile.get_display())
    gicon = getattr(tile, "gicon", None)
    if gicon is not None:
        return theme.lookup_by_gicon(gicon, ICON_PX, tile.get_scale_factor(), Gtk.TextDirection.NONE, 0)
    return theme.lookup_icon("application-x-executable", None, ICON_PX, tile.get_scale_factor(),
                             Gtk.TextDirection.NONE, 0)


_shots = None


def shots() -> WindowShots:
    global _shots
    if _shots is None:
        _shots = WindowShots()
    return _shots


def close_window(vid) -> None:
    shots().ipc.call("window-rules/close-view", {"id": vid})


def restore(vid) -> None:
    ipc = shots().ipc
    ipc.call("wm-actions/set-minimized", {"view_id": vid, "state": False})
    ipc.call("window-rules/focus-view", {"id": vid})


def attach(tile, dock) -> None:
    """Hovering `tile` shows its app's open windows (pictures, names)."""
    state = {"pop": None, "src": 0, "inside": False, "refresh": 0, "wait": 0}

    def refresh():
        pop = state["pop"]
        if pop is None:
            state["refresh"] = 0
            return False
        for vid in list(pop.pics):
            shots().snapshot(vid, lambda v, t, p=pop: state["pop"] is p and p.set_shot(v, t))
        return True

    def close_later():
        if state["src"]:
            GLib.source_remove(state["src"])

        def close():
            state["src"] = 0
            if state["pop"] is not None and not state["inside"]:
                state["pop"].popdown()
                state["pop"] = None
                if state["refresh"]:
                    GLib.source_remove(state["refresh"])
                    state["refresh"] = 0
            return False
        state["src"] = GLib.timeout_add(300, close)

    def forget(pop):
        """Closed by itself (a window picked, Esc, a click elsewhere): this
        icon's previews show again (Vini: "sometimes the hover doesn't show"
        -- the closed one was still taken as open, and hover() gave up)."""
        if state["pop"] is pop:
            state["pop"], state["inside"] = None, False
            if state["refresh"]:
                GLib.source_remove(state["refresh"])
                state["refresh"] = 0

    def hover(*_a):
        """The pointer came onto the icon: the previews after HOVER_MS, if it stays."""
        if state["src"]:                             # (back from the previews: they stay)
            GLib.source_remove(state["src"])
            state["src"] = 0
        if state["pop"] is not None or state["wait"]:
            return

        def due():
            state["wait"] = 0
            enter()
            return False
        state["wait"] = GLib.timeout_add(HOVER_MS, due)

    def left(*_a):
        if state["wait"]:
            GLib.source_remove(state["wait"])
            state["wait"] = 0
        close_later()

    def enter(*_a):
        s = shots()
        if not s.enabled or tile.key is None or not dock.windows.get(tile.key):
            return                                   # (not running: the name label shows)
        found = s.open_windows(tile.key)
        if not found:
            return                                   # (a single window gets its preview too: Vini)
        if state["src"]:
            GLib.source_remove(state["src"])
            state["src"] = 0
        if state["pop"] is None:
            tile.label.popdown()
            pop = PreviewPopover(tile, dock.away, found, restore, close_window)
            gap_from_dock(pop, tile, dock)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: state.update(inside=True))
            motion.connect("leave", lambda *_: (state.update(inside=False), close_later()))
            pop.add_controller(motion)
            pop.connect("closed", lambda p: forget(p))
            state["pop"] = pop
            pop.popup()
            GLib.idle_add(lambda: (tile.label.popdown(), False)[1])     # the name label yields
            refresh()                                                    # pictures now, then live
            state["refresh"] = GLib.timeout_add(REFRESH_MS, refresh)

    motion = Gtk.EventControllerMotion()
    motion.connect("enter", hover)
    motion.connect("leave", left)
    tile.add_controller(motion)


def gap_from_dock(pop, tile, dock) -> None:
    """The previews stand off the Dock as far as a maximized window does
    (Vini: they sat against the icon): edge_gap past the Dock's plate, not
    the icon -- the plate's inner side is SHADOW in from the Dock's window."""
    from .dock import SHADOW
    win = tile.get_root()
    edge = getattr(dock, "edge", "bottom")
    gap = (getattr(dock, "cfg", None) or {}).get("edge_gap", 8)
    ok, pt = tile.compute_point(win, Graphene.Point().init(0, 0)) if win is not None else (False, None)
    if not ok:
        return
    w, h = tile.get_width(), tile.get_height()
    rect = Gdk.Rectangle()
    if edge == "bottom":                                       # plate top, in the tile's own coordinates
        rect.x, rect.y, rect.width, rect.height = 0, round(SHADOW - pt.y) - gap, w, 1
    elif edge == "left":                                       # plate's right side
        rect.x, rect.y, rect.width, rect.height = round(win.get_width() - SHADOW - pt.x) + gap - 1, 0, 1, h
    else:                                                      # right: the plate's left side
        rect.x, rect.y, rect.width, rect.height = round(SHADOW - pt.x) - gap, 0, 1, h
    pop.set_pointing_to(rect)
