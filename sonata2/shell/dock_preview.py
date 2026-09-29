"""Previews of minimized windows in the Dock (Vini's request; macOS shows
minimized windows as separate Dock icons instead): hovering an app that has
minimized windows shows their pictures above its icon; click one to bring it
back.

Wayland gives no client pictures of other windows, so the Dock keeps a
recent picture of each window: when a window gets the focus (it's on top
then), Wayfire's IPC tells us where it is and grim captures that area,
scaled down. Needs the Wayfire `ipc` + `ipc-rules` plugins and grim;
without them there are no previews (the name label shows as before)."""
import shutil
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import apps, ui  # noqa: E402
from ..wl.wfipc import WayfireIPC  # noqa: E402

THUMB_W = 220
CAPTURE_DELAY_MS = 700          # let the window finish drawing / animating

ui.register("""
popover.dock-preview > contents { background: %(panel_material)s; border-radius: 12px; padding: 8px;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_menu)s; }
.dock-preview button.shot { padding: 4px; border-radius: 8px; background: none; border: none; box-shadow: none; }
.dock-preview button.shot:hover { background: alpha(%(label)s, 0.12); }
.dock-preview picture { border-radius: 6px; }
.dock-preview label { color: %(label)s; font-family: %(font)s; font-size: %(text_small)s; margin-top: 4px; }
""", key="dock-preview")


class WindowShots:
    """view id -> {"tex", "app", "title", "minimized"}, kept up to date."""

    def __init__(self):
        self.ipc = WayfireIPC()
        self.shots = {}
        self.enabled = self.ipc.available and shutil.which("grim") is not None
        self._pending = 0
        if self.enabled:
            self.enabled = self.ipc.watch(["view-focused", "view-minimized", "view-unmapped",
                                           "view-title-changed"], self._event)

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
        if ev == "view-focused" and view.get("type", "toplevel") == "toplevel" and not entry["minimized"]:
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

    def minimized_for(self, key: str):
        """[(view id, texture, title)] of this Dock app's minimized windows."""
        out = []
        for vid, e in self.shots.items():
            if e.get("minimized") and e.get("tex") is not None and \
                    (apps.match_app_id(e.get("app", "")) or e.get("app")) == key:
                out.append((vid, e["tex"], e.get("title", "")))
        return out


class PreviewPopover(Gtk.Popover):
    """The pictures above a Dock icon; a click restores that window."""

    def __init__(self, tile, position, shots, restore):
        super().__init__(css_classes=["dock-preview"], has_arrow=False, autohide=False, position=position)
        box = Gtk.Box(spacing=6)
        for vid, tex, title in shots[:4]:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            pic = Gtk.Picture(paintable=tex, can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
            pic.set_size_request(THUMB_W // 1.4, int(THUMB_W / 1.4 * tex.get_height() / max(1, tex.get_width())))
            col.append(pic)
            col.append(Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22))
            b = Gtk.Button(child=col, css_classes=["shot"], can_focus=False)
            b.connect("clicked", lambda _b, v=vid: (self.popdown(), restore(v)))
            box.append(b)
        self.set_child(box)
        self.set_parent(tile)
        self.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))


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
    """Hovering `tile` shows the previews of its app's minimized windows."""
    state = {"pop": None, "src": 0, "inside": False}

    def close_later():
        if state["src"]:
            GLib.source_remove(state["src"])

        def close():
            state["src"] = 0
            if state["pop"] is not None and not state["inside"]:
                state["pop"].popdown()
                state["pop"] = None
            return False
        state["src"] = GLib.timeout_add(300, close)

    def enter(*_a):
        s = shots()
        if not s.enabled or tile.key is None:
            return
        found = s.minimized_for(tile.key)
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

    motion = Gtk.EventControllerMotion()
    motion.connect("enter", enter)
    motion.connect("leave", lambda *_: close_later())
    tile.add_controller(motion)
