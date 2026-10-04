"""Restart, Shut Down and Log Out quit the apps first (macOS): every window
is asked to close, apps left running without one (Chrome in the background,
tray apps) get SIGTERM -- the polite quit, Chrome saves its session on it --,
and only when they are gone does the computer restart. Straight to
systemctl, they were killed with the session and Chrome asked to restore
its tabs at every start (Vini).

An app that keeps a window open (its "Save changes?" question) stops it,
like on macOS: nothing else happens, and Sonata says which app.

    end_session("restart", manager)     # manager: wl.toplevels.ToplevelManager
    quit_app(manager, windows, key)     # Quit in the Dock / app menu: one app, all of it"""
import os
import signal

from gi.repository import Gio, GLib

TIMEOUT_MS = 12000
POLL_MS = 250
OWN = "sonata2"                     # Sonata's shell: never signalled (its own apps' windows close)


def tray_pids(conn=None) -> set:
    """Processes behind the tray icons (apps that run without a window)."""
    from .tray import WATCHER, WATCHER_PATH, split_service
    try:
        conn = conn or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        items = conn.call_sync(WATCHER, WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", (WATCHER, "RegisteredStatusNotifierItems")),
                               None, Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
    except GLib.Error:
        return set()
    out = set()
    for item in items:
        name = split_service(item)[0]
        try:
            out.add(conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "GetConnectionUnixProcessID", GLib.Variant("(s)", (name,)), None,
                                   Gio.DBusCallFlags.NONE, 500, None).unpack()[0])
        except GLib.Error:
            pass
    return out


def _views() -> list:
    from ..wl.wfipc import WayfireIPC
    views = WayfireIPC().call("window-rules/list-views")
    return [v for v in views if isinstance(v, dict)] if isinstance(views, list) else []


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _app_of_view(v) -> str:
    from .. import apps
    aid = v.get("app-id") or ""
    return apps.match_app_id(aid) or aid


def app_pids(views, tray=(), key=None) -> set:
    """The apps' processes to quit: each app window's and tray icon's, never
    Sonata's own shell nor this process. key: only that app's windows."""
    pids = {v.get("pid") for v in views
            if v.get("type") == "toplevel" and isinstance(v.get("pid"), int) and v["pid"] > 1
            and OWN not in (v.get("app-id") or "") and (key is None or _app_of_view(v) == key)}
    pids |= {p for p in tray if isinstance(p, int) and p > 1}
    pids.discard(os.getpid())
    # (an X11 window's pid can be Xwayland's: it goes with the session, never before)
    return {p for p in pids if OWN not in _cmdline(p) and "Xwayland" not in _cmdline(p).split(" ")[0]}


class Quitter:
    """One Restart / Shut Down / Log Out in progress. Functions are
    injectable for the tests."""

    def __init__(self, kind, manager, on_done, on_cancel, views=_views, tray=tray_pids,
                 alive=_alive, kill=os.kill, timeout_ms=TIMEOUT_MS, only=None, key=None):
        self.kind, self.manager = kind, manager
        self.on_done, self.on_cancel = on_done, on_cancel
        self.views, self.alive, self.kill = views, alive, kill
        self.only = None if only is None else {id(t) for t in only}     # just these windows (one app)
        self.pids = app_pids(views(), tray() if key is None else (), key=key)
        self.signalled = set()
        self.asked = set()                   # windows asked to close (once each)
        self.left_ms = timeout_ms
        self.waited_ms = 0

    def windows(self) -> list:
        ts = self.manager.toplevels if self.manager else []
        if self.only is not None:                 # one app's windows, Sonata's own apps included
            return [t for t in ts if id(t) in self.only]
        return [t for t in ts if OWN not in (t.app_id or "")]

    def start(self) -> None:
        if self.tick():
            GLib.timeout_add(POLL_MS, self._poll)

    def _poll(self) -> bool:
        self.left_ms -= POLL_MS
        self.waited_ms += POLL_MS
        return self.tick()

    def tick(self) -> bool:
        """True while waiting. Each window is asked to close once (a manager
        made just now lists them a moment later); processes whose windows
        are all gone (or that never had one) get SIGTERM once."""
        for t in self.windows():
            if id(t) not in self.asked:
                self.asked.add(id(t))
                self.manager.close(t)
        with_window = {v.get("pid") for v in self.views() if v.get("type") == "toplevel"}
        for pid in self.pids - with_window - self.signalled:
            self.signalled.add(pid)
            try:
                self.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.pids = {p for p in self.pids if self.alive(p)}
        open_ = self.windows()
        if not open_ and not self.pids and self.waited_ms >= 2 * POLL_MS:
            self.on_done(self.kind)
            return False
        if self.left_ms <= 0:
            if open_:
                self.on_cancel(self.kind, open_[0])          # an app said no (unsaved work)
            else:
                self.on_done(self.kind)                      # only background stragglers: go on
            return False
        return True


CANCELLED = {"restart": "restart", "shutdown": "shut down", "logout": "log out"}


def _cancelled(kind, t) -> None:
    from .. import apps, ui
    did = apps.match_app_id(t.app_id or "")
    info = apps.lookup(did) if did else None
    name = info.get_display_name() if info else (t.title or t.app_id or "An app")
    ui.dialog.alert(f"{name} canceled {CANCELLED.get(kind, kind)}.",
                    "Quit it, then try again.", [("ok", "OK", "default")])


def quit_app(manager, windows, key: str) -> Quitter:
    """Quit (the Dock's menu, the app menu): the app's windows close, then
    the app itself gets SIGTERM if it's still running without one (Chrome in
    the background) -- macOS quits the whole app (Vini). One that keeps a
    window ("Save changes?") just stays, nothing else happens."""
    q = Quitter("quit", manager, lambda _k: None, lambda _k, _t: None, only=list(windows), key=key)
    q.start()
    return q


def end_session(kind: str, manager=None) -> Quitter:
    """Quit the apps, then sleep/restart/shut down/log out (system.power_action)."""
    from ..backend import system
    if manager is None:
        try:
            from gi.repository import Gdk
            from ..wl.toplevels import ToplevelManager
            manager = ToplevelManager(Gdk.Display.get_default())
            manager = manager if manager.available else None
        except Exception:
            manager = None
    q = Quitter(kind, manager, system.power_action, _cancelled)
    q.start()
    return q
