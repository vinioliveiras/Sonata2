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
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import apps, ui  # noqa: E402
from ..wl.wfipc import WayfireIPC  # noqa: E402

THUMB_W = 220
SHOT_W = 2 * THUMB_W            # (crisp at 2x scale)
MAX_WINDOWS = 6
REFRESH_MS = 1500               # pictures drawn again while the previews are open: live, cheaply
CAPTURE_DELAY_MS = 700          # let the window finish drawing / animating

ui.register("""
popover.dock-preview > contents { background: %(panel_material)s; border-radius: %(r_dialog)s; padding: 8px;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_menu)s; }
.dock-preview button.shot { padding: 4px; border-radius: 8px; background: none; border: none; box-shadow: none; }
.dock-preview button.shot:hover { background: alpha(%(label)s, 0.12); }
.dock-preview picture { border-radius: 6px; }
.dock-preview label { color: %(label)s; font-family: %(font)s; font-size: %(text_small)s; margin-top: 4px; }
.dock-preview .shot-frame { border-radius: 6px; background: alpha(%(label)s, 0.06); }
.dock-preview .minimized picture { opacity: 0.6; }
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
        region = f"{g['x']},{g['y']} {g['width']}x{g['height']}"
        scale = min(1.0, THUMB_W / g["width"])

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


class PreviewPopover(Gtk.Popover):
    """The windows above a Dock icon -- picture and name; a click brings that
    window forward (restored when minimized). Pictures arrive later (set_shot)."""

    PIC_W, PIC_H = int(THUMB_W / 1.4), int(THUMB_W / 1.4 * 0.625)       # a 16:10 frame for every window

    def __init__(self, tile, position, windows, restore):
        super().__init__(css_classes=["dock-preview"], has_arrow=False, autohide=False, position=position)
        box = Gtk.Box(spacing=6)
        self.pics = {}
        for w in windows:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["minimized"] if w["minimized"] else [])
            pic = Gtk.Picture(paintable=w.get("tex") or _icon(tile), can_shrink=True,
                              content_fit=Gtk.ContentFit.CONTAIN, css_classes=["shot-frame"])
            pic.set_size_request(self.PIC_W, self.PIC_H)
            col.append(pic)
            col.append(Gtk.Label(label=w["title"] or tile.name, ellipsize=Pango.EllipsizeMode.END,
                                 max_width_chars=22))
            b = Gtk.Button(child=col, css_classes=["shot"], can_focus=False, tooltip_text=w["title"] or None)
            b.connect("clicked", lambda _b, v=w["id"]: (self.popdown(), restore(v)))
            box.append(b)
            self.pics[w["id"]] = pic
        self.set_child(box)
        self.set_parent(tile)
        self.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))

    def set_shot(self, vid, tex) -> None:
        pic = self.pics.get(vid)
        if pic is not None:
            pic.set_paintable(tex)


def _icon(tile):
    """The app's icon, standing in for a window's picture until it comes."""
    theme = Gtk.IconTheme.get_for_display(tile.get_display())
    gicon = getattr(tile, "gicon", None)
    if gicon is not None:
        return theme.lookup_by_gicon(gicon, 64, tile.get_scale_factor(), Gtk.TextDirection.NONE, 0)
    return theme.lookup_icon("application-x-executable", None, 64, tile.get_scale_factor(),
                             Gtk.TextDirection.NONE, 0)


_shots = None


def shots() -> WindowShots:
    global _shots
    if _shots is None:
        _shots = WindowShots()
    return _shots


def restore(vid) -> None:
    ipc = shots().ipc
    ipc.call("wm-actions/set-minimized", {"view_id": vid, "state": False})
    ipc.call("window-rules/focus-view", {"id": vid})


def attach(tile, dock) -> None:
    """Hovering `tile` shows its app's open windows (pictures, names)."""
    state = {"pop": None, "src": 0, "inside": False, "refresh": 0}

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

    def enter(*_a):
        s = shots()
        if not s.enabled or tile.key is None or not dock.windows.get(tile.key):
            return                                   # (not running: the name label shows)
        found = s.open_windows(tile.key)
        if not found:
            return
        if state["src"]:
            GLib.source_remove(state["src"])
            state["src"] = 0
        if state["pop"] is None:
            tile.label.popdown()
            pop = PreviewPopover(tile, dock.away, found, restore)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: state.update(inside=True))
            motion.connect("leave", lambda *_: (state.update(inside=False), close_later()))
            pop.add_controller(motion)
            state["pop"] = pop
            pop.popup()
            GLib.idle_add(lambda: (tile.label.popdown(), False)[1])     # the name label yields
            refresh()                                                    # pictures now, then live
            state["refresh"] = GLib.timeout_add(REFRESH_MS, refresh)

    motion = Gtk.EventControllerMotion()
    motion.connect("enter", enter)
    motion.connect("leave", lambda *_: close_later())
    tile.add_controller(motion)
