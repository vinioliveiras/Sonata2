"""Screen sharing in the menu bar (macOS: the purple screen-sharing pill).

A browser sharing the screen opens a bar of its own ("<site> is sharing your
screen.", no app id): wl.toplevels keeps it out of the Dock and Alt+Tab
(ToplevelManager.share_bars). While one is open, a pill over the middle of
the menu bar names the site; a click brings the browser's bar to the front,
its stop button ends the sharing (stop_sharing)."""
from gi.repository import Gtk, Pango

from .capture import BarPill, stop_button


def site_of(title: str) -> str:
    """"b.siobud.com is sharing your screen." -> "b.siobud.com"."""
    from ..wl.toplevels import SHARE_BAR_TEXT
    for t in SHARE_BAR_TEXT:
        if t in (title or ""):
            return title.split(t, 1)[0].strip()
    return ""


def cast_nodes(dump) -> list:
    """PipeWire ids of the screen-sharing streams (xdg-desktop-portal-wlr's "xdpw-stream-…")."""
    out = []
    for obj in dump or []:
        props = ((obj.get("info") or {}).get("props") or {}) if isinstance(obj, dict) else {}
        if obj.get("type", "").endswith(":Node") and str(props.get("node.name", "")).startswith("xdpw-stream"):
            out.append(obj.get("id"))
    return [i for i in out if isinstance(i, int)]


def stop_sharing() -> bool:
    """Ends every screen share: its PipeWire stream goes away (the app sees
    the share end, as with its own Stop button); if that can't be done, the
    portal's capture service starts again (also ending them)."""
    import json
    import shutil
    import subprocess
    ok = False
    if shutil.which("pw-dump") and shutil.which("pw-cli"):
        try:
            dump = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout or "[]")
            for node in cast_nodes(dump):
                ok = subprocess.run(["pw-cli", "destroy", str(node)], capture_output=True,
                                    timeout=5).returncode == 0 or ok
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if not ok:
        try:
            ok = subprocess.run(["systemctl", "--user", "restart", "xdg-desktop-portal-wlr"],
                                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    return ok


class SharingControl(BarPill):
    def __init__(self, app, manager):
        super().__init__(app, "Screen Sharing", "sonata2-sharing")
        self.manager = manager
        self.box.add_css_class("share-pill")
        what = Gtk.Box(spacing=6)                        # the site: a click brings the browser's bar
        what.append(Gtk.Image(icon_name="video-display-symbolic", css_classes=["share-icon"]))
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=28, margin_end=2)
        what.append(self.label)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._front())
        what.add_controller(click)
        what.set_cursor_from_name("pointer")
        self.box.append(what)
        from .livedraw import pen_button                 # draw on the shared screen (livedraw.py)
        self.box.append(pen_button(app))
        # Stop Sharing (Vini), like the recording pill's stop button
        stop = stop_button("Stop Sharing")
        stop.connect("clicked", lambda *_: self._stop())
        self.box.append(stop)
        manager.listeners.append(self.update)
        self.update()

    def text(self) -> str:
        sites = [s for s in (site_of(b.title) for b in self.manager.share_bars) if s]
        return ", ".join(dict.fromkeys(sites)) or "Sharing Screen"

    def update(self):
        bars = self.manager.share_bars
        if bars:
            self.label.set_label(self.text())
            self.set_tooltip_text("Sharing your screen. Click for the browser's controls.")
            if not self.get_visible():
                self.show_on()
        elif self.get_visible():
            self.set_visible(False)
            from . import livedraw                       # the sharing ended: the drawing goes too
            livedraw.get(self.get_application()).stop()

    def _stop(self):
        from ..backend import system
        system.run_async(stop_sharing)

    def _front(self):
        for b in self.manager.share_bars:
            self.manager.activate(b)
